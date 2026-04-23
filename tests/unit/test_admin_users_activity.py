"""Unit tests for Phase 1.8-C — Admin Users page activity columns.

Tests:
1-8.  _rel_time() helper — various time deltas
9.    _rel_time(None) returns "never"
10.   timezone-naive dt is handled without error
11.   bnet_token_expired flag set correctly
12.   pages_7d None coerced to empty list
13.   pages_7d list passed through unchanged
14.   last_active_rel / last_login_rel derived from rel_time
15.   active_week count — user active in last 7 days
16.   active_week count — user active 8 days ago excluded
17.   active_week count — user with None last_active_at excluded
18.   never_logged_in count — login_count 0
19.   never_logged_in count — login_count > 0 excluded
20.   ORDER BY last_active_at DESC NULLS LAST — verify SQL contains it
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-for-jwt-32chars!!")
os.environ.setdefault("APP_ENV", "testing")


from guild_portal.pages.admin_pages import _rel_time


# ---------------------------------------------------------------------------
# _rel_time helper
# ---------------------------------------------------------------------------

class TestRelTime:
    def _ago(self, **kwargs) -> datetime:
        return datetime.now(timezone.utc) - timedelta(**kwargs)

    def test_none_returns_never(self):
        assert _rel_time(None) == "never"

    def test_just_now_seconds(self):
        assert _rel_time(self._ago(seconds=30)) == "just now"

    def test_just_now_59_seconds(self):
        assert _rel_time(self._ago(seconds=59)) == "just now"

    def test_minutes(self):
        assert _rel_time(self._ago(minutes=5)) == "5m ago"

    def test_minutes_59(self):
        assert _rel_time(self._ago(minutes=59)) == "59m ago"

    def test_hours(self):
        assert _rel_time(self._ago(hours=3)) == "3h ago"

    def test_hours_23(self):
        assert _rel_time(self._ago(hours=23)) == "23h ago"

    def test_days(self):
        assert _rel_time(self._ago(days=5)) == "5d ago"

    def test_days_29(self):
        assert _rel_time(self._ago(days=29)) == "29d ago"

    def test_months(self):
        assert _rel_time(self._ago(days=60)) == "2mo ago"

    def test_years(self):
        assert _rel_time(self._ago(days=400)) == "1y ago"

    def test_naive_datetime_handled(self):
        # timezone-naive dt should not raise
        naive = datetime.utcnow() - timedelta(hours=2)
        result = _rel_time(naive)
        assert result == "2h ago"


# ---------------------------------------------------------------------------
# Row processing logic
# ---------------------------------------------------------------------------

def _make_raw_row(
    *,
    id=1,
    email="test@example.com",
    is_active=True,
    created_at=None,
    last_login_at=None,
    last_active_at=None,
    login_count=0,
    player_id=None,
    display_name=None,
    rank_name=None,
    battletag=None,
    last_bnet_sync=None,
    bnet_token_expires_at=None,
    views_7d=0,
    views_total=0,
    last_activity_date=None,
    pages_7d=None,
):
    return {
        "id": id,
        "email": email,
        "is_active": is_active,
        "created_at": created_at or datetime.now(timezone.utc),
        "last_login_at": last_login_at,
        "last_active_at": last_active_at,
        "login_count": login_count,
        "player_id": player_id,
        "display_name": display_name,
        "rank_name": rank_name,
        "battletag": battletag,
        "last_bnet_sync": last_bnet_sync,
        "bnet_token_expires_at": bnet_token_expires_at,
        "views_7d": views_7d,
        "views_total": views_total,
        "last_activity_date": last_activity_date,
        "pages_7d": pages_7d,
    }


def _process_row(raw: dict) -> dict:
    """Mirror the processing logic in admin_users()."""
    now = datetime.now(timezone.utc)
    u = dict(raw)
    expires_at = u.get("bnet_token_expires_at")
    u["bnet_token_expired"] = bool(expires_at and expires_at <= now)
    u["last_active_rel"] = _rel_time(u.get("last_active_at"))
    u["last_login_rel"] = _rel_time(u.get("last_login_at"))
    u["pages_7d"] = u.get("pages_7d") or []
    return u


class TestRowProcessing:
    def test_bnet_token_not_expired(self):
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        u = _process_row(_make_raw_row(bnet_token_expires_at=expires))
        assert u["bnet_token_expired"] is False

    def test_bnet_token_expired(self):
        expires = datetime.now(timezone.utc) - timedelta(hours=1)
        u = _process_row(_make_raw_row(bnet_token_expires_at=expires))
        assert u["bnet_token_expired"] is True

    def test_bnet_token_none_not_expired(self):
        u = _process_row(_make_raw_row(bnet_token_expires_at=None))
        assert u["bnet_token_expired"] is False

    def test_pages_7d_none_becomes_empty_list(self):
        u = _process_row(_make_raw_row(pages_7d=None))
        assert u["pages_7d"] == []

    def test_pages_7d_list_passed_through(self):
        paths = ["/admin/users", "/roster"]
        u = _process_row(_make_raw_row(pages_7d=paths))
        assert u["pages_7d"] == paths

    def test_last_active_rel_populated(self):
        active = datetime.now(timezone.utc) - timedelta(hours=2)
        u = _process_row(_make_raw_row(last_active_at=active))
        assert u["last_active_rel"] == "2h ago"

    def test_last_active_rel_never(self):
        u = _process_row(_make_raw_row(last_active_at=None))
        assert u["last_active_rel"] == "never"

    def test_last_login_rel_populated(self):
        login = datetime.now(timezone.utc) - timedelta(days=3)
        u = _process_row(_make_raw_row(last_login_at=login))
        assert u["last_login_rel"] == "3d ago"

    def test_last_login_rel_never(self):
        u = _process_row(_make_raw_row(last_login_at=None))
        assert u["last_login_rel"] == "never"


# ---------------------------------------------------------------------------
# Stat pill computation
# ---------------------------------------------------------------------------

def _compute_stats(users: list[dict]) -> dict:
    """Mirror active_week / never_logged_in computation from admin_users()."""
    now = datetime.now(timezone.utc)
    active_week = sum(
        1 for u in users
        if u.get("last_active_at") is not None
        and (now - (
            u["last_active_at"].replace(tzinfo=timezone.utc)
            if u["last_active_at"].tzinfo is None
            else u["last_active_at"]
        )).days < 7
    )
    never_logged_in = sum(1 for u in users if not u.get("login_count"))
    return {"active_week": active_week, "never_logged_in": never_logged_in}


class TestStatPills:
    def test_active_week_recent_user(self):
        users = [
            _make_raw_row(last_active_at=datetime.now(timezone.utc) - timedelta(days=2)),
        ]
        s = _compute_stats(users)
        assert s["active_week"] == 1

    def test_active_week_excludes_old(self):
        users = [
            _make_raw_row(last_active_at=datetime.now(timezone.utc) - timedelta(days=8)),
        ]
        s = _compute_stats(users)
        assert s["active_week"] == 0

    def test_active_week_excludes_none(self):
        users = [_make_raw_row(last_active_at=None)]
        s = _compute_stats(users)
        assert s["active_week"] == 0

    def test_active_week_mixed(self):
        users = [
            _make_raw_row(id=1, last_active_at=datetime.now(timezone.utc) - timedelta(days=1)),
            _make_raw_row(id=2, last_active_at=datetime.now(timezone.utc) - timedelta(days=10)),
            _make_raw_row(id=3, last_active_at=None),
        ]
        s = _compute_stats(users)
        assert s["active_week"] == 1

    def test_never_logged_in_zero_count(self):
        users = [_make_raw_row(login_count=0)]
        s = _compute_stats(users)
        assert s["never_logged_in"] == 1

    def test_never_logged_in_excludes_nonzero(self):
        users = [_make_raw_row(login_count=5)]
        s = _compute_stats(users)
        assert s["never_logged_in"] == 0

    def test_never_logged_in_mixed(self):
        users = [
            _make_raw_row(id=1, login_count=0),
            _make_raw_row(id=2, login_count=3),
            _make_raw_row(id=3, login_count=0),
        ]
        s = _compute_stats(users)
        assert s["never_logged_in"] == 2


# ---------------------------------------------------------------------------
# SQL sanity check
# ---------------------------------------------------------------------------

class TestAdminUsersSql:
    def test_query_orders_by_last_active_at(self):
        import inspect
        from guild_portal.pages import admin_pages
        src = inspect.getsource(admin_pages.admin_users)
        assert "last_active_at DESC NULLS LAST" in src

    def test_query_selects_activity_columns(self):
        import inspect
        from guild_portal.pages import admin_pages
        src = inspect.getsource(admin_pages.admin_users)
        for col in ("views_7d", "views_total", "pages_7d", "last_login_at", "login_count"):
            assert col in src, f"Expected '{col}' in admin_users SQL"

    def test_query_joins_user_activity(self):
        import inspect
        from guild_portal.pages import admin_pages
        src = inspect.getsource(admin_pages.admin_users)
        assert "user_activity" in src
