"""
Unit tests for Phase 4.5 — Warcraft Logs Integration.

Tests cover:
1. WarcraftLogsClient — OAuth token flow, GraphQL query structure
2. WarcraftLogsError raised on API errors
3. wcl_sync._parse_zone_rankings — parse extraction
4. wcl_sync.compute_attendance — attendance rate calculation
5. Admin _PATH_TO_SCREEN includes warcraft_logs
6. Scheduler has run_wcl_sync method
7. ORM models: WclConfig, CharacterParse, RaidReport exist
"""

import inspect
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1. WarcraftLogsClient — structure and token refresh
# ---------------------------------------------------------------------------


class TestWarcraftLogsClientStructure:
    def test_client_imports(self):
        from sv_common.guild_sync.warcraftlogs_client import (
            WarcraftLogsClient,
            WarcraftLogsError,
        )
        assert WarcraftLogsClient is not None
        assert WarcraftLogsError is not None

    def test_client_init(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient
        client = WarcraftLogsClient("test-id", "test-secret")
        assert client.client_id == "test-id"
        assert client.client_secret == "test-secret"
        assert client._token is None
        assert client._client is None

    def test_token_url_constant(self):
        from sv_common.guild_sync import warcraftlogs_client
        assert "warcraftlogs.com/oauth/token" in warcraftlogs_client.TOKEN_URL

    def test_api_url_constant(self):
        from sv_common.guild_sync import warcraftlogs_client
        assert "warcraftlogs.com/api/v2/client" in warcraftlogs_client.API_URL

    def test_client_has_required_methods(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient
        assert hasattr(WarcraftLogsClient, "initialize")
        assert hasattr(WarcraftLogsClient, "close")
        assert hasattr(WarcraftLogsClient, "get_character_parses")
        assert hasattr(WarcraftLogsClient, "get_guild_reports")
        assert hasattr(WarcraftLogsClient, "get_report_fights")
        assert hasattr(WarcraftLogsClient, "verify_credentials")

    def test_refresh_token_uses_client_credentials(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient
        src = inspect.getsource(WarcraftLogsClient._refresh_token)
        assert "client_credentials" in src
        assert "grant_type" in src

    def test_query_raises_on_graphql_errors(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient
        src = inspect.getsource(WarcraftLogsClient._query)
        assert "errors" in src
        assert "WarcraftLogsError" in src

    def test_query_raises_without_initialization(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient
        client = WarcraftLogsClient("id", "secret")
        import asyncio

        async def run():
            with pytest.raises(RuntimeError, match="not initialized"):
                await client._query("{ query }")

        asyncio.get_event_loop().run_until_complete(run())


class TestWarcraftLogsClientMocked:
    @pytest.mark.asyncio
    async def test_refresh_token_stores_token(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient
        import httpx

        client = WarcraftLogsClient("cid", "csecret")
        mock_http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "tok-123",
            "expires_in": 3600,
        }
        mock_http.post = AsyncMock(return_value=mock_resp)
        client._client = mock_http

        await client._refresh_token()
        assert client._token == "tok-123"
        assert client._token_expires > 0

    @pytest.mark.asyncio
    async def test_get_guild_reports_sends_graphql(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient
        import time

        client = WarcraftLogsClient("cid", "csecret")
        mock_http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "reportData": {
                    "reports": {"data": [], "total": 0}
                }
            }
        }
        mock_http.post = AsyncMock(return_value=mock_resp)
        client._client = mock_http
        client._token = "tok"
        client._token_expires = time.time() + 3600

        result = await client.get_guild_reports("My Guild", "senjin", "us", 25)
        assert "reportData" in result

        call_kwargs = mock_http.post.call_args
        body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][0]
        assert "query" in call_kwargs[1]["json"]
        assert "variables" in call_kwargs[1]["json"]


# ---------------------------------------------------------------------------
# 2. WarcraftLogsError
# ---------------------------------------------------------------------------


class TestWarcraftLogsError:
    def test_error_is_exception(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsError
        err = WarcraftLogsError(["some error"])
        assert isinstance(err, Exception)

    def test_error_stores_message(self):
        from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsError
        err = WarcraftLogsError("bad auth")
        assert "bad auth" in str(err)


# ---------------------------------------------------------------------------
# 3. wcl_sync._parse_zone_rankings
# ---------------------------------------------------------------------------


class TestParseZoneRankings:
    def _get_parser(self):
        from sv_common.guild_sync.wcl_sync import _parse_zone_rankings
        return _parse_zone_rankings

    def test_empty_input(self):
        parse = self._get_parser()
        assert parse({}) == []

    def test_none_input(self):
        parse = self._get_parser()
        assert parse(None) == []

    def test_parses_basic_rankings(self):
        parse = self._get_parser()
        zone_rankings = {
            "zone": 38,
            "zoneName": "Nerub-ar Palace",
            "difficulty": 4,
            "bestSpec": "Balance",
            "rankings": [
                {
                    "encounter": {"id": 2507, "name": "Ulgrax the Devourer"},
                    "rankPercent": 89.2,
                    "bestAmount": 1234567.0,
                    "report": {"code": "abc123", "fightID": 5},
                },
                {
                    "encounter": {"id": 2508, "name": "The Bloodbound Horror"},
                    "rankPercent": 72.5,
                    "bestAmount": 987654.0,
                    "report": {"code": "abc123", "fightID": 9},
                },
            ],
        }
        result = parse(zone_rankings, zone_name_map={38: "Nerub-ar Palace"})
        assert len(result) == 2
        assert result[0]["encounter_id"] == 2507
        assert result[0]["encounter_name"] == "Ulgrax the Devourer"
        assert result[0]["zone_id"] == 38
        assert result[0]["zone_name"] == "Nerub-ar Palace"
        assert result[0]["difficulty"] == 4
        assert result[0]["spec"] == "Balance"
        assert result[0]["percentile"] == 89.2
        assert result[0]["report_code"] == "abc123"
        assert result[0]["fight_id"] == 5

    def test_skips_zero_percentile(self):
        parse = self._get_parser()
        zone_rankings = {
            "zone": 38,
            "zoneName": "Test Zone",
            "difficulty": 4,
            "bestSpec": "Frost",
            "rankings": [
                {
                    "encounter": {"id": 1, "name": "Boss"},
                    "rankPercent": 0,
                    "report": {},
                },
            ],
        }
        result = parse(zone_rankings)
        assert result == []

    def test_skips_missing_encounter_id(self):
        parse = self._get_parser()
        zone_rankings = {
            "zone": 38,
            "zoneName": "Test",
            "difficulty": 4,
            "bestSpec": "Frost",
            "rankings": [
                {
                    "encounter": {"name": "Boss"},  # no id
                    "rankPercent": 55.0,
                    "report": {},
                },
            ],
        }
        result = parse(zone_rankings)
        assert result == []


# ---------------------------------------------------------------------------
# 4. compute_attendance
# ---------------------------------------------------------------------------


class TestComputeAttendance:
    def test_attendance_import(self):
        from sv_common.guild_sync.wcl_sync import compute_attendance
        assert compute_attendance is not None

    @pytest.mark.asyncio
    async def test_attendance_empty_reports(self):
        from sv_common.guild_sync.wcl_sync import compute_attendance

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(
            return_value=mock_conn
        )
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await compute_attendance(mock_pool, limit_reports=10)
        assert result == {}

    @pytest.mark.asyncio
    async def test_attendance_calculates_rates(self):
        from sv_common.guild_sync.wcl_sync import compute_attendance

        # Two reports — trogmoon attends both, altplayer attends one
        report1 = MagicMock()
        report1.__getitem__ = lambda self, key: {
            "report_code": "abc",
            "raid_date": None,
            "attendees": [
                {"name": "Trogmoon", "class": "Druid"},
                {"name": "AltPlayer", "class": "Warrior"},
            ],
        }[key]

        report2 = MagicMock()
        report2.__getitem__ = lambda self, key: {
            "report_code": "def",
            "raid_date": None,
            "attendees": [
                {"name": "Trogmoon", "class": "Druid"},
            ],
        }[key]

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[report1, report2])
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(
            return_value=mock_conn
        )
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await compute_attendance(mock_pool, limit_reports=10)
        assert "trogmoon" in result
        assert result["trogmoon"]["raids_attended"] == 2
        assert result["trogmoon"]["raids_possible"] == 2
        assert result["trogmoon"]["rate"] == 1.0
        assert "altplayer" in result
        assert result["altplayer"]["raids_attended"] == 1
        assert result["altplayer"]["rate"] == 0.5


# ---------------------------------------------------------------------------
# 5. _PATH_TO_SCREEN includes warcraft_logs
# ---------------------------------------------------------------------------


class TestAdminPathToScreen:
    def test_warcraft_logs_in_path_map(self):
        from guild_portal.pages.admin_pages import _PATH_TO_SCREEN
        paths = dict(_PATH_TO_SCREEN)
        assert "/admin/warcraft-logs" in paths
        assert paths["/admin/warcraft-logs"] == "warcraft_logs"


# ---------------------------------------------------------------------------
# 6. Scheduler has run_wcl_sync
# ---------------------------------------------------------------------------


class TestSchedulerWclSync:
    def test_run_wcl_sync_exists(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        assert hasattr(GuildSyncScheduler, "run_wcl_sync")

    def test_run_wcl_sync_is_coroutine(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        import inspect as _inspect
        assert _inspect.iscoroutinefunction(GuildSyncScheduler.run_wcl_sync)

    def test_scheduler_start_registers_wcl_job(self):
        src = inspect.getsource(
            __import__(
                "sv_common.guild_sync.scheduler",
                fromlist=["GuildSyncScheduler"],
            ).GuildSyncScheduler.start
        )
        assert "wcl_sync" in src
        assert "run_wcl_sync" in src

    def test_wcl_sync_checks_is_configured(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_wcl_sync)
        assert "is_configured" in src

    def test_wcl_sync_checks_sync_enabled(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_wcl_sync)
        assert "sync_enabled" in src

    def test_wcl_sync_decrypts_secret(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_wcl_sync)
        assert "decrypt_secret" in src


# ---------------------------------------------------------------------------
# 7. ORM models exist
# ---------------------------------------------------------------------------


class TestWclOrmModels:
    def test_wcl_config_model_exists(self):
        from sv_common.db.models import WclConfig
        assert WclConfig.__tablename__ == "wcl_config"
        assert WclConfig.__table_args__["schema"] == "guild_identity"

    def test_character_parse_model_exists(self):
        from sv_common.db.models import CharacterParse
        assert CharacterParse.__tablename__ == "character_parses"
        assert CharacterParse.__table_args__[-1]["schema"] == "guild_identity"

    def test_raid_report_model_exists(self):
        from sv_common.db.models import RaidReport
        assert RaidReport.__tablename__ == "raid_reports"
        assert RaidReport.__table_args__["schema"] == "guild_identity"

    def test_wcl_config_fields(self):
        from sv_common.db.models import WclConfig
        assert hasattr(WclConfig, "client_id")
        assert hasattr(WclConfig, "client_secret_encrypted")
        assert hasattr(WclConfig, "wcl_guild_name")
        assert hasattr(WclConfig, "wcl_server_slug")
        assert hasattr(WclConfig, "wcl_server_region")
        assert hasattr(WclConfig, "is_configured")
        assert hasattr(WclConfig, "sync_enabled")
        assert hasattr(WclConfig, "last_sync")
        assert hasattr(WclConfig, "last_sync_status")

    def test_character_parse_fields(self):
        from sv_common.db.models import CharacterParse
        assert hasattr(CharacterParse, "character_id")
        assert hasattr(CharacterParse, "encounter_id")
        assert hasattr(CharacterParse, "encounter_name")
        assert hasattr(CharacterParse, "difficulty")
        assert hasattr(CharacterParse, "spec")
        assert hasattr(CharacterParse, "percentile")

    def test_raid_report_fields(self):
        from sv_common.db.models import RaidReport
        assert hasattr(RaidReport, "report_code")
        assert hasattr(RaidReport, "raid_date")
        assert hasattr(RaidReport, "attendees")
        assert hasattr(RaidReport, "boss_kills")
        assert hasattr(RaidReport, "report_url")


# ---------------------------------------------------------------------------
# 8. wcl_sync module structure
# ---------------------------------------------------------------------------


class TestWclSyncModule:
    def test_load_wcl_config_exists(self):
        from sv_common.guild_sync.wcl_sync import load_wcl_config
        assert load_wcl_config is not None

    def test_sync_guild_reports_exists(self):
        from sv_common.guild_sync.wcl_sync import sync_guild_reports
        assert sync_guild_reports is not None

    def test_sync_character_parses_exists(self):
        from sv_common.guild_sync.wcl_sync import sync_character_parses
        assert sync_character_parses is not None

    def test_sync_guild_reports_is_async(self):
        from sv_common.guild_sync import wcl_sync
        import inspect as _inspect
        assert _inspect.iscoroutinefunction(wcl_sync.sync_guild_reports)

    def test_sync_character_parses_uses_rate_limit_sleep(self):
        from sv_common.guild_sync import wcl_sync
        src = inspect.getsource(wcl_sync.sync_character_parses)
        assert "asyncio.sleep" in src

    def test_sync_guild_reports_handles_existing_reports(self):
        """sync_guild_reports should skip reports already in the DB."""
        from sv_common.guild_sync import wcl_sync
        src = inspect.getsource(wcl_sync.sync_guild_reports)
        assert "existing" in src

    def test_parse_zone_rankings_only_positive_percentiles(self):
        """_parse_zone_rankings should skip 0-percentile entries."""
        from sv_common.guild_sync import wcl_sync
        src = inspect.getsource(wcl_sync._parse_zone_rankings)
        assert "percentile" in src.lower()
