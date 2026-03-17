"""
Unit tests for Phase 6.4 — scheduler.run_bnet_character_refresh error reporting.

Tests:
1. report_error called with bnet_token_expired when token is None
2. resolve_issue called on success
3. report_error called with bnet_sync_error on sync exception
4. maybe_notify_discord receives is_first_occurrence=False on repeat errors
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scheduler():
    """Build a GuildSyncScheduler with all external deps mocked out."""
    from sv_common.guild_sync.scheduler import GuildSyncScheduler

    db_pool = MagicMock()
    bot = MagicMock()
    audit_channel_id = 99999

    with patch("sv_common.guild_sync.scheduler.BlizzardClient") as mock_bc_cls, \
         patch("sv_common.guild_sync.scheduler.get_site_config", return_value={}), \
         patch.dict(os.environ, {
             "BLIZZARD_CLIENT_ID": "test-client-id",
             "BLIZZARD_CLIENT_SECRET": "test-client-secret",
         }):
        mock_bc_cls.return_value = MagicMock()
        scheduler = GuildSyncScheduler(db_pool, bot, audit_channel_id)

    scheduler.scheduler = MagicMock()
    return scheduler


def _make_pool_with_rows(rows):
    """Return a mock asyncpg pool whose acquire() yields a conn with fetch returning rows."""
    conn = AsyncMock()
    conn.fetch.return_value = rows
    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = cm
    return pool, conn


# ---------------------------------------------------------------------------
# 1. report_error called with bnet_token_expired when token is None
# ---------------------------------------------------------------------------


class TestRunBnetRefreshExpiredToken:
    @pytest.mark.asyncio
    async def test_reports_bnet_token_expired_when_token_none(self):
        scheduler = _make_scheduler()

        bnet_rows = [{"player_id": 1, "battletag": "Trog#1234"}]
        pool, conn = _make_pool_with_rows(bnet_rows)
        scheduler.db_pool = pool

        mock_report_result = {"id": 1, "is_first_occurrence": True, "occurrence_count": 1}

        with patch("sv_common.guild_sync.bnet_character_sync.get_valid_access_token", new=AsyncMock(return_value=None)), \
             patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)) as mock_report, \
             patch("sv_common.errors.resolve_issue", new=AsyncMock()) as mock_resolve, \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new=AsyncMock()) as mock_notify:
            await scheduler.run_bnet_character_refresh()

        # report_error called with bnet_token_expired
        mock_report.assert_awaited()
        call_args = mock_report.await_args_list[0][0]
        assert call_args[1] == "bnet_token_expired"
        assert call_args[4] == "scheduler"  # source_module is 5th arg (index 4)

        # resolve_issue NOT called (token was None — no success)
        mock_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_battletag_as_identifier(self):
        scheduler = _make_scheduler()

        bnet_rows = [{"player_id": 7, "battletag": "Rocket#5678"}]
        pool, conn = _make_pool_with_rows(bnet_rows)
        scheduler.db_pool = pool

        mock_report_result = {"id": 2, "is_first_occurrence": True, "occurrence_count": 1}

        with patch("sv_common.guild_sync.bnet_character_sync.get_valid_access_token", new=AsyncMock(return_value=None)), \
             patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)) as mock_report, \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new=AsyncMock()):
            await scheduler.run_bnet_character_refresh()

        call_kwargs = mock_report.await_args_list[0][1]
        assert call_kwargs.get("identifier") == "Rocket#5678"

    @pytest.mark.asyncio
    async def test_maybe_notify_discord_called_with_is_first_occurrence(self):
        scheduler = _make_scheduler()

        bnet_rows = [{"player_id": 1, "battletag": "Trog#1234"}]
        pool, conn = _make_pool_with_rows(bnet_rows)
        scheduler.db_pool = pool

        mock_report_result = {"id": 1, "is_first_occurrence": True, "occurrence_count": 1}

        with patch("sv_common.guild_sync.bnet_character_sync.get_valid_access_token", new=AsyncMock(return_value=None)), \
             patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)), \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new=AsyncMock()) as mock_notify:
            await scheduler.run_bnet_character_refresh()

        mock_notify.assert_awaited()
        # 7th positional arg is is_first_occurrence
        notify_is_first = mock_notify.await_args_list[0][0][6]
        assert notify_is_first is True


# ---------------------------------------------------------------------------
# 2. resolve_issue called on success
# ---------------------------------------------------------------------------


class TestRunBnetRefreshResolveOnSuccess:
    @pytest.mark.asyncio
    async def test_resolve_issue_called_on_success(self):
        scheduler = _make_scheduler()

        bnet_rows = [{"player_id": 3, "battletag": "Tank#9999"}]
        pool, conn = _make_pool_with_rows(bnet_rows)
        scheduler.db_pool = pool

        sync_stats = {"linked": 2, "new_characters": 1, "skipped": 0}

        with patch("sv_common.guild_sync.bnet_character_sync.get_valid_access_token", new=AsyncMock(return_value="valid-token")), \
             patch("sv_common.guild_sync.bnet_character_sync.sync_bnet_characters", new=AsyncMock(return_value=sync_stats)), \
             patch("sv_common.errors.report_error", new=AsyncMock()) as mock_report, \
             patch("sv_common.errors.resolve_issue", new=AsyncMock()) as mock_resolve:
            await scheduler.run_bnet_character_refresh()

        # report_error NOT called on success
        mock_report.assert_not_awaited()

        # resolve_issue called twice: bnet_token_expired + bnet_sync_error
        assert mock_resolve.await_count == 2
        resolved_types = [c[0][1] for c in mock_resolve.await_args_list]
        assert "bnet_token_expired" in resolved_types
        assert "bnet_sync_error" in resolved_types

        # Both resolved with battletag as identifier
        resolved_ids = [c[1].get("identifier") for c in mock_resolve.await_args_list]
        assert all(i == "Tank#9999" for i in resolved_ids)


# ---------------------------------------------------------------------------
# 3. report_error called with bnet_sync_error on sync exception
# ---------------------------------------------------------------------------


class TestRunBnetRefreshSyncException:
    @pytest.mark.asyncio
    async def test_reports_bnet_sync_error_on_exception(self):
        scheduler = _make_scheduler()

        bnet_rows = [{"player_id": 5, "battletag": "Healer#1111"}]
        pool, conn = _make_pool_with_rows(bnet_rows)
        scheduler.db_pool = pool

        mock_report_result = {"id": 3, "is_first_occurrence": True, "occurrence_count": 1}

        with patch("sv_common.guild_sync.bnet_character_sync.get_valid_access_token", new=AsyncMock(return_value="valid-token")), \
             patch("sv_common.guild_sync.bnet_character_sync.sync_bnet_characters", new=AsyncMock(side_effect=RuntimeError("API error"))), \
             patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)) as mock_report, \
             patch("sv_common.errors.resolve_issue", new=AsyncMock()) as mock_resolve, \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new=AsyncMock()) as mock_notify:
            await scheduler.run_bnet_character_refresh()

        mock_report.assert_awaited()
        call_args = mock_report.await_args_list[0][0]
        assert call_args[1] == "bnet_sync_error"
        assert "API error" in call_args[3]  # summary is 4th arg (index 3)

        # resolve_issue NOT called on failure
        mock_resolve.assert_not_awaited()

        # maybe_notify_discord called
        mock_notify.assert_awaited()


# ---------------------------------------------------------------------------
# 4. maybe_notify_discord receives is_first_occurrence=False on repeat errors
# ---------------------------------------------------------------------------


class TestRunBnetRefreshSuppressRepeatNotification:
    @pytest.mark.asyncio
    async def test_notify_receives_false_for_repeat_occurrence(self):
        """When report_error returns is_first_occurrence=False, maybe_notify_discord
        receives that value so the routing layer can suppress repeat pings."""
        scheduler = _make_scheduler()

        bnet_rows = [{"player_id": 2, "battletag": "Mage#2222"}]
        pool, conn = _make_pool_with_rows(bnet_rows)
        scheduler.db_pool = pool

        # is_first_occurrence=False (repeat error)
        mock_report_result = {"id": 1, "is_first_occurrence": False, "occurrence_count": 5}

        with patch("sv_common.guild_sync.bnet_character_sync.get_valid_access_token", new=AsyncMock(return_value=None)), \
             patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)), \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new=AsyncMock()) as mock_notify:
            await scheduler.run_bnet_character_refresh()

        mock_notify.assert_awaited()
        # 7th positional arg is is_first_occurrence
        notify_is_first = mock_notify.await_args_list[0][0][6]
        assert notify_is_first is False
