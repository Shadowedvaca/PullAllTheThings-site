"""Integration tests for admin and guild API endpoints."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildRank, Player, User
from sv_common.auth.passwords import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_rank(db: AsyncSession, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _make_player(
    db: AsyncSession, rank_id: int, display_name: str
) -> Player:
    player = Player(display_name=display_name, guild_rank_id=rank_id)
    db.add(player)
    await db.flush()
    return player


async def _make_admin_headers(db: AsyncSession, rank_level: int = 5) -> dict:
    """Create an officer/admin player with a User and return auth headers."""
    from sv_common.auth.jwt import create_access_token

    rank = GuildRank(name=f"AdminRank_{rank_level}", level=rank_level)
    db.add(rank)
    await db.flush()

    user = User(email=f"admin_{rank_level}@test.com", password_hash=hash_password("pw"))
    db.add(user)
    await db.flush()

    player = Player(
        display_name=f"AdminPlayer_{rank_level}",
        guild_rank_id=rank.id,
        website_user_id=user.id,
    )
    db.add(player)
    await db.flush()

    token = create_access_token(
        user_id=user.id,
        member_id=player.id,
        rank_level=rank_level,
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Admin ranks
# ---------------------------------------------------------------------------


async def test_list_ranks(client: AsyncClient, db_session: AsyncSession):
    headers = await _make_admin_headers(db_session)
    await _make_rank(db_session, "Initiate_lr", 1)
    await _make_rank(db_session, "Officer_lr", 4)

    resp = await client.get("/api/v1/admin/ranks", headers=headers)
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    names = [r["name"] for r in body["data"]]
    assert "Initiate_lr" in names
    assert "Officer_lr" in names


async def test_create_rank_via_api(client: AsyncClient, db_session: AsyncSession):
    headers = await _make_admin_headers(db_session)

    resp = await client.post(
        "/api/v1/admin/ranks",
        json={"name": "Legend_cr", "level": 99, "description": "Top tier"},
        headers=headers,
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["name"] == "Legend_cr"
    assert body["data"]["level"] == 99


# ---------------------------------------------------------------------------
# Admin players (via /members endpoints)
# ---------------------------------------------------------------------------


async def test_create_player_via_api(client: AsyncClient, db_session: AsyncSession):
    headers = await _make_admin_headers(db_session)
    rank = await _make_rank(db_session, "Initiate_cpva", 1)

    resp = await client.post(
        "/api/v1/admin/members",
        json={"display_name": "NewGuy_cpva", "guild_rank_id": rank.id},
        headers=headers,
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["display_name"] == "NewGuy_cpva"


async def test_update_player_rank_via_api(client: AsyncClient, db_session: AsyncSession):
    headers = await _make_admin_headers(db_session)
    initiate_rank = await _make_rank(db_session, "Initiate_uprva", 1)
    veteran_rank = await _make_rank(db_session, "Veteran_uprva", 3)
    player = await _make_player(db_session, initiate_rank.id, "RankUp_uprva")

    resp = await client.patch(
        f"/api/v1/admin/members/{player.id}",
        json={"guild_rank_id": veteran_rank.id},
        headers=headers,
    )
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["guild_rank_id"] == veteran_rank.id


async def test_get_player_detail(client: AsyncClient, db_session: AsyncSession):
    headers = await _make_admin_headers(db_session)
    rank = await _make_rank(db_session, "Member_gpd", 2)
    player = await _make_player(db_session, rank.id, "Detail User_gpd")

    resp = await client.get(f"/api/v1/admin/members/{player.id}", headers=headers)
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["data"]["display_name"] == "Detail User_gpd"
    assert "characters" in body["data"]
    assert body["data"]["characters"] == []


async def test_get_nonexistent_player_returns_error(
    client: AsyncClient, db_session: AsyncSession
):
    headers = await _make_admin_headers(db_session)

    resp = await client.get("/api/v1/admin/members/99999", headers=headers)
    body = resp.json()

    assert resp.status_code == 200  # API returns 200 with ok=False
    assert body["ok"] is False


# ---------------------------------------------------------------------------
# Auth guard — admin routes require Officer+ (rank 4+)
# ---------------------------------------------------------------------------


async def test_admin_route_blocked_without_auth(client: AsyncClient):
    resp = await client.get("/api/v1/admin/ranks")
    assert resp.status_code == 401


async def test_admin_route_blocked_for_low_rank(
    client: AsyncClient, db_session: AsyncSession
):
    """A player with rank 2 (Member) cannot access admin routes."""
    from sv_common.auth.jwt import create_access_token

    rank = GuildRank(name="Member_arblr", level=2)
    db_session.add(rank)
    await db_session.flush()

    user = User(email="lowrank@test.com", password_hash=hash_password("pw"))
    db_session.add(user)
    await db_session.flush()

    player = Player(
        display_name="LowRankPlayer_arblr",
        guild_rank_id=rank.id,
        website_user_id=user.id,
    )
    db_session.add(player)
    await db_session.flush()

    token = create_access_token(user_id=user.id, member_id=player.id, rank_level=2)
    resp = await client.get(
        "/api/v1/admin/ranks", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


async def test_roster_endpoint_returns_ok(
    client: AsyncClient, db_session: AsyncSession
):
    """Public roster returns ok=True (may be empty if no players have main chars set)."""
    resp = await client.get("/api/v1/guild/roster")
    body = resp.json()

    assert resp.status_code == 200
    assert body["ok"] is True
    assert "members" in body["data"]
