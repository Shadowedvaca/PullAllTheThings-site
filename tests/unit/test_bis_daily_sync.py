"""
Unit tests for Phase 1.7-B + 1.7-C — hourly encounter probe, BIS daily sync scheduler,
hash dedup in sync_target(), and adaptive backoff.

Tests:
1.  start() registers encounter_probe job
2.  start() registers bis_daily_sync job
3.  run_encounter_probe: count > baseline → targets reset + site_config updated + cache updated
4.  run_encounter_probe: count == baseline → no-op (no DB writes)
5.  run_encounter_probe: count < baseline → no-op (no DB writes)
6.  run_encounter_probe: baseline is None → records baseline, no target reset
7.  run_encounter_probe: exception does not propagate to caller
8.  run_bis_daily_sync: fires without error
9.  run_bis_daily_sync: accepts triggered_by kwarg
10. _update_target_backoff: u.gg origin always 1-day interval
11. _update_target_backoff: changed content resets interval to 1
12. _update_target_backoff: unchanged content doubles interval
13. _update_target_backoff: interval capped at 14 days
14. sync_target: matching hash skips bis_scrape_raw insert, sets status unchanged
15. sync_target: different hash inserts new row with content_hash
16. run_bis_daily_sync: skipped (not due) targets counted correctly
17. run_bis_daily_sync: inserts bis_daily_runs row with correct counts
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
# 8 + 9. BIS daily sync — fires without error (no due targets)
# ---------------------------------------------------------------------------


def _make_pool_with_fetch_and_execute(fetch_return=None, execute_mock=None):
    """Return a mock pool whose conn.fetch returns fetch_return and conn.execute is capturable."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = execute_mock or AsyncMock()

    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


class TestBisDailySyncLoop:
    @pytest.mark.asyncio
    async def test_fires_without_error(self):
        scheduler = _make_scheduler()
        pool, _ = _make_pool_with_fetch_and_execute(fetch_return=[])
        scheduler.db_pool = pool
        await scheduler.run_bis_daily_sync()

    @pytest.mark.asyncio
    async def test_accepts_triggered_by_kwarg(self):
        scheduler = _make_scheduler()
        pool, _ = _make_pool_with_fetch_and_execute(fetch_return=[])
        scheduler.db_pool = pool
        await scheduler.run_bis_daily_sync(triggered_by="manual")

    @pytest.mark.asyncio
    async def test_skipped_targets_counted(self):
        """Targets whose next_check_at is in the future are counted as skipped, not fetched."""
        from datetime import datetime, timezone, timedelta

        future = datetime.now(timezone.utc) + timedelta(days=3)

        scheduler = _make_scheduler()
        execute_calls = []

        async def capture_execute(*args, **kwargs):
            execute_calls.append(args)

        # conn.fetch returns 2 targets both not due
        skipped_target = {
            "id": 1, "source_id": 10, "spec_id": 5, "hero_talent_id": None,
            "content_type": "raid", "url": "https://example.com", "preferred_technique": "html_parse",
            "check_interval_days": 7, "items_found": 5, "next_check_at": future, "origin": "icy_veins",
        }

        class FakeRecord(dict):
            def get(self, k, d=None):
                return super().get(k, d)

        records = [FakeRecord(skipped_target), FakeRecord({**skipped_target, "id": 2})]

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=records)
        conn.execute = AsyncMock(side_effect=capture_execute)

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler._bis_sync_target") as mock_sync:
            await scheduler.run_bis_daily_sync()

        # No targets were fetched (all skipped)
        mock_sync.assert_not_called()

        # bis_daily_runs INSERT should include targets_skipped=2
        insert_calls = [c for c in execute_calls if "bis_daily_runs" in c[0]]
        assert len(insert_calls) == 1
        # Parameters: triggered_by, checked, changed, unchanged, failed, skipped, duration, notes
        args = insert_calls[0]
        assert args[6] == 2  # targets_skipped

    @pytest.mark.asyncio
    async def test_inserts_bis_daily_runs_row(self):
        """run_bis_daily_sync inserts a row into landing.bis_daily_runs."""
        scheduler = _make_scheduler()
        execute_calls = []

        async def capture_execute(*args, **kwargs):
            execute_calls.append(args)

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock(side_effect=capture_execute)

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)
        scheduler.db_pool = pool

        await scheduler.run_bis_daily_sync(triggered_by="manual")

        insert_calls = [c for c in execute_calls if "bis_daily_runs" in c[0]]
        assert len(insert_calls) == 1
        # triggered_by is first param
        assert insert_calls[0][1] == "manual"


# ---------------------------------------------------------------------------
# 10–13. _update_target_backoff: backoff logic
# ---------------------------------------------------------------------------


class TestUpdateTargetBackoff:
    async def _call_backoff(self, changed: bool, origin: str, current_interval: int) -> int:
        """Call _update_target_backoff with a mock conn and return the new interval."""
        from sv_common.guild_sync.bis_sync import _update_target_backoff

        captured = {}

        conn = AsyncMock()
        async def capture_execute(sql, new_interval, target_id):
            captured["interval"] = new_interval
        conn.execute = AsyncMock(side_effect=capture_execute)

        await _update_target_backoff(conn, target_id=1, changed=changed, origin=origin,
                                     current_interval=current_interval)
        return captured["interval"]

    @pytest.mark.asyncio
    async def test_ugg_always_daily(self):
        interval = await self._call_backoff(changed=False, origin="ugg", current_interval=7)
        assert interval == 1

    @pytest.mark.asyncio
    async def test_changed_resets_to_one_day(self):
        interval = await self._call_backoff(changed=True, origin="icy_veins", current_interval=6)
        assert interval == 1

    @pytest.mark.asyncio
    async def test_unchanged_doubles_interval(self):
        interval = await self._call_backoff(changed=False, origin="icy_veins", current_interval=3)
        assert interval == 6

    @pytest.mark.asyncio
    async def test_interval_capped_at_14(self):
        interval = await self._call_backoff(changed=False, origin="method", current_interval=12)
        assert interval == 14

    @pytest.mark.asyncio
    async def test_interval_capped_at_14_from_14(self):
        interval = await self._call_backoff(changed=False, origin="icy_veins", current_interval=14)
        assert interval == 14


# ---------------------------------------------------------------------------
# 14–15. sync_target: hash dedup
# ---------------------------------------------------------------------------


class TestSyncTargetHashDedup:
    def _make_target_row(self, origin="icy_veins"):
        return {
            "id": 42, "url": "https://example.com/bis", "preferred_technique": "html_parse",
            "source_id": 3, "spec_id": 7, "hero_talent_id": None, "content_type": "raid",
            "check_interval_days": 3, "items_found": 5, "origin": origin,
        }

    @pytest.mark.asyncio
    async def test_matching_hash_skips_insert(self):
        """When content hash matches the stored hash, no new bis_scrape_raw row is inserted."""
        from sv_common.guild_sync.bis_sync import sync_target

        raw_html = "<html>some content</html>"
        import hashlib
        expected_hash = hashlib.sha256(raw_html.encode()).hexdigest()

        insert_sqls = []

        async def fake_extract(url, technique, **kwargs):
            # Return empty slots (no items found), raw content
            return [], [], None, raw_html

        conn = AsyncMock()
        # fetchrow returns target row (main query)
        # fetchval returns existing hash (hash check query)
        fetchval_call_count = 0
        async def fetchval_side(*args, **kwargs):
            nonlocal fetchval_call_count
            fetchval_call_count += 1
            return expected_hash  # existing hash matches new hash

        conn.fetchrow = AsyncMock(return_value=None)  # _target_row is passed directly
        conn.fetchval = AsyncMock(side_effect=fetchval_side)

        async def execute_side(*args, **kwargs):
            if "INSERT INTO landing.bis_scrape_raw" in args[0]:
                insert_sqls.append(args[0])
        conn.execute = AsyncMock(side_effect=execute_side)

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)

        target_row = self._make_target_row()
        with patch("sv_common.guild_sync.bis_sync._extract", new_callable=AsyncMock,
                   return_value=([], [], None, raw_html)):
            result = await sync_target(pool, 42, _target_row=target_row)

        assert result["status"] == "unchanged"
        assert not insert_sqls, "No bis_scrape_raw insert expected when hash matches"

    @pytest.mark.asyncio
    async def test_different_hash_inserts_row(self):
        """When content hash differs, a new bis_scrape_raw row is inserted with content_hash."""
        from sv_common.guild_sync.bis_sync import sync_target

        raw_html = "<html>new content</html>"
        old_hash = "aabbcc" * 10 + "aabb"  # 64 hex chars (different from actual hash)

        insert_sqls = []
        insert_params = []

        conn = AsyncMock()
        async def fetchval_side(*args, **kwargs):
            return old_hash  # doesn't match new content hash

        conn.fetchval = AsyncMock(side_effect=fetchval_side)

        async def execute_side(*args, **kwargs):
            if "INSERT INTO landing.bis_scrape_raw" in args[0]:
                insert_sqls.append(args[0])
                insert_params.extend(args[1:])
        conn.execute = AsyncMock(side_effect=execute_side)

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)

        target_row = self._make_target_row()
        with patch("sv_common.guild_sync.bis_sync._extract", new_callable=AsyncMock,
                   return_value=([], [], None, raw_html)):
            result = await sync_target(pool, 42, _target_row=target_row)

        assert result["status"] == "failed"  # no slots extracted
        assert len(insert_sqls) == 1, "Expected one bis_scrape_raw insert"
        # content_hash should be the 6th parameter in the INSERT VALUES ($1..$6)
        assert "content_hash" in insert_sqls[0]
