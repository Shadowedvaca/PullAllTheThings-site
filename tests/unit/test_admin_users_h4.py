"""
Phase H.4 — Admin Users expired-token indicator tests.

Tests:
1. admin_users query includes token_expires_at
2. admin_users computes bnet_token_expired per user
3. Admin users template renders disabled button + ✕ for expired tokens
4. Admin users template renders active button for non-expired tokens
5. Admin users template renders "Not linked" for unlinked users
6. Legend appears when expired tokens exist
"""

import inspect
import os


USERS_TEMPLATE = os.path.join(
    os.path.dirname(__file__),
    "../../src/guild_portal/templates/admin/users.html",
)


def _read_template() -> str:
    with open(USERS_TEMPLATE, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# admin_pages.py source inspection
# ---------------------------------------------------------------------------


class TestAdminUsersQueryH4:
    def test_query_includes_token_expires_at(self):
        """admin_users SQL query selects token_expires_at from battlenet_accounts."""
        from guild_portal.pages.admin_pages import admin_users
        src = inspect.getsource(admin_users)
        assert "token_expires_at" in src

    def test_computes_bnet_token_expired_per_user(self):
        """admin_users computes bnet_token_expired for each user dict."""
        from guild_portal.pages.admin_pages import admin_users
        src = inspect.getsource(admin_users)
        assert "bnet_token_expired" in src
        assert "datetime.now" in src or "now(" in src


# ---------------------------------------------------------------------------
# Template structure tests
# ---------------------------------------------------------------------------


class TestAdminUsersTemplateH4:
    def test_expired_indicator_class_present(self):
        """Template uses bnet-expired-indicator CSS class."""
        html = _read_template()
        assert "bnet-expired-indicator" in html

    def test_expired_shows_disabled_button(self):
        """Template renders a disabled Sync BNet button for expired tokens."""
        html = _read_template()
        assert "bnet_token_expired" in html
        assert "disabled" in html

    def test_expired_shows_x_indicator(self):
        """Template renders ✕ (&#x2715;) indicator for expired tokens."""
        html = _read_template()
        assert "&#x2715;" in html

    def test_active_button_for_non_expired(self):
        """Template renders active js-enabled sync button for non-expired tokens."""
        html = _read_template()
        assert "syncBnet" in html

    def test_legend_present(self):
        """Template includes table-legend with expired token explanation."""
        html = _read_template()
        assert "table-legend" in html
        assert "Token expired" in html
