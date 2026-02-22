"""Authentication API — register, login, profile."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from patt.deps import get_current_member, get_db
from sv_common.auth.invite_codes import consume_invite_code, validate_invite_code
from sv_common.auth.jwt import create_access_token
from sv_common.auth.passwords import hash_password, verify_password
from sv_common.db.models import GuildMember, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RegisterBody(BaseModel):
    code: str
    discord_username: str
    password: str


class LoginBody(BaseModel):
    discord_username: str
    password: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/register")
async def register(body: RegisterBody, db: AsyncSession = Depends(get_db)):
    """Register with an invite code + Discord username + password.

    - Validates the invite code
    - Confirms discord_username matches the member the code was generated for
    - Creates a User record, hashes password
    - Links user to guild_member, sets registered_at
    - Consumes the invite code
    - Returns a JWT
    """
    invite = await validate_invite_code(db, body.code)
    if invite is None:
        raise HTTPException(status_code=400, detail="Invalid, expired, or already-used invite code.")

    # Verify the invite is for this Discord username
    member_result = await db.execute(
        select(GuildMember).where(GuildMember.id == invite.member_id)
    )
    member = member_result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=400, detail="Invite code is not tied to a valid member.")

    if member.discord_username.lower() != body.discord_username.lower():
        raise HTTPException(
            status_code=400,
            detail="Discord username does not match the invite code.",
        )

    if member.user_id is not None:
        raise HTTPException(status_code=400, detail="This member is already registered.")

    # Create the User record
    user = User(password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()

    # Link user to member
    member.user_id = user.id
    member.registered_at = datetime.now(timezone.utc)
    await db.flush()

    # Consume the invite code
    await consume_invite_code(db, body.code)

    # Load rank level for token
    rank_level = member.rank.level if member.rank else 1

    token = create_access_token(
        user_id=user.id,
        member_id=member.id,
        rank_level=rank_level,
    )
    return {"ok": True, "data": {"token": token}}


@router.post("/login")
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
    """Log in with Discord username + password. Returns JWT."""
    result = await db.execute(
        select(GuildMember).where(
            GuildMember.discord_username == body.discord_username
        )
    )
    member = result.scalar_one_or_none()

    if member is None or member.user_id is None:
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    user_result = await db.execute(select(User).where(User.id == member.user_id))
    user = user_result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive.")

    rank_level = member.rank.level if member.rank else 1

    token = create_access_token(
        user_id=user.id,
        member_id=member.id,
        rank_level=rank_level,
    )
    return {"ok": True, "data": {"token": token}}


@router.get("/me")
async def get_me(
    member: GuildMember = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """Return the current authenticated member's profile."""
    # Eager-load characters
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.characters), selectinload(GuildMember.rank))
        .where(GuildMember.id == member.id)
    )
    full_member = result.scalar_one()

    return {
        "ok": True,
        "data": {
            "id": full_member.id,
            "discord_username": full_member.discord_username,
            "display_name": full_member.display_name,
            "discord_id": full_member.discord_id,
            "rank": {
                "id": full_member.rank.id,
                "name": full_member.rank.name,
                "level": full_member.rank.level,
            },
            "registered_at": full_member.registered_at.isoformat() if full_member.registered_at else None,
            "characters": [
                {
                    "id": c.id,
                    "name": c.name,
                    "realm": c.realm,
                    "class": c.class_,
                    "spec": c.spec,
                    "role": c.role,
                    "main_alt": c.main_alt,
                }
                for c in full_member.characters
            ],
        },
    }
