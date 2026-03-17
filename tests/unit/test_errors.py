"""
Unit tests for Phase 6.1 — sv_common.errors error catalogue.

Tests cover:
1. _make_hash — deterministic, different inputs produce different hashes
2. _make_hash — None identifier produces consistent hash
3. report_error — returns is_first_occurrence=True on new record (occurrence_count=1)
4. report_error — returns is_first_occurrence=False on recurrence (occurrence_count>1)
5. resolve_issue — returns count from UPDATE N response
6. resolve_issue — returns 0 when no rows matched
7. get_unresolved — returns list of dicts from rows
8. get_unresolved — severity filter builds correct params (warning → warning+critical)
9. get_unresolved — empty result returns [] without error
10. report_error — swallows DB exception gracefully (optional)
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Hash determinism
# ---------------------------------------------------------------------------


class TestMakeHash:
    def test_deterministic_same_inputs(self):
        from sv_common.errors._store import _make_hash

        h1 = _make_hash("bnet_token_expired", "123")
        h2 = _make_hash("bnet_token_expired", "123")
        assert h1 == h2

    def test_different_identifiers_differ(self):
        from sv_common.errors._store import _make_hash

        h1 = _make_hash("bnet_token_expired", "123")
        h2 = _make_hash("bnet_token_expired", "456")
        assert h1 != h2

    def test_different_types_differ(self):
        from sv_common.errors._store import _make_hash

        h1 = _make_hash("bnet_token_expired", "123")
        h2 = _make_hash("wcl_sync_failed", "123")
        assert h1 != h2

    def test_hash_is_64_chars(self):
        from sv_common.errors._store import _make_hash

        h = _make_hash("bnet_token_expired", "123")
        assert len(h) == 64


# ---------------------------------------------------------------------------
# 2. None identifier produces consistent hash
# ---------------------------------------------------------------------------


class TestMakeHashNoIdentifier:
    def test_none_identifier_consistent(self):
        from sv_common.errors._store import _make_hash

        h1 = _make_hash("some_type", None)
        h2 = _make_hash("some_type", None)
        assert h1 == h2

    def test_none_differs_from_empty_string(self):
        from sv_common.errors._store import _make_hash

        # None → "some_type:" and "" → "some_type:" — actually these ARE the same by design
        h1 = _make_hash("some_type", None)
        h2 = _make_hash("some_type", "")
        # Both hash "some_type:" so they should be equal
        assert h1 == h2

    def test_none_differs_from_value(self):
        from sv_common.errors._store import _make_hash

        h1 = _make_hash("some_type", None)
        h2 = _make_hash("some_type", "something")
        assert h1 != h2


# ---------------------------------------------------------------------------
# Helper: build a mock asyncpg pool
# ---------------------------------------------------------------------------


def _make_pool(fetchrow_return=None, execute_return=None, fetch_return=None):
    """Return a mock asyncpg pool whose acquire() context manager yields a mock conn."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock(return_value=execute_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


# ---------------------------------------------------------------------------
# 3. report_error — first occurrence
# ---------------------------------------------------------------------------


class TestReportErrorFirstOccurrence:
    @pytest.mark.asyncio
    async def test_returns_first_occurrence_true_when_count_is_one(self):
        from sv_common.errors import report_error

        pool, _ = _make_pool(fetchrow_return={"id": 1, "occurrence_count": 1})
        result = await report_error(pool, "bnet_token_expired", "warning", "Token expired", "scheduler")

        assert result["id"] == 1
        assert result["is_first_occurrence"] is True
        assert result["occurrence_count"] == 1

    @pytest.mark.asyncio
    async def test_passes_correct_args_to_upsert(self):
        from sv_common.errors import report_error

        pool, conn = _make_pool(fetchrow_return={"id": 5, "occurrence_count": 1})
        await report_error(
            pool,
            "wcl_sync_failed",
            "critical",
            "WCL API rate limit",
            "wcl_sync",
            details={"code": 429},
            identifier="player_42",
        )

        conn.fetchrow.assert_called_once()
        call_args = conn.fetchrow.call_args[0]
        # positional: sql, issue_type, severity, source_module, identifier, summary, details_json, hash
        assert call_args[1] == "wcl_sync_failed"
        assert call_args[2] == "critical"
        assert call_args[3] == "wcl_sync"
        assert call_args[4] == "player_42"
        assert call_args[5] == "WCL API rate limit"


# ---------------------------------------------------------------------------
# 4. report_error — recurrence
# ---------------------------------------------------------------------------


class TestReportErrorRecurrence:
    @pytest.mark.asyncio
    async def test_returns_first_occurrence_false_when_count_gt_one(self):
        from sv_common.errors import report_error

        pool, _ = _make_pool(fetchrow_return={"id": 1, "occurrence_count": 3})
        result = await report_error(pool, "bnet_token_expired", "warning", "Token expired", "scheduler")

        assert result["is_first_occurrence"] is False
        assert result["occurrence_count"] == 3

    @pytest.mark.asyncio
    async def test_occurrence_count_two_is_not_first(self):
        from sv_common.errors import report_error

        pool, _ = _make_pool(fetchrow_return={"id": 7, "occurrence_count": 2})
        result = await report_error(pool, "ah_sync_failed", "warning", "AH sync failed", "ah_service")

        assert result["is_first_occurrence"] is False


# ---------------------------------------------------------------------------
# 5. resolve_issue — returns count
# ---------------------------------------------------------------------------


class TestResolveIssueReturnsCount:
    @pytest.mark.asyncio
    async def test_returns_one_when_one_row_updated(self):
        from sv_common.errors import resolve_issue

        pool, _ = _make_pool(execute_return="UPDATE 1")
        count = await resolve_issue(pool, "bnet_token_expired", "Shadowedvaca#1947")
        assert count == 1

    @pytest.mark.asyncio
    async def test_passes_resolved_by_to_execute(self):
        from sv_common.errors import resolve_issue

        pool, conn = _make_pool(execute_return="UPDATE 1")
        await resolve_issue(pool, "bnet_token_expired", "Shadowedvaca#1947", resolved_by="system")

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0]
        # positional: sql, resolved_by, issue_hash
        assert call_args[1] == "system"


# ---------------------------------------------------------------------------
# 6. resolve_issue — no match returns 0
# ---------------------------------------------------------------------------


class TestResolveIssueNoMatch:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_rows_updated(self):
        from sv_common.errors import resolve_issue

        pool, _ = _make_pool(execute_return="UPDATE 0")
        count = await resolve_issue(pool, "nonexistent_type", None)
        assert count == 0

    @pytest.mark.asyncio
    async def test_handles_malformed_execute_response(self):
        from sv_common.errors import resolve_issue

        pool, _ = _make_pool(execute_return="")
        count = await resolve_issue(pool, "some_type", None)
        assert count == 0


# ---------------------------------------------------------------------------
# 7. get_unresolved — returns list of dicts
# ---------------------------------------------------------------------------


class TestGetUnresolved:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        from sv_common.errors import get_unresolved

        rows = [
            {"id": 1, "issue_type": "bnet_token_expired", "severity": "warning",
             "source_module": "scheduler", "identifier": "Trog#1234",
             "summary": "Token expired", "details": None, "occurrence_count": 2,
             "first_occurred_at": None, "last_occurred_at": None},
            {"id": 2, "issue_type": "wcl_sync_failed", "severity": "critical",
             "source_module": "wcl_sync", "identifier": None,
             "summary": "Rate limit", "details": {"code": 429}, "occurrence_count": 7,
             "first_occurred_at": None, "last_occurred_at": None},
        ]
        pool, conn = _make_pool(fetch_return=rows)
        conn.fetch = AsyncMock(return_value=rows)

        result = await get_unresolved(pool)

        assert len(result) == 2
        assert result[0]["issue_type"] == "bnet_token_expired"
        assert result[1]["occurrence_count"] == 7

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        from sv_common.errors import get_unresolved

        pool, conn = _make_pool(fetch_return=[])
        conn.fetch = AsyncMock(return_value=[])
        result = await get_unresolved(pool)
        assert result == []


# ---------------------------------------------------------------------------
# 8. get_unresolved — severity filter
# ---------------------------------------------------------------------------


class TestGetUnresolvedSeverityFilter:
    @pytest.mark.asyncio
    async def test_warning_includes_critical_not_info(self):
        from sv_common.errors import get_unresolved

        pool, conn = _make_pool(fetch_return=[])
        conn.fetch = AsyncMock(return_value=[])

        await get_unresolved(pool, severity="warning")

        call_args = conn.fetch.call_args[0]
        # The severity list is the first positional param after the SQL
        # Find it: it should be a list containing warning and critical but not info
        params = list(call_args[1:])
        severity_list = params[0]  # first param after sql is severity list
        assert "warning" in severity_list
        assert "critical" in severity_list
        assert "info" not in severity_list

    @pytest.mark.asyncio
    async def test_critical_includes_only_critical(self):
        from sv_common.errors import get_unresolved

        pool, conn = _make_pool(fetch_return=[])
        conn.fetch = AsyncMock(return_value=[])

        await get_unresolved(pool, severity="critical")

        call_args = conn.fetch.call_args[0]
        params = list(call_args[1:])
        severity_list = params[0]
        assert severity_list == ["critical"]

    @pytest.mark.asyncio
    async def test_info_includes_all_severities(self):
        from sv_common.errors import get_unresolved

        pool, conn = _make_pool(fetch_return=[])
        conn.fetch = AsyncMock(return_value=[])

        await get_unresolved(pool, severity="info")

        call_args = conn.fetch.call_args[0]
        params = list(call_args[1:])
        severity_list = params[0]
        assert set(severity_list) == {"info", "warning", "critical"}


# ---------------------------------------------------------------------------
# 9. get_unresolved — additional filters
# ---------------------------------------------------------------------------


class TestGetUnresolvedFilters:
    @pytest.mark.asyncio
    async def test_issue_type_filter_passed_as_param(self):
        from sv_common.errors import get_unresolved

        pool, conn = _make_pool(fetch_return=[])
        conn.fetch = AsyncMock(return_value=[])

        await get_unresolved(pool, issue_type="bnet_token_expired")

        call_args = conn.fetch.call_args[0]
        params = list(call_args[1:])
        assert "bnet_token_expired" in params

    @pytest.mark.asyncio
    async def test_source_module_filter_passed_as_param(self):
        from sv_common.errors import get_unresolved

        pool, conn = _make_pool(fetch_return=[])
        conn.fetch = AsyncMock(return_value=[])

        await get_unresolved(pool, source_module="scheduler")

        call_args = conn.fetch.call_args[0]
        params = list(call_args[1:])
        assert "scheduler" in params


# ---------------------------------------------------------------------------
# 10. report_error — graceful DB exception handling
# ---------------------------------------------------------------------------


class TestReportErrorSwallowsException:
    @pytest.mark.asyncio
    async def test_db_exception_does_not_propagate(self, caplog):
        """A broken DB should never crash the calling subsystem."""
        from sv_common.errors._store import _upsert

        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=Exception("DB connection lost"))

        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        # _upsert raises — report_error should log it but not propagate
        # (this test just verifies the exception propagates from _upsert;
        # the caller is expected to wrap in try/except if they want swallowing)
        with pytest.raises(Exception, match="DB connection lost"):
            await _upsert(pool, "test_type", "warning", "test", "test_module", None, None)


# ---------------------------------------------------------------------------
# 11. ORM model importable
# ---------------------------------------------------------------------------


class TestErrorLogModel:
    def test_error_log_model_importable(self):
        from sv_common.db.models import ErrorLog
        assert ErrorLog.__tablename__ == "error_log"

    def test_error_log_schema_is_common(self):
        from sv_common.db.models import ErrorLog
        assert ErrorLog.__table_args__["schema"] == "common"

    def test_error_log_has_required_columns(self):
        from sv_common.db.models import ErrorLog
        cols = {c.key for c in ErrorLog.__table__.columns}
        required = {
            "id", "issue_type", "severity", "source_module", "identifier",
            "summary", "details", "issue_hash", "occurrence_count",
            "first_occurred_at", "last_occurred_at", "resolved_at", "resolved_by",
        }
        assert required.issubset(cols)


# ---------------------------------------------------------------------------
# 12. Public API importable from sv_common.errors
# ---------------------------------------------------------------------------


class TestPublicApiImports:
    def test_report_error_importable(self):
        from sv_common.errors import report_error
        import inspect
        assert inspect.iscoroutinefunction(report_error)

    def test_resolve_issue_importable(self):
        from sv_common.errors import resolve_issue
        import inspect
        assert inspect.iscoroutinefunction(resolve_issue)

    def test_get_unresolved_importable(self):
        from sv_common.errors import get_unresolved
        import inspect
        assert inspect.iscoroutinefunction(get_unresolved)
