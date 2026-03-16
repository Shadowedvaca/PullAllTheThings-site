"""Admin API routes — guild management (Officer+ required)."""

import logging
from datetime import date, datetime, time, timezone
from typing import Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sqlalchemy import text

from guild_portal.deps import get_db, require_rank
from sv_common.config_cache import set_site_config
from sv_common.db.models import (
    DiscordConfig, GuildRank, Player, PlayerAvailability, RaidAttendance, RaidEvent, RecurringEvent,
    Role, RaidSeason, ScreenPermission, SiteConfig, Specialization, WowCharacter, WowClass,
)
from sv_common.identity import ranks as rank_service
from sv_common.identity import members as member_service
from guild_portal.services import season_service

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
    expansion_name: str
    season_number: int
    start_date: date
    is_new_expansion: bool = False
    is_active: bool = True


class SeasonUpdate(BaseModel):
    expansion_name: str | None = None
    season_number: int | None = None
    is_new_expansion: bool | None = None
    is_active: bool | None = None
    blizzard_mplus_season_id: int | None = None


class PlayerCreate(BaseModel):
    display_name: str
    guild_rank_id: int | None = None


class PlayerUpdate(BaseModel):
    display_name: str | None = None
    guild_rank_id: int | None = None
    guild_rank_source: str | None = None
    is_active: bool | None = None


class RecurringEventCreate(BaseModel):
    label: str
    event_type: str = "raid"
    day_of_week: int
    default_start_time: str = "21:00"  # "HH:MM"
    default_duration_minutes: int = 120
    discord_channel_id: str | None = None
    raid_helper_template_id: str | None = "wowretail2"
    is_active: bool = True
    display_on_public: bool = True


class RecurringEventUpdate(BaseModel):
    label: str | None = None
    event_type: str | None = None
    day_of_week: int | None = None
    default_start_time: str | None = None
    default_duration_minutes: int | None = None
    discord_channel_id: str | None = None
    raid_helper_template_id: str | None = None
    is_active: bool | None = None
    display_on_public: bool | None = None


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
                "expansion_name": s.expansion_name,
                "season_number": s.season_number,
                "display_name": s.display_name,
                "start_date": s.start_date.isoformat(),
                "is_new_expansion": s.is_new_expansion,
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
        expansion_name=body.expansion_name,
        season_number=body.season_number,
        start_date=body.start_date,
        is_new_expansion=body.is_new_expansion,
        is_active=body.is_active,
    )
    await db.commit()
    return {
        "ok": True,
        "data": {
            "id": season.id,
            "expansion_name": season.expansion_name,
            "season_number": season.season_number,
            "display_name": season.display_name,
            "start_date": season.start_date.isoformat(),
            "is_new_expansion": season.is_new_expansion,
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
    if body.expansion_name is not None:
        season.expansion_name = body.expansion_name
    if body.season_number is not None:
        season.season_number = body.season_number
    if body.is_new_expansion is not None:
        season.is_new_expansion = body.is_new_expansion
    if body.is_active is not None:
        season.is_active = body.is_active
    if "blizzard_mplus_season_id" in body.model_fields_set:
        season.blizzard_mplus_season_id = body.blizzard_mplus_season_id
    await db.commit()
    return {
        "ok": True,
        "data": {
            "id": season.id,
            "expansion_name": season.expansion_name,
            "season_number": season.season_number,
            "display_name": season.display_name,
            "start_date": season.start_date.isoformat(),
            "is_new_expansion": season.is_new_expansion,
            "is_active": season.is_active,
            "blizzard_mplus_season_id": season.blizzard_mplus_season_id,
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
            "has_bot_token": bool(row.bot_token_encrypted) if row else False,
        },
    }


@router.patch("/bot-connection")
async def update_bot_connection(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(require_rank(5)),
):
    """Update bot token and/or Discord guild ID. Guild Leader only.
    Changes take effect after the app restarts (bot reconnects on startup).
    """
    from sv_common.crypto import encrypt_secret
    from guild_portal.config import get_settings

    result = await db.execute(select(DiscordConfig).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        settings = get_settings()
        row = DiscordConfig(guild_discord_id=settings.discord_guild_id or "0")
        db.add(row)
        await db.flush()

    if payload.get("bot_token", "").strip():
        settings = get_settings()
        row.bot_token_encrypted = encrypt_secret(
            payload["bot_token"].strip(), settings.jwt_secret_key
        )
        logger.info("Bot token updated by %s", admin.display_name)

    if payload.get("discord_guild_id", "").strip():
        row.guild_discord_id = payload["discord_guild_id"].strip()
        logger.info(
            "Discord guild ID updated to %s by %s",
            row.guild_discord_id, admin.display_name,
        )

    await db.commit()
    return {
        "ok": True,
        "data": {
            "has_bot_token": bool(row.bot_token_encrypted),
            "guild_discord_id": row.guild_discord_id,
        },
    }


_BOT_BOOL_FIELDS = {"bot_dm_enabled", "feature_invite_dm", "feature_onboarding_dm"}


@router.patch("/bot-settings")
async def update_bot_settings(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(require_rank(4)),
):
    """Update bot configuration — supports bot_dm_enabled, feature_invite_dm, feature_onboarding_dm, audit_channel_id."""
    from guild_portal.config import get_settings
    result = await db.execute(select(DiscordConfig).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        # Create the config row on first use
        guild_id = get_settings().discord_guild_id or "0"
        row = DiscordConfig(guild_discord_id=guild_id)
        db.add(row)
        await db.flush()

    for field in _BOT_BOOL_FIELDS:
        if field in payload:
            setattr(row, field, bool(payload[field]))

    if "audit_channel_id" in payload:
        row.audit_channel_id = payload["audit_channel_id"] or None

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
            "audit_channel_id": row.audit_channel_id,
        },
    }


@router.post("/onboarding/process-queue")
async def process_onboarding_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Immediately run the onboarding deadline checker — resumes awaiting_dm sessions,
    retries pending verification, escalates overdue sessions."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        raise HTTPException(status_code=503, detail="Guild sync pool not available")

    from sv_common.guild_sync.onboarding.deadline_checker import OnboardingDeadlineChecker
    from sv_common.discord.bot import get_bot

    # Fetch audit_channel_id from DB (same logic as scheduler startup)
    result = await db.execute(select(DiscordConfig).limit(1))
    config = result.scalar_one_or_none()
    audit_channel_id = None
    if config and config.audit_channel_id:
        try:
            audit_channel_id = int(config.audit_channel_id)
        except (ValueError, TypeError):
            pass

    checker = OnboardingDeadlineChecker(
        db_pool=pool,
        bot=get_bot(),
        audit_channel_id=audit_channel_id,
    )
    stats = await checker.run()
    return {"ok": True, "data": stats}


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


# ---------------------------------------------------------------------------
# Recurring Events (Phase 3.1)
# ---------------------------------------------------------------------------


def _event_to_dict(ev: "RecurringEvent") -> dict:
    return {
        "id": ev.id,
        "label": ev.label,
        "event_type": ev.event_type,
        "day_of_week": ev.day_of_week,
        "default_start_time": ev.default_start_time.strftime("%H:%M") if ev.default_start_time else None,
        "default_duration_minutes": ev.default_duration_minutes,
        "discord_channel_id": ev.discord_channel_id,
        "raid_helper_template_id": ev.raid_helper_template_id,
        "is_active": ev.is_active,
        "display_on_public": ev.display_on_public,
    }


@router.get("/recurring-events")
async def list_recurring_events(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RecurringEvent).order_by(RecurringEvent.day_of_week)
    )
    events = list(result.scalars().all())
    return {"ok": True, "data": [_event_to_dict(e) for e in events]}


@router.post("/recurring-events")
async def create_recurring_event(
    body: RecurringEventCreate, db: AsyncSession = Depends(get_db)
):
    if not (0 <= body.day_of_week <= 6):
        raise HTTPException(status_code=400, detail="day_of_week must be 0–6")

    # Enforce at most one active row per day_of_week
    existing = await db.execute(
        select(RecurringEvent).where(
            RecurringEvent.day_of_week == body.day_of_week,
            RecurringEvent.is_active.is_(True),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"An active recurring event already exists for day_of_week={body.day_of_week}",
        )

    try:
        start_time = time.fromisoformat(body.default_start_time)
    except ValueError:
        raise HTTPException(status_code=400, detail="default_start_time must be HH:MM")

    ev = RecurringEvent(
        label=body.label,
        event_type=body.event_type,
        day_of_week=body.day_of_week,
        default_start_time=start_time,
        default_duration_minutes=body.default_duration_minutes,
        discord_channel_id=body.discord_channel_id,
        raid_helper_template_id=body.raid_helper_template_id,
        is_active=body.is_active,
        display_on_public=body.display_on_public,
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return {"ok": True, "data": _event_to_dict(ev)}


@router.patch("/recurring-events/{event_id}")
async def update_recurring_event(
    event_id: int, body: RecurringEventUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(RecurringEvent).where(RecurringEvent.id == event_id)
    )
    ev = result.scalar_one_or_none()
    if not ev:
        raise HTTPException(status_code=404, detail=f"Recurring event {event_id} not found")

    if body.label is not None:
        ev.label = body.label
    if body.event_type is not None:
        ev.event_type = body.event_type
    if body.day_of_week is not None:
        if not (0 <= body.day_of_week <= 6):
            raise HTTPException(status_code=400, detail="day_of_week must be 0–6")
        ev.day_of_week = body.day_of_week
    if body.default_start_time is not None:
        try:
            ev.default_start_time = time.fromisoformat(body.default_start_time)
        except ValueError:
            raise HTTPException(status_code=400, detail="default_start_time must be HH:MM")
    if body.default_duration_minutes is not None:
        ev.default_duration_minutes = body.default_duration_minutes
    if body.discord_channel_id is not None:
        ev.discord_channel_id = body.discord_channel_id
    if body.raid_helper_template_id is not None:
        ev.raid_helper_template_id = body.raid_helper_template_id
    if body.is_active is not None:
        ev.is_active = body.is_active
    if body.display_on_public is not None:
        ev.display_on_public = body.display_on_public

    await db.commit()
    await db.refresh(ev)
    return {"ok": True, "data": _event_to_dict(ev)}


@router.delete("/recurring-events/{event_id}")
async def delete_recurring_event(
    event_id: int, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(RecurringEvent).where(RecurringEvent.id == event_id)
    )
    ev = result.scalar_one_or_none()
    if not ev:
        raise HTTPException(status_code=404, detail=f"Recurring event {event_id} not found")
    await db.delete(ev)
    await db.commit()
    return {"ok": True, "data": {"deleted": True}}


# ---------------------------------------------------------------------------
# Availability by day (Phase 3.1 — shared by availability page + raid tools)
# ---------------------------------------------------------------------------

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@router.get("/availability-by-day")
async def get_availability_by_day(db: AsyncSession = Depends(get_db)):
    """Return per-day availability summary with role breakdown and weighted score."""
    from sqlalchemy import func as sa_func
    from sv_common.db.models import (
        GuildRank, Player, PlayerAvailability, RecurringEvent, Role, Specialization
    )

    # Total active players
    total_result = await db.execute(
        select(sa_func.count(Player.id)).where(Player.is_active.is_(True))
    )
    total_active = total_result.scalar() or 0

    # All active recurring events keyed by day_of_week
    events_result = await db.execute(
        select(RecurringEvent).where(RecurringEvent.is_active.is_(True))
    )
    events_by_day: dict[int, RecurringEvent] = {
        e.day_of_week: e for e in events_result.scalars().all()
    }

    days = []
    for dow in range(7):
        avail_result = await db.execute(
            select(PlayerAvailability)
            .join(Player, Player.id == PlayerAvailability.player_id)
            .options(
                selectinload(PlayerAvailability.player)
                .selectinload(Player.guild_rank),
                selectinload(PlayerAvailability.player)
                .selectinload(Player.main_spec)
                .selectinload(Specialization.default_role),
            )
            .where(PlayerAvailability.day_of_week == dow)
            .where(Player.on_raid_hiatus.is_(False))
        )
        avail_rows = list(avail_result.scalars().all())

        available_count = len(avail_rows)
        availability_pct = round(available_count / total_active * 100, 1) if total_active else 0.0
        weighted_score = sum(
            r.player.guild_rank.scheduling_weight if r.player.guild_rank else 0
            for r in avail_rows
        )

        # Role breakdown
        role_breakdown: dict[str, int] = {}
        player_list = []
        for row in avail_rows:
            p = row.player
            rank_name = p.guild_rank.name if p.guild_rank else None
            sched_weight = p.guild_rank.scheduling_weight if p.guild_rank else 0
            main_role = None
            if p.main_spec and p.main_spec.default_role:
                main_role = p.main_spec.default_role.name
            if main_role:
                role_breakdown[main_role] = role_breakdown.get(main_role, 0) + 1
            player_list.append(
                {
                    "player_id": p.id,
                    "display_name": p.display_name,
                    "rank": rank_name,
                    "scheduling_weight": sched_weight,
                    "main_role": main_role,
                    "earliest_start": row.earliest_start.strftime("%H:%M") if row.earliest_start else None,
                    "available_hours": float(row.available_hours),
                }
            )

        recurring_event = None
        ev = events_by_day.get(dow)
        if ev:
            recurring_event = {
                "id": ev.id,
                "label": ev.label,
                "default_start_time": ev.default_start_time.strftime("%H:%M") if ev.default_start_time else None,
                "default_duration_minutes": ev.default_duration_minutes,
                "is_active": ev.is_active,
                "display_on_public": ev.display_on_public,
            }

        days.append(
            {
                "day_of_week": dow,
                "day_name": _DAY_NAMES[dow],
                "available_count": available_count,
                "availability_pct": availability_pct,
                "weighted_score": weighted_score,
                "recurring_event": recurring_event,
                "role_breakdown": role_breakdown,
                "players": player_list,
            }
        )

    return {
        "ok": True,
        "data": {
            "total_active_players": total_active,
            "days": days,
        },
    }


# ---------------------------------------------------------------------------
# Raid-Helper Config (Phase 3.4)
# ---------------------------------------------------------------------------

_RAID_CONFIG_FIELDS = {
    "raid_helper_api_key",
    "raid_helper_server_id",
    "raid_creator_discord_id",
    "raid_channel_id",
    "raid_voice_channel_id",
    "raid_default_template_id",
    "audit_channel_id",
    "raid_event_timezone",
    "raid_default_start_time",
    "raid_default_duration_minutes",
}


@router.get("/raid-config")
async def get_raid_config(db: AsyncSession = Depends(get_db)):
    """Return current Raid-Helper config. API key is masked."""
    result = await db.execute(select(DiscordConfig).limit(1))
    row = result.scalar_one_or_none()

    def _mask(val: str | None) -> str | None:
        if not val:
            return val
        return val[:4] + "****" if len(val) > 4 else "****"

    return {
        "ok": True,
        "data": {
            "raid_helper_api_key": _mask(row.raid_helper_api_key if row else None),
            "raid_helper_server_id": row.raid_helper_server_id if row else None,
            "raid_creator_discord_id": row.raid_creator_discord_id if row else None,
            "raid_channel_id": row.raid_channel_id if row else None,
            "raid_voice_channel_id": row.raid_voice_channel_id if row else None,
            "raid_default_template_id": (row.raid_default_template_id if row else None) or "wowretail2",
            "audit_channel_id": row.audit_channel_id if row else None,
            "raid_event_timezone": (row.raid_event_timezone if row else None) or "America/New_York",
            "raid_default_start_time": (row.raid_default_start_time if row else None) or "21:00",
            "raid_default_duration_minutes": (row.raid_default_duration_minutes if row else None) or 120,
        },
    }


@router.patch("/raid-config")
async def update_raid_config(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(require_rank(4)),
):
    """Update Raid-Helper config fields. Only fields present in the body are updated."""
    from guild_portal.config import get_settings

    result = await db.execute(select(DiscordConfig).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        guild_id = get_settings().discord_guild_id or "0"
        row = DiscordConfig(guild_discord_id=guild_id)
        db.add(row)
        await db.flush()

    for field in _RAID_CONFIG_FIELDS:
        if field in payload:
            val = payload[field]
            # Don't overwrite a real key with the masked placeholder
            if field == "raid_helper_api_key" and val and val.endswith("****"):
                continue
            setattr(row, field, val or None)

    await db.commit()
    logger.info("Raid config updated by %s", admin.display_name)
    return {"ok": True, "data": {"saved": True}}


@router.get("/raid-config/test")
async def test_raid_config(db: AsyncSession = Depends(get_db)):
    """Test the Raid-Helper API connection."""
    from guild_portal.services.raid_helper_service import test_connection, RaidHelperError

    result = await db.execute(select(DiscordConfig).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        return {"ok": False, "error": "No config found"}

    config = {
        "raid_helper_api_key": row.raid_helper_api_key,
        "raid_helper_server_id": row.raid_helper_server_id,
    }
    try:
        data = await test_connection(config)
        return {"ok": True, "data": data}
    except RaidHelperError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.warning("Raid-Helper test failed: %s", e)
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Raid Events — create with Raid-Helper + DB record (Phase 3.4)
# ---------------------------------------------------------------------------


class RaidEventCreate(BaseModel):
    title: str
    event_type: str = "raid"
    event_date: str  # "YYYY-MM-DD"
    start_time: str  # "HH:MM"
    timezone: str = "America/New_York"
    duration_minutes: int = 120
    channel_id: str | None = None
    description: str = ""
    recurring_event_id: int | None = None
    player_overrides: dict[str, str] = {}  # player_id → "accepted"|"tentative"|"bench"|"skip"


@router.post("/raid-events")
async def create_raid_event(
    body: RaidEventCreate,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(require_rank(4)),
):
    """Create a raid event in Raid-Helper and store in patt.raid_events."""
    import zoneinfo
    from guild_portal.services.raid_helper_service import create_event, RaidHelperError
    from sqlalchemy.orm import selectinload as sil

    # 1. Load Raid-Helper config
    cfg_result = await db.execute(select(DiscordConfig).limit(1))
    cfg = cfg_result.scalar_one_or_none()
    if not cfg or not cfg.raid_helper_api_key or not cfg.raid_helper_server_id:
        return {"ok": False, "error": "Raid-Helper is not configured. Set API Key and Server ID in Raid Tools → Raid-Helper Configuration."}

    config = {
        "raid_helper_api_key": cfg.raid_helper_api_key,
        "raid_helper_server_id": cfg.raid_helper_server_id,
        "raid_creator_discord_id": cfg.raid_creator_discord_id,
        "raid_channel_id": cfg.raid_channel_id,
        "raid_default_template_id": cfg.raid_default_template_id or "wowretail2",
    }

    # 2. Convert event_date + start_time + timezone → UTC
    try:
        tz = zoneinfo.ZoneInfo(body.timezone)
        local_dt = datetime.fromisoformat(f"{body.event_date}T{body.start_time}:00").replace(tzinfo=tz)
        start_utc = local_dt.astimezone(timezone.utc)
    except Exception as e:
        return {"ok": False, "error": f"Invalid date/time: {e}"}

    end_utc = start_utc.replace(microsecond=0)
    from datetime import timedelta
    end_utc = start_utc + timedelta(minutes=body.duration_minutes)

    # 3. Build roster signups
    players_result = await db.execute(
        select(Player)
        .options(
            sil(Player.guild_rank),
            sil(Player.discord_user),
            sil(Player.main_character).selectinload(WowCharacter.wow_class),
            sil(Player.main_spec).selectinload(Specialization.default_role),
        )
        .where(Player.is_active.is_(True), Player.main_character_id.is_not(None))
    )
    active_players = list(players_result.scalars().all())

    # Load availability: who's available on the raid day, and who has any records
    raid_dow = date.fromisoformat(body.event_date).weekday()  # 0=Mon, 6=Sun
    avail_result = await db.execute(
        select(PlayerAvailability.player_id, PlayerAvailability.day_of_week)
    )
    available_on_day = {
        row.player_id for row in avail_result.all() if row.day_of_week == raid_dow
    }

    signups = []
    attendance_rows = []
    for p in active_players:
        rank_level = p.guild_rank.level if p.guild_rank else 0
        override = body.player_overrides.get(str(p.id))

        # Determine status
        if override == "skip":
            continue
        elif override:
            status = override  # explicit override
        elif p.on_raid_hiatus or p.id not in available_on_day:
            status = "absence"
        elif rank_level >= 2 and p.auto_invite_events:
            status = "accepted"
        else:
            status = "tentative"

        signed_up = status in ("accepted", "tentative")

        # Build RH signup entry if player has a discord_id
        if p.discord_user and p.discord_user.discord_id:
            entry: dict = {
                "userId": p.discord_user.discord_id,
                "status": status,
            }
            # Add class/spec if available
            if p.main_character and p.main_spec:
                spec_key = None
                if p.main_character.wow_class and p.main_spec:
                    class_name = p.main_character.wow_class.name if hasattr(p.main_character, "wow_class") else None
                    if class_name:
                        from guild_portal.services.raid_helper_service import SPEC_TO_RAID_HELPER
                        spec_key = SPEC_TO_RAID_HELPER.get((class_name, p.main_spec.name))
                if spec_key:
                    entry["class"] = spec_key[0]
                    entry["spec"] = spec_key[1]
            signups.append(entry)

        attendance_rows.append({
            "player_id": p.id,
            "signed_up": signed_up,
            "character_id": p.main_character_id,
        })

    # 4. Call Raid-Helper API
    channel_id = body.channel_id or cfg.raid_channel_id or ""
    template_id = cfg.raid_default_template_id or "wowretail2"

    logger.info(
        "Building Raid-Helper event '%s': %d active players, %d with Discord linked (signups)",
        body.title, len(active_players), len(signups),
    )

    try:
        rh_result = await create_event(
            config=config,
            title=body.title,
            event_type=body.event_type,
            start_time_utc=start_utc,
            start_time_local=local_dt,
            duration_minutes=body.duration_minutes,
            channel_id=channel_id,
            description=body.description,
            template_id=template_id,
        )
    except RaidHelperError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.error("Raid-Helper create_event failed: %s", e)
        return {"ok": False, "error": f"Raid-Helper error: {e}"}

    # 4b. Add signups via dedicated per-user endpoint (creation payload ignores them)
    if signups and rh_result.get("event_id"):
        from guild_portal.services.raid_helper_service import add_signups_to_event
        await add_signups_to_event(
            api_key=config["raid_helper_api_key"],
            event_id=str(rh_result["event_id"]),
            signups=signups,
        )

    # 5. Insert patt.raid_events row
    # Determine active season
    season_result = await db.execute(
        select(RaidSeason)
        .where(RaidSeason.is_active.is_(True))
        .order_by(RaidSeason.start_date.desc())
        .limit(1)
    )
    season = season_result.scalar_one_or_none()

    from datetime import date as date_type
    event_date = date_type.fromisoformat(body.event_date)

    raid_event = RaidEvent(
        season_id=season.id if season else None,
        title=body.title,
        event_date=event_date,
        start_time_utc=start_utc,
        end_time_utc=end_utc,
        raid_helper_event_id=str(rh_result["event_id"]) if rh_result.get("event_id") else None,
        discord_channel_id=channel_id or None,
        recurring_event_id=body.recurring_event_id,
        auto_booked=False,
        raid_helper_payload=rh_result.get("payload"),
        created_by_player_id=admin.id,
    )
    db.add(raid_event)
    await db.flush()

    # 6. Batch-insert attendance rows
    for row_data in attendance_rows:
        att = RaidAttendance(
            event_id=raid_event.id,
            player_id=row_data["player_id"],
            signed_up=row_data["signed_up"],
            attended=False,
            character_id=row_data["character_id"],
            source="auto",
        )
        db.add(att)

    await db.commit()

    logger.info(
        "Raid event created by %s: '%s' on %s (RH id: %s)",
        admin.display_name, body.title, body.event_date, rh_result.get("event_id"),
    )

    return {
        "ok": True,
        "data": {
            "raid_event_id": raid_event.id,
            "raid_helper_event_id": rh_result.get("event_id"),
            "event_url": rh_result.get("event_url", ""),
        },
    }


# ---------------------------------------------------------------------------
# Screen Permissions (Settings nav visibility)
# ---------------------------------------------------------------------------


class ScreenPermUpdate(BaseModel):
    min_rank_level: Optional[int] = None


@router.patch("/screen-permissions/{perm_id}")
async def update_screen_permission(
    perm_id: int,
    body: ScreenPermUpdate,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(require_rank(4)),
):
    result = await db.execute(
        select(ScreenPermission).where(ScreenPermission.id == perm_id)
    )
    perm = result.scalar_one_or_none()
    if not perm:
        return {"ok": False, "error": "Screen permission not found"}

    if body.min_rank_level is not None:
        if body.min_rank_level < 1 or body.min_rank_level > 5:
            return {"ok": False, "error": "min_rank_level must be between 1 and 5"}
        perm.min_rank_level = body.min_rank_level

    perm.updated_at = datetime.now(timezone.utc)
    return {
        "ok": True,
        "data": {
            "id": perm.id,
            "screen_key": perm.screen_key,
            "min_rank_level": perm.min_rank_level,
        },
    }


# ---------------------------------------------------------------------------
# Site Config (GL-only — require rank level 5)
# ---------------------------------------------------------------------------


class SiteConfigUpdate(BaseModel):
    guild_name: str | None = None
    guild_tagline: str | None = None
    guild_mission: str | None = None
    discord_invite_url: str | None = None
    accent_color_hex: str | None = None
    realm_display_name: str | None = None
    home_realm_slug: str | None = None
    guild_name_slug: str | None = None
    logo_url: str | None = None
    enable_guild_quotes: bool | None = None
    enable_contests: bool | None = None
    current_mplus_season_id: int | None = None


@router.patch(
    "/site-config",
    dependencies=[Depends(require_rank(5))],  # GL only
)
async def update_site_config(
    body: SiteConfigUpdate,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    """Update site_config row. Refreshes the in-process config cache after save."""
    result = await db.execute(select(SiteConfig).limit(1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        return {"ok": False, "error": "No site_config row found — run migration 0032"}

    # Validate accent color
    if body.accent_color_hex is not None:
        import re
        if not re.match(r'^#[0-9a-fA-F]{6}$', body.accent_color_hex):
            return {"ok": False, "error": "accent_color_hex must be a 6-digit hex color (e.g. #d4a84b)"}

    # Apply updates for fields explicitly provided
    payload = body.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(cfg, field, value)

    cfg.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(cfg)

    # Refresh in-process cache so templates pick up changes immediately
    updated = {
        col.key: getattr(cfg, col.key)
        for col in SiteConfig.__table__.columns
    }
    set_site_config(updated)

    return {"ok": True, "data": {"guild_name": cfg.guild_name}}


# ---------------------------------------------------------------------------
# Progression Config (Officer+) — Phase 4.3
# ---------------------------------------------------------------------------


class MplusSeasonUpdate(BaseModel):
    current_mplus_season_id: int | None = None


@router.patch("/progression/mplus-season")
async def update_mplus_season(
    body: MplusSeasonUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update current M+ season ID in site_config. Officer+ only."""
    result = await db.execute(select(SiteConfig).limit(1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        return {"ok": False, "error": "No site_config row found"}

    cfg.current_mplus_season_id = body.current_mplus_season_id
    cfg.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(cfg)

    updated = {
        col.key: getattr(cfg, col.key)
        for col in SiteConfig.__table__.columns
    }
    set_site_config(updated)

    return {"ok": True, "data": {"current_mplus_season_id": cfg.current_mplus_season_id}}


# ===========================================================================
# Attendance — /api/v1/admin/attendance
# ===========================================================================


class AttendanceSettingsUpdate(BaseModel):
    attendance_feature_enabled: bool | None = None
    attendance_min_pct: int | None = None
    attendance_late_grace_min: int | None = None
    attendance_early_leave_min: int | None = None
    attendance_trailing_events: int | None = None
    attendance_habitual_window: int | None = None
    attendance_habitual_threshold: int | None = None


class ExcusedUpdate(BaseModel):
    noted_absence: bool


@router.get("/attendance/season")
async def get_attendance_season(
    request: Request,
    season_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Season grid data: players × events attendance matrix."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "Guild sync pool not available"}

    async with pool.acquire() as conn:
        # Check if feature enabled
        enabled = await conn.fetchval(
            "SELECT attendance_feature_enabled FROM common.discord_config LIMIT 1"
        )

        # Load available seasons
        seasons = await conn.fetch(
            "SELECT id, expansion_name, season_number FROM patt.raid_seasons ORDER BY start_date DESC"
        )
        available_seasons = [
            {
                "id": s["id"],
                "display_name": _season_display_name(s),
            }
            for s in seasons
        ]

        # Select active season
        if season_id:
            season = await conn.fetchrow(
                "SELECT id, expansion_name, season_number FROM patt.raid_seasons WHERE id = $1",
                season_id,
            )
        else:
            season = await conn.fetchrow(
                "SELECT id, expansion_name, season_number FROM patt.raid_seasons WHERE is_active = TRUE ORDER BY start_date DESC LIMIT 1"
            )

        if not season:
            return {
                "ok": True,
                "data": {
                    "feature_disabled": not enabled,
                    "available_seasons": available_seasons,
                    "season": None,
                    "events": [],
                    "players": [],
                    "unlinked_users": [],
                },
            }

        # Load events for this season
        events = await conn.fetch(
            """
            SELECT id, title, event_date, start_time_utc, end_time_utc,
                   attendance_processed_at, log_url, voice_tracking_enabled
            FROM patt.raid_events
            WHERE season_id = $1
            ORDER BY event_date
            """,
            season["id"],
        )

        now = datetime.now(timezone.utc)
        event_ids = [e["id"] for e in events]

        # Load all attendance records for these events
        if event_ids:
            att_rows = await conn.fetch(
                """
                SELECT id, event_id, player_id, attended, source, noted_absence,
                       minutes_present, first_join_at, last_leave_at, joined_late, left_early
                FROM patt.raid_attendance
                WHERE event_id = ANY($1::int[])
                """,
                event_ids,
            )
        else:
            att_rows = []

        # Index attendance by (event_id, player_id)
        att_index: dict = {}
        for row in att_rows:
            att_index[(row["event_id"], row["player_id"])] = row

        # Load all players with ranks
        players = await conn.fetch(
            """
            SELECT p.id, p.display_name, gr.name AS rank_name, gr.level AS rank_level
            FROM guild_identity.players p
            LEFT JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
            WHERE p.is_active = TRUE
            ORDER BY gr.level DESC NULLS LAST, p.display_name
            """
        )

        # Config for habitual check
        cfg = await conn.fetchrow(
            """
            SELECT attendance_habitual_window, attendance_habitual_threshold,
                   attendance_min_pct, attendance_trailing_events
            FROM common.discord_config LIMIT 1
            """
        )
        hab_window = cfg["attendance_habitual_window"] if cfg else 5
        hab_threshold = cfg["attendance_habitual_threshold"] if cfg else 3
        trailing = cfg["attendance_trailing_events"] if cfg else 8

        # Build event objects
        event_objs = []
        for e in events:
            processed = e["attendance_processed_at"] is not None
            has_wcl = e["log_url"] is not None
            is_live = e["start_time_utc"] <= now <= e["end_time_utc"]
            event_objs.append({
                "id": e["id"],
                "date": e["event_date"].isoformat(),
                "title": e["title"],
                "processed": processed,
                "has_wcl": has_wcl,
                "is_live": is_live,
            })

        # Build player rows
        player_objs = []
        for p in players:
            pid = p["id"]
            attendance: dict = {}
            total_eligible = 0
            total_attended = 0

            for e in events:
                eid = e["id"]
                cell = att_index.get((eid, pid))
                is_live = e["start_time_utc"] <= now <= e["end_time_utc"]
                processed = e["attendance_processed_at"] is not None

                if is_live:
                    attendance[str(eid)] = {"status": "live"}
                elif not processed and cell is None:
                    attendance[str(eid)] = {"status": "nodata"}
                elif not processed:
                    attendance[str(eid)] = {"status": "pending"}
                elif cell is None:
                    attendance[str(eid)] = {"status": "nodata"}
                else:
                    total_eligible += 1
                    if cell["attended"]:
                        total_attended += 1

                    if cell["noted_absence"]:
                        status = "excused"
                    elif cell["attended"]:
                        status = "attended"
                    else:
                        status = "absent"

                    attendance[str(eid)] = {
                        "status": status,
                        "source": cell["source"],
                        "minutes_present": cell["minutes_present"],
                        "pct": (cell["minutes_present"] / ((e["end_time_utc"] - e["start_time_utc"]).total_seconds() / 60) * 100) if cell["minutes_present"] is not None else None,
                        "joined_late": cell["joined_late"],
                        "left_early": cell["left_early"],
                        "noted_absence": cell["noted_absence"],
                        "attendance_id": cell["id"],
                        "first_join_at": cell["first_join_at"].isoformat() if cell["first_join_at"] else None,
                        "last_leave_at": cell["last_leave_at"].isoformat() if cell["last_leave_at"] else None,
                    }

            pct = (total_attended / total_eligible * 100) if total_eligible > 0 else None

            # Habitual check
            recent_voice = await conn.fetch(
                """
                SELECT joined_late, left_early
                FROM patt.raid_attendance ra
                JOIN patt.raid_events re ON re.id = ra.event_id
                WHERE ra.player_id = $1 AND ra.joined_late IS NOT NULL
                  AND re.attendance_processed_at IS NOT NULL
                ORDER BY re.start_time_utc DESC
                LIMIT $2
                """,
                pid,
                hab_window,
            )
            late_count = sum(1 for r in recent_voice if r["joined_late"])
            early_count = sum(1 for r in recent_voice if r["left_early"])
            habitual_late = late_count >= hab_threshold
            habitual_early = early_count >= hab_threshold
            habitual_summary = ""
            if habitual_late:
                habitual_summary += f"Late to {late_count}/{len(recent_voice)} recent raids"
            if habitual_early:
                sep = " · " if habitual_summary else ""
                habitual_summary += f"{sep}Left early {early_count}/{len(recent_voice)} recent raids"

            # Attendance status for dot badge
            att_status_result = await conn.fetch(
                """
                SELECT ra.attended, ra.noted_absence
                FROM patt.raid_attendance ra
                JOIN patt.raid_events re ON re.id = ra.event_id
                WHERE ra.player_id = $1 AND re.attendance_processed_at IS NOT NULL
                ORDER BY re.start_time_utc DESC
                LIMIT $2
                """,
                pid,
                trailing,
            )
            att_status = _compute_att_status(att_status_result, cfg["attendance_min_pct"] if cfg else 75, enabled)

            player_objs.append({
                "id": pid,
                "name": p["display_name"] or f"Player#{pid}",
                "rank": p["rank_name"] or "Unknown",
                "rank_level": p["rank_level"] or 0,
                "attendance": attendance,
                "total_attended": total_attended,
                "total_eligible": total_eligible,
                "pct": pct,
                "habitual_late": habitual_late,
                "habitual_early": habitual_early,
                "habitual_summary": habitual_summary,
                "attendance_status": att_status["status"],
                "attendance_summary": att_status["summary"],
            })

        # Unlinked users
        if event_ids:
            unlinked_rows = await conn.fetch(
                """
                SELECT val.discord_user_id, COUNT(DISTINCT val.event_id) AS event_count
                FROM patt.voice_attendance_log val
                WHERE val.event_id = ANY($1::int[])
                  AND NOT EXISTS (
                      SELECT 1 FROM guild_identity.discord_users du
                      WHERE du.discord_id = val.discord_user_id
                  )
                GROUP BY val.discord_user_id
                ORDER BY event_count DESC
                """,
                event_ids,
            )
            unlinked = [dict(r) for r in unlinked_rows]
        else:
            unlinked = []

    return {
        "ok": True,
        "data": {
            "feature_disabled": not enabled,
            "available_seasons": available_seasons,
            "season": {
                "id": season["id"],
                "display_name": _season_display_name(season),
            },
            "events": event_objs,
            "players": player_objs,
            "unlinked_users": unlinked,
        },
    }


@router.get("/attendance/event/{event_id}")
async def get_attendance_event(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Per-event breakdown with all attendance records and voice presence."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "Guild sync pool not available"}

    async with pool.acquire() as conn:
        event = await conn.fetchrow(
            """
            SELECT id, title, event_date, start_time_utc, end_time_utc,
                   log_url, voice_channel_id, attendance_processed_at
            FROM patt.raid_events WHERE id = $1
            """,
            event_id,
        )
        if not event:
            return {"ok": False, "error": "Event not found"}

        records = await conn.fetch(
            """
            SELECT ra.id, ra.player_id, p.display_name AS player_name,
                   ra.attended, ra.source, ra.noted_absence,
                   ra.minutes_present, ra.first_join_at, ra.last_leave_at,
                   ra.joined_late, ra.left_early, ra.signed_up
            FROM patt.raid_attendance ra
            JOIN guild_identity.players p ON p.id = ra.player_id
            WHERE ra.event_id = $1
            ORDER BY p.display_name
            """,
            event_id,
        )

        duration_min = (event["end_time_utc"] - event["start_time_utc"]).total_seconds() / 60

        sources_set = set(r["source"] for r in records if r["source"])
        source_summary = "+".join(sorted(sources_set)) if sources_set else "none"

        attended_count = sum(1 for r in records if r["attended"])
        excused_count = sum(1 for r in records if r["noted_absence"])
        absent_count = sum(1 for r in records if not r["attended"] and not r["noted_absence"])

        record_objs = []
        for r in records:
            pct = (r["minutes_present"] / duration_min * 100) if r["minutes_present"] is not None and duration_min > 0 else None
            record_objs.append({
                "id": r["id"],
                "player_id": r["player_id"],
                "player_name": r["player_name"] or f"Player#{r['player_id']}",
                "attended": r["attended"],
                "source": r["source"],
                "noted_absence": r["noted_absence"],
                "minutes_present": r["minutes_present"],
                "pct": pct,
                "first_join_at": r["first_join_at"].isoformat() if r["first_join_at"] else None,
                "last_leave_at": r["last_leave_at"].isoformat() if r["last_leave_at"] else None,
                "joined_late": r["joined_late"],
                "left_early": r["left_early"],
                "signed_up": r["signed_up"],
            })

    return {
        "ok": True,
        "data": {
            "event": {
                "id": event["id"],
                "title": event["title"],
                "date": event["event_date"].isoformat(),
                "start_time": event["start_time_utc"].strftime("%H:%M"),
                "end_time": event["end_time_utc"].strftime("%H:%M"),
                "log_url": event["log_url"],
                "processed": event["attendance_processed_at"] is not None,
            },
            "summary": {
                "attended": attended_count,
                "absent": absent_count,
                "excused": excused_count,
                "sources": source_summary,
            },
            "records": record_objs,
        },
    }


@router.post("/attendance/event/{event_id}/reprocess")
async def reprocess_attendance_event(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trigger re-processing of both passes for a raid event."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "Guild sync pool not available"}

    # Reset processed_at so it will be re-stamped
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT id FROM patt.raid_events WHERE id = $1", event_id
        )
        if not exists:
            return {"ok": False, "error": "Event not found"}
        await conn.execute(
            "UPDATE patt.raid_events SET attendance_processed_at = NULL WHERE id = $1",
            event_id,
        )

    from sv_common.guild_sync.attendance_processor import process_event
    import asyncio

    # Get audit channel if available
    audit_channel = None
    try:
        from sv_common.discord.bot import get_bot
        bot = get_bot()
        if bot and not bot.is_closed():
            async with pool.acquire() as conn:
                audit_id = await conn.fetchval(
                    "SELECT audit_channel_id FROM common.discord_config LIMIT 1"
                )
            if audit_id:
                audit_channel = bot.get_channel(int(audit_id))
    except Exception:
        pass

    asyncio.create_task(process_event(pool, event_id, audit_channel))
    return {"ok": True, "message": f"Re-processing triggered for event {event_id}"}


@router.patch("/attendance/record/{record_id}")
async def update_attendance_record(
    record_id: int,
    body: ExcusedUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Toggle noted_absence on an attendance record."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "Guild sync pool not available"}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM patt.raid_attendance WHERE id = $1", record_id
        )
        if not row:
            return {"ok": False, "error": "Record not found"}
        await conn.execute(
            "UPDATE patt.raid_attendance SET noted_absence = $1 WHERE id = $2",
            body.noted_absence,
            record_id,
        )
    return {"ok": True}


@router.get("/attendance/export")
async def export_attendance_csv(
    request: Request,
    season_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """CSV export of the season attendance grid."""
    from fastapi.responses import StreamingResponse
    import io
    import csv

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "Guild sync pool not available"}

    async with pool.acquire() as conn:
        if season_id:
            season = await conn.fetchrow(
                "SELECT id, expansion_name, season_number FROM patt.raid_seasons WHERE id = $1",
                season_id,
            )
        else:
            season = await conn.fetchrow(
                "SELECT id, expansion_name, season_number FROM patt.raid_seasons WHERE is_active = TRUE ORDER BY start_date DESC LIMIT 1"
            )

        if not season:
            return {"ok": False, "error": "No season found"}

        events = await conn.fetch(
            "SELECT id, title, event_date FROM patt.raid_events WHERE season_id = $1 ORDER BY event_date",
            season["id"],
        )
        players = await conn.fetch(
            """
            SELECT p.id, p.display_name, gr.name AS rank_name
            FROM guild_identity.players p
            LEFT JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
            WHERE p.is_active = TRUE
            ORDER BY gr.level DESC NULLS LAST, p.display_name
            """
        )
        event_ids = [e["id"] for e in events]
        att_rows = []
        if event_ids:
            att_rows = await conn.fetch(
                """
                SELECT event_id, player_id, attended, noted_absence, source
                FROM patt.raid_attendance WHERE event_id = ANY($1::int[])
                """,
                event_ids,
            )

    att_index = {(r["event_id"], r["player_id"]): r for r in att_rows}

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(
        ["Player", "Rank"] + [f"{e['event_date']} — {e['title']}" for e in events] + ["Total", "Pct"]
    )

    for p in players:
        pid = p["id"]
        cells = []
        total_att = 0
        total_elig = 0
        for e in events:
            rec = att_index.get((e["id"], pid))
            if rec is None:
                cells.append("—")
            elif rec["noted_absence"]:
                cells.append("excused")
                total_elig += 1
                total_att += 1
            elif rec["attended"]:
                cells.append("attended")
                total_elig += 1
                total_att += 1
            else:
                cells.append("absent")
                total_elig += 1
        pct_str = f"{round(total_att / total_elig * 100)}%" if total_elig > 0 else "—"
        writer.writerow([p["display_name"] or f"#{pid}", p["rank_name"] or ""] + cells + [f"{total_att}/{total_elig}", pct_str])

    output.seek(0)
    filename = f"attendance-{season['id']}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/attendance/settings")
async def get_attendance_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return current attendance configuration from discord_config."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "Guild sync pool not available"}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT attendance_feature_enabled, attendance_min_pct, attendance_late_grace_min,
                   attendance_early_leave_min, attendance_trailing_events,
                   attendance_habitual_window, attendance_habitual_threshold
            FROM common.discord_config LIMIT 1
            """
        )
    if not row:
        return {"ok": False, "error": "No discord_config found"}
    return {"ok": True, "data": dict(row)}


@router.patch("/attendance/settings")
async def update_attendance_settings(
    body: AttendanceSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update attendance configuration in discord_config."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "Guild sync pool not available"}

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return {"ok": False, "error": "No fields to update"}

    set_clauses = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(fields))
    values = list(fields.values())

    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE common.discord_config SET {set_clauses}",
            *values,
        )
        # Reload and return updated config
        row = await conn.fetchrow(
            """
            SELECT attendance_feature_enabled, attendance_min_pct, attendance_late_grace_min,
                   attendance_early_leave_min, attendance_trailing_events,
                   attendance_habitual_window, attendance_habitual_threshold
            FROM common.discord_config LIMIT 1
            """
        )

    return {"ok": True, "data": dict(row)}


# ---------------------------------------------------------------------------
# Attendance helpers
# ---------------------------------------------------------------------------


def _season_display_name(season) -> str:
    if season["expansion_name"] and season["season_number"] is not None:
        return f"{season['expansion_name']} Season {season['season_number']}"
    return season["expansion_name"] or "Unknown Season"


def _compute_att_status(rows, min_pct: int, feature_enabled: bool) -> dict:
    if not feature_enabled:
        return {"status": "none", "summary": ""}
    if len(rows) < 3:
        return {"status": "new", "summary": f"{len(rows)} events"}
    attended = sum(1 for r in rows if r["attended"] or r["noted_absence"])
    total = len(rows)
    pct = (attended / total) * 100 if total > 0 else 0
    summary = f"{attended}/{total} raids"
    if pct >= min_pct:
        status = "good"
    elif pct >= 50:
        status = "at_risk"
    else:
        status = "concern"
    return {"status": status, "summary": summary}
