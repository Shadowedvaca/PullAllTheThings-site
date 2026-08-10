"""Route-level regression contracts for revocable authentication sessions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_api_logout_revokes_present_cookie_token():
    from guild_portal.api.auth_routes import logout

    request = MagicMock()
    request.headers = {}
    request.cookies = {"patt_token": "signed-session"}
    db = AsyncMock()
    with patch("guild_portal.api.auth_routes.revoke_token", new=AsyncMock()) as revoke:
        result = await logout(request, db)

    revoke.assert_awaited_once_with(db, "signed-session")
    assert result["data"]["revoked"] is True


@pytest.mark.asyncio
async def test_page_logout_revokes_token_and_clears_cookie():
    from guild_portal.pages.auth_pages import logout

    request = MagicMock()
    request.cookies = {"patt_token": "signed-session"}
    db = AsyncMock()
    with patch("sv_common.auth.sessions.revoke_token", new=AsyncMock()) as revoke:
        response = await logout(request, db)

    revoke.assert_awaited_once_with(db, "signed-session")
    assert response.status_code == 302
    assert "patt_token=" in response.headers["set-cookie"]


def test_production_cookie_uses_exact_lifetime_and_security_attributes():
    from guild_portal.pages.auth_pages import _set_auth_cookie

    response = MagicMock()
    settings = MagicMock(app_env="production")
    with patch("guild_portal.config.get_settings", return_value=settings):
        _set_auth_cookie(response, "token", 720 * 60)

    response.set_cookie.assert_called_once_with(
        key="patt_token",
        value="token",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=720 * 60,
        path="/",
    )


@pytest.mark.asyncio
async def test_dependencies_fail_closed_when_session_validation_fails():
    from guild_portal.deps import (
        get_authenticated_session,
        get_current_player,
        get_page_member,
    )

    request = MagicMock()
    request.cookies = {"patt_token": "bad-session"}
    db = AsyncMock()
    with patch(
        "guild_portal.deps.authenticate_session",
        new=AsyncMock(side_effect=Exception("revoked")),
    ):
        with pytest.raises(HTTPException) as current_error:
            await get_current_player(request, None, db)
        with pytest.raises(HTTPException) as session_error:
            await get_authenticated_session(request, None, db)
        assert await get_page_member(request, db) is None

    assert current_error.value.status_code == 401
    assert session_error.value.status_code == 401


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_admin_deactivation_revokes_all_sessions():
    from guild_portal.pages.admin_pages import admin_toggle_user_active

    user = MagicMock(id=5, is_active=True)
    db = AsyncMock()
    db.execute.return_value = _result(user)
    with (
        patch(
            "guild_portal.pages.admin_pages._require_admin",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("sv_common.auth.sessions.revoke_all_sessions", new=AsyncMock()) as revoke,
    ):
        response = await admin_toggle_user_active(MagicMock(), 5, db)

    revoke.assert_awaited_once_with(db, 5, "account_deactivated")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_password_reset_revokes_all_sessions():
    from guild_portal.pages.admin_pages import admin_reset_user_password

    user = MagicMock(id=6, password_hash="old")
    db = AsyncMock()
    db.execute.return_value = _result(user)
    with (
        patch(
            "guild_portal.pages.admin_pages._require_admin",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("sv_common.auth.sessions.revoke_all_sessions", new=AsyncMock()) as revoke,
    ):
        response = await admin_reset_user_password(MagicMock(), 6, db)

    revoke.assert_awaited_once_with(db, 6, "password_reset")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_officer_can_revoke_all_user_sessions():
    from guild_portal.pages.admin_pages import admin_revoke_user_sessions

    db = AsyncMock()
    db.execute.return_value = _result(7)
    with (
        patch(
            "guild_portal.pages.admin_pages._require_admin",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("sv_common.auth.sessions.revoke_all_sessions", new=AsyncMock()) as revoke,
    ):
        response = await admin_revoke_user_sessions(MagicMock(), 7, db)

    revoke.assert_awaited_once_with(db, 7, "officer_revoke_all")
    assert response.status_code == 200
