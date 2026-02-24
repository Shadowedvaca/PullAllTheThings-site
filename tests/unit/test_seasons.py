"""Unit tests for patt.services.season_service."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import RaidSeason
from patt.services import season_service


async def _create_season(
    db: AsyncSession,
    name: str,
    start_date: date,
    is_active: bool = True,
) -> RaidSeason:
    return await season_service.create_season(
        db, name=name, start_date=start_date, is_active=is_active
    )


# ---------------------------------------------------------------------------
# get_current_season
# ---------------------------------------------------------------------------


async def test_get_current_season_returns_latest_started(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    s1 = await _create_season(db_session, "Season 1", today - timedelta(days=60))
    s2 = await _create_season(db_session, "Season 2", today - timedelta(days=10))

    current = await season_service.get_current_season(db_session)

    assert current is not None
    assert current.name == "Season 2"


async def test_get_current_season_ignores_future_start_dates(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    past = await _create_season(db_session, "Past Season", today - timedelta(days=30))
    await _create_season(db_session, "Future Season", today + timedelta(days=30))

    current = await season_service.get_current_season(db_session)

    assert current is not None
    assert current.name == "Past Season"


async def test_get_current_season_ignores_inactive(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    active = await _create_season(db_session, "Active Season", today - timedelta(days=20))
    inactive = await _create_season(
        db_session, "Inactive Season", today - timedelta(days=5), is_active=False
    )

    current = await season_service.get_current_season(db_session)

    assert current is not None
    assert current.name == "Active Season"


async def test_get_current_season_returns_none_when_no_seasons(db_session: AsyncSession):
    current = await season_service.get_current_season(db_session)
    # No seasons in DB (fresh transaction) — should be None
    assert current is None


async def test_create_season(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    season = await season_service.create_season(
        db_session,
        name="Liberation of Undermine",
        start_date=today,
        is_active=True,
    )

    assert season.id is not None
    assert season.name == "Liberation of Undermine"
    assert season.start_date == today
    assert season.is_active is True
