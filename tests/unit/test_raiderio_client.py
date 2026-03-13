"""
Unit tests for Phase 4.4: Raider.IO client and sync

Tests cover:
  - RaiderIOClient._parse_profile() with sample API response
  - RaiderIOClient.get_character_profile() with mock httpx (success, 400, timeout)
  - sync_raiderio_profiles() with mock client and DB pool
  - Score color parsing
  - Raid progression extraction
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sv_common.guild_sync.raiderio_client import RaiderIOClient, RaiderIOProfile


# ---------------------------------------------------------------------------
# Sample Raider.IO API response fixture
# ---------------------------------------------------------------------------

SAMPLE_RIO_RESPONSE = {
    "name": "Trogmoon",
    "realm": "Sen'jin",
    "region": "us",
    "profile_url": "https://raider.io/characters/us/senjin/trogmoon",
    "achievement_points": 45000,
    "mythic_plus_scores_by_season": [
        {
            "season": "season-tww-2",
            "scores": {
                "all": 2450.5,
                "dps": 2450.5,
                "healer": 0.0,
                "tank": 0.0,
            },
            "segments": {
                "all": {
                    "score": 2450.5,
                    "color": {"r": 163, "g": 53, "b": 238},
                }
            },
        }
    ],
    "mythic_plus_best_runs": [
        {
            "dungeon": "The Stonevault",
            "short_name": "SV",
            "mythic_level": 15,
            "num_keystone_upgrades": 2,
            "score": 185.5,
            "affixes": [
                {"name": "Fortified"},
                {"name": "Bursting"},
            ],
        },
        {
            "dungeon": "Ara-Kara, City of Echoes",
            "short_name": "AK",
            "mythic_level": 12,
            "num_keystone_upgrades": 0,
            "score": 120.0,
            "affixes": [{"name": "Tyrannical"}],
        },
    ],
    "mythic_plus_recent_runs": [
        {
            "dungeon": "Grim Batol",
            "short_name": "GB",
            "mythic_level": 10,
            "num_keystone_upgrades": 1,
            "score": 95.0,
            "completed_at": "2026-03-10T22:15:00.000Z",
        }
    ],
    "raid_progression": {
        "nerubar-palace": {
            "summary": "8/8 H",
            "total_bosses": 8,
            "normal_bosses_killed": 8,
            "heroic_bosses_killed": 8,
            "mythic_bosses_killed": 0,
        },
    },
    "gear": {
        "item_level_equipped": 626,
    },
}


# ---------------------------------------------------------------------------
# _parse_profile tests
# ---------------------------------------------------------------------------


class TestParseProfile:
    def setup_method(self):
        self.client = RaiderIOClient(region="us")

    def test_parses_basic_fields(self):
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        assert profile.name == "Trogmoon"
        assert profile.realm == "Sen'jin"
        assert profile.region == "us"
        assert profile.profile_url == "https://raider.io/characters/us/senjin/trogmoon"
        assert profile.achievement_points == 45000

    def test_parses_mplus_scores(self):
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        assert profile.overall_score == 2450.5
        assert profile.dps_score == 2450.5
        assert profile.healer_score == 0.0
        assert profile.tank_score == 0.0

    def test_parses_score_color(self):
        """Score color is derived from the segment RGB values."""
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        # r=163 → a3, g=53 → 35, b=238 → ee
        assert profile.score_color == "#a335ee"

    def test_parses_best_runs(self):
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        assert len(profile.best_runs) == 2
        stonevault = profile.best_runs[0]
        assert stonevault["dungeon"] == "The Stonevault"
        assert stonevault["level"] == 15
        assert stonevault["timed"] is True  # num_keystone_upgrades > 0
        assert stonevault["score"] == 185.5
        assert "Fortified" in stonevault["affixes"]

    def test_parses_timed_flag(self):
        """timed is True when num_keystone_upgrades > 0."""
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        arakara = profile.best_runs[1]
        assert arakara["timed"] is False  # num_keystone_upgrades == 0

    def test_parses_recent_runs(self):
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        assert len(profile.recent_runs) == 1
        run = profile.recent_runs[0]
        assert run["dungeon"] == "Grim Batol"
        assert run["level"] == 10
        assert run["completed_at"] == "2026-03-10T22:15:00.000Z"

    def test_parses_raid_progression(self):
        """raid_progression is taken from the last tier's summary."""
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        assert profile.raid_progression == "8/8 H"

    def test_parses_gear_ilvl(self):
        profile = self.client._parse_profile(SAMPLE_RIO_RESPONSE)
        assert profile.gear_ilvl == 626

    def test_no_mplus_scores(self):
        """Missing M+ data returns zero scores and empty runs."""
        data = {"name": "Ghost", "realm": "Realm", "region": "us"}
        profile = self.client._parse_profile(data)
        assert profile.overall_score == 0.0
        assert profile.score_color is None
        assert profile.best_runs == []
        assert profile.recent_runs == []

    def test_no_raid_progression(self):
        """Missing raid progression returns None."""
        data = {"name": "Ghost", "realm": "Realm", "region": "us"}
        profile = self.client._parse_profile(data)
        assert profile.raid_progression is None

    def test_multiple_raid_tiers(self):
        """With multiple raid tiers, the last one is used for summary."""
        data = {
            "name": "Char",
            "realm": "Realm",
            "region": "us",
            "raid_progression": {
                "old-raid": {"summary": "4/4 M"},
                "new-raid": {"summary": "3/8 M"},
            },
        }
        profile = self.client._parse_profile(data)
        assert profile.raid_progression == "3/8 M"


# ---------------------------------------------------------------------------
# get_character_profile tests (mocked httpx)
# ---------------------------------------------------------------------------


class TestGetCharacterProfile:
    @pytest.mark.asyncio
    async def test_success_returns_profile(self):
        """200 response returns a parsed RaiderIOProfile."""
        client = RaiderIOClient(region="us")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_RIO_RESPONSE
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        profile = await client.get_character_profile("senjin", "Trogmoon")

        assert profile is not None
        assert isinstance(profile, RaiderIOProfile)
        assert profile.name == "Trogmoon"
        assert profile.overall_score == 2450.5

    @pytest.mark.asyncio
    async def test_400_returns_none(self):
        """400 response (character not found) returns None."""
        client = RaiderIOClient(region="us")
        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        profile = await client.get_character_profile("senjin", "Nonexistent")
        assert profile is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """Network/HTTP error returns None without raising."""
        import httpx
        client = RaiderIOClient(region="us")
        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        client._client = mock_http_client

        profile = await client.get_character_profile("senjin", "Trogmoon")
        assert profile is None

    @pytest.mark.asyncio
    async def test_request_includes_correct_params(self):
        """Request is made with correct region, realm, name, and fields."""
        client = RaiderIOClient(region="us")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_RIO_RESPONSE
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        client._client = mock_http_client

        await client.get_character_profile("senjin", "Trogmoon")

        call_kwargs = mock_http_client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs[0][1]
        assert params["region"] == "us"
        assert params["realm"] == "senjin"
        assert params["name"] == "trogmoon"  # lowercased

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self):
        """Calling without initialize() raises RuntimeError."""
        client = RaiderIOClient()
        with pytest.raises(RuntimeError, match="not initialized"):
            await client.get_character_profile("senjin", "Trogmoon")


# ---------------------------------------------------------------------------
# sync_raiderio_profiles tests
# ---------------------------------------------------------------------------


class TestSyncRaiderIOProfiles:
    @pytest.mark.asyncio
    async def test_upserts_profiles(self):
        """sync_raiderio_profiles calls execute for each profile returned."""
        from sv_common.guild_sync.progression_sync import sync_raiderio_profiles

        profile = RaiderIOProfile(
            name="Trogmoon",
            realm="senjin",
            region="us",
            overall_score=2450.5,
            dps_score=2450.5,
            healer_score=0.0,
            tank_score=0.0,
            score_color="#a335ee",
            raid_progression="8/8 H",
            best_runs=[],
            recent_runs=[],
            profile_url="https://raider.io/characters/us/senjin/trogmoon",
        )

        mock_client = AsyncMock()
        mock_client.get_guild_profiles = AsyncMock(return_value={1: profile})

        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        characters = [{"id": 1, "character_name": "Trogmoon", "realm_slug": "senjin"}]
        stats = await sync_raiderio_profiles(
            mock_pool, mock_client, characters, "senjin"
        )

        assert stats["synced"] == 1
        assert stats["total"] == 1
        assert mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_empty_characters_returns_zero(self):
        """Empty character list returns zero stats without errors."""
        from sv_common.guild_sync.progression_sync import sync_raiderio_profiles

        mock_client = AsyncMock()
        mock_client.get_guild_profiles = AsyncMock(return_value={})

        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        stats = await sync_raiderio_profiles(mock_pool, mock_client, [], "senjin")
        assert stats["synced"] == 0
        assert stats["total"] == 0

    @pytest.mark.asyncio
    async def test_partial_results(self):
        """Characters not found on Raider.IO are counted in total but not synced."""
        from sv_common.guild_sync.progression_sync import sync_raiderio_profiles

        # Client returns profiles for only 2 of 3 characters
        profile = RaiderIOProfile(
            name="Trogmoon", realm="senjin", region="us",
            overall_score=2450.5, dps_score=2450.5, healer_score=0.0, tank_score=0.0,
            score_color=None, raid_progression=None,
        )
        mock_client = AsyncMock()
        mock_client.get_guild_profiles = AsyncMock(return_value={1: profile, 3: profile})

        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            )
        )

        characters = [
            {"id": 1, "character_name": "A", "realm_slug": "senjin"},
            {"id": 2, "character_name": "B", "realm_slug": "senjin"},
            {"id": 3, "character_name": "C", "realm_slug": "senjin"},
        ]
        stats = await sync_raiderio_profiles(mock_pool, mock_client, characters, "senjin")
        assert stats["synced"] == 2
        assert stats["total"] == 3

    def test_character_name_mapping(self):
        """Characters with 'character_name' key are mapped to 'name' for RIO client."""
        # This verifies the mapping logic inline, not via async
        characters = [
            {"id": 1, "character_name": "Trogmoon", "realm_slug": "senjin"},
        ]
        rio_chars = [
            {"id": c["id"], "name": c["character_name"], "realm_slug": c.get("realm_slug", "senjin")}
            for c in characters
        ]
        assert rio_chars[0]["name"] == "Trogmoon"
        assert rio_chars[0]["realm_slug"] == "senjin"
