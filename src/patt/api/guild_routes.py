"""Public guild API routes — roster, rank info, availability, and Mito content."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_db
from sv_common.db.models import (
    MemberAvailability,
    MitoQuote,
    MitoTitle,
    Player,
    WowCharacter,
)
from sv_common.identity import ranks as rank_service

router = APIRouter(prefix="/api/v1/guild", tags=["guild"])

DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


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
    """Return roster: players who have a main character set."""
    result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.main_character).selectinload(WowCharacter.wow_class),
            selectinload(Player.main_character).selectinload(WowCharacter.active_spec),
            selectinload(Player.main_spec),
        )
        .where(Player.main_character_id.is_not(None))
        .where(Player.is_active.is_(True))
        .order_by(Player.display_name)
    )
    players = list(result.scalars().all())

    roster = []
    for p in players:
        mc = p.main_character
        entry: dict = {
            "display_name": p.display_name,
            "rank": p.guild_rank.name if p.guild_rank else "Unknown",
            "main_character": None,
        }
        if mc:
            armory_url = (
                f"https://worldofwarcraft.blizzard.com/en-us/character/us"
                f"/{mc.realm_slug}/{mc.character_name.lower()}"
            )
            entry["main_character"] = {
                "name": mc.character_name,
                "realm": mc.realm_slug,
                "class": mc.wow_class.name if mc.wow_class else None,
                "spec": p.main_spec.name if p.main_spec else (
                    mc.active_spec.name if mc.active_spec else None
                ),
                "armory_url": armory_url,
                "item_level": mc.item_level,
            }
        roster.append(entry)

    return {"ok": True, "data": {"members": roster}}


# ---------------------------------------------------------------------------
# Availability endpoints (now keyed by player.id or display_name)
# ---------------------------------------------------------------------------


@router.get("/availability")
async def get_availability(db: AsyncSession = Depends(get_db)):
    """Returns availability data shaped for the raid admin dashboard."""
    players_result = await db.execute(
        select(Player).options(selectinload(Player.availability))
    )
    players = list(players_result.scalars().all())

    rows = []
    for player in players:
        avail_by_day = {a.day_of_week: a for a in player.availability}
        notes = ""
        auto_signup = False
        wants_reminders = False
        for a in player.availability:
            if a.notes:
                notes = a.notes
            auto_signup = a.auto_signup
            wants_reminders = a.wants_reminders
            break

        row: dict[str, Any] = {
            "display_name": player.display_name,
            "notes": notes,
            "autoSignup": auto_signup,
            "wantsReminders": wants_reminders,
        }
        for day in DAYS_OF_WEEK:
            d = avail_by_day.get(day)
            row[day] = d.available if d else False
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
