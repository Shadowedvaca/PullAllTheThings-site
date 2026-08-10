"""Integration coverage for current public availability and guild quote APIs.

These contracts replaced the retired Phase 5 legacy Mito write endpoints.
Public routes remain read-only; Officer+ quote mutations live under the admin API.
"""

from datetime import time
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_quote_subject(db: AsyncSession, suffix: str):
    from sv_common.auth.sessions import issue_session_token
    from sv_common.auth.passwords import hash_password
    from sv_common.db.models import GuildRank, Player, QuoteSubject, User

    rank = GuildRank(name=f"Officer_quotes_{suffix}", level=4)
    db.add(rank)
    await db.flush()
    user = User(
        email=f"quotes_{suffix}@test.com",
        password_hash=hash_password("password123"),
    )
    db.add(user)
    await db.flush()
    player = Player(
        display_name=f"Quote Subject {suffix}",
        guild_rank_id=rank.id,
        website_user_id=user.id,
    )
    db.add(player)
    await db.flush()
    subject = QuoteSubject(
        player_id=player.id,
        command_slug=f"quotes-{suffix}",
        display_name=player.display_name,
        active=True,
    )
    db.add(subject)
    await db.flush()
    token = (await issue_session_token(
        db,
        user_id=user.id,
        member_id=player.id,
        rank_level=rank.level,
    )).token
    return subject, {"Authorization": f"Bearer {token}"}


async def test_availability_endpoint_returns_current_time_windows(
    client: AsyncClient, db_session: AsyncSession
):
    from sv_common.db.models import GuildRank, Player, PlayerAvailability

    rank = GuildRank(name="Member_avtest", level=23, description="Test")
    db_session.add(rank)
    await db_session.flush()
    player = Player(display_name="availtester", guild_rank_id=rank.id)
    db_session.add(player)
    await db_session.flush()
    for day, start, hours in [
        (0, time(18, 0), Decimal("4.0")),
        (3, time(19, 30), Decimal("3.5")),
        (4, time(17, 0), Decimal("5.0")),
    ]:
        db_session.add(
            PlayerAvailability(
                player_id=player.id,
                day_of_week=day,
                earliest_start=start,
                available_hours=hours,
            )
        )
    await db_session.flush()

    response = await client.get("/api/v1/guild/availability")
    assert response.status_code == 200
    row = next(
        item for item in response.json()["data"]
        if item["display_name"] == "availtester"
    )
    assert row["days"]["monday"] == {
        "earliest_start": "18:00",
        "available_hours": 4.0,
    }
    assert row["days"]["thursday"]["earliest_start"] == "19:30"
    assert row["days"]["friday"]["available_hours"] == 5.0


async def test_availability_endpoint_empty_db(
    client: AsyncClient, db_session: AsyncSession
):
    response = await client.get("/api/v1/guild/availability")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": []}


async def test_public_quotes_returns_empty_collections(
    client: AsyncClient, db_session: AsyncSession
):
    response = await client.get("/api/v1/guild/quotes")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "quotes": [],
        "titles": [],
        "subject": None,
    }


async def test_admin_add_quote_is_visible_publicly(
    client: AsyncClient, db_session: AsyncSession
):
    subject, headers = await _make_quote_subject(db_session, "add-quote")
    response = await client.post(
        f"/api/v1/admin/quote-subjects/{subject.id}/quotes",
        json={"quote": "Less QQ more pew pew"},
        headers=headers,
    )
    assert response.status_code == 200
    quote_id = response.json()["data"]["id"]

    public = await client.get("/api/v1/guild/quotes?subject=quotes-add-quote")
    assert [quote["id"] for quote in public.json()["data"]["quotes"]] == [quote_id]


async def test_admin_add_title_is_visible_publicly(
    client: AsyncClient, db_session: AsyncSession
):
    subject, headers = await _make_quote_subject(db_session, "add-title")
    response = await client.post(
        f"/api/v1/admin/quote-subjects/{subject.id}/titles",
        json={"title": "Bubble Hearth Champion"},
        headers=headers,
    )
    assert response.status_code == 200
    title_id = response.json()["data"]["id"]

    public = await client.get("/api/v1/guild/quotes?subject=quotes-add-title")
    assert [title["id"] for title in public.json()["data"]["titles"]] == [title_id]


async def test_admin_can_update_and_delete_quote(
    client: AsyncClient, db_session: AsyncSession
):
    subject, headers = await _make_quote_subject(db_session, "update-delete")
    created = await client.post(
        f"/api/v1/admin/quote-subjects/{subject.id}/quotes",
        json={"quote": "Original quote"},
        headers=headers,
    )
    quote_id = created.json()["data"]["id"]

    updated = await client.put(
        f"/api/v1/admin/quotes/{quote_id}",
        json={"quote": "Updated quote"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["quote"] == "Updated quote"

    deleted = await client.delete(
        f"/api/v1/admin/quotes/{quote_id}", headers=headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True

    public = await client.get("/api/v1/guild/quotes?subject=quotes-update-delete")
    assert public.json()["data"]["quotes"] == []


async def test_admin_update_nonexistent_quote_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    _, headers = await _make_quote_subject(db_session, "missing")
    response = await client.put(
        "/api/v1/admin/quotes/99999",
        json={"quote": "Ghost"},
        headers=headers,
    )
    assert response.status_code == 404
