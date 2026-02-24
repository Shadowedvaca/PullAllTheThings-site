"""Admin API routes — guild management (Officer+ required)."""

import logging

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from patt.deps import get_db, require_rank
from sv_common.db.models import Player
from sv_common.identity import ranks as rank_service
from sv_common.identity import members as member_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_rank(4))],  # Officer+ for all admin routes
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RankCreate(BaseModel):
    name: str
    level: int
    description: str | None = None
    discord_role_id: str | None = None


class RankUpdate(BaseModel):
    name: str | None = None
    level: int | None = None
    description: str | None = None
    discord_role_id: str | None = None


class PlayerCreate(BaseModel):
    display_name: str
    guild_rank_id: int | None = None


class PlayerUpdate(BaseModel):
    display_name: str | None = None
    guild_rank_id: int | None = None
    guild_rank_source: str | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Ranks
# ---------------------------------------------------------------------------


@router.get("/ranks")
async def list_ranks(db: AsyncSession = Depends(get_db)):
    all_ranks = await rank_service.get_all_ranks(db)
    return {
        "ok": True,
        "data": [
            {
                "id": r.id,
                "name": r.name,
                "level": r.level,
                "discord_role_id": r.discord_role_id,
                "description": r.description,
            }
            for r in all_ranks
        ],
    }


@router.post("/ranks")
async def create_rank(body: RankCreate, db: AsyncSession = Depends(get_db)):
    try:
        rank = await rank_service.create_rank(
            db,
            name=body.name,
            level=body.level,
            description=body.description,
            discord_role_id=body.discord_role_id,
        )
        return {
            "ok": True,
            "data": {"id": rank.id, "name": rank.name, "level": rank.level},
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.patch("/ranks/{rank_id}")
async def update_rank(
    rank_id: int, body: RankUpdate, db: AsyncSession = Depends(get_db)
):
    try:
        updates = body.model_dump(exclude_none=True)
        rank = await rank_service.update_rank(db, rank_id, **updates)
        return {
            "ok": True,
            "data": {"id": rank.id, "name": rank.name, "level": rank.level},
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.delete("/ranks/{rank_id}")
async def delete_rank(rank_id: int, db: AsyncSession = Depends(get_db)):
    deleted = await rank_service.delete_rank(db, rank_id)
    if not deleted:
        return {"ok": False, "error": f"Rank {rank_id} not found"}
    return {"ok": True, "data": {"deleted": True}}


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


@router.get("/members")
async def list_members(db: AsyncSession = Depends(get_db)):
    all_players = await member_service.get_all_players(db)
    return {
        "ok": True,
        "data": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "guild_rank_id": p.guild_rank_id,
                "is_active": p.is_active,
            }
            for p in all_players
        ],
    }


@router.post("/members")
async def create_member(body: PlayerCreate, db: AsyncSession = Depends(get_db)):
    try:
        player = await member_service.create_player(
            db,
            display_name=body.display_name,
            guild_rank_id=body.guild_rank_id,
        )
        return {
            "ok": True,
            "data": {
                "id": player.id,
                "display_name": player.display_name,
                "guild_rank_id": player.guild_rank_id,
            },
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.patch("/members/{player_id}")
async def update_member(
    player_id: int, body: PlayerUpdate, db: AsyncSession = Depends(get_db)
):
    try:
        updates = body.model_dump(exclude_none=True)
        player = await member_service.update_player(db, player_id, **updates)
        return {"ok": True, "data": {"id": player.id, "guild_rank_id": player.guild_rank_id}}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.get("/members/{player_id}")
async def get_member(player_id: int, db: AsyncSession = Depends(get_db)):
    from sv_common.identity import characters as char_service
    player = await member_service.get_player_by_id(db, player_id)
    if player is None:
        return {"ok": False, "error": f"Player {player_id} not found"}
    chars = await char_service.get_characters_for_player(db, player_id)
    return {
        "ok": True,
        "data": {
            "id": player.id,
            "display_name": player.display_name,
            "guild_rank_id": player.guild_rank_id,
            "characters": [
                {
                    "id": c.id,
                    "name": c.character_name,
                    "realm": c.realm_slug,
                    "removed_at": c.removed_at.isoformat() if c.removed_at else None,
                }
                for c in chars
            ],
        },
    }


# ---------------------------------------------------------------------------
# Invite codes
# ---------------------------------------------------------------------------


@router.post("/members/{player_id}/send-invite")
async def send_invite(
    player_id: int,
    request: Request,
    admin: Player = Depends(require_rank(4)),
    db: AsyncSession = Depends(get_db),
):
    """Generate an invite code for a player and DM it via Discord bot."""
    from sv_common.auth.invite_codes import generate_invite_code
    from sv_common.discord import dm as dm_module

    target = await member_service.get_player_by_id(db, player_id)
    if target is None:
        return {"ok": False, "error": f"Player {player_id} not found"}

    if target.discord_user_id is None:
        return {"ok": False, "error": "Player has no linked Discord account — cannot send DM"}

    code = await generate_invite_code(db, player_id=player_id, created_by_id=admin.id)

    base_url = str(request.base_url).rstrip("/")
    register_url = f"{base_url}/register?code={code}"

    discord_id = None
    if target.discord_user:
        discord_id = target.discord_user.discord_id

    sent = False
    if discord_id:
        try:
            from sv_common.discord.bot import bot
            sent = await dm_module.send_registration_dm(
                bot=bot,
                discord_id=discord_id,
                invite_code=code,
                register_url=register_url,
            )
        except Exception as exc:
            logger.warning("Bot DM failed for player %s: %s", player_id, exc)

    return {
        "ok": True,
        "data": {
            "code": code,
            "discord_id": discord_id,
            "dm_sent": sent,
        },
    }
