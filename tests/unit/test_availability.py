"""Unit tests for patt.services.availability_service."""

from datetime import time
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildRank, Player, PlayerAvailability
from patt.services import availability_service


async def _make_rank(db: AsyncSession, name: str, level: int, weight: int = 1) -> GuildRank:
    rank = GuildRank(name=name, level=level, scheduling_weight=weight)
    db.add(rank)
    await db.flush()
    return rank


async def _make_player(db: AsyncSession, rank_id: int, name: str = "Trog") -> Player:
    player = Player(display_name=name, guild_rank_id=rank_id)
    db.add(player)
    await db.flush()
    return player


# ---------------------------------------------------------------------------
# set_player_availability
# ---------------------------------------------------------------------------


async def test_set_availability_creates_row(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_av1", 2)
    player = await _make_player(db_session, rank.id, "Player_av1")

    row = await availability_service.set_player_availability(
        db_session,
        player_id=player.id,
        day_of_week=0,  # Monday
        earliest_start=time(19, 0),
        available_hours=Decimal("3.0"),
    )

    assert row.id is not None
    assert row.player_id == player.id
    assert row.day_of_week == 0
    assert row.earliest_start == time(19, 0)
    assert row.available_hours == Decimal("3.0")


async def test_set_availability_updates_existing(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_av2", 2)
    player = await _make_player(db_session, rank.id, "Player_av2")

    # Create initial row
    await availability_service.set_player_availability(
        db_session,
        player_id=player.id,
        day_of_week=2,  # Wednesday
        earliest_start=time(18, 0),
        available_hours=Decimal("2.0"),
    )

    # Update same day — should upsert
    updated = await availability_service.set_player_availability(
        db_session,
        player_id=player.id,
        day_of_week=2,
        earliest_start=time(20, 0),
        available_hours=Decimal("4.5"),
    )

    assert updated.earliest_start == time(20, 0)
    assert updated.available_hours == Decimal("4.5")

    # Verify only one row exists
    rows = await availability_service.get_player_availability(db_session, player.id)
    assert len(rows) == 1


async def test_clear_availability_removes_all_days(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_av3", 2)
    player = await _make_player(db_session, rank.id, "Player_av3")

    for day in range(4):
        await availability_service.set_player_availability(
            db_session,
            player_id=player.id,
            day_of_week=day,
            earliest_start=time(19, 0),
            available_hours=Decimal("3.0"),
        )

    deleted = await availability_service.clear_player_availability(db_session, player.id)
    assert deleted == 4

    rows = await availability_service.get_player_availability(db_session, player.id)
    assert rows == []


async def test_get_availability_includes_scheduling_weight(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Officer_av4", 4, weight=5)
    player = await _make_player(db_session, rank.id, "Player_av4")

    await availability_service.set_player_availability(
        db_session,
        player_id=player.id,
        day_of_week=4,  # Friday
        earliest_start=time(18, 30),
        available_hours=Decimal("4.0"),
    )

    results = await availability_service.get_all_availability_for_day(db_session, 4)

    # Filter to our player (other tests may have added rows)
    our = [r for r in results if r["player_id"] == player.id]
    assert len(our) == 1
    assert our[0]["scheduling_weight"] == 5
    assert our[0]["display_name"] == "Player_av4"
    assert our[0]["earliest_start"] == time(18, 30)


async def test_availability_day_range_validation(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_av5", 2)
    player = await _make_player(db_session, rank.id, "Player_av5")

    with pytest.raises(ValueError, match="day_of_week must be 0"):
        await availability_service.set_player_availability(
            db_session,
            player_id=player.id,
            day_of_week=7,  # invalid
            earliest_start=time(19, 0),
            available_hours=Decimal("3.0"),
        )

    with pytest.raises(ValueError, match="day_of_week must be 0"):
        await availability_service.set_player_availability(
            db_session,
            player_id=player.id,
            day_of_week=-1,  # invalid
            earliest_start=time(19, 0),
            available_hours=Decimal("3.0"),
        )
