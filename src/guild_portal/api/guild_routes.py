"""Public guild API routes — roster, rank info, availability, and guild quote content."""

import statistics
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from guild_portal.deps import get_db
from sv_common.db.models import (
    GuildQuote,
    GuildQuoteTitle,
    QuoteSubject,
    Player,
    PlayerAvailability,
    PlayerCharacter,
    RaidSeason,
    RaiderIOProfile,
    Specialization,
    WowCharacter,
)
from sv_common.identity import ranks as rank_service

router = APIRouter(prefix="/api/v1/guild", tags=["guild"])

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


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
                "description": r.description,
            }
            for r in all_ranks
        ],
    }


@router.get("/roster")
async def get_roster(db: AsyncSession = Depends(get_db)):
    """Return roster: players who have a main character set, with all characters for alt view."""
    result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.main_character).selectinload(WowCharacter.wow_class),
            selectinload(Player.main_character)
            .selectinload(WowCharacter.active_spec)
            .selectinload(Specialization.default_role),
            selectinload(Player.main_spec).selectinload(Specialization.default_role),
            selectinload(Player.offspec_character).selectinload(WowCharacter.wow_class),
            selectinload(Player.offspec_character)
            .selectinload(WowCharacter.active_spec)
            .selectinload(Specialization.default_role),
            selectinload(Player.offspec_spec).selectinload(Specialization.default_role),
            selectinload(Player.characters)
            .selectinload(PlayerCharacter.character)
            .selectinload(WowCharacter.wow_class),
            selectinload(Player.characters)
            .selectinload(PlayerCharacter.character)
            .selectinload(WowCharacter.active_spec)
            .selectinload(Specialization.default_role),
        )
        .where(Player.main_character_id.is_not(None))
        .where(Player.is_active.is_(True))
        .where(Player.on_raid_hiatus.is_(False))
        .order_by(Player.display_name)
    )
    players = list(result.unique().scalars().all())

    # Collect all character IDs for a single Raider.IO batch query
    all_char_ids: set[int] = set()
    for p in players:
        if p.main_character:
            all_char_ids.add(p.main_character.id)
        if p.offspec_character:
            all_char_ids.add(p.offspec_character.id)
        for pc in p.characters:
            if pc.character:
                all_char_ids.add(pc.character.id)

    rio_by_char: dict[int, RaiderIOProfile] = {}
    if all_char_ids:
        rio_result = await db.execute(
            select(RaiderIOProfile).where(
                RaiderIOProfile.season == "current",
                RaiderIOProfile.character_id.in_(list(all_char_ids)),
            )
        )
        for r in rio_result.scalars():
            rio_by_char[r.character_id] = r

    def _rio_data(char_id: int | None) -> dict:
        r = rio_by_char.get(char_id) if char_id else None
        return {
            "rio_score": float(r.overall_score) if r and r.overall_score else None,
            "rio_color": r.score_color if r else None,
            "rio_raid_prog": r.raid_progression if r else None,
            "rio_url": r.profile_url if r else None,
        }

    roster = []
    for p in players:
        mc = p.main_character
        spec = p.main_spec or (mc.active_spec if mc else None)
        role = spec.default_role if spec else None

        main_char_data = None
        if mc:
            armory_url = (
                f"https://worldofwarcraft.blizzard.com/en-us/character/us"
                f"/{mc.realm_slug}/{mc.character_name.lower()}"
            )
            main_char_data = {
                "character_id": mc.id,
                "character_name": mc.character_name,
                "realm_slug": mc.realm_slug,
                "class_name": mc.wow_class.name if mc.wow_class else None,
                "spec_name": spec.name if spec else None,
                "role_name": role.name if role else None,
                "item_level": mc.item_level,
                "armory_url": armory_url,
                **_rio_data(mc.id),
            }

        sc = p.offspec_character
        sec_spec = p.offspec_spec or (sc.active_spec if sc else None)
        sec_role = sec_spec.default_role if sec_spec else None
        secondary_char_data = None
        if sc:
            secondary_char_data = {
                "character_id": sc.id,
                "character_name": sc.character_name,
                "realm_slug": sc.realm_slug,
                "class_name": sc.wow_class.name if sc.wow_class else None,
                "spec_name": sec_spec.name if sec_spec else None,
                "role_name": sec_role.name if sec_role else None,
                "item_level": sc.item_level,
                "armory_url": (
                    f"https://worldofwarcraft.blizzard.com/en-us/character/us"
                    f"/{sc.realm_slug}/{sc.character_name.lower()}"
                ),
                **_rio_data(sc.id),
            }

        all_chars = []
        for pc in p.characters:
            char = pc.character
            if not char:
                continue
            char_spec = char.active_spec
            char_role = char_spec.default_role if char_spec else None
            all_chars.append(
                {
                    "character_id": char.id,
                    "character_name": char.character_name,
                    "realm_slug": char.realm_slug,
                    "class_name": char.wow_class.name if char.wow_class else None,
                    "spec_name": char_spec.name if char_spec else None,
                    "role_name": char_role.name if char_role else None,
                    "item_level": char.item_level,
                    "armory_url": (
                        f"https://worldofwarcraft.blizzard.com/en-us/character/us"
                        f"/{char.realm_slug}/{char.character_name.lower()}"
                    ),
                    "is_main": mc is not None and char.id == mc.id,
                    **_rio_data(char.id),
                }
            )

        roster.append(
            {
                "player_id": p.id,
                "display_name": p.display_name,
                "rank_name": p.guild_rank.name if p.guild_rank else "Unknown",
                "rank_level": p.guild_rank.level if p.guild_rank else 0,
                "main_character": main_char_data,
                "secondary_character": secondary_char_data,
                "characters": all_chars,
            }
        )

    return {"ok": True, "data": {"players": roster}}


# ---------------------------------------------------------------------------
# Progression endpoint
# ---------------------------------------------------------------------------


@router.get("/progression")
async def get_progression(db: AsyncSession = Depends(get_db)):
    """Return aggregated M+ score and raid progression stats for the guild.

    Public endpoint — no auth required.
    """
    # M+ stats from Raider.IO profiles (current season, score > 0)
    rio_result = await db.execute(
        text("""
            SELECT r.overall_score, r.score_color, r.raid_progression,
                   wc.character_name
            FROM guild_identity.raiderio_profiles r
            JOIN guild_identity.wow_characters wc ON wc.id = r.character_id
            WHERE r.season = 'current' AND r.overall_score > 0
              AND wc.in_guild = TRUE
            ORDER BY r.overall_score DESC
        """)
    )
    rio_rows = rio_result.fetchall()

    scores = [float(row.overall_score) for row in rio_rows]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    median_score = round(statistics.median(scores), 1) if scores else 0.0

    top_10 = [
        {
            "name": row.character_name,
            "score": float(row.overall_score),
            "color": row.score_color,
        }
        for row in rio_rows[:10]
    ]

    # Guild best progression string — independent of M+ score so it shows at
    # the start of a new season when everyone's score has reset to 0
    prog_result = await db.execute(
        text("""
            SELECT r.raid_progression
            FROM guild_identity.raiderio_profiles r
            JOIN guild_identity.wow_characters wc ON wc.id = r.character_id
            WHERE r.season = 'current' AND r.raid_progression IS NOT NULL
              AND wc.in_guild = TRUE
            ORDER BY r.overall_score DESC NULLS LAST
            LIMIT 1
        """)
    )
    prog_row = prog_result.fetchone()
    guild_best = prog_row.raid_progression if prog_row else None

    # Raid clearers from character_raid_progress — filtered to current season's raid tier
    active_season_result = await db.execute(
        select(RaidSeason).where(RaidSeason.is_active == True)
    )
    active_season = active_season_result.scalar_one_or_none()
    current_raid_ids: list[int] = (
        active_season.current_raid_ids or [] if active_season else []
    )

    if current_raid_ids:
        raid_result = await db.execute(
            text("""
                SELECT difficulty, COUNT(DISTINCT character_id) AS cnt
                FROM guild_identity.character_raid_progress
                WHERE raid_id = ANY(:raid_ids)
                GROUP BY difficulty
            """).bindparams(raid_ids=current_raid_ids)
        )
    else:
        raid_result = await db.execute(
            text("""
                SELECT difficulty, COUNT(DISTINCT character_id) AS cnt
                FROM guild_identity.character_raid_progress
                GROUP BY difficulty
            """)
        )
    diff_counts = {row.difficulty: int(row.cnt) for row in raid_result}

    return {
        "ok": True,
        "data": {
            "mythic_plus": {
                "average_score": avg_score,
                "median_score": median_score,
                "top_10": top_10,
            },
            "raid_progression": {
                "guild_best": guild_best,
                "heroic_clearers": diff_counts.get("heroic", 0),
                "mythic_progressed": diff_counts.get("mythic", 0),
            },
        },
    }


# ---------------------------------------------------------------------------
# Warcraft Logs public endpoint — Phase 4.5
# ---------------------------------------------------------------------------


@router.get("/parses")
async def get_parses(db: AsyncSession = Depends(get_db)):
    """Return aggregate WCL parse data (public, no auth required).

    Returns best heroic parses per character per encounter, grouped by zone.
    Only includes heroic (difficulty=4) parses with percentile > 0.
    """
    result = await db.execute(
        text("""
            SELECT cp.encounter_name, cp.zone_name, cp.spec,
                   cp.percentile, cp.amount, cp.difficulty,
                   wc.character_name
            FROM guild_identity.character_parses cp
            JOIN guild_identity.wow_characters wc ON wc.id = cp.character_id
            WHERE cp.difficulty = 4 AND cp.percentile > 0
              AND wc.in_guild = TRUE
            ORDER BY cp.zone_name, cp.encounter_name, cp.percentile DESC
        """)
    )
    rows = result.fetchall()

    # Group by zone → character → encounters
    zones: dict[str, dict] = {}
    char_enc: dict[tuple, dict] = {}  # (char_name, enc_name) → best parse

    for row in rows:
        key = (row.character_name, row.encounter_name)
        if key not in char_enc:
            char_enc[key] = {
                "boss": row.encounter_name,
                "percentile": float(row.percentile),
                "amount": float(row.amount) if row.amount else None,
                "spec": row.spec,
            }

    # Build by-character dict
    char_encounters: dict[str, list] = {}
    for (char_name, enc_name), parse in char_enc.items():
        char_encounters.setdefault(char_name, []).append(parse)

    # Detect zone name for response
    zone_name = rows[0].zone_name if rows else None

    characters = [
        {"name": char_name, "encounters": encounters}
        for char_name, encounters in sorted(char_encounters.items())
    ]

    return {
        "ok": True,
        "data": {
            "zone": zone_name,
            "difficulty": "Heroic",
            "characters": characters,
        },
    }


# ---------------------------------------------------------------------------
# Availability endpoints — new time-window format (patt.player_availability)
# ---------------------------------------------------------------------------


@router.get("/availability")
async def get_availability(db: AsyncSession = Depends(get_db)):
    """Returns player availability windows for the raid scheduling dashboard."""
    result = await db.execute(
        select(Player)
        .options(selectinload(Player.availability))
        .where(Player.is_active.is_(True))
        .order_by(Player.display_name)
    )
    players = list(result.scalars().all())

    rows = []
    for player in players:
        avail_by_day = {a.day_of_week: a for a in player.availability}
        row: dict[str, Any] = {
            "player_id": player.id,
            "display_name": player.display_name,
            "timezone": player.timezone,
            "auto_invite_events": player.auto_invite_events,
            "days": {},
        }
        for day_idx, day_name in enumerate(DAY_NAMES):
            a = avail_by_day.get(day_idx)
            if a:
                row["days"][day_name] = {
                    "earliest_start": a.earliest_start.strftime("%H:%M"),
                    "available_hours": float(a.available_hours),
                }
            else:
                row["days"][day_name] = None
        rows.append(row)

    return {"ok": True, "data": rows}


# ---------------------------------------------------------------------------
# Guild quotes endpoints (public read-only)
# ---------------------------------------------------------------------------


@router.get("/quotes")
async def get_quotes(subject: str | None = None, db: AsyncSession = Depends(get_db)):
    """Returns guild quotes and titles for all subjects (or filtered by ?subject=slug).

    Write operations (POST/PUT/DELETE) have been moved to the admin API (Phase 4.8).
    """
    if subject:
        # Filter by subject slug
        subj_result = await db.execute(
            select(QuoteSubject).where(
                QuoteSubject.command_slug == subject,
                QuoteSubject.active.is_(True),
            )
        )
        subj = subj_result.scalar_one_or_none()
        if not subj:
            return {"ok": True, "data": {"quotes": [], "titles": [], "subject": None}}
        quotes_result = await db.execute(
            select(GuildQuote).where(GuildQuote.subject_id == subj.id).order_by(GuildQuote.id)
        )
        titles_result = await db.execute(
            select(GuildQuoteTitle)
            .where(GuildQuoteTitle.subject_id == subj.id)
            .order_by(GuildQuoteTitle.id)
        )
        subject_data = {
            "id": subj.id,
            "command_slug": subj.command_slug,
            "display_name": subj.display_name,
        }
    else:
        quotes_result = await db.execute(select(GuildQuote).order_by(GuildQuote.id))
        titles_result = await db.execute(select(GuildQuoteTitle).order_by(GuildQuoteTitle.id))
        subject_data = None

    quotes = [{"id": q.id, "quote": q.quote} for q in quotes_result.scalars()]
    titles = [{"id": t.id, "title": t.title} for t in titles_result.scalars()]
    return {"ok": True, "data": {"quotes": quotes, "titles": titles, "subject": subject_data}}


# ---------------------------------------------------------------------------
# AH Prices public endpoint — Phase 5.3
# ---------------------------------------------------------------------------


@router.get("/ah-prices")
async def get_ah_prices(realm_id: int = 0, request: Request = None):
    """Return AH prices for the specified connected realm (0 = region-wide commodities)."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": False, "error": "unavailable"}
    try:
        from sv_common.guild_sync.ah_service import get_prices_for_realm, get_available_realms
        prices = await get_prices_for_realm(pool, realm_id)
        realms = await get_available_realms(pool)
        prices_filtered = [p for p in prices if p.get("min_buyout") is not None]
        return {"ok": True, "data": {"prices": prices_filtered, "available_realms": realms}}
    except Exception:
        return {"ok": False, "error": "unavailable"}
