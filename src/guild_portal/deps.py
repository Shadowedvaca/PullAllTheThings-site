"""FastAPI dependencies shared across routes."""

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.config import get_settings
from sv_common.db.engine import get_session_factory
from sv_common.auth.sessions import AuthenticatedSession, authenticate_session
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
    token_str: str | None = None
    if credentials is not None:
        token_str = credentials.credentials
    else:
        token_str = request.cookies.get(COOKIE_NAME)

    if not token_str:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    try:
        return (await authenticate_session(token_str, db)).player
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from exc


async def get_authenticated_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedSession:
    token = credentials.credentials if credentials else request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    try:
        return await authenticate_session(token, db)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from exc


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
        return (await authenticate_session(token, db)).player
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
