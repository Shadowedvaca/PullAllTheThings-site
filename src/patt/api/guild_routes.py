"""Public guild API routes — roster, rank info, availability, and Mito content."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_db
from sv_common.db.models import (
    MitoQuote,
    MitoTitle,
    Player,
    PlayerAvailability,
    PlayerCharacter,
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
        .order_by(Player.display_name)
    )
    players = list(result.unique().scalars().all())

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
                }
            )

        roster.append(
            {
                "player_id": p.id,
                "display_name": p.display_name,
                "rank_name": p.guild_rank.name if p.guild_rank else "Unknown",
                "rank_level": p.guild_rank.level if p.guild_rank else 0,
                "main_character": main_char_data,
                "characters": all_chars,
            }
        )

    return {"ok": True, "data": {"players": roster}}


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
# Mito's Corner endpoints
# ---------------------------------------------------------------------------


@router.get("/mito")
async def get_mito(db: AsyncSession = Depends(get_db)):
    """Returns all Mito quotes and titles."""
    quotes_result = await db.execute(select(MitoQuote).order_by(MitoQuote.id))
    titles_result = await db.execute(select(MitoTitle).order_by(MitoTitle.id))
    quotes = [{"id": q.id, "quote": q.quote} for q in quotes_result.scalars()]
    titles = [{"id": t.id, "title": t.title} for t in titles_result.scalars()]
    return {"ok": True, "data": {"quotes": quotes, "titles": titles}}


class MitoQuoteBody(BaseModel):
    quote: str


class MitoTitleBody(BaseModel):
    title: str


@router.post("/mito/quotes")
async def add_mito_quote(body: MitoQuoteBody, db: AsyncSession = Depends(get_db)):
    quote = MitoQuote(quote=body.quote.strip())
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return {"ok": True, "data": {"id": quote.id, "quote": quote.quote}}


@router.put("/mito/quotes/{quote_id}")
async def update_mito_quote(
    quote_id: int, body: MitoQuoteBody, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(MitoQuote).where(MitoQuote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    quote.quote = body.quote.strip()
    await db.commit()
    return {"ok": True, "data": {"id": quote.id, "quote": quote.quote}}


@router.delete("/mito/quotes/{quote_id}")
async def delete_mito_quote(quote_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MitoQuote).where(MitoQuote.id == quote_id))
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    await db.delete(quote)
    await db.commit()
    return {"ok": True}


@router.post("/mito/titles")
async def add_mito_title(body: MitoTitleBody, db: AsyncSession = Depends(get_db)):
    title = MitoTitle(title=body.title.strip())
    db.add(title)
    await db.commit()
    await db.refresh(title)
    return {"ok": True, "data": {"id": title.id, "title": title.title}}


@router.put("/mito/titles/{title_id}")
async def update_mito_title(
    title_id: int, body: MitoTitleBody, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(MitoTitle).where(MitoTitle.id == title_id))
    title = result.scalar_one_or_none()
    if not title:
        raise HTTPException(status_code=404, detail="Title not found")
    title.title = body.title.strip()
    await db.commit()
    return {"ok": True, "data": {"id": title.id, "title": title.title}}


@router.delete("/mito/titles/{title_id}")
async def delete_mito_title(title_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MitoTitle).where(MitoTitle.id == title_id))
    title = result.scalar_one_or_none()
    if not title:
        raise HTTPException(status_code=404, detail="Title not found")
    await db.delete(title)
    await db.commit()
    return {"ok": True}
