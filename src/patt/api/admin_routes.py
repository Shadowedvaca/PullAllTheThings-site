"""Admin API routes — guild management (Officer+ required)."""

import logging
from datetime import date

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy import text

from patt.deps import get_db, require_rank
from sv_common.db.models import DiscordConfig, Player, Role, RaidSeason, Specialization, WowClass
from sv_common.identity import ranks as rank_service
from sv_common.identity import members as member_service
from patt.services import season_service

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
    scheduling_weight: int = 0


class RankUpdate(BaseModel):
    name: str | None = None
    level: int | None = None
    description: str | None = None
    discord_role_id: str | None = None
    scheduling_weight: int | None = None
    wow_rank_index: int | None = None


class RoleUpdate(BaseModel):
    name: str | None = None


class SeasonCreate(BaseModel):
    name: str
    start_date: date
    is_active: bool = True


class SeasonUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


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
                "scheduling_weight": r.scheduling_weight,
                "wow_rank_index": r.wow_rank_index,
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
            scheduling_weight=body.scheduling_weight,
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
            "data": {
                "id": rank.id,
                "name": rank.name,
                "level": rank.level,
                "scheduling_weight": rank.scheduling_weight,
            },
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
# Combat Roles (read + edit)
# ---------------------------------------------------------------------------


@router.get("/roles")
async def list_roles(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Role).order_by(Role.id))
    roles = list(result.scalars().all())
    return {
        "ok": True,
        "data": [{"id": r.id, "name": r.name} for r in roles],
    }


@router.patch("/roles/{role_id}")
async def update_role(
    role_id: int, body: RoleUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail=f"Role {role_id} not found")
    if body.name is not None:
        role.name = body.name
    await db.commit()
    return {"ok": True, "data": {"id": role.id, "name": role.name}}


# ---------------------------------------------------------------------------
# WoW Classes + Specializations (read-only)
# ---------------------------------------------------------------------------


@router.get("/classes")
async def list_classes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WowClass).order_by(WowClass.name)
    )
    classes = list(result.scalars().all())
    return {
        "ok": True,
        "data": [
            {"id": c.id, "name": c.name, "color_hex": c.color_hex}
            for c in classes
        ],
    }


@router.get("/specializations")
async def list_specializations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Specialization)
        .options(
            selectinload(Specialization.wow_class),
            selectinload(Specialization.default_role),
        )
        .order_by(Specialization.class_id, Specialization.name)
    )
    specs = list(result.scalars().all())
    return {
        "ok": True,
        "data": [
            {
                "id": s.id,
                "class_id": s.class_id,
                "class_name": s.wow_class.name if s.wow_class else None,
                "name": s.name,
                "default_role_id": s.default_role_id,
                "default_role": s.default_role.name if s.default_role else None,
                "wowhead_slug": s.wowhead_slug,
            }
            for s in specs
        ],
    }


# ---------------------------------------------------------------------------
# Raid Seasons
# ---------------------------------------------------------------------------


@router.get("/seasons")
async def list_seasons(db: AsyncSession = Depends(get_db)):
    seasons = await season_service.get_all_seasons(db)
    return {
        "ok": True,
        "data": [
            {
                "id": s.id,
                "name": s.name,
                "start_date": s.start_date.isoformat(),
                "is_active": s.is_active,
                "created_at": s.created_at.isoformat(),
            }
            for s in seasons
        ],
    }


@router.post("/seasons")
async def create_season(body: SeasonCreate, db: AsyncSession = Depends(get_db)):
    season = await season_service.create_season(
        db,
        name=body.name,
        start_date=body.start_date,
        is_active=body.is_active,
    )
    await db.commit()
    return {
        "ok": True,
        "data": {
            "id": season.id,
            "name": season.name,
            "start_date": season.start_date.isoformat(),
            "is_active": season.is_active,
        },
    }


@router.patch("/seasons/{season_id}")
async def update_season(
    season_id: int, body: SeasonUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(RaidSeason).where(RaidSeason.id == season_id))
    season = result.scalar_one_or_none()
    if not season:
        raise HTTPException(status_code=404, detail=f"Season {season_id} not found")
    if body.name is not None:
        season.name = body.name
    if body.is_active is not None:
        season.is_active = body.is_active
    await db.commit()
    return {
        "ok": True,
        "data": {
            "id": season.id,
            "name": season.name,
            "start_date": season.start_date.isoformat(),
            "is_active": season.is_active,
        },
    }


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
# Bot settings
# ---------------------------------------------------------------------------


@router.get("/bot-settings")
async def get_bot_settings(db: AsyncSession = Depends(get_db)):
    """Get current bot configuration."""
    result = await db.execute(select(DiscordConfig).limit(1))
    row = result.scalar_one_or_none()
    return {
        "ok": True,
        "data": {
            "bot_dm_enabled": row.bot_dm_enabled if row else False,
            "feature_invite_dm": row.feature_invite_dm if row else False,
            "feature_onboarding_dm": row.feature_onboarding_dm if row else False,
            "role_sync_interval_hours": row.role_sync_interval_hours if row else 24,
            "guild_discord_id": row.guild_discord_id if row else None,
        },
    }


_BOT_BOOL_FIELDS = {"bot_dm_enabled", "feature_invite_dm", "feature_onboarding_dm"}


@router.patch("/bot-settings")
async def update_bot_settings(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(require_rank(4)),
):
    """Update bot configuration — supports bot_dm_enabled, feature_invite_dm, feature_onboarding_dm."""
    result = await db.execute(select(DiscordConfig).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        return {"ok": False, "error": "No discord_config row found"}

    for field in _BOT_BOOL_FIELDS:
        if field in payload:
            setattr(row, field, bool(payload[field]))

    await db.commit()
    logger.info(
        "Bot settings updated by %s: %s",
        admin.display_name,
        {f: getattr(row, f) for f in _BOT_BOOL_FIELDS},
    )
    return {
        "ok": True,
        "data": {
            "bot_dm_enabled": row.bot_dm_enabled,
            "feature_invite_dm": row.feature_invite_dm,
            "feature_onboarding_dm": row.feature_onboarding_dm,
        },
    }


@router.get("/onboarding-stats")
async def get_onboarding_stats(db: AsyncSession = Depends(get_db)):
    """Get counts of onboarding sessions by state."""
    result = await db.execute(
        text("""
            SELECT state, COUNT(*) as count
            FROM guild_identity.onboarding_sessions
            WHERE state NOT IN ('provisioned', 'manually_resolved', 'declined')
            GROUP BY state
        """)
    )
    stats = {row.state: row.count for row in result}
    return {"ok": True, "data": stats}


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
