"""Availability service — CRUD for player raid time windows."""

from datetime import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sv_common.db.models import GuildRank, Player, PlayerAvailability


async def get_player_availability(
    db: AsyncSession, player_id: int
) -> list[PlayerAvailability]:
    """Return all availability rows for a player, ordered by day_of_week."""
    result = await db.execute(
        select(PlayerAvailability)
        .where(PlayerAvailability.player_id == player_id)
        .order_by(PlayerAvailability.day_of_week)
    )
    return list(result.scalars().all())


async def set_player_availability(
    db: AsyncSession,
    player_id: int,
    day_of_week: int,
    earliest_start: time,
    available_hours: Decimal,
) -> PlayerAvailability:
    """Upsert a single day's availability for a player.

    day_of_week: 0=Monday … 6=Sunday (ISO weekday).
    earliest_start: local time in player's timezone (stored as-is).
    available_hours: must be between 0 (exclusive) and 16 (inclusive).
    """
    if not (0 <= day_of_week <= 6):
        raise ValueError(f"day_of_week must be 0–6, got {day_of_week}")
    if not (Decimal("0") < Decimal(str(available_hours)) <= Decimal("16")):
        raise ValueError(f"available_hours must be >0 and <=16, got {available_hours}")

    result = await db.execute(
        select(PlayerAvailability).where(
            PlayerAvailability.player_id == player_id,
            PlayerAvailability.day_of_week == day_of_week,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.earliest_start = earliest_start
        existing.available_hours = available_hours
        await db.flush()
        await db.refresh(existing)
        return existing

    row = PlayerAvailability(
        player_id=player_id,
        day_of_week=day_of_week,
        earliest_start=earliest_start,
        available_hours=available_hours,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def clear_player_availability(db: AsyncSession, player_id: int) -> int:
    """Delete all availability rows for a player. Returns number deleted."""
    result = await db.execute(
        select(PlayerAvailability).where(PlayerAvailability.player_id == player_id)
    )
    rows = list(result.scalars().all())
    for row in rows:
        await db.delete(row)
    await db.flush()
    return len(rows)


async def get_all_availability_for_day(
    db: AsyncSession, day_of_week: int
) -> list[dict]:
    """Return availability + player + scheduling_weight for a given day.

    Used for scheduling optimization (scoring). Returns dicts with:
      player_id, display_name, day_of_week, earliest_start, available_hours,
      scheduling_weight (from guild rank, 0 if no rank set).
    """
    result = await db.execute(
        select(PlayerAvailability)
        .options(
            selectinload(PlayerAvailability.player).selectinload(Player.guild_rank)
        )
        .where(PlayerAvailability.day_of_week == day_of_week)
    )
    rows = list(result.scalars().all())

    return [
        {
            "player_id": row.player_id,
            "display_name": row.player.display_name,
            "day_of_week": row.day_of_week,
            "earliest_start": row.earliest_start,
            "available_hours": row.available_hours,
            "scheduling_weight": (
                row.player.guild_rank.scheduling_weight
                if row.player.guild_rank
                else 0
            ),
        }
        for row in rows
    ]
