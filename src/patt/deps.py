"""FastAPI dependencies shared across routes."""

from collections.abc import AsyncGenerator

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.config import get_settings
from sv_common.db.engine import get_session_factory
from sv_common.db.models import GuildRank, Player

_bearer = HTTPBearer(auto_error=False)

COOKIE_NAME = "patt_token"


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a database session per request."""
    settings = get_settings()
    factory = get_session_factory(settings.database_url)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _decode_token(token: str) -> dict:
    """Decode and validate a JWT string. Raises jwt exceptions on failure."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


async def get_current_player(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Player:
    """Extract JWT from Authorization header or cookie, validate, return the player.

    Tries Bearer token first; falls back to the session cookie so that
    browser fetch() calls from admin pages work without a separate token.
    Raises HTTP 401 if no valid token is found.
    """
    from sv_common.auth.jwt import decode_access_token

    token_str: str | None = None
    if credentials is not None:
        token_str = credentials.credentials
    else:
        token_str = request.cookies.get(COOKIE_NAME)

    if not token_str:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        payload = decode_access_token(token_str)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")

    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload.")

    result = await db.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.website_user_id == user_id)
    )
    player = result.scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=401, detail="Player not found.")

    return player


# Alias for backward compatibility
get_current_member = get_current_player


def require_rank(min_level: int):
    """Dependency factory — raises HTTP 403 if player rank < min_level."""

    async def _check(player: Player = Depends(get_current_player)) -> Player:
        rank_level = player.guild_rank.level if player.guild_rank else 0
        if rank_level < min_level:
            raise HTTPException(
                status_code=403,
                detail=f"Requires rank level {min_level} or higher.",
            )
        return player

    return _check


# ---------------------------------------------------------------------------
# Cookie-based auth for page routes
# ---------------------------------------------------------------------------


async def get_page_member(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Player | None:
    """Read JWT from HTTP-only cookie; return player or None if not logged in."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = _decode_token(token)
        user_id = payload.get("user_id")
        if user_id is None:
            return None
        result = await db.execute(
            select(Player)
            .options(selectinload(Player.guild_rank))
            .where(Player.website_user_id == user_id)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None


async def require_page_member(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Player:
    """Cookie-based auth that raises 401 if not logged in."""
    player = await get_page_member(request, db)
    if player is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return player


def require_page_rank(min_level: int):
    """Page-route dependency factory — raises 403 if player rank < min_level."""

    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db),
    ) -> Player:
        player = await get_page_member(request, db)
        if player is None:
            raise HTTPException(status_code=401, detail="Login required.")
        rank_level = player.guild_rank.level if player.guild_rank else 0
        if rank_level < min_level:
            raise HTTPException(status_code=403, detail="Insufficient rank.")
        return player

    return _check
