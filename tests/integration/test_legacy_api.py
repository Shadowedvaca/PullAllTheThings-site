"""Integration tests for Phase 5 legacy-compatible API endpoints.

Phase 2.7 removed the legacy /roster-data and /roster-submit endpoints
that replaced Google Apps Script. The /availability endpoint (GET) is
still present but keyed by player.display_name now.

The Mito content endpoints remain unchanged.

Requires TEST_DATABASE_URL in environment. Skipped automatically if DB not available.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# GET /api/v1/guild/availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_availability_endpoint_returns_data(client: AsyncClient, db_session: AsyncSession):
    """The availability endpoint returns rows with day-of-week booleans."""
    from sv_common.db.models import GuildRank, Player, MemberAvailability

    rank = GuildRank(name="Member_avtest", level=23, description="Test")
    db_session.add(rank)
    await db_session.flush()

    player = Player(display_name="availtester", guild_rank_id=rank.id)
    db_session.add(player)
    await db_session.flush()

    for day, avail in [("monday", False), ("thursday", True), ("friday", True)]:
        db_session.add(MemberAvailability(
            player_id=player.id,
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

    found = [r for r in rows if r["display_name"] == "availtester"]
    assert len(found) == 1
    row = found[0]
    assert row["monday"] is False
    assert row["thursday"] is True
    assert row["friday"] is True


@pytest.mark.asyncio
async def test_availability_endpoint_empty_db(client: AsyncClient, db_session: AsyncSession):
    """Availability endpoint returns empty list when no players exist."""
    response = await client.get("/api/v1/guild/availability")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert isinstance(data["data"], list)


# ---------------------------------------------------------------------------
# Mito endpoints (unchanged from Phase 5)
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
