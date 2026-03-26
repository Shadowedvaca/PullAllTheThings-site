"""
Unit tests for the admin reset-password endpoint and template.

Tests:
1. POST /admin/users/{id}/reset-password endpoint exists in admin_pages
2. Endpoint calls generate_temp_password and hash_password
3. Endpoint returns temp_password in response
4. Template includes Reset PW button
5. Template includes confirm modal
6. Template includes result modal with copyable password display
7. JS resetPassword, doReset, copyTempPw functions are present
"""

import inspect
import os

import pytest

USERS_TEMPLATE = os.path.join(
    os.path.dirname(__file__),
    "../../src/guild_portal/templates/admin/users.html",
)
ADMIN_PAGES = os.path.join(
    os.path.dirname(__file__),
    "../../src/guild_portal/pages/admin_pages.py",
)


def _read_template() -> str:
    with open(USERS_TEMPLATE, encoding="utf-8") as f:
        return f.read()


def _read_admin_pages() -> str:
    with open(ADMIN_PAGES, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Endpoint source inspection
# ---------------------------------------------------------------------------


class TestAdminResetPasswordEndpoint:
    def test_endpoint_route_registered(self):
        """admin_pages registers POST /users/{user_id}/reset-password."""
        src = _read_admin_pages()
        assert 'router.post("/users/{user_id}/reset-password")' in src

    def test_endpoint_calls_generate_temp_password(self):
        """Endpoint uses generate_temp_password() to create the temp credential."""
        src = _read_admin_pages()
        assert "generate_temp_password" in src

    def test_endpoint_calls_hash_password(self):
        """Endpoint hashes the temp password before saving."""
        src = _read_admin_pages()
        assert "hash_password(temp_pw)" in src

    def test_endpoint_returns_temp_password_in_response(self):
        """Endpoint returns temp_password in the response data."""
        src = _read_admin_pages()
        assert '"temp_password"' in src or "'temp_password'" in src

    def test_endpoint_requires_admin(self):
        """Endpoint calls _require_admin before proceeding."""
        from guild_portal.pages.admin_pages import admin_reset_user_password
        src = inspect.getsource(admin_reset_user_password)
        assert "_require_admin" in src

    def test_endpoint_returns_404_for_missing_user(self):
        """Endpoint returns 404 when user not found."""
        from guild_portal.pages.admin_pages import admin_reset_user_password
        src = inspect.getsource(admin_reset_user_password)
        assert "404" in src


# ---------------------------------------------------------------------------
# Template structure
# ---------------------------------------------------------------------------


class TestAdminResetPasswordTemplate:
    def test_reset_button_present(self):
        """Template has a Reset PW button per user row."""
        html = _read_template()
        assert "resetPassword(this)" in html
        assert "Reset PW" in html

    def test_reset_button_style_defined(self):
        """Template defines ua-btn--reset CSS class."""
        html = _read_template()
        assert "ua-btn--reset" in html

    def test_confirm_modal_present(self):
        """Template includes the confirmation modal before executing reset."""
        html = _read_template()
        assert "reset-confirm-modal" in html
        assert "Reset Password" in html

    def test_result_modal_present(self):
        """Template includes the result modal that shows the temp password."""
        html = _read_template()
        assert "reset-result-modal" in html
        assert "reset-result-pw" in html

    def test_copy_button_present(self):
        """Template has a Copy button in the result modal."""
        html = _read_template()
        assert "copyTempPw()" in html

    def test_js_reset_password_function(self):
        """Template includes the resetPassword JS function."""
        html = _read_template()
        assert "function resetPassword(" in html

    def test_js_do_reset_function(self):
        """Template includes the doReset JS function that calls the API."""
        html = _read_template()
        assert "async function doReset(" in html
        assert "reset-password" in html

    def test_js_copy_temp_pw_function(self):
        """Template includes copyTempPw using clipboard API."""
        html = _read_template()
        assert "function copyTempPw(" in html
        assert "clipboard" in html

    def test_js_close_functions_present(self):
        """Template includes modal close functions."""
        html = _read_template()
        assert "closeResetConfirm" in html
        assert "closeResetResult" in html
