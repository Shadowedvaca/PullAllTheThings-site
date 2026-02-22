"""Integration tests for admin and guild API endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildMember, GuildRank


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_rank(db: AsyncSession, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _make_member(
    db: AsyncSession, rank_id: int, username: str, display_name: str | None = None
) -> GuildMember:
    member = GuildMember(
        discord_username=username,
        display_name=display_name,
        rank_id=rank_id,
    )
    db.add(member)
    await db.flush()
    return member


# ---------------------------------------------------------------------------
# Admin ranks
# ---------------------------------------------------------------------------


async def test_list_ranks(client: AsyncClient, db_session: AsyncSession):
    await _make_rank(db_session, "Initiate_lr", 1)
    await _make_rank(db_session, "Officer_lr", 4)

    resp = await client.get("/api/v1/admin/ranks")
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    names = [r["name"] for r in body["data"]]
    assert "Initiate_lr" in names
    assert "Officer_lr" in names


async def test_create_rank_via_api(client: AsyncClient):
    resp = await client.post(
        "/api/v1/admin/ranks",
        json={"name": "Legend_cr", "level": 99, "description": "Top tier"},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["name"] == "Legend_cr"
    assert body["data"]["level"] == 99


# ---------------------------------------------------------------------------
# Admin members
# ---------------------------------------------------------------------------


async def test_create_member_via_api(client: AsyncClient, db_session: AsyncSession):
    rank = await _make_rank(db_session, "Initiate_cmva", 1)

    resp = await client.post(
        "/api/v1/admin/members",
        json={
            "discord_username": "newguy_cmva",
            "display_name": "New Guy",
            "rank_id": rank.id,
        },
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["discord_username"] == "newguy_cmva"


async def test_update_member_rank_via_api(client: AsyncClient, db_session: AsyncSession):
    initiate_rank = await _make_rank(db_session, "Initiate_umrva", 1)
    veteran_rank = await _make_rank(db_session, "Veteran_umrva", 3)
    member = await _make_member(db_session, initiate_rank.id, "rankup_umrva")

    resp = await client.patch(
        f"/api/v1/admin/members/{member.id}",
        json={"rank_id": veteran_rank.id},
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["rank_id"] == veteran_rank.id


# ---------------------------------------------------------------------------
# Admin characters
# ---------------------------------------------------------------------------


async def test_add_character_to_member(client: AsyncClient, db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_actm", 2)
    member = await _make_member(db_session, rank.id, "charowner_actm")

    resp = await client.post(
        f"/api/v1/admin/members/{member.id}/characters",
        json={
            "name": "Trogmoon",
            "realm": "Sen'jin",
            "wow_class": "Druid",
            "spec": "Balance",
            "role": "ranged_dps",
            "main_alt": "main",
        },
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["name"] == "Trogmoon"
    assert "senjin" in body["data"]["armory_url"]


async def test_full_member_detail_includes_characters(
    client: AsyncClient, db_session: AsyncSession
):
    rank = await _make_rank(db_session, "Member_fmdic", 2)
    member = await _make_member(
        db_session, rank.id, "detailuser_fmdic", display_name="Detail User"
    )

    # Add a character via API
    await client.post(
        f"/api/v1/admin/members/{member.id}/characters",
        json={
            "name": "DetailChar",
            "realm": "Stormrage",
            "wow_class": "Warrior",
            "role": "tank",
            "main_alt": "main",
        },
    )

    resp = await client.get(f"/api/v1/admin/members/{member.id}")
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["display_name"] == "Detail User"
    assert len(body["data"]["characters"]) == 1
    assert body["data"]["characters"][0]["name"] == "DetailChar"


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


async def test_roster_endpoint_returns_formatted_data(
    client: AsyncClient, db_session: AsyncSession
):
    rank = await _make_rank(db_session, "GuildLeader_refd", 5)
    member = await _make_member(
        db_session, rank.id, "trog_refd", display_name="Trog"
    )

    # Add main character
    await client.post(
        f"/api/v1/admin/members/{member.id}/characters",
        json={
            "name": "Trogmoon",
            "realm": "Sen'jin",
            "wow_class": "Druid",
            "spec": "Balance",
            "role": "ranged_dps",
            "main_alt": "main",
        },
    )

    resp = await client.get("/api/v1/guild/roster")
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    members_list = body["data"]["members"]
    assert len(members_list) >= 1

    trog_entry = next((m for m in members_list if m["display_name"] == "Trog"), None)
    assert trog_entry is not None
    assert trog_entry["rank"] == "GuildLeader_refd"
    assert trog_entry["main_character"] is not None
    assert trog_entry["main_character"]["name"] == "Trogmoon"
    assert trog_entry["main_character"]["spec"] == "Balance"
    assert "senjin" in trog_entry["main_character"]["armory_url"]
