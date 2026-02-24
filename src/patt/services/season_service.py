"""Season service — CRUD for raid seasons."""

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import RaidSeason


async def get_current_season(db: AsyncSession) -> RaidSeason | None:
    """Return the current season: latest start_date <= today, is_active=True."""
    today = datetime.now(timezone.utc).date()
    result = await db.execute(
        select(RaidSeason)
        .where(RaidSeason.is_active.is_(True))
        .where(RaidSeason.start_date <= today)
        .order_by(RaidSeason.start_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_all_seasons(db: AsyncSession) -> list[RaidSeason]:
    """Return all seasons, newest first."""
    result = await db.execute(
        select(RaidSeason).order_by(RaidSeason.start_date.desc())
    )
    return list(result.scalars().all())


async def create_season(
    db: AsyncSession,
    name: str,
    start_date: date,
    is_active: bool = True,
) -> RaidSeason:
    """Create a new raid season."""
    season = RaidSeason(name=name, start_date=start_date, is_active=is_active)
    db.add(season)
    await db.flush()
    await db.refresh(season)
    return season
