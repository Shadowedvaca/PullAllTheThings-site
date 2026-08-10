"""ActivityMiddleware — records page views per authenticated user after each response."""

import logging
import re
from datetime import date
from typing import Callable

import asyncpg
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from guild_portal.config import get_settings
from sv_common.auth.sessions import authenticate_session
from sv_common.db.engine import get_session_factory

logger = logging.getLogger(__name__)

# Paths where activity should NOT be recorded
_IGNORE_PREFIXES = (
    "/static/",
    "/favicon",
    "/health",
)

# Endpoint path suffixes that indicate polling — skip these
_IGNORE_SUFFIXES = (
    "-status",
    "-log",
)

# Regex for slot-level polling: /api/v1/me/gear-plan/<id>/available-items
_AVAILABLE_ITEMS_RE = re.compile(r"^/api/v1/me/gear-plan/\d+/available-items")


def _should_skip(path: str) -> bool:
    if any(path.startswith(p) for p in _IGNORE_PREFIXES):
        return True
    if any(path.endswith(s) for s in _IGNORE_SUFFIXES):
        return True
    if _AVAILABLE_ITEMS_RE.match(path):
        return True
    return False


async def _record_activity(pool: asyncpg.Pool, user_id: int, path: str) -> None:
    try:
        async with pool.acquire() as conn:
            today = date.today()
            await conn.execute(
                """
                INSERT INTO common.user_activity (user_id, activity_date, page_views, pages_visited)
                VALUES ($1, $2, 1, ARRAY[$3]::text[])
                ON CONFLICT (user_id, activity_date) DO UPDATE
                SET page_views    = user_activity.page_views + 1,
                    pages_visited = CASE
                        WHEN $3 = ANY(user_activity.pages_visited)
                        THEN user_activity.pages_visited
                        ELSE user_activity.pages_visited || ARRAY[$3]::text[]
                    END,
                    updated_at = NOW()
                """,
                user_id,
                today,
                path,
            )
            await conn.execute(
                """
                UPDATE common.users
                SET last_active_at = NOW(), updated_at = NOW()
                WHERE id = $1
                  AND (last_active_at IS NULL OR last_active_at < NOW() - INTERVAL '5 minutes')
                """,
                user_id,
            )
    except Exception:
        logger.debug("Activity record failed for user %s path %s", user_id, path, exc_info=True)


class ActivityMiddleware(BaseHTTPMiddleware):
    """Records page-view activity for authenticated users after the response is flushed."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        token = request.cookies.get("patt_token")
        if not token or _should_skip(request.url.path):
            return response

        pool: asyncpg.Pool | None = getattr(request.app.state, "guild_sync_pool", None)
        if pool is None:
            return response

        task = BackgroundTask(
            _record_authenticated_activity,
            pool,
            token,
            request.url.path,
        )
        if response.background is None:
            response.background = task
        else:
            existing = response.background
            response.background = BackgroundTask(_chain_tasks, existing, task)

        return response


async def _record_authenticated_activity(
    pool: asyncpg.Pool, token: str, path: str
) -> None:
    """Revalidate session state immediately before recording activity."""
    factory = get_session_factory(get_settings().database_url)
    try:
        async with factory() as db:
            authenticated = await authenticate_session(token, db)
            await _record_activity(pool, authenticated.user.id, path)
    except Exception:
        return


async def _chain_tasks(first: BackgroundTask, second: BackgroundTask) -> None:
    await first()
    await second()
