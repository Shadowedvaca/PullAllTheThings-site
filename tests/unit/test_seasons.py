"""Unit tests for patt.services.season_service."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import RaidSeason
from patt.services import season_service


async def _create_season(
    db: AsyncSession,
    expansion_name: str,
    season_number: int = 1,
    start_date: date = None,
    is_active: bool = True,
) -> RaidSeason:
    if start_date is None:
        start_date = datetime.now(timezone.utc).date()
    return await season_service.create_season(
        db,
        expansion_name=expansion_name,
        season_number=season_number,
        start_date=start_date,
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# get_current_season
# ---------------------------------------------------------------------------


async def test_get_current_season_returns_latest_started(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    s1 = await _create_season(db_session, "Khaz Algar", 1, today - timedelta(days=60))
    s2 = await _create_season(db_session, "Midnight", 1, today - timedelta(days=10))

    current = await season_service.get_current_season(db_session)

    assert current is not None
    assert current.display_name == "Midnight Season 1"


async def test_get_current_season_ignores_future_start_dates(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    past = await _create_season(db_session, "Khaz Algar", 1, today - timedelta(days=30))
    await _create_season(db_session, "Midnight", 1, today + timedelta(days=30))

    current = await season_service.get_current_season(db_session)

    assert current is not None
    assert current.display_name == "Khaz Algar Season 1"


async def test_get_current_season_ignores_inactive(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    active = await _create_season(db_session, "Midnight", 1, today - timedelta(days=20))
    inactive = await _create_season(
        db_session, "Midnight", 2, today - timedelta(days=5), is_active=False
    )

    current = await season_service.get_current_season(db_session)

    assert current is not None
    assert current.display_name == "Midnight Season 1"


async def test_get_current_season_returns_none_when_no_seasons(db_session: AsyncSession):
    current = await season_service.get_current_season(db_session)
    # No seasons in DB (fresh transaction) — should be None
    assert current is None


async def test_create_season(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()

    season = await season_service.create_season(
        db_session,
        expansion_name="Midnight",
        season_number=1,
        start_date=today,
        is_active=True,
    )

    assert season.id is not None
    assert season.expansion_name == "Midnight"
    assert season.season_number == 1
    assert season.display_name == "Midnight Season 1"
    assert season.start_date == today
    assert season.is_active is True
