"""Public guild API routes — roster, rank info, availability, and guild quote content."""

import statistics
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from guild_portal.deps import get_db
from sv_common.db.models import (
    GuildQuote,
    GuildQuoteTitle,
    Player,
    PlayerAvailability,
    PlayerCharacter,
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

    # Guild best progression string — from the highest-scored character
    guild_best = rio_rows[0].raid_progression if rio_rows else None

    # Raid clearers from character_raid_progress
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
# Guild quotes endpoints
# ---------------------------------------------------------------------------


@router.get("/quotes")
async def get_quotes(db: AsyncSession = Depends(get_db)):
    """Returns all guild quotes and titles."""
    quotes_result = await db.execute(select(GuildQuote).order_by(GuildQuote.id))
    titles_result = await db.execute(select(GuildQuoteTitle).order_by(GuildQuoteTitle.id))
    quotes = [{"id": q.id, "quote": q.quote} for q in quotes_result.scalars()]
    titles = [{"id": t.id, "title": t.title} for t in titles_result.scalars()]
    return {"ok": True, "data": {"quotes": quotes, "titles": titles}}


class GuildQuoteBody(BaseModel):
    quote: str


class GuildQuoteTitleBody(BaseModel):
    title: str


@router.post("/quotes")
async def add_guild_quote(body: GuildQuoteBody, db: AsyncSession = Depends(get_db)):
    quote = GuildQuote(quote=body.quote.strip())
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return {"ok": True, "data": {"id": quote.id, "quote": quote.quote}}


@router.put("/quotes/{quote_id}")
async def update_guild_quote(
    quote_id: int, body: GuildQuoteBody, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(GuildQuote).where(GuildQuote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    quote.quote = body.quote.strip()
    await db.commit()
    return {"ok": True, "data": {"id": quote.id, "quote": quote.quote}}


@router.delete("/quotes/{quote_id}")
async def delete_guild_quote(quote_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GuildQuote).where(GuildQuote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    await db.delete(quote)
    await db.commit()
    return {"ok": True}


@router.post("/quote-titles")
async def add_guild_quote_title(body: GuildQuoteTitleBody, db: AsyncSession = Depends(get_db)):
    title = GuildQuoteTitle(title=body.title.strip())
    db.add(title)
    await db.commit()
    await db.refresh(title)
    return {"ok": True, "data": {"id": title.id, "title": title.title}}


@router.put("/quote-titles/{title_id}")
async def update_guild_quote_title(
    title_id: int, body: GuildQuoteTitleBody, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(GuildQuoteTitle).where(GuildQuoteTitle.id == title_id))
    title = result.scalar_one_or_none()
    if not title:
        raise HTTPException(status_code=404, detail="Title not found")
    title.title = body.title.strip()
    await db.commit()
    return {"ok": True, "data": {"id": title.id, "title": title.title}}


@router.delete("/quote-titles/{title_id}")
async def delete_guild_quote_title(title_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GuildQuoteTitle).where(GuildQuoteTitle.id == title_id))
    title = result.scalar_one_or_none()
    if not title:
        raise HTTPException(status_code=404, detail="Title not found")
    await db.delete(title)
    await db.commit()
    return {"ok": True}
