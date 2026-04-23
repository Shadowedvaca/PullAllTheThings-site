"""Unit tests for guild_portal.middleware.activity — path filtering and record logic."""

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-for-jwt-32chars!")
os.environ.setdefault("APP_ENV", "testing")


# ---------------------------------------------------------------------------
# _should_skip
# ---------------------------------------------------------------------------


class TestShouldSkip:
    def _skip(self, path: str) -> bool:
        from guild_portal.middleware.activity import _should_skip
        return _should_skip(path)

    def test_static_prefix_skipped(self):
        assert self._skip("/static/js/main.js") is True

    def test_favicon_skipped(self):
        assert self._skip("/favicon.ico") is True

    def test_health_skipped(self):
        assert self._skip("/health") is True

    def test_enrich_classify_status_skipped(self):
        assert self._skip("/api/v1/admin/bis/enrich-classify-status") is True

    def test_landing_status_skipped(self):
        assert self._skip("/api/v1/admin/bis/landing-status") is True

    def test_scrape_log_skipped(self):
        assert self._skip("/api/v1/admin/bis/scrape-log") is True

    def test_available_items_polling_skipped(self):
        assert self._skip("/api/v1/me/gear-plan/42/available-items") is True

    def test_regular_page_not_skipped(self):
        assert self._skip("/roster") is False

    def test_my_characters_not_skipped(self):
        assert self._skip("/my-characters") is False

    def test_admin_page_not_skipped(self):
        assert self._skip("/admin/raid-tools") is False

    def test_gear_plan_api_not_skipped(self):
        assert self._skip("/api/v1/me/gear-plan/42") is False

    def test_bis_matrix_api_not_skipped(self):
        assert self._skip("/api/v1/admin/bis/matrix") is False

    def test_login_api_not_skipped(self):
        assert self._skip("/api/v1/auth/login") is False

    def test_admin_users_page_not_skipped(self):
        assert self._skip("/admin/users") is False


# ---------------------------------------------------------------------------
# _record_activity
# ---------------------------------------------------------------------------


class TestRecordActivity:
    @pytest.mark.asyncio
    async def test_first_visit_inserts_row(self):
        from guild_portal.middleware.activity import _record_activity

        conn = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await _record_activity(pool, user_id=7, path="/roster")

        assert conn.execute.call_count == 2
        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "INSERT INTO common.user_activity" in first_call_sql
        assert "ON CONFLICT" in first_call_sql

    @pytest.mark.asyncio
    async def test_subsequent_visit_increments(self):
        from guild_portal.middleware.activity import _record_activity

        conn = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        # Call twice — both should use upsert (increment handled by SQL)
        await _record_activity(pool, user_id=7, path="/roster")
        await _record_activity(pool, user_id=7, path="/roster")

        assert conn.execute.call_count == 4  # 2 calls per visit

    @pytest.mark.asyncio
    async def test_last_active_update_included(self):
        from guild_portal.middleware.activity import _record_activity

        conn = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await _record_activity(pool, user_id=3, path="/my-characters")

        second_call_sql = conn.execute.call_args_list[1][0][0]
        assert "UPDATE common.users" in second_call_sql
        assert "last_active_at" in second_call_sql

    @pytest.mark.asyncio
    async def test_db_error_silently_swallowed(self):
        from guild_portal.middleware.activity import _record_activity

        conn = AsyncMock()
        conn.execute.side_effect = Exception("DB error")
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await _record_activity(pool, user_id=1, path="/roster")

    @pytest.mark.asyncio
    async def test_path_passed_to_insert(self):
        from guild_portal.middleware.activity import _record_activity

        conn = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await _record_activity(pool, user_id=5, path="/admin/gear-plan-admin")

        insert_args = conn.execute.call_args_list[0][0]
        assert "/admin/gear-plan-admin" in insert_args


# ---------------------------------------------------------------------------
# ActivityMiddleware.dispatch
# ---------------------------------------------------------------------------


class TestActivityMiddlewareDispatch:
    def _make_request(self, path: str, token: str | None = None) -> MagicMock:
        request = MagicMock()
        request.url.path = path
        request.cookies = {"patt_token": token} if token else {}
        pool = MagicMock()
        request.app.state.guild_sync_pool = pool
        return request

    def _make_valid_token(self, user_id: int = 42) -> str:
        import jwt
        from guild_portal.config import get_settings
        settings = get_settings()
        payload = {
            "user_id": user_id,
            "member_id": 1,
            "rank_level": 5,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=60),
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_unauthenticated_request_skips_tracking(self):
        from guild_portal.middleware.activity import ActivityMiddleware

        middleware = ActivityMiddleware(app=MagicMock())
        request = self._make_request("/roster", token=None)
        mock_response = MagicMock()
        mock_response.background = None

        call_next = AsyncMock(return_value=mock_response)
        response = await middleware.dispatch(request, call_next)

        assert response.background is None

    @pytest.mark.asyncio
    async def test_static_path_skips_tracking(self):
        from guild_portal.middleware.activity import ActivityMiddleware

        middleware = ActivityMiddleware(app=MagicMock())
        token = self._make_valid_token()
        request = self._make_request("/static/js/main.js", token=token)
        mock_response = MagicMock()
        mock_response.background = None

        call_next = AsyncMock(return_value=mock_response)
        response = await middleware.dispatch(request, call_next)

        assert response.background is None

    @pytest.mark.asyncio
    async def test_authenticated_page_sets_background_task(self):
        from guild_portal.middleware.activity import ActivityMiddleware
        from starlette.background import BackgroundTask

        middleware = ActivityMiddleware(app=MagicMock())
        token = self._make_valid_token(user_id=7)
        request = self._make_request("/roster", token=token)
        mock_response = MagicMock()
        mock_response.background = None

        call_next = AsyncMock(return_value=mock_response)
        response = await middleware.dispatch(request, call_next)

        assert response.background is not None

    @pytest.mark.asyncio
    async def test_expired_token_skips_tracking(self):
        from guild_portal.middleware.activity import ActivityMiddleware
        import jwt
        from guild_portal.config import get_settings

        settings = get_settings()
        expired_payload = {
            "user_id": 1,
            "member_id": 1,
            "rank_level": 5,
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
            "iat": datetime.now(timezone.utc) - timedelta(minutes=65),
        }
        expired_token = jwt.encode(expired_payload, settings.jwt_secret_key, algorithm="HS256")

        middleware = ActivityMiddleware(app=MagicMock())
        request = self._make_request("/roster", token=expired_token)
        mock_response = MagicMock()
        mock_response.background = None

        call_next = AsyncMock(return_value=mock_response)
        response = await middleware.dispatch(request, call_next)

        assert response.background is None

    @pytest.mark.asyncio
    async def test_no_pool_skips_tracking(self):
        from guild_portal.middleware.activity import ActivityMiddleware

        middleware = ActivityMiddleware(app=MagicMock())
        token = self._make_valid_token()
        request = self._make_request("/roster", token=token)
        request.app.state.guild_sync_pool = None
        mock_response = MagicMock()
        mock_response.background = None

        call_next = AsyncMock(return_value=mock_response)
        response = await middleware.dispatch(request, call_next)

        assert response.background is None

    @pytest.mark.asyncio
    async def test_existing_background_task_chained(self):
        from guild_portal.middleware.activity import ActivityMiddleware
        from starlette.background import BackgroundTask

        middleware = ActivityMiddleware(app=MagicMock())
        token = self._make_valid_token(user_id=9)
        request = self._make_request("/my-characters", token=token)

        existing_task = BackgroundTask(lambda: None)
        mock_response = MagicMock()
        mock_response.background = existing_task

        call_next = AsyncMock(return_value=mock_response)
        response = await middleware.dispatch(request, call_next)

        # Should have been replaced with a chaining task, not the original
        assert response.background is not existing_task
        assert response.background is not None
