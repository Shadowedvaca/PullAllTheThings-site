"""
Unit tests for Phase 1.7-B — hourly encounter probe + BIS daily sync scheduler stubs.

Tests:
1.  start() registers encounter_probe job
2.  start() registers bis_daily_sync job
3.  run_encounter_probe: count > baseline → targets reset + site_config updated + cache updated
4.  run_encounter_probe: count == baseline → no-op (no DB writes)
5.  run_encounter_probe: count < baseline → no-op (no DB writes)
6.  run_encounter_probe: baseline is None → records baseline, no target reset
7.  run_encounter_probe: exception does not propagate to caller
8.  run_bis_daily_sync: fires without error (stub logs, no exception)
9.  run_bis_daily_sync: accepts triggered_by kwarg
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

    # Replace the real APScheduler with a mock so start() doesn't spin up threads
    scheduler.scheduler = MagicMock()
    return scheduler


def _make_pool_with_fetchval(fetchval_return, execute_mock=None):
    """Return a mock asyncpg pool whose acquire() context manager provides a conn
    with fetchval() returning fetchval_return and execute() optionally captured."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.execute = execute_mock or AsyncMock()

    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


# ---------------------------------------------------------------------------
# 1 + 2. Job registration
# ---------------------------------------------------------------------------


class TestSchedulerRegistersNewJobs:
    @pytest.mark.asyncio
    async def test_registers_encounter_probe_job(self):
        scheduler = _make_scheduler()
        with patch.object(scheduler.blizzard_client, "initialize", new_callable=AsyncMock):
            await scheduler.start()

        ids = [c.kwargs.get("id") for c in scheduler.scheduler.add_job.call_args_list]
        assert "encounter_probe" in ids

    @pytest.mark.asyncio
    async def test_registers_bis_daily_sync_job(self):
        scheduler = _make_scheduler()
        with patch.object(scheduler.blizzard_client, "initialize", new_callable=AsyncMock):
            await scheduler.start()

        ids = [c.kwargs.get("id") for c in scheduler.scheduler.add_job.call_args_list]
        assert "bis_daily_sync" in ids


# ---------------------------------------------------------------------------
# 3. Patch signal detected — targets reset + site_config + cache updated
# ---------------------------------------------------------------------------


class TestEncounterProbeNewEncounters:
    @pytest.mark.asyncio
    async def test_targets_reset_when_count_higher(self):
        scheduler = _make_scheduler()
        execute_mock = AsyncMock()
        pool, conn = _make_pool_with_fetchval(50, execute_mock)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_bis_encounter_baseline", return_value=40), \
             patch("sv_common.guild_sync.scheduler.set_bis_encounter_baseline") as mock_set:
            await scheduler.run_encounter_probe()

        # Two execute() calls: UPDATE bis_scrape_targets + UPDATE site_config
        assert execute_mock.await_count == 2
        calls_sql = [c.args[0] for c in execute_mock.call_args_list]
        assert any("bis_scrape_targets" in sql for sql in calls_sql)
        assert any("site_config" in sql for sql in calls_sql)

        # Cache updated to new count
        mock_set.assert_called_once_with(50)

    @pytest.mark.asyncio
    async def test_site_config_updated_with_new_count(self):
        scheduler = _make_scheduler()
        execute_mock = AsyncMock()
        pool, conn = _make_pool_with_fetchval(55, execute_mock)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_bis_encounter_baseline", return_value=40), \
             patch("sv_common.guild_sync.scheduler.set_bis_encounter_baseline"):
            await scheduler.run_encounter_probe()

        site_config_calls = [
            c for c in execute_mock.call_args_list if "site_config" in c.args[0]
        ]
        assert len(site_config_calls) == 1
        assert site_config_calls[0].args[1] == 55


# ---------------------------------------------------------------------------
# 4 + 5. No change / count lower — no-op
# ---------------------------------------------------------------------------


class TestEncounterProbeNoChange:
    @pytest.mark.asyncio
    async def test_no_writes_when_count_equals_baseline(self):
        scheduler = _make_scheduler()
        execute_mock = AsyncMock()
        pool, conn = _make_pool_with_fetchval(40, execute_mock)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_bis_encounter_baseline", return_value=40), \
             patch("sv_common.guild_sync.scheduler.set_bis_encounter_baseline") as mock_set:
            await scheduler.run_encounter_probe()

        execute_mock.assert_not_called()
        mock_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_writes_when_count_lower_than_baseline(self):
        scheduler = _make_scheduler()
        execute_mock = AsyncMock()
        pool, conn = _make_pool_with_fetchval(30, execute_mock)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_bis_encounter_baseline", return_value=40), \
             patch("sv_common.guild_sync.scheduler.set_bis_encounter_baseline") as mock_set:
            await scheduler.run_encounter_probe()

        execute_mock.assert_not_called()
        mock_set.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Baseline is None — first run records baseline, no target reset
# ---------------------------------------------------------------------------


class TestEncounterProbeFirstRun:
    @pytest.mark.asyncio
    async def test_records_baseline_on_first_run(self):
        scheduler = _make_scheduler()
        execute_mock = AsyncMock()
        pool, conn = _make_pool_with_fetchval(42, execute_mock)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_bis_encounter_baseline", return_value=None), \
             patch("sv_common.guild_sync.scheduler.set_bis_encounter_baseline") as mock_set:
            await scheduler.run_encounter_probe()

        # Only one execute(): the site_config baseline seed; no target reset
        assert execute_mock.await_count == 1
        sql = execute_mock.call_args.args[0]
        assert "site_config" in sql
        assert "bis_scrape_targets" not in sql

        mock_set.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_no_target_reset_on_first_run(self):
        scheduler = _make_scheduler()
        execute_mock = AsyncMock()
        pool, conn = _make_pool_with_fetchval(42, execute_mock)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_bis_encounter_baseline", return_value=None), \
             patch("sv_common.guild_sync.scheduler.set_bis_encounter_baseline"):
            await scheduler.run_encounter_probe()

        calls_sql = [c.args[0] for c in execute_mock.call_args_list]
        assert not any("bis_scrape_targets" in sql for sql in calls_sql)


# ---------------------------------------------------------------------------
# 7. Exception does not propagate
# ---------------------------------------------------------------------------


class TestEncounterProbeExceptionHandling:
    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        scheduler = _make_scheduler()
        pool = MagicMock()
        pool.acquire.side_effect = RuntimeError("DB connection failed")
        scheduler.db_pool = pool

        # Should not raise — errors are caught and logged
        await scheduler.run_encounter_probe()


# ---------------------------------------------------------------------------
# 8 + 9. BIS daily sync stub
# ---------------------------------------------------------------------------


class TestBisDailySyncStub:
    @pytest.mark.asyncio
    async def test_fires_without_error(self):
        scheduler = _make_scheduler()
        await scheduler.run_bis_daily_sync()

    @pytest.mark.asyncio
    async def test_accepts_triggered_by_kwarg(self):
        scheduler = _make_scheduler()
        await scheduler.run_bis_daily_sync(triggered_by="manual")
