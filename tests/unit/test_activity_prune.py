"""Unit tests for Phase 1.8-D — User activity retention prune job.

Tests:
1. run_activity_prune: executes DELETE with correct SQL
2. run_activity_prune: logs completion message with result
3. run_activity_prune: DB exception does not propagate
4. run_activity_prune: DB exception calls report_error with warning severity
5. run_activity_prune: DB exception calls maybe_notify_discord
6. start() registers activity_prune job in production
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scheduler():
    """Build a GuildSyncScheduler with all external deps mocked out."""
    from sv_common.guild_sync.scheduler import GuildSyncScheduler

    db_pool = MagicMock()
    bot = MagicMock()
    audit_channel_id = 99999

    env_overrides = {
        "BLIZZARD_CLIENT_ID": "test-client-id",
        "BLIZZARD_CLIENT_SECRET": "test-client-secret",
    }

    with patch("sv_common.guild_sync.scheduler.BlizzardClient") as mock_bc_cls, \
         patch("sv_common.guild_sync.scheduler.get_site_config", return_value={}), \
         patch.dict(os.environ, env_overrides):
        mock_bc_cls.return_value = MagicMock()
        scheduler = GuildSyncScheduler(db_pool, bot, audit_channel_id)

    scheduler.scheduler = MagicMock()
    return scheduler


# ---------------------------------------------------------------------------
# 1–5. run_activity_prune behaviour
# ---------------------------------------------------------------------------


class TestRunActivityPrune:
    @pytest.mark.asyncio
    async def test_executes_delete_sql(self):
        scheduler = _make_scheduler()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 5")
        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool_ctx.__aexit__ = AsyncMock(return_value=False)
        scheduler.db_pool.acquire = MagicMock(return_value=mock_pool_ctx)

        await scheduler.run_activity_prune()

        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "DELETE FROM common.user_activity" in sql
        assert "CURRENT_DATE - 90" in sql

    @pytest.mark.asyncio
    async def test_logs_completion(self, caplog):
        import logging
        scheduler = _make_scheduler()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 12")
        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool_ctx.__aexit__ = AsyncMock(return_value=False)
        scheduler.db_pool.acquire = MagicMock(return_value=mock_pool_ctx)

        with caplog.at_level(logging.INFO, logger="sv_common.guild_sync.scheduler"):
            await scheduler.run_activity_prune()

        assert any("Activity prune complete" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_handles_db_exception_without_propagating(self):
        scheduler = _make_scheduler()

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__aenter__ = AsyncMock(side_effect=Exception("DB is down"))
        mock_pool_ctx.__aexit__ = AsyncMock(return_value=False)
        scheduler.db_pool.acquire = MagicMock(return_value=mock_pool_ctx)

        with patch("sv_common.errors.report_error", new_callable=AsyncMock,
                   return_value={"is_first_occurrence": True}), \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new_callable=AsyncMock):
            # Should not raise
            await scheduler.run_activity_prune()

    @pytest.mark.asyncio
    async def test_exception_calls_report_error_as_warning(self):
        scheduler = _make_scheduler()

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        mock_pool_ctx.__aexit__ = AsyncMock(return_value=False)
        scheduler.db_pool.acquire = MagicMock(return_value=mock_pool_ctx)

        with patch("sv_common.errors.report_error", new_callable=AsyncMock,
                   return_value={"is_first_occurrence": True}) as mock_report, \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new_callable=AsyncMock):
            await scheduler.run_activity_prune()

        mock_report.assert_called_once()
        args = mock_report.call_args[0]
        assert args[1] == "activity_prune_failed"
        assert args[2] == "warning"

    @pytest.mark.asyncio
    async def test_exception_calls_maybe_notify_discord(self):
        scheduler = _make_scheduler()

        mock_pool_ctx = MagicMock()
        mock_pool_ctx.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        mock_pool_ctx.__aexit__ = AsyncMock(return_value=False)
        scheduler.db_pool.acquire = MagicMock(return_value=mock_pool_ctx)

        with patch("sv_common.errors.report_error", new_callable=AsyncMock,
                   return_value={"is_first_occurrence": True}), \
             patch("guild_portal.services.error_routing.maybe_notify_discord", new_callable=AsyncMock) as mock_notify:
            await scheduler.run_activity_prune()

        mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# 6. start() registers activity_prune job
# ---------------------------------------------------------------------------


class TestSchedulerRegistersActivityPruneJob:
    @pytest.mark.asyncio
    async def test_start_registers_activity_prune_job(self):
        scheduler = _make_scheduler()

        with patch.object(scheduler.blizzard_client, "initialize", new_callable=AsyncMock), \
             patch.dict(os.environ, {"APP_ENV": "production"}):
            await scheduler.start()

        ids_from_kwargs = [
            c.kwargs.get("id") for c in scheduler.scheduler.add_job.call_args_list
            if c.kwargs.get("id")
        ]
        assert "activity_prune" in ids_from_kwargs
