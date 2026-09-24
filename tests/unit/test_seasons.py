"""Unit tests for patt.services.season_service."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import RaidSeason
from guild_portal.services import season_service


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


async def test_get_current_season_returns_none_when_no_seasons(
    db_session: AsyncSession,
):
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


async def test_activating_new_season_deactivates_previous(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()
    old = await _create_season(db_session, "Midnight", 1, today - timedelta(days=90))
    new = await _create_season(db_session, "Midnight", 2, today)
    await db_session.refresh(old)

    assert old.is_active is False
    assert new.is_active is True


async def test_create_season_persists_complete_configuration(db_session: AsyncSession):
    today = datetime.now(timezone.utc).date()
    season = await season_service.create_season(
        db_session,
        expansion_name="Midnight",
        season_number=2,
        start_date=today,
        blizzard_mplus_season_id=18,
        current_raid_ids=[1317, 1320],
        current_instance_ids=[1322, 1304],
        tier_set_ids=[2055, 2056],
        quality_ilvl_map={"M": {"min": 318, "max": 334}},
        crafted_ilvl_map={"M": {"min": 318, "max": 331}},
    )

    assert season.blizzard_mplus_season_id == 18
    assert season.current_raid_ids == [1317, 1320]
    assert season.current_instance_ids == [1322, 1304]
    assert season.tier_set_ids == [2055, 2056]
    assert season.quality_ilvl_map["M"]["max"] == 334
