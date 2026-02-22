"""Migrate guild data from Google Apps Script / Sheets to PostgreSQL.

Phase 5 deliverable. Run once after deploying the platform:

    python scripts/migrate_sheets.py

Idempotent: running multiple times will update existing records, not duplicate them.
Keyed on discord_username for members and (name, realm) for characters.

Requirements: DATABASE_URL and GOOGLE_APPS_SCRIPT_URL in .env (or environment).
"""

import asyncio
import sys
import urllib.request
import json
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv  # type: ignore

load_dotenv()

import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from sv_common.db.models import (
    Character,
    GuildMember,
    GuildRank,
    MemberAvailability,
    MitoQuote,
    MitoTitle,
)

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

ROLE_MAP = {
    "tank": "tank",
    "healer": "healer",
    "melee": "melee_dps",
    "ranged": "ranged_dps",
    "melee_dps": "melee_dps",
    "ranged_dps": "ranged_dps",
    "dps": "ranged_dps",  # fallback
}

REALM = "Sen'jin"


def normalize_role(role_raw: str) -> str:
    return ROLE_MAP.get(role_raw.lower().strip(), "ranged_dps")


def build_armory_url(name: str) -> str:
    return f"https://worldofwarcraft.blizzard.com/en-us/character/us/senjin/{name.lower()}"


def fetch_sheets_data(url: str) -> dict:
    """Fetch data from the Google Apps Script endpoint."""
    print(f"Fetching data from Apps Script: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "PATT-Migration/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not data.get("success"):
        raise RuntimeError(f"Apps Script returned success=false: {data}")
    return data


async def get_or_create_member(
    session: AsyncSession,
    discord_username: str,
    discord_id: str | None,
    default_rank: GuildRank,
) -> tuple[GuildMember, bool]:
    """Return (member, created). Upserts discord_id if provided."""
    result = await session.execute(
        select(GuildMember).where(GuildMember.discord_username == discord_username)
    )
    member = result.scalar_one_or_none()
    created = False

    if member is None:
        member = GuildMember(
            discord_username=discord_username,
            display_name=discord_username,
            rank_id=default_rank.id,
            rank_source="manual",
        )
        session.add(member)
        await session.flush()
        created = True

    if discord_id and not member.discord_id:
        # Only set if not already claimed by another member
        taken_res = await session.execute(
            select(GuildMember).where(GuildMember.discord_id == discord_id)
        )
        taken = taken_res.scalar_one_or_none()
        if taken is None:
            member.discord_id = discord_id

    return member, created


async def upsert_character(
    session: AsyncSession,
    member: GuildMember,
    char_data: dict,
) -> tuple[Character, bool]:
    char_name = char_data["character"].strip()
    char_class = char_data.get("class", "").strip()
    spec = char_data.get("spec", "").strip()
    role = normalize_role(char_data.get("role", ""))
    main_alt = "main" if char_data.get("mainAlt", "").lower() == "main" else "alt"
    armory_url = build_armory_url(char_name)

    result = await session.execute(
        select(Character)
        .where(Character.name == char_name)
        .where(Character.realm == REALM)
    )
    char = result.scalar_one_or_none()
    created = False

    if char is None:
        char = Character(
            member_id=member.id,
            name=char_name,
            realm=REALM,
            class_=char_class,
            spec=spec,
            role=role,
            main_alt=main_alt,
            armory_url=armory_url,
        )
        session.add(char)
        created = True
    else:
        # Update in case any fields changed
        char.member_id = member.id
        char.class_ = char_class
        char.spec = spec
        char.role = role
        char.main_alt = main_alt
        char.armory_url = armory_url

    return char, created


async def upsert_availability(
    session: AsyncSession,
    member: GuildMember,
    avail_data: dict,
) -> None:
    notes = (avail_data.get("notes") or "").strip()
    auto_signup = bool(avail_data.get("autoSignup", False))
    wants_reminders = bool(avail_data.get("wantsReminders", False))

    for day in DAYS:
        value = avail_data.get(day)
        # Apps Script may return True/False bool or "TRUE"/"FALSE" string
        is_available = value is True or str(value).upper() == "TRUE"

        result = await session.execute(
            select(MemberAvailability)
            .where(MemberAvailability.member_id == member.id)
            .where(MemberAvailability.day_of_week == day)
        )
        row = result.scalar_one_or_none()

        if row is None:
            row = MemberAvailability(
                member_id=member.id,
                day_of_week=day,
                available=is_available,
                notes=notes,
                auto_signup=auto_signup,
                wants_reminders=wants_reminders,
            )
            session.add(row)
        else:
            row.available = is_available
            row.notes = notes
            row.auto_signup = auto_signup
            row.wants_reminders = wants_reminders


async def migrate_mito(
    session: AsyncSession,
    quotes: list[str],
    titles: list[str],
) -> tuple[int, int]:
    """Migrate Mito quotes and titles. Skips exact duplicates."""
    q_count = 0
    t_count = 0

    existing_quotes_res = await session.execute(select(MitoQuote))
    existing_quotes = {q.quote for q in existing_quotes_res.scalars()}

    for quote_text in quotes:
        text = quote_text.strip()
        if text and text not in existing_quotes:
            session.add(MitoQuote(quote=text))
            existing_quotes.add(text)
            q_count += 1

    existing_titles_res = await session.execute(select(MitoTitle))
    existing_titles = {t.title for t in existing_titles_res.scalars()}

    for title_text in titles:
        text = title_text.strip()
        if text and text not in existing_titles:
            session.add(MitoTitle(title=text))
            existing_titles.add(text)
            t_count += 1

    return q_count, t_count


async def run_migration(database_url: str, script_url: str) -> None:
    engine = create_async_engine(database_url, echo=False)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Fetch data
    data = fetch_sheets_data(script_url)

    availability_rows: list[dict] = data.get("availability") or []
    characters_rows: list[dict] = data.get("characters") or []
    discord_ids: dict[str, str] = data.get("discordIds") or {}
    mito_quotes: list[str] = data.get("mitoQuotes") or []
    mito_titles: list[str] = data.get("mitoTitles") or []

    print(f"  {len(availability_rows)} availability rows")
    print(f"  {len(characters_rows)} character rows")
    print(f"  {len(discord_ids)} Discord ID mappings")
    print(f"  {len(mito_quotes)} Mito quotes")
    print(f"  {len(mito_titles)} Mito titles")
    print()

    # Track counts
    members_created = 0
    members_updated = 0
    chars_created = 0
    chars_updated = 0
    issues: list[str] = []

    async with factory() as session:
        # Get default rank (Member = level 2, fallback to lowest)
        rank_res = await session.execute(select(GuildRank).where(GuildRank.level == 2))
        default_rank = rank_res.scalar_one_or_none()
        if not default_rank:
            rank_res2 = await session.execute(select(GuildRank).order_by(GuildRank.level).limit(1))
            default_rank = rank_res2.scalar_one_or_none()
        if not default_rank:
            print("ERROR: No ranks found in database. Run 'alembic upgrade head' and seed first.")
            return

        # --- Migrate availability (one entry per member) ---
        print("Migrating members and availability...")
        for row in availability_rows:
            discord_name = (row.get("discord") or "").strip()
            if not discord_name:
                issues.append("Skipped availability row with no discord name")
                continue

            discord_id = discord_ids.get(discord_name) or discord_ids.get(discord_name.lower())

            member, created = await get_or_create_member(
                session, discord_name, discord_id, default_rank
            )
            if created:
                members_created += 1
            else:
                members_updated += 1

            await upsert_availability(session, member, row)
            await session.flush()

        # --- Migrate characters ---
        print("Migrating characters...")
        for row in characters_rows:
            discord_name = (row.get("discord") or "").strip()
            char_name = (row.get("character") or "").strip()

            if not discord_name:
                issues.append(f"Character '{char_name}' has no discord name — skipped")
                continue
            if not char_name:
                issues.append(f"Discord user '{discord_name}' has a character row with no name — skipped")
                continue

            # Find or create member (may not exist if they skipped availability)
            discord_id = discord_ids.get(discord_name) or discord_ids.get(discord_name.lower())
            member, created = await get_or_create_member(
                session, discord_name, discord_id, default_rank
            )
            if created:
                members_created += 1

            _, char_created = await upsert_character(session, member, row)
            if char_created:
                chars_created += 1
            else:
                chars_updated += 1

            await session.flush()

        # --- Migrate Mito content ---
        print("Migrating Mito quotes and titles...")
        q_new, t_new = await migrate_mito(session, mito_quotes, mito_titles)

        # --- Commit ---
        await session.commit()

    await engine.dispose()

    # --- Print summary ---
    print()
    print("=" * 60)
    print("MIGRATION COMPLETE")
    print("=" * 60)
    print(f"  Members created:     {members_created}")
    print(f"  Members updated:     {members_updated}")
    print(f"  Characters created:  {chars_created}")
    print(f"  Characters updated:  {chars_updated}")
    print(f"  Mito quotes added:   {q_new}")
    print(f"  Mito titles added:   {t_new}")
    print()
    if issues:
        print(f"  ⚠  {len(issues)} issue(s) flagged:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  No issues flagged.")
    print()
    print("Next steps:")
    print("  1. Review imported data in the admin UI")
    print("  2. Assign correct ranks to members (all defaulted to 'Member')")
    print("  3. Map Discord roles to guild ranks in admin UI")
    print("  4. The Google Sheet remains as a read-only archive — do not delete it")


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    script_url = os.getenv("GOOGLE_APPS_SCRIPT_URL")

    if not database_url:
        print("ERROR: DATABASE_URL not set in environment / .env")
        sys.exit(1)
    if not script_url:
        print("ERROR: GOOGLE_APPS_SCRIPT_URL not set in environment / .env")
        sys.exit(1)

    asyncio.run(run_migration(database_url, script_url))


if __name__ == "__main__":
    main()
