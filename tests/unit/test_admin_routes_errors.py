"""
Unit tests for Phase 6.2 — admin API endpoints for error log and routing.

Tests:
1. GET /errors/unresolved returns list + total
2. POST /errors/{id}/resolve returns ok=true
3. POST /errors/9999/resolve returns 404
4. GET /errors/routing returns rules list
5. PATCH /errors/routing/{id} updates rule
6. PATCH /errors/routing/9999 returns 404
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_async_pool(fetchrow_return=None, execute_return=None):
    """Mock asyncpg pool for direct-pool endpoints."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock(return_value=execute_return or "UPDATE 1")
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


# ---------------------------------------------------------------------------
# 1. GET /errors/unresolved
# ---------------------------------------------------------------------------


class TestGetErrorsUnresolved:
    @pytest.mark.asyncio
    async def test_returns_list_and_total(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        mock_errors = [
            {
                "id": 1, "issue_type": "bnet_token_expired", "severity": "warning",
                "source_module": "scheduler", "identifier": "Trog#1234",
                "summary": "Token expired", "occurrence_count": 3,
                "first_occurred_at": now, "last_occurred_at": now,
            },
            {
                "id": 2, "issue_type": "wcl_sync_failed", "severity": "critical",
                "source_module": "wcl_sync", "identifier": None,
                "summary": "Rate limit", "occurrence_count": 7,
                "first_occurred_at": now, "last_occurred_at": now,
            },
        ]

        with patch("guild_portal.api.admin_routes.get_unresolved", new=AsyncMock(return_value=mock_errors)):
            from guild_portal.api.admin_routes import get_errors_unresolved
            mock_request = MagicMock()
            mock_request.app.state.guild_sync_pool = MagicMock()

            result = await get_errors_unresolved(request=mock_request)

            assert result["ok"] is True
            assert len(result["data"]["errors"]) == 2
            assert result["data"]["total"] == 2
            assert result["data"]["errors"][0]["issue_type"] == "bnet_token_expired"

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        with patch("guild_portal.api.admin_routes.get_unresolved", new=AsyncMock(return_value=[])):
            from guild_portal.api.admin_routes import get_errors_unresolved
            mock_request = MagicMock()
            mock_request.app.state.guild_sync_pool = MagicMock()

            result = await get_errors_unresolved(request=mock_request)

            assert result["ok"] is True
            assert result["data"]["errors"] == []
            assert result["data"]["total"] == 0


# ---------------------------------------------------------------------------
# 2. POST /errors/{id}/resolve — success
# ---------------------------------------------------------------------------


class TestResolveErrorSuccess:
    @pytest.mark.asyncio
    async def test_returns_ok_on_valid_error(self):
        from guild_portal.api.admin_routes import resolve_error_manually
        from fastapi import HTTPException

        pool, conn = _make_async_pool(
            fetchrow_return={"id": 5, "issue_type": "bnet_token_expired", "identifier": "Trog#1234"}
        )

        mock_request = MagicMock()
        mock_request.app.state.guild_sync_pool = pool

        mock_db = MagicMock()

        with patch("guild_portal.deps.get_page_member", new=AsyncMock(return_value=None)):
            result = await resolve_error_manually(
                error_id=5, request=mock_request, db=mock_db
            )

        assert result["ok"] is True
        assert result["data"]["resolved"] is True


# ---------------------------------------------------------------------------
# 3. POST /errors/9999/resolve — 404
# ---------------------------------------------------------------------------


class TestResolveErrorNotFound:
    @pytest.mark.asyncio
    async def test_returns_404_when_not_found(self):
        from guild_portal.api.admin_routes import resolve_error_manually
        from fastapi import HTTPException

        pool, conn = _make_async_pool(fetchrow_return=None)

        mock_request = MagicMock()
        mock_request.app.state.guild_sync_pool = pool
        mock_db = MagicMock()

        with patch("guild_portal.deps.get_page_member", new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc_info:
                await resolve_error_manually(
                    error_id=9999, request=mock_request, db=mock_db
                )

        assert exc_info.value.status_code == 404
