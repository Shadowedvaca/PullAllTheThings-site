"""FastAPI dependencies shared across routes."""

from collections.abc import AsyncGenerator

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from patt.config import get_settings
from sv_common.db.engine import get_session_factory
from sv_common.db.models import GuildMember

_bearer = HTTPBearer(auto_error=False)


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


async def get_current_member(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> GuildMember:
    """Extract JWT from Authorization header, validate, return the member.

    Raises HTTP 401 if token is missing or invalid.
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    from sv_common.auth.jwt import decode_access_token

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")

    member_id = payload.get("member_id")
    if member_id is None:
        raise HTTPException(status_code=401, detail="Invalid token payload.")

    result = await db.execute(select(GuildMember).where(GuildMember.id == member_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=401, detail="Member not found.")

    return member


def require_rank(min_level: int):
    """Dependency factory — raises HTTP 403 if member rank < min_level."""

    async def _check(member: GuildMember = Depends(get_current_member)) -> GuildMember:
        from sqlalchemy.orm import selectinload
        # rank_level is in the member.rank relationship; load it if needed
        rank_level = member.rank.level if member.rank else 0
        if rank_level < min_level:
            raise HTTPException(
                status_code=403,
                detail=f"Requires rank level {min_level} or higher.",
            )
        return member

    return _check
