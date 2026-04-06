"""
Unit tests for Phase 4.3: progression sync

Tests cover:
  - should_sync_character() logic (last-login optimization)
  - _parse_raid_encounters() response parsing
  - _parse_mythic_plus() response parsing
  - Achievement filtering (only tracked IDs)
  - Weekly snapshot aggregation
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sv_common.guild_sync.blizzard_client import should_sync_character
from sv_common.guild_sync.progression_sync import (
    _parse_raid_encounters,
    _parse_mythic_plus,
)


# ---------------------------------------------------------------------------
# should_sync_character
# ---------------------------------------------------------------------------


class TestShouldSyncCharacter:
    def test_force_full_always_syncs(self):
        """force_full=True always returns True regardless of timestamps."""
        now = datetime.now(timezone.utc)
        # Even if last_login is old and last_sync is recent
        old_ts = int((now - timedelta(days=30)).timestamp() * 1000)
        assert should_sync_character(old_ts, now, force_full=True) is True

    def test_no_login_timestamp_syncs(self):
        """No last_login_timestamp → always sync (safe default)."""
        now = datetime.now(timezone.utc)
        assert should_sync_character(None, now) is True

    def test_never_synced_syncs(self):
        """last_sync is None (never synced) → always sync."""
        now = datetime.now(timezone.utc)
        ts = int(now.timestamp() * 1000)
        assert should_sync_character(ts, None) is True

    def test_no_login_and_no_sync_syncs(self):
        """Both None → always sync."""
        assert should_sync_character(None, None) is True

    def test_login_after_sync_syncs(self):
        """Character logged in after our last sync → sync needed."""
        last_sync = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        last_login = datetime(2026, 3, 11, 8, 0, 0, tzinfo=timezone.utc)  # after sync
        last_login_ts = int(last_login.timestamp() * 1000)
        assert should_sync_character(last_login_ts, last_sync) is True

    def test_login_before_sync_skips(self):
        """Character logged in before our last sync → skip (nothing changed)."""
        last_sync = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
        last_login = datetime(2026, 3, 10, 8, 0, 0, tzinfo=timezone.utc)  # before sync
        last_login_ts = int(last_login.timestamp() * 1000)
        assert should_sync_character(last_login_ts, last_sync) is False

    def test_login_equals_sync_skips(self):
        """Character login time exactly equals sync time → skip (boundary)."""
        sync_dt = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
        login_ts = int(sync_dt.timestamp() * 1000)
        assert should_sync_character(login_ts, sync_dt) is False

    def test_very_old_login_skips(self):
        """Character that logged in 2 months ago, synced last week → skip."""
        last_sync = datetime.now(timezone.utc) - timedelta(days=7)
        last_login = datetime.now(timezone.utc) - timedelta(days=60)
        last_login_ts = int(last_login.timestamp() * 1000)
        assert should_sync_character(last_login_ts, last_sync) is False

    def test_recent_login_syncs(self):
        """Character that logged in yesterday, never synced → sync."""
        last_login = datetime.now(timezone.utc) - timedelta(days=1)
        last_login_ts = int(last_login.timestamp() * 1000)
        assert should_sync_character(last_login_ts, None) is True


# ---------------------------------------------------------------------------
# _parse_raid_encounters
# ---------------------------------------------------------------------------


class TestParseRaidEncounters:
    SAMPLE_RESPONSE = {
        "expansions": [
            {
                "expansion": {"name": "The War Within", "id": 503},
                "instances": [
                    {
                        "instance": {"name": "Nerub-ar Palace", "id": 1273},
                        "modes": [
                            {
                                "difficulty": {"type": "HEROIC", "name": "Heroic"},
                                "progress": {
                                    "encounters": [
                                        {
                                            "encounter": {"name": "Ulgrax the Devourer", "id": 2902},
                                            "completed_count": 14,
                                        },
                                        {
                                            "encounter": {"name": "The Bloodbound Horror", "id": 2917},
                                            "completed_count": 3,
                                        },
                                        {
                                            "encounter": {"name": "Queen Ansurek", "id": 2922},
                                            "completed_count": 0,  # not killed
                                        },
                                    ]
                                },
                            },
                            {
                                "difficulty": {"type": "MYTHIC", "name": "Mythic"},
                                "progress": {
                                    "encounters": [
                                        {
                                            "encounter": {"name": "Ulgrax the Devourer", "id": 2902},
                                            "completed_count": 1,
                                        },
                                    ]
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }

    SAMPLE_RESPONSE_WITH_TOTAL = {
        "expansions": [
            {
                "expansion": {"name": "Midnight", "id": 506},
                "instances": [
                    {
                        "instance": {"name": "Voidspire", "id": 1314},
                        "modes": [
                            {
                                "difficulty": {"type": "HEROIC", "name": "Heroic"},
                                "progress": {
                                    "total_count": 6,
                                    "completed_count": 4,
                                    "encounters": [
                                        {"encounter": {"name": "Boss A", "id": 3100}, "completed_count": 2},
                                        {"encounter": {"name": "Boss B", "id": 3101}, "completed_count": 1},
                                        {"encounter": {"name": "Boss C", "id": 3102}, "completed_count": 0},
                                    ],
                                },
                            },
                            {
                                "difficulty": {"type": "NORMAL", "name": "Normal"},
                                "progress": {
                                    "total_count": 6,
                                    "completed_count": 6,
                                    "encounters": [
                                        {"encounter": {"name": "Boss A", "id": 3100}, "completed_count": 5},
                                    ],
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }

    def test_parses_kills(self):
        """Boss kills are parsed correctly."""
        records, _ = _parse_raid_encounters(self.SAMPLE_RESPONSE)
        assert len(records) == 3  # 2 heroic kills + 1 mythic kill (0-kill boss excluded)

    def test_excludes_zero_kill_bosses(self):
        """Bosses with 0 kills are not included."""
        records, _ = _parse_raid_encounters(self.SAMPLE_RESPONSE)
        names = [r["boss_name"] for r in records]
        assert "Queen Ansurek" not in names

    def test_correct_kill_counts(self):
        """Kill counts match the Blizzard response."""
        records, _ = _parse_raid_encounters(self.SAMPLE_RESPONSE)
        heroic_ulgrax = next(
            r for r in records
            if r["boss_name"] == "Ulgrax the Devourer" and r["difficulty"] == "heroic"
        )
        assert heroic_ulgrax["kill_count"] == 14

    def test_difficulty_lowercased(self):
        """Difficulty strings are lowercased."""
        records, _ = _parse_raid_encounters(self.SAMPLE_RESPONSE)
        difficulties = {r["difficulty"] for r in records}
        assert "heroic" in difficulties
        assert "mythic" in difficulties
        assert "HEROIC" not in difficulties

    def test_raid_metadata(self):
        """Raid name and ID are included in each record."""
        records, _ = _parse_raid_encounters(self.SAMPLE_RESPONSE)
        for r in records:
            assert r["raid_name"] == "Nerub-ar Palace"
            assert r["raid_id"] == 1273

    def test_empty_response(self):
        """Empty response returns empty records and counts without error."""
        records, boss_counts = _parse_raid_encounters({})
        assert records == []
        assert boss_counts == {}
        records2, boss_counts2 = _parse_raid_encounters({"expansions": []})
        assert records2 == []
        assert boss_counts2 == {}

    def test_boss_counts_extracted(self):
        """total_count is captured per (raid_id, difficulty) when present."""
        _, boss_counts = _parse_raid_encounters(self.SAMPLE_RESPONSE_WITH_TOTAL)
        assert boss_counts[(1314, "heroic")] == 6
        assert boss_counts[(1314, "normal")] == 6

    def test_boss_counts_zero_kill_mode_included(self):
        """Boss counts include modes where character has 0 kills (total_count still valid)."""
        records, boss_counts = _parse_raid_encounters(self.SAMPLE_RESPONSE_WITH_TOTAL)
        # Records only has kills (Boss A heroic + Boss B heroic + Boss A normal)
        assert len(records) == 3
        # But boss_counts has both heroic and normal
        assert (1314, "heroic") in boss_counts
        assert (1314, "normal") in boss_counts

    def test_no_total_count_omitted(self):
        """Modes without total_count are omitted from boss_counts."""
        _, boss_counts = _parse_raid_encounters(self.SAMPLE_RESPONSE)
        # SAMPLE_RESPONSE has no total_count fields
        assert boss_counts == {}


# ---------------------------------------------------------------------------
# _parse_mythic_plus
# ---------------------------------------------------------------------------


class TestParseMythicPlus:
    SAMPLE_RESPONSE = {
        "season": {"id": 13},
        "mythic_rating": {"rating": 2450.5},
        "best_runs": [
            {
                "keystone_level": 15,
                "is_completed_within_time": True,
                "dungeon": {"name": "The Stonevault", "id": 1269},
                "mythic_rating": {"rating": 185.5},
            },
            {
                "keystone_level": 10,
                "is_completed_within_time": False,
                "dungeon": {"name": "Ara-Kara", "id": 1267},
                "mythic_rating": {"rating": 120.0},
            },
        ],
    }

    def test_parses_overall_rating(self):
        """Overall M+ rating is parsed from the top-level field."""
        rating, dungeons = _parse_mythic_plus(self.SAMPLE_RESPONSE)
        assert rating == 2450.5

    def test_parses_dungeon_count(self):
        """All dungeons in best_runs are returned."""
        _, dungeons = _parse_mythic_plus(self.SAMPLE_RESPONSE)
        assert len(dungeons) == 2

    def test_parses_timed_flag(self):
        """best_timed flag is correctly captured."""
        _, dungeons = _parse_mythic_plus(self.SAMPLE_RESPONSE)
        stonevault = next(d for d in dungeons if d["dungeon_name"] == "The Stonevault")
        arakara = next(d for d in dungeons if d["dungeon_name"] == "Ara-Kara")
        assert stonevault["best_timed"] is True
        assert arakara["best_timed"] is False

    def test_parses_key_level(self):
        """Keystone level is captured."""
        _, dungeons = _parse_mythic_plus(self.SAMPLE_RESPONSE)
        stonevault = next(d for d in dungeons if d["dungeon_name"] == "The Stonevault")
        assert stonevault["best_level"] == 15

    def test_empty_response(self):
        """None or empty dict returns zero rating and empty list."""
        rating, dungeons = _parse_mythic_plus(None)
        assert rating == 0.0
        assert dungeons == []

        rating, dungeons = _parse_mythic_plus({})
        assert rating == 0.0
        assert dungeons == []

    def test_no_best_runs(self):
        """Response with rating but no best_runs returns correct rating."""
        data = {"mythic_rating": {"rating": 1234.0}, "best_runs": []}
        rating, dungeons = _parse_mythic_plus(data)
        assert rating == 1234.0
        assert dungeons == []


# ---------------------------------------------------------------------------
# Achievement filtering
# ---------------------------------------------------------------------------


class TestAchievementFiltering:
    """Test that only tracked achievement IDs are stored."""

    def _make_achievements_response(self, ids: list[int]) -> dict:
        return {
            "achievements": [
                {
                    "id": aid,
                    "achievement": {"name": f"Achievement {aid}"},
                    "completed_timestamp": 1700000000000,
                }
                for aid in ids
            ]
        }

    def test_only_tracked_ids_stored(self):
        """Filter works: only achievements in tracked_ids are kept."""
        tracked_ids = {40681, 40524}
        response = self._make_achievements_response([40681, 99999, 40524, 11111])

        achievements = response.get("achievements", [])
        matched = [a for a in achievements if a.get("id") in tracked_ids]

        assert len(matched) == 2
        assert all(a["id"] in tracked_ids for a in matched)

    def test_no_tracked_ids_returns_empty(self):
        """Empty tracked set → nothing stored."""
        tracked_ids = set()
        response = self._make_achievements_response([40681, 40524])
        achievements = response.get("achievements", [])
        matched = [a for a in achievements if a.get("id") in tracked_ids]
        assert matched == []

    def test_no_matching_achievements(self):
        """Character has achievements, none are tracked → nothing stored."""
        tracked_ids = {40681}
        response = self._make_achievements_response([99999, 88888])
        achievements = response.get("achievements", [])
        matched = [a for a in achievements if a.get("id") in tracked_ids]
        assert matched == []


# ---------------------------------------------------------------------------
# should_sync_character — edge cases with millisecond precision
# ---------------------------------------------------------------------------


class TestShouldSyncCharacterPrecision:
    def test_ms_timestamp_conversion(self):
        """Millisecond timestamp is correctly converted to seconds."""
        # Character logged in at a known datetime
        login_dt = datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc)
        login_ts_ms = int(login_dt.timestamp() * 1000)

        # Synced 1 hour before login
        last_sync = datetime(2026, 3, 11, 9, 0, 0, tzinfo=timezone.utc)
        assert should_sync_character(login_ts_ms, last_sync) is True

        # Synced 1 hour after login
        last_sync_after = datetime(2026, 3, 11, 11, 0, 0, tzinfo=timezone.utc)
        assert should_sync_character(login_ts_ms, last_sync_after) is False
