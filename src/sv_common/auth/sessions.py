"""Database-backed PATT authentication-session policy and revocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from guild_portal.config import get_settings
from sv_common.auth.jwt import create_access_token, decode_access_token
from sv_common.db.models import AuthSession, Player, User


class SessionAuthenticationError(jwt.InvalidTokenError):
    """A signed token does not identify a currently authorized session."""


@dataclass(frozen=True)
class IssuedSession:
    token: str
    max_age_seconds: int
    expires_at: datetime
    session_id: str


@dataclass(frozen=True)
class AuthenticatedSession:
    payload: dict
    session: AuthSession
    user: User
    player: Player


def session_lifetime_minutes(rank_level: int) -> int:
    """Return the approved absolute lifetime for the current privilege level."""
    settings = get_settings()
    if rank_level >= settings.jwt_privileged_rank_level:
        return settings.jwt_privileged_expire_minutes
    return settings.jwt_member_expire_minutes


async def issue_session_token(
    db: AsyncSession,
    *,
    user_id: int,
    member_id: int,
    rank_level: int,
) -> IssuedSession:
    """Persist an independently revocable session and return its signed JWT."""
    lifetime_minutes = session_lifetime_minutes(rank_level)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=lifetime_minutes)
    session_id = str(uuid4())
    db.add(
        AuthSession(
            id=session_id,
            user_id=user_id,
            rank_level_at_issue=rank_level,
            issued_at=now,
            expires_at=expires_at,
        )
    )
    await db.flush()
    token = create_access_token(
        user_id=user_id,
        member_id=member_id,
        rank_level=rank_level,
        expires_minutes=lifetime_minutes,
        session_id=session_id,
    )
    return IssuedSession(
        token=token,
        max_age_seconds=lifetime_minutes * 60,
        expires_at=expires_at,
        session_id=session_id,
    )


async def authenticate_session(token: str, db: AsyncSession) -> AuthenticatedSession:
    """Validate signature, session state, active user, player, and current rank."""
    payload = decode_access_token(token)
    session_id = payload.get("jti")
    user_id = payload.get("user_id")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(user_id, int)
    ):
        raise SessionAuthenticationError("Token is not bound to a session.")

    result = await db.execute(
        select(AuthSession)
        .options(
            selectinload(AuthSession.user)
            .selectinload(User.player)
            .selectinload(Player.guild_rank),
            selectinload(AuthSession.user)
            .selectinload(User.player)
            .selectinload(Player.main_character),
        )
        .where(AuthSession.id == session_id, AuthSession.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise SessionAuthenticationError("Session does not exist.")

    now = datetime.now(timezone.utc)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if session.revoked_at is not None or expires_at <= now:
        raise SessionAuthenticationError("Session is revoked or expired.")

    user = session.user
    player = user.player if user else None
    if user is None or not user.is_active or player is None:
        raise SessionAuthenticationError("Account is inactive or unlinked.")

    current_rank = player.guild_rank.level if player.guild_rank else 0
    if current_rank != session.rank_level_at_issue:
        await revoke_session(db, session.id, "privilege_change")
        raise SessionAuthenticationError("Privilege changed; login is required.")

    return AuthenticatedSession(
        payload=payload, session=session, user=user, player=player
    )


async def revoke_session(db: AsyncSession, session_id: str, reason: str) -> None:
    """Revoke one session if it is still active."""
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
    )
    await db.flush()


async def revoke_token(db: AsyncSession, token: str, reason: str = "logout") -> None:
    """Best-effort revocation for a signed token, including an expired token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return
    session_id = payload.get("jti")
    if isinstance(session_id, str) and session_id:
        await revoke_session(db, session_id, reason)


async def revoke_all_sessions(db: AsyncSession, user_id: int, reason: str) -> None:
    """Revoke every active session for one user."""
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc), revoked_reason=reason)
    )
    await db.flush()
