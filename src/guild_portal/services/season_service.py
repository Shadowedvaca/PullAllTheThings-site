"""Season service — CRUD for raid seasons."""

from datetime import date, datetime, timezone

from sqlalchemy import select, update
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
    result = await db.execute(select(RaidSeason).order_by(RaidSeason.start_date.desc()))
    return list(result.scalars().all())


async def create_season(
    db: AsyncSession,
    expansion_name: str,
    season_number: int,
    start_date: date,
    is_new_expansion: bool = False,
    is_active: bool = True,
    **season_config,
) -> RaidSeason:
    """Create a fully configured season, atomically activating it if requested."""
    effective_active = is_active and start_date <= datetime.now(timezone.utc).date()
    if effective_active:
        await db.execute(update(RaidSeason).values(is_active=False))
    season = RaidSeason(
        expansion_name=expansion_name,
        season_number=season_number,
        start_date=start_date,
        is_new_expansion=is_new_expansion,
        is_active=effective_active,
        **season_config,
    )
    db.add(season)
    await db.flush()
    await db.refresh(season)
    return season


async def update_season(
    db: AsyncSession, season: RaidSeason, changes: dict
) -> RaidSeason:
    """Apply changes while preserving the single-active-season invariant."""
    if changes.get("is_active") is True:
        await db.execute(
            update(RaidSeason).where(RaidSeason.id != season.id).values(is_active=False)
        )
    for field, value in changes.items():
        if field in {"current_raid_ids", "current_instance_ids"}:
            value = value or None
        elif field == "tier_set_ids":
            value = value or []
        elif field in {"quality_ilvl_map", "crafted_ilvl_map"}:
            value = value or None
        setattr(season, field, value)
    await db.flush()
    return season
