"""
Unit tests for crafting sync logic.

Tests cadence computation, expansion name derivation, and season display name.
No database required.
"""

from datetime import datetime, timedelta, timezone

import pytest

from sv_common.guild_sync.crafting_sync import (
    CraftingSyncConfig,
    SeasonData,
    compute_sync_cadence,
    derive_expansion_name,
    get_season_display_name,
    EXPANSION_SORT_ORDER,
)


def _make_config(**kwargs) -> CraftingSyncConfig:
    defaults = {
        "id": 1,
        "current_cadence": "weekly",
        "cadence_override_until": None,
        "last_sync_at": None,
    }
    defaults.update(kwargs)
    return CraftingSyncConfig(**defaults)


def _make_season(**kwargs) -> SeasonData:
    defaults = {
        "id": 1,
        "expansion_name": "Khaz Algar",
        "season_number": 2,
        "start_date": datetime.now(timezone.utc) - timedelta(days=5),
        "is_new_expansion": False,
    }
    defaults.update(kwargs)
    return SeasonData(**defaults)


# ── derive_expansion_name ────────────────────────────────────────────────────

class TestDeriveExpansionName:
    def test_khaz_algar_blacksmithing(self):
        exp, sort = derive_expansion_name("Khaz Algar Blacksmithing", "Blacksmithing")
        assert exp == "Khaz Algar"
        assert sort == 90

    def test_dragon_isles_alchemy(self):
        exp, sort = derive_expansion_name("Dragon Isles Alchemy", "Alchemy")
        assert exp == "Dragon Isles"
        assert sort == 80

    def test_classic_cooking(self):
        exp, sort = derive_expansion_name("Classic Cooking", "Cooking")
        assert exp == "Classic"
        assert sort == 0

    def test_unknown_expansion_returns_minus_one(self):
        exp, sort = derive_expansion_name("Shadowforce Engineering", "Engineering")
        assert sort == -1

    def test_shadowlands(self):
        exp, sort = derive_expansion_name("Shadowlands Jewelcrafting", "Jewelcrafting")
        assert exp == "Shadowlands"
        assert sort == 70

    def test_northrend(self):
        exp, sort = derive_expansion_name("Northrend Inscription", "Inscription")
        assert exp == "Northrend"
        assert sort == 20

    def test_expansion_sort_order_is_increasing(self):
        """Newer expansions should have higher sort_order than older ones."""
        assert EXPANSION_SORT_ORDER["Khaz Algar"] > EXPANSION_SORT_ORDER["Dragon Isles"]
        assert EXPANSION_SORT_ORDER["Dragon Isles"] > EXPANSION_SORT_ORDER["Shadowlands"]
        assert EXPANSION_SORT_ORDER["Classic"] == 0


# ── compute_sync_cadence ─────────────────────────────────────────────────────

class TestComputeSyncCadence:
    def test_no_season_returns_weekly(self):
        config = _make_config()
        cadence, days = compute_sync_cadence(config, season=None)
        assert cadence == "weekly"
        assert days == 0

    def test_new_expansion_season_within_28_days_is_daily(self):
        start = datetime.now(timezone.utc) - timedelta(days=10)
        config = _make_config()
        season = _make_season(start_date=start, is_new_expansion=True)
        cadence, days = compute_sync_cadence(config, season)
        assert cadence == "daily"
        assert days == 18  # 28 - 10

    def test_regular_season_within_14_days_is_daily(self):
        start = datetime.now(timezone.utc) - timedelta(days=5)
        config = _make_config()
        season = _make_season(start_date=start, is_new_expansion=False)
        cadence, days = compute_sync_cadence(config, season)
        assert cadence == "daily"
        assert days == 9  # 14 - 5

    def test_new_expansion_after_28_days_is_weekly(self):
        start = datetime.now(timezone.utc) - timedelta(days=30)
        config = _make_config()
        season = _make_season(start_date=start, is_new_expansion=True)
        cadence, days = compute_sync_cadence(config, season)
        assert cadence == "weekly"
        assert days == 0

    def test_regular_season_after_14_days_is_weekly(self):
        start = datetime.now(timezone.utc) - timedelta(days=20)
        config = _make_config()
        season = _make_season(start_date=start, is_new_expansion=False)
        cadence, days = compute_sync_cadence(config, season)
        assert cadence == "weekly"
        assert days == 0

    def test_admin_override_takes_priority_over_season(self):
        start = datetime.now(timezone.utc) - timedelta(days=50)
        override_until = datetime.now(timezone.utc) + timedelta(days=3)
        config = _make_config(cadence_override_until=override_until)
        season = _make_season(start_date=start, is_new_expansion=False)
        cadence, days = compute_sync_cadence(config, season)
        assert cadence == "daily"
        assert days >= 2

    def test_admin_override_takes_priority_when_no_season(self):
        override_until = datetime.now(timezone.utc) + timedelta(days=5)
        config = _make_config(cadence_override_until=override_until)
        cadence, days = compute_sync_cadence(config, season=None)
        assert cadence == "daily"
        assert days >= 4

    def test_expired_override_falls_through_to_season(self):
        start = datetime.now(timezone.utc) - timedelta(days=50)
        override_until = datetime.now(timezone.utc) - timedelta(days=1)
        config = _make_config(cadence_override_until=override_until)
        season = _make_season(start_date=start)
        cadence, _ = compute_sync_cadence(config, season)
        assert cadence == "weekly"

    def test_daily_days_remaining_decreases_over_time(self):
        start_recent = datetime.now(timezone.utc) - timedelta(days=2)
        start_older = datetime.now(timezone.utc) - timedelta(days=10)
        config = _make_config()
        _, days_recent = compute_sync_cadence(config, _make_season(start_date=start_recent))
        _, days_older = compute_sync_cadence(config, _make_season(start_date=start_older))
        assert days_recent > days_older

    def test_exactly_at_boundary_new_expansion(self):
        """Day 28 exactly should still be daily."""
        start = datetime.now(timezone.utc) - timedelta(days=28)
        config = _make_config()
        season = _make_season(start_date=start, is_new_expansion=True)
        cadence, days = compute_sync_cadence(config, season)
        assert cadence == "daily"
        assert days == 0

    def test_day_zero_season_start(self):
        """Season just started today is daily."""
        start = datetime.now(timezone.utc)
        config = _make_config()
        season = _make_season(start_date=start)
        cadence, _ = compute_sync_cadence(config, season)
        assert cadence == "daily"


# ── get_season_display_name ──────────────────────────────────────────────────

class TestGetSeasonDisplayName:
    def test_builds_name_from_expansion_and_number(self):
        season = _make_season(expansion_name="Midnight", season_number=1)
        assert get_season_display_name(season) == "Midnight Season 1"

    def test_no_season_returns_fallback(self):
        assert get_season_display_name(None) == "No season configured"

    def test_multi_word_expansion_name(self):
        season = _make_season(expansion_name="The War Within", season_number=2)
        assert get_season_display_name(season) == "The War Within Season 2"

    def test_season_number_increments(self):
        season = _make_season(expansion_name="Khaz Algar", season_number=3)
        assert get_season_display_name(season) == "Khaz Algar Season 3"
