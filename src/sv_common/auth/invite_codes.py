"""Invite code generation, validation, and consumption."""

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import InviteCode

# Unambiguous chars: no 0/O, 1/I/L
_CHARSET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


def _generate_code() -> str:
    return "".join(random.choices(_CHARSET, k=_CODE_LENGTH))


async def generate_invite_code(
    db: AsyncSession,
    player_id: int,
    created_by_id: int,
    expires_hours: int = 72,
) -> str:
    """Generate an invite code for a player. Returns the code string."""
    code = _generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    invite = InviteCode(
        code=code,
        player_id=player_id,
        created_by_player_id=created_by_id,
        expires_at=expires_at,
    )
    db.add(invite)
    await db.flush()
    return code


async def validate_invite_code(db: AsyncSession, code: str) -> InviteCode | None:
    """Return the InviteCode if valid (exists, not used, not expired). Otherwise None."""
    result = await db.execute(select(InviteCode).where(InviteCode.code == code))
    invite = result.scalar_one_or_none()
    if invite is None:
        return None
    if invite.used_at is not None:
        return None
    if invite.expires_at is not None:
        now = datetime.now(timezone.utc)
        expires = invite.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now > expires:
            return None
    return invite


async def consume_invite_code(db: AsyncSession, code: str) -> InviteCode:
    """Mark the invite code as used. Returns the updated InviteCode.

    Raises ValueError if the code is invalid, already used, or expired.
    """
    invite = await validate_invite_code(db, code)
    if invite is None:
        raise ValueError(f"Invite code '{code}' is invalid, already used, or expired.")
    invite.used_at = datetime.now(timezone.utc)
    await db.flush()
    return invite
