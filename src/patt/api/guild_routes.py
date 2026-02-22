"""Public guild API routes — roster, rank info, availability, and Mito content."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_db
from sv_common.db.models import (
    Character,
    GuildMember,
    MemberAvailability,
    MitoQuote,
    MitoTitle,
)
from sv_common.identity import ranks as rank_service

router = APIRouter(prefix="/api/v1/guild", tags=["guild"])

DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# ---------------------------------------------------------------------------
# Role normalization helpers
# ---------------------------------------------------------------------------

_ROLE_FROM_SHEET = {
    "tank": "tank",
    "healer": "healer",
    "melee": "melee_dps",
    "ranged": "ranged_dps",
    # Accept already-normalized values too
    "melee_dps": "melee_dps",
    "ranged_dps": "ranged_dps",
}


def normalize_role(role: str) -> str:
    """Normalize legacy sheet role values to DB enum."""
    return _ROLE_FROM_SHEET.get(role.lower(), role.lower())


def denormalize_role(role: str) -> str:
    """Convert DB role back to legacy sheet display value."""
    mapping = {
        "tank": "Tank",
        "healer": "Healer",
        "melee_dps": "Melee",
        "ranged_dps": "Ranged",
    }
    return mapping.get(role, role.capitalize())


def build_armory_url(name: str, realm: str = "senjin") -> str:
    realm_slug = realm.lower().replace("'", "").replace(" ", "-")
    return f"https://worldofwarcraft.blizzard.com/en-us/character/us/{realm_slug}/{name.lower()}"


# ---------------------------------------------------------------------------
# Existing endpoints
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
                "description": r.description,
            }
            for r in all_ranks
        ],
    }


@router.get("/roster")
async def get_roster(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .order_by(GuildMember.discord_username)
    )
    members = list(result.scalars().all())

    if not members:
        return {"ok": True, "data": {"members": []}}

    member_ids = [m.id for m in members]
    chars_result = await db.execute(
        select(Character)
        .where(Character.member_id.in_(member_ids))
        .where(Character.main_alt == "main")
    )
    main_chars: dict[int, Character] = {
        c.member_id: c for c in chars_result.scalars().all()
    }

    roster = []
    for member in members:
        main_char = main_chars.get(member.id)
        entry: dict = {
            "display_name": member.display_name or member.discord_username,
            "rank": member.rank.name if member.rank else "Unknown",
            "main_character": None,
        }
        if main_char:
            entry["main_character"] = {
                "name": main_char.name,
                "realm": main_char.realm,
                "class": main_char.class_,
                "spec": main_char.spec,
                "role": main_char.role,
                "armory_url": main_char.armory_url,
            }
        roster.append(entry)

    return {"ok": True, "data": {"members": roster}}


# ---------------------------------------------------------------------------
# Legacy compatibility endpoints (Phase 5)
# ---------------------------------------------------------------------------


@router.get("/roster-data")
async def get_roster_data(db: AsyncSession = Depends(get_db)):
    """
    Returns data in the same shape the legacy HTML tools expect.
    Replaces the Google Apps Script doGet endpoint.

    Response shape:
      {
        "success": true,
        "availability": [{discord, monday, tuesday, ...sunday, notes, autoSignup, wantsReminders}],
        "characters": [{discord, character, class, spec, role, mainAlt}],
        "discordIds": {"username": "snowflake_id"}
      }
    """
    members_result = await db.execute(
        select(GuildMember).options(
            selectinload(GuildMember.characters),
            selectinload(GuildMember.availability),
        )
    )
    members = list(members_result.scalars().all())

    availability_rows = []
    characters_rows = []
    discord_ids: dict[str, str] = {}

    for member in members:
        username = member.discord_username

        # Build discord ID map
        if member.discord_id:
            discord_ids[username] = member.discord_id

        # Build availability row
        avail_by_day: dict[str, MemberAvailability] = {
            a.day_of_week: a for a in member.availability
        }
        notes = ""
        auto_signup = False
        wants_reminders = False
        # Pull shared fields from any day (they're duplicated per row for simplicity)
        for day_row in member.availability:
            if day_row.notes:
                notes = day_row.notes
            auto_signup = day_row.auto_signup
            wants_reminders = day_row.wants_reminders
            break

        avail_entry: dict[str, Any] = {"discord": username, "notes": notes,
                                        "autoSignup": auto_signup, "wantsReminders": wants_reminders}
        for day in DAYS_OF_WEEK:
            row = avail_by_day.get(day)
            avail_entry[day] = row.available if row else False

        availability_rows.append(avail_entry)

        # Build character rows
        for char in member.characters:
            characters_rows.append({
                "discord": username,
                "character": char.name,
                "class": char.class_,
                "spec": char.spec or "",
                "role": denormalize_role(char.role),
                "mainAlt": "Main" if char.main_alt == "main" else "Alt",
            })

    return {
        "success": True,
        "availability": availability_rows,
        "characters": characters_rows,
        "discordIds": discord_ids,
        "validationIssues": [],
    }


@router.post("/roster-submit")
async def roster_submit(body: dict, db: AsyncSession = Depends(get_db)):
    """
    Accepts roster form submissions from the legacy roster.html form.
    Creates or updates guild_member + character records.
    Upserts availability rows.
    """
    discord_name = (body.get("discordName") or "").strip()
    character_name = (body.get("characterName") or "").strip()
    char_class = (body.get("class") or "").strip()
    spec = (body.get("spec") or "").strip()
    role_raw = (body.get("role") or "").strip()
    main_alt_raw = (body.get("mainAlt") or "Main").strip()
    availability: dict[str, bool] = body.get("availability") or {}
    auto_signup: bool = bool(body.get("autoSignup", False))
    wants_reminders: bool = bool(body.get("wantsReminders", False))
    notes: str = (body.get("notes") or "").strip()

    if not discord_name or not character_name:
        raise HTTPException(status_code=422, detail="discordName and characterName are required")

    role = normalize_role(role_raw) if role_raw else "ranged_dps"
    main_alt = "main" if main_alt_raw.lower() == "main" else "alt"
    realm = "Sen'jin"

    # Find or create member
    member_result = await db.execute(
        select(GuildMember).where(GuildMember.discord_username == discord_name)
    )
    member = member_result.scalar_one_or_none()

    if not member:
        from sv_common.db.models import GuildRank

        # Use rank level 2 (Member) or fall back to lowest rank
        rank_res = await db.execute(select(GuildRank).where(GuildRank.level == 2))
        default_rank = rank_res.scalar_one_or_none()
        if not default_rank:
            rank_res2 = await db.execute(select(GuildRank).order_by(GuildRank.level).limit(1))
            default_rank = rank_res2.scalar_one_or_none()
        if not default_rank:
            raise HTTPException(status_code=500, detail="No ranks configured. Run seed first.")

        member = GuildMember(
            discord_username=discord_name,
            display_name=discord_name,
            rank_id=default_rank.id,
            rank_source="manual",
        )
        db.add(member)
        await db.flush()

    # Upsert character
    char_result = await db.execute(
        select(Character).where(Character.name == character_name).where(Character.realm == realm)
    )
    char = char_result.scalar_one_or_none()
    armory_url = build_armory_url(character_name, realm)

    if char:
        char.member_id = member.id
        char.class_ = char_class
        char.spec = spec
        char.role = role
        char.main_alt = main_alt
        char.armory_url = armory_url
    else:
        char = Character(
            member_id=member.id,
            name=character_name,
            realm=realm,
            class_=char_class,
            spec=spec,
            role=role,
            main_alt=main_alt,
            armory_url=armory_url,
        )
        db.add(char)

    # Upsert availability rows
    for day in DAYS_OF_WEEK:
        is_available = bool(availability.get(day, False))
        avail_result = await db.execute(
            select(MemberAvailability)
            .where(MemberAvailability.member_id == member.id)
            .where(MemberAvailability.day_of_week == day)
        )
        avail_row = avail_result.scalar_one_or_none()
        if avail_row:
            avail_row.available = is_available
            avail_row.notes = notes
            avail_row.auto_signup = auto_signup
            avail_row.wants_reminders = wants_reminders
        else:
            avail_row = MemberAvailability(
                member_id=member.id,
                day_of_week=day,
                available=is_available,
                notes=notes,
                auto_signup=auto_signup,
                wants_reminders=wants_reminders,
            )
            db.add(avail_row)

    await db.commit()
    return {"success": True, "message": "Roster entry saved."}


@router.get("/availability")
async def get_availability(db: AsyncSession = Depends(get_db)):
    """Returns availability data shaped for the raid admin dashboard."""
    members_result = await db.execute(
        select(GuildMember).options(selectinload(GuildMember.availability))
    )
    members = list(members_result.scalars().all())

    rows = []
    for member in members:
        avail_by_day = {a.day_of_week: a for a in member.availability}
        notes = ""
        auto_signup = False
        wants_reminders = False
        for a in member.availability:
            if a.notes:
                notes = a.notes
            auto_signup = a.auto_signup
            wants_reminders = a.wants_reminders
            break

        row: dict[str, Any] = {
            "discord": member.discord_username,
            "notes": notes,
            "autoSignup": auto_signup,
            "wantsReminders": wants_reminders,
        }
        for day in DAYS_OF_WEEK:
            d = avail_by_day.get(day)
            row[day] = d.available if d else False
        rows.append(row)

    return {"ok": True, "data": rows}


class AvailabilitySubmitBody(BaseModel):
    discordName: str
    availability: dict[str, bool] = {}
    autoSignup: bool = False
    wantsReminders: bool = False
    notes: str = ""


@router.post("/availability")
async def post_availability(body: AvailabilitySubmitBody, db: AsyncSession = Depends(get_db)):
    """Accepts availability form submissions and updates member schedule."""
    discord_name = body.discordName.strip()
    if not discord_name:
        raise HTTPException(status_code=422, detail="discordName is required")

    member_result = await db.execute(
        select(GuildMember).where(GuildMember.discord_username == discord_name)
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail=f"Member '{discord_name}' not found")

    for day in DAYS_OF_WEEK:
        is_available = bool(body.availability.get(day, False))
        avail_result = await db.execute(
            select(MemberAvailability)
            .where(MemberAvailability.member_id == member.id)
            .where(MemberAvailability.day_of_week == day)
        )
        avail_row = avail_result.scalar_one_or_none()
        if avail_row:
            avail_row.available = is_available
            avail_row.notes = body.notes
            avail_row.auto_signup = body.autoSignup
            avail_row.wants_reminders = body.wantsReminders
        else:
            avail_row = MemberAvailability(
                member_id=member.id,
                day_of_week=day,
                available=is_available,
                notes=body.notes,
                auto_signup=body.autoSignup,
                wants_reminders=body.wantsReminders,
            )
            db.add(avail_row)

    await db.commit()
    return {"ok": True, "message": "Availability updated."}


# ---------------------------------------------------------------------------
# Mito's Corner endpoints (Phase 5)
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
