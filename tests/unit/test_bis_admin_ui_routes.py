"""
Unit tests for Phase 1.7-F — BIS admin UI API endpoints.

Tests:
1.  PATCH /targets/{id}: is_active=False silences the target (DB update called)
2.  PATCH /targets/{id}: is_active=True re-activates the target
3.  PATCH /targets/{id}: empty body is a no-op (no DB update)
4.  PATCH /targets/{id}: check_interval_days update
5.  POST /targets/reactivate-all: resets all targets, returns updated count
6.  GET /daily-runs: returns list of run records with correct structure
7.  GET /daily-runs: empty table returns empty list
8.  GET /daily-runs: delta_added/removed JSON strings are parsed to lists
9.  GET /patch-signal: monitoring=True when guide targets at 1-day interval
10. GET /patch-signal: monitoring=False when no recent 1-day guide targets
11. GET /patch-signal: includes encounter_baseline from site_config
12. GET /patch-signal: last_probe_at is None when no targets fetched
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchval=None, fetchrow=None, fetch=None, execute="UPDATE 1"):
    """Build a mock asyncpg pool."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.execute = AsyncMock(return_value=execute)

    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


# ---------------------------------------------------------------------------
# 1-4. PATCH /targets/{id}
# ---------------------------------------------------------------------------


class TestPatchTargetStatus:
    @pytest.mark.asyncio
    async def test_is_active_false_calls_update(self):
        from guild_portal.api.bis_routes import patch_target_status, TargetStatusUpdate

        pool, conn = _make_pool()
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        body = TargetStatusUpdate(is_active=False)
        result = await patch_target_status(1, body, request)

        assert result == {"ok": True}
        conn.execute.assert_awaited_once()
        sql, *args = conn.execute.call_args[0]
        assert "is_active" in sql
        assert False in args

    @pytest.mark.asyncio
    async def test_is_active_true_re_activates(self):
        from guild_portal.api.bis_routes import patch_target_status, TargetStatusUpdate

        pool, conn = _make_pool()
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        body = TargetStatusUpdate(is_active=True)
        result = await patch_target_status(2, body, request)

        assert result == {"ok": True}
        conn.execute.assert_awaited_once()
        sql, *args = conn.execute.call_args[0]
        assert "is_active" in sql
        assert True in args

    @pytest.mark.asyncio
    async def test_empty_body_is_noop(self):
        from guild_portal.api.bis_routes import patch_target_status, TargetStatusUpdate

        pool, conn = _make_pool()
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        body = TargetStatusUpdate()  # all None
        result = await patch_target_status(1, body, request)

        assert result == {"ok": True}
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_interval_days_update(self):
        from guild_portal.api.bis_routes import patch_target_status, TargetStatusUpdate

        pool, conn = _make_pool()
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        body = TargetStatusUpdate(check_interval_days=7)
        result = await patch_target_status(3, body, request)

        assert result == {"ok": True}
        sql, *args = conn.execute.call_args[0]
        assert "check_interval_days" in sql
        assert 7 in args


# ---------------------------------------------------------------------------
# 5. POST /targets/reactivate-all
# ---------------------------------------------------------------------------


class TestReactivateAllTargets:
    @pytest.mark.asyncio
    async def test_returns_updated_count(self):
        from guild_portal.api.bis_routes import reactivate_all_targets

        pool, conn = _make_pool(execute="UPDATE 42")
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await reactivate_all_targets(request)

        assert result["ok"] is True
        assert result["updated"] == 42
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "is_active = TRUE" in sql
        assert "next_check_at = NOW()" in sql

    @pytest.mark.asyncio
    async def test_calls_update_all_targets(self):
        from guild_portal.api.bis_routes import reactivate_all_targets

        pool, conn = _make_pool(execute="UPDATE 10")
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await reactivate_all_targets(request)

        assert result["ok"] is True
        # No WHERE clause — applies to all targets
        sql = conn.execute.call_args[0][0]
        assert "WHERE" not in sql


# ---------------------------------------------------------------------------
# 6-8. GET /daily-runs
# ---------------------------------------------------------------------------


class TestGetDailyRuns:
    @pytest.mark.asyncio
    async def test_returns_run_list(self):
        from guild_portal.api.bis_routes import get_daily_runs

        now = datetime.now(timezone.utc)
        fake_rows = [
            {
                "id": 1, "run_at": now, "triggered_by": "manual", "patch_signal": False,
                "targets_checked": 5, "targets_changed": 2, "targets_failed": 0,
                "targets_skipped": 1, "bis_entries_before": 1000, "bis_entries_after": 1005,
                "trinket_ratings_before": 200, "trinket_ratings_after": 200,
                "delta_added": None, "delta_removed": None,
                "duration_seconds": 45.2, "email_sent_at": None, "notes": None,
            },
        ]

        pool, conn = _make_pool(fetch=fake_rows)
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await get_daily_runs(request, limit=10)

        assert result["ok"] is True
        assert len(result["runs"]) == 1
        run = result["runs"][0]
        assert run["id"] == 1
        assert run["triggered_by"] == "manual"
        assert run["targets_checked"] == 5
        assert isinstance(run["run_at"], str)  # serialised to ISO string

    @pytest.mark.asyncio
    async def test_empty_table_returns_empty_list(self):
        from guild_portal.api.bis_routes import get_daily_runs

        pool, conn = _make_pool(fetch=[])
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await get_daily_runs(request, limit=10)

        assert result["ok"] is True
        assert result["runs"] == []

    @pytest.mark.asyncio
    async def test_delta_json_strings_parsed_to_lists(self):
        import json
        from guild_portal.api.bis_routes import get_daily_runs

        now = datetime.now(timezone.utc)
        added = [{"spec_id": 1, "source_id": 2, "slot": "head", "blizzard_item_id": 999, "item_name": "Hat"}]
        removed = [{"spec_id": 1, "source_id": 2, "slot": "chest", "blizzard_item_id": 888, "item_name": "Vest"}]

        fake_rows = [
            {
                "id": 2, "run_at": now, "triggered_by": "scheduled", "patch_signal": False,
                "targets_checked": 10, "targets_changed": 1, "targets_failed": 0,
                "targets_skipped": 0, "bis_entries_before": 500, "bis_entries_after": 501,
                "trinket_ratings_before": 100, "trinket_ratings_after": 100,
                "delta_added": json.dumps(added), "delta_removed": json.dumps(removed),
                "duration_seconds": 30.0, "email_sent_at": None, "notes": None,
            },
        ]

        pool, conn = _make_pool(fetch=fake_rows)
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await get_daily_runs(request, limit=10)

        assert result["ok"] is True
        run = result["runs"][0]
        assert isinstance(run["delta_added"], list)
        assert run["delta_added"][0]["item_name"] == "Hat"
        assert isinstance(run["delta_removed"], list)
        assert run["delta_removed"][0]["item_name"] == "Vest"


# ---------------------------------------------------------------------------
# 9-12. GET /patch-signal
# ---------------------------------------------------------------------------


class TestGetPatchSignal:
    def _make_signal_pool(self, monitoring=True, encounter_count=42, last_probe=None):
        """Build a pool where fetchval is called for monitoring, then last_probe_at;
        fetchrow is called for site_config."""
        now = datetime.now(timezone.utc) if last_probe is None else last_probe

        conn = MagicMock()
        # First fetchval call: EXISTS query for monitoring
        # Second fetchval call: MAX(last_fetched)
        conn.fetchval = AsyncMock(side_effect=[monitoring, now])
        conn.fetchrow = AsyncMock(return_value={"bis_encounter_count": encounter_count})
        conn.fetch = AsyncMock(return_value=[])
        conn.execute = AsyncMock(return_value="UPDATE 0")

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)
        return pool

    @pytest.mark.asyncio
    async def test_monitoring_true_when_guide_targets_recent(self):
        from guild_portal.api.bis_routes import get_patch_signal

        pool = self._make_signal_pool(monitoring=True, encounter_count=88)
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await get_patch_signal(request)

        assert result["ok"] is True
        assert result["monitoring"] is True

    @pytest.mark.asyncio
    async def test_monitoring_false_when_quiet(self):
        from guild_portal.api.bis_routes import get_patch_signal

        pool = self._make_signal_pool(monitoring=False, encounter_count=42)
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await get_patch_signal(request)

        assert result["ok"] is True
        assert result["monitoring"] is False

    @pytest.mark.asyncio
    async def test_returns_encounter_baseline(self):
        from guild_portal.api.bis_routes import get_patch_signal

        pool = self._make_signal_pool(monitoring=False, encounter_count=55)
        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await get_patch_signal(request)

        assert result["encounter_baseline"] == 55

    @pytest.mark.asyncio
    async def test_last_probe_at_none_when_no_targets(self):
        from guild_portal.api.bis_routes import get_patch_signal

        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=[False, None])  # monitoring=False, last_probe=None
        conn.fetchrow = AsyncMock(return_value={"bis_encounter_count": 10})

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)

        request = MagicMock()
        request.app.state.guild_sync_pool = pool

        result = await get_patch_signal(request)

        assert result["ok"] is True
        assert result["last_probe_at"] is None
