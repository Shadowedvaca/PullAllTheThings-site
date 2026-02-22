"""Integration tests for Phase 5 legacy API endpoints.

Tests the endpoints that replace Google Apps Script:
  GET  /api/v1/guild/roster-data
  POST /api/v1/guild/roster-submit
  GET  /api/v1/guild/availability
  POST /api/v1/guild/availability
  GET/POST/PUT/DELETE /api/v1/guild/mito/*

Requires TEST_DATABASE_URL in environment. Skipped automatically if DB not available.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_member_rank(db: AsyncSession):
    """Ensure a Member rank (level 2) exists for roster-submit tests."""
    from sv_common.db.models import GuildRank
    from sqlalchemy import select

    res = await db.execute(select(GuildRank).where(GuildRank.level == 2))
    rank = res.scalar_one_or_none()
    if rank is None:
        rank = GuildRank(name="Member", level=2, description="Regular member")
        db.add(rank)
        await db.flush()
    return rank


# ---------------------------------------------------------------------------
# GET /api/v1/guild/roster-data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roster_data_endpoint_matches_expected_shape(client: AsyncClient, db_session: AsyncSession):
    """The /roster-data endpoint returns the shape the legacy HTML pages expect."""
    response = await client.get("/api/v1/guild/roster-data")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "availability" in data
    assert "characters" in data
    assert "discordIds" in data
    assert isinstance(data["availability"], list)
    assert isinstance(data["characters"], list)
    assert isinstance(data["discordIds"], dict)


@pytest.mark.asyncio
async def test_roster_data_returns_member_availability(client: AsyncClient, db_session: AsyncSession):
    """Member availability rows are included in roster-data response."""
    from sv_common.db.models import GuildMember, GuildRank, MemberAvailability

    rank = GuildRank(name="Member_av", level=22, description="Test")
    db_session.add(rank)
    await db_session.flush()

    member = GuildMember(
        discord_username="testuser_av",
        discord_id="123456789012345678",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()

    avail = MemberAvailability(
        member_id=member.id,
        day_of_week="thursday",
        available=True,
        auto_signup=True,
        wants_reminders=False,
    )
    db_session.add(avail)
    await db_session.flush()

    response = await client.get("/api/v1/guild/roster-data")
    assert response.status_code == 200
    data = response.json()

    found = [a for a in data["availability"] if a["discord"] == "testuser_av"]
    assert len(found) == 1
    assert found[0]["thursday"] is True
    assert found[0]["autoSignup"] is True

    # Discord ID should be in the map
    assert data["discordIds"].get("testuser_av") == "123456789012345678"


# ---------------------------------------------------------------------------
# POST /api/v1/guild/roster-submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roster_submit_creates_member_and_character(client: AsyncClient, db_session: AsyncSession):
    """Submitting the roster form creates a GuildMember and Character record."""
    from sqlalchemy import select
    from sv_common.db.models import Character, GuildMember

    await _seed_member_rank(db_session)

    payload = {
        "discordName": "newplayer",
        "characterName": "Shadowblade",
        "class": "Rogue",
        "spec": "Assassination",
        "role": "Melee",
        "mainAlt": "Main",
        "availability": {"thursday": True, "friday": True},
        "autoSignup": True,
        "wantsReminders": False,
        "notes": "Test note",
    }

    response = await client.post("/api/v1/guild/roster-submit", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

    # Verify member was created
    member_res = await db_session.execute(
        select(GuildMember).where(GuildMember.discord_username == "newplayer")
    )
    member = member_res.scalar_one_or_none()
    assert member is not None

    # Verify character was created
    char_res = await db_session.execute(
        select(Character)
        .where(Character.name == "Shadowblade")
        .where(Character.realm == "Sen'jin")
    )
    char = char_res.scalar_one_or_none()
    assert char is not None
    assert char.role == "melee_dps"
    assert char.main_alt == "main"
    assert char.class_ == "Rogue"
    assert char.spec == "Assassination"


@pytest.mark.asyncio
async def test_roster_submit_updates_existing_member(client: AsyncClient, db_session: AsyncSession):
    """Submitting again for the same discord name updates, doesn't duplicate."""
    from sqlalchemy import select
    from sv_common.db.models import Character, GuildMember

    await _seed_member_rank(db_session)

    payload = {
        "discordName": "returning_player",
        "characterName": "Frostmourne",
        "class": "Death Knight",
        "spec": "Frost",
        "role": "Melee",
        "mainAlt": "Main",
        "availability": {},
        "autoSignup": False,
        "wantsReminders": False,
        "notes": "",
    }

    # Submit twice
    r1 = await client.post("/api/v1/guild/roster-submit", json=payload)
    r2 = await client.post("/api/v1/guild/roster-submit", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Should only have one member with this username
    members_res = await db_session.execute(
        select(GuildMember).where(GuildMember.discord_username == "returning_player")
    )
    members = list(members_res.scalars().all())
    assert len(members) == 1

    # Should only have one character with this name+realm
    chars_res = await db_session.execute(
        select(Character)
        .where(Character.name == "Frostmourne")
        .where(Character.realm == "Sen'jin")
    )
    chars = list(chars_res.scalars().all())
    assert len(chars) == 1


@pytest.mark.asyncio
async def test_roster_submit_missing_required_fields_returns_422(client: AsyncClient, db_session: AsyncSession):
    """roster-submit requires discordName and characterName."""
    response = await client.post("/api/v1/guild/roster-submit", json={"discordName": "someone"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/guild/availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_availability_endpoint_returns_day_data(client: AsyncClient, db_session: AsyncSession):
    """The availability endpoint returns rows keyed by day with boolean values."""
    from sv_common.db.models import GuildMember, GuildRank, MemberAvailability

    rank = GuildRank(name="Member_avtest", level=23, description="Test")
    db_session.add(rank)
    await db_session.flush()

    member = GuildMember(
        discord_username="availtester",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()

    for day, avail in [("monday", False), ("thursday", True), ("friday", True)]:
        db_session.add(MemberAvailability(
            member_id=member.id,
            day_of_week=day,
            available=avail,
            auto_signup=False,
            wants_reminders=False,
        ))
    await db_session.flush()

    response = await client.get("/api/v1/guild/availability")
    assert response.status_code == 200
    data = response.json()

    assert data["ok"] is True
    rows = data["data"]
    assert isinstance(rows, list)

    found = [r for r in rows if r["discord"] == "availtester"]
    assert len(found) == 1
    row = found[0]
    assert row["monday"] is False
    assert row["thursday"] is True
    assert row["friday"] is True


# ---------------------------------------------------------------------------
# POST /api/v1/guild/availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_availability_submit_updates_member_schedule(client: AsyncClient, db_session: AsyncSession):
    """POST /availability updates the member's availability rows."""
    from sqlalchemy import select
    from sv_common.db.models import GuildMember, GuildRank, MemberAvailability

    rank = GuildRank(name="Member_avup", level=24, description="Test")
    db_session.add(rank)
    await db_session.flush()

    member = GuildMember(discord_username="scheduler", rank_id=rank.id)
    db_session.add(member)
    await db_session.flush()

    payload = {
        "discordName": "scheduler",
        "availability": {"monday": True, "wednesday": False, "friday": True},
        "autoSignup": True,
        "wantsReminders": False,
        "notes": "Updated schedule",
    }

    response = await client.post("/api/v1/guild/availability", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True

    # Verify DB
    avail_res = await db_session.execute(
        select(MemberAvailability).where(MemberAvailability.member_id == member.id)
    )
    rows = {r.day_of_week: r for r in avail_res.scalars().all()}
    assert rows["monday"].available is True
    assert rows["wednesday"].available is False
    assert rows["friday"].available is True
    assert rows["monday"].auto_signup is True
    assert rows["monday"].notes == "Updated schedule"


@pytest.mark.asyncio
async def test_availability_submit_unknown_member_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Submitting availability for a nonexistent member returns 404."""
    payload = {
        "discordName": "ghost_user_xyz",
        "availability": {"monday": True},
    }
    response = await client.post("/api/v1/guild/availability", json=payload)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Mito endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mito_get_returns_empty_lists_initially(client: AsyncClient, db_session: AsyncSession):
    response = await client.get("/api/v1/guild/mito")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "quotes" in data["data"]
    assert "titles" in data["data"]


@pytest.mark.asyncio
async def test_mito_add_and_retrieve_quote(client: AsyncClient, db_session: AsyncSession):
    r = await client.post("/api/v1/guild/mito/quotes", json={"quote": "Less QQ more pew pew"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    quote_id = data["data"]["id"]
    assert data["data"]["quote"] == "Less QQ more pew pew"

    # Retrieve
    r2 = await client.get("/api/v1/guild/mito")
    quotes = r2.json()["data"]["quotes"]
    ids = [q["id"] for q in quotes]
    assert quote_id in ids


@pytest.mark.asyncio
async def test_mito_add_and_retrieve_title(client: AsyncClient, db_session: AsyncSession):
    r = await client.post("/api/v1/guild/mito/titles", json={"title": "Bubble Hearth Champion"})
    assert r.status_code == 200
    title_id = r.json()["data"]["id"]

    r2 = await client.get("/api/v1/guild/mito")
    titles = r2.json()["data"]["titles"]
    ids = [t["id"] for t in titles]
    assert title_id in ids


@pytest.mark.asyncio
async def test_mito_update_quote(client: AsyncClient, db_session: AsyncSession):
    r = await client.post("/api/v1/guild/mito/quotes", json={"quote": "Original quote"})
    quote_id = r.json()["data"]["id"]

    r2 = await client.put(f"/api/v1/guild/mito/quotes/{quote_id}", json={"quote": "Updated quote"})
    assert r2.status_code == 200
    assert r2.json()["data"]["quote"] == "Updated quote"


@pytest.mark.asyncio
async def test_mito_delete_quote(client: AsyncClient, db_session: AsyncSession):
    r = await client.post("/api/v1/guild/mito/quotes", json={"quote": "To be deleted"})
    quote_id = r.json()["data"]["id"]

    r2 = await client.delete(f"/api/v1/guild/mito/quotes/{quote_id}")
    assert r2.status_code == 200
    assert r2.json()["ok"] is True

    # Should no longer exist
    r3 = await client.get("/api/v1/guild/mito")
    ids = [q["id"] for q in r3.json()["data"]["quotes"]]
    assert quote_id not in ids


@pytest.mark.asyncio
async def test_mito_update_nonexistent_quote_returns_404(client: AsyncClient, db_session: AsyncSession):
    r = await client.put("/api/v1/guild/mito/quotes/99999", json={"quote": "Ghost"})
    assert r.status_code == 404
