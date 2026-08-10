"""PostgreSQL integration coverage for revocable authentication sessions."""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.auth.passwords import hash_password
from sv_common.auth.sessions import (
    SessionAuthenticationError,
    authenticate_session,
    issue_session_token,
    revoke_token,
)
from sv_common.db.models import AuthSession, GuildRank, Player, User


async def _identity(db: AsyncSession, *, rank_level: int = 3):
    rank = GuildRank(
        name=f"Session Rank {rank_level}",
        level=rank_level,
        description="Synthetic session test rank",
    )
    db.add(rank)
    await db.flush()
    user = User(
        email=f"session-{rank_level}@test.invalid",
        password_hash=hash_password("synthetic-password"),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    player = Player(
        display_name=f"Session Player {rank_level}",
        guild_rank_id=rank.id,
        website_user_id=user.id,
    )
    db.add(player)
    await db.flush()
    player.guild_rank = rank
    user.player = player
    return user, player


@pytest.mark.asyncio
async def test_member_session_authenticates_and_logout_revokes(
    db_session: AsyncSession,
):
    user, player = await _identity(db_session)
    issued = await issue_session_token(
        db_session,
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
    )
    assert (await authenticate_session(issued.token, db_session)).player.id == player.id

    await revoke_token(db_session, issued.token)
    with pytest.raises(SessionAuthenticationError):
        await authenticate_session(issued.token, db_session)


@pytest.mark.asyncio
async def test_inactive_user_is_denied_even_if_token_signature_is_valid(
    db_session: AsyncSession,
):
    user, player = await _identity(db_session)
    issued = await issue_session_token(
        db_session,
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
    )
    user.is_active = False
    await db_session.flush()
    with pytest.raises(SessionAuthenticationError):
        await authenticate_session(issued.token, db_session)


@pytest.mark.asyncio
async def test_password_change_trigger_revokes_existing_sessions(
    db_session: AsyncSession,
):
    user, player = await _identity(db_session)
    issued = await issue_session_token(
        db_session,
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
    )
    user.password_hash = hash_password("different-synthetic-password")
    await db_session.flush()
    db_session.expire_all()

    result = await db_session.execute(
        select(AuthSession).where(AuthSession.id == issued.session_id)
    )
    session = result.scalar_one()
    assert session.revoked_at is not None
    assert session.revoked_reason == "password_change"


@pytest.mark.asyncio
async def test_rank_change_trigger_revokes_and_requires_reauthentication(
    db_session: AsyncSession,
):
    user, player = await _identity(db_session, rank_level=3)
    issued = await issue_session_token(
        db_session,
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
    )
    officer = GuildRank(name="Session Officer", level=4)
    db_session.add(officer)
    await db_session.flush()
    player.guild_rank_id = officer.id
    await db_session.flush()
    db_session.expire_all()

    result = await db_session.execute(
        select(AuthSession).where(AuthSession.id == issued.session_id)
    )
    session = result.scalar_one()
    assert session.revoked_reason == "privilege_change"


@pytest.mark.asyncio
async def test_api_login_returns_privileged_absolute_expiry(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user, _player = await _identity(db_session, rank_level=4)
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "discord_username": user.email,
            "password": "synthetic-password",
        },
    )
    assert response.status_code == 200
    expires_at = datetime.fromisoformat(response.json()["data"]["expires_at"])
    remaining = expires_at - datetime.now(timezone.utc)
    assert 11.9 * 60 * 60 < remaining.total_seconds() <= 12 * 60 * 60


@pytest.mark.asyncio
async def test_cross_origin_cookie_logout_is_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user, player = await _identity(db_session)
    issued = await issue_session_token(
        db_session,
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
    )
    response = await client.post(
        "/logout",
        cookies={"patt_token": issued.token},
        headers={"Origin": "https://attacker.invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 403
