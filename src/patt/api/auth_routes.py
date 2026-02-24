"""Authentication API — register, login, profile."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_current_player, get_db
from sv_common.auth.invite_codes import consume_invite_code, validate_invite_code
from sv_common.auth.jwt import create_access_token
from sv_common.auth.passwords import hash_password, verify_password
from sv_common.db.models import Player, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RegisterBody(BaseModel):
    code: str
    discord_username: str  # Used as the login identifier (stored in User.email)
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
    - Creates a User record (email = discord_username for login lookup)
    - Links user to the player the code was generated for
    - Consumes the invite code
    - Returns a JWT
    """
    invite = await validate_invite_code(db, body.code)
    if invite is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid, expired, or already-used invite code.",
        )

    # Verify the invite is tied to a player
    if invite.player_id is None:
        raise HTTPException(
            status_code=400, detail="Invite code is not tied to a valid player."
        )

    player_result = await db.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.id == invite.player_id)
    )
    player = player_result.scalar_one_or_none()
    if player is None:
        raise HTTPException(
            status_code=400, detail="Invite code is not tied to a valid player."
        )

    if player.website_user_id is not None:
        raise HTTPException(status_code=400, detail="This player is already registered.")

    # Normalize discord_username as login identifier
    login_email = body.discord_username.lower().strip()

    # Check no existing user with this email
    existing_result = await db.execute(
        select(User).where(User.email == login_email)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail="An account with this Discord username already exists.",
        )

    # Create the User record
    user = User(email=login_email, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()

    # Link user to player
    player.website_user_id = user.id
    await db.flush()

    # Consume the invite code
    await consume_invite_code(db, body.code)

    rank_level = player.guild_rank.level if player.guild_rank else 1

    token = create_access_token(
        user_id=user.id,
        member_id=player.id,  # kept for JWT compat; resolves via user_id
        rank_level=rank_level,
    )
    return {"ok": True, "data": {"token": token}}


@router.post("/login")
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
    """Log in with Discord username + password. Returns JWT."""
    login_email = body.discord_username.lower().strip()

    user_result = await db.execute(
        select(User).where(User.email == login_email)
    )
    user = user_result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive.")

    # Find the player linked to this user
    player_result = await db.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.website_user_id == user.id)
    )
    player = player_result.scalar_one_or_none()
    if player is None:
        raise HTTPException(status_code=401, detail="No player account linked.")

    rank_level = player.guild_rank.level if player.guild_rank else 1

    token = create_access_token(
        user_id=user.id,
        member_id=player.id,
        rank_level=rank_level,
    )
    return {"ok": True, "data": {"token": token}}


@router.get("/me")
async def get_me(
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return the current authenticated player's profile."""
    result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.characters),
            selectinload(Player.discord_user),
        )
        .where(Player.id == player.id)
    )
    full_player = result.scalar_one()

    chars = []
    for pc in full_player.characters:
        c = pc.character
        if c:
            chars.append({
                "id": c.id,
                "name": c.character_name,
                "realm": c.realm_slug,
                "removed_at": c.removed_at.isoformat() if c.removed_at else None,
            })

    return {
        "ok": True,
        "data": {
            "id": full_player.id,
            "display_name": full_player.display_name,
            "discord_username": (
                full_player.discord_user.username if full_player.discord_user else None
            ),
            "rank": {
                "id": full_player.guild_rank.id,
                "name": full_player.guild_rank.name,
                "level": full_player.guild_rank.level,
            } if full_player.guild_rank else None,
            "characters": chars,
        },
    }
