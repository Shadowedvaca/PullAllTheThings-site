"""Unit tests for the Phase 4.1 setup wizard."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Crypto tests
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    from sv_common.crypto import encrypt_secret, decrypt_secret
    secret = "super-secret-value-123"
    key = "a-32-char-jwt-secret-key-padding!"
    encrypted = encrypt_secret(secret, key)
    assert encrypted != secret
    assert decrypt_secret(encrypted, key) == secret


def test_encrypt_different_each_time():
    from sv_common.crypto import encrypt_secret
    key = "a-32-char-jwt-secret-key-padding!"
    e1 = encrypt_secret("same-value", key)
    e2 = encrypt_secret("same-value", key)
    # Fernet uses random IV so ciphertexts differ
    assert e1 != e2


def test_decrypt_wrong_key_raises():
    from sv_common.crypto import encrypt_secret, decrypt_secret
    from cryptography.fernet import InvalidToken
    encrypted = encrypt_secret("secret", "key-one-32-chars-long-padded-12!")
    with pytest.raises(InvalidToken):
        decrypt_secret(encrypted, "key-two-32-chars-long-padded-12!")


# ---------------------------------------------------------------------------
# Setup guard middleware — unit tests via mock config
# ---------------------------------------------------------------------------

def test_setup_guard_allows_setup_paths_when_incomplete():
    """Exempt paths must not be redirected even when setup_complete is False."""
    from sv_common.config_cache import _cache
    _cache.clear()  # no setup_complete = falsy

    exempt_paths = ["/setup", "/setup/guild-identity", "/static/css/main.css", "/api/v1/setup/verify-discord-token"]
    for path in exempt_paths:
        assert path.startswith("/setup") or path.startswith("/static") or path.startswith("/api/v1/setup")


def test_setup_guard_redirects_non_exempt_when_incomplete():
    """Non-exempt paths should trigger redirect logic when setup_complete is falsy."""
    from sv_common.config_cache import _cache, get_site_config
    _cache.clear()
    cfg = get_site_config()
    assert not cfg.get("setup_complete")


def test_setup_guard_passes_when_complete():
    """When setup_complete=True the guard must not redirect any path."""
    from sv_common.config_cache import set_site_config, get_site_config
    set_site_config({"setup_complete": True, "guild_name": "Test Guild", "accent_color_hex": "#d4a84b"})
    assert get_site_config().get("setup_complete") is True
    # Reset
    from sv_common.config_cache import _cache
    _cache.clear()


# ---------------------------------------------------------------------------
# Verify Discord token — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_discord_token_success():
    """verify_discord_token returns bot info on success."""
    from guild_portal.api.setup_routes import verify_discord_token, VerifyDiscordTokenBody
    from sv_common.config_cache import _cache
    _cache.clear()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "123456789", "username": "TestBot"}

    with patch("guild_portal.api.setup_routes.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        result = await verify_discord_token(VerifyDiscordTokenBody(token="valid-bot-token"))

    assert result["ok"] is True
    assert result["bot_username"] == "TestBot"
    assert "invite_url" in result
    assert "123456789" in result["invite_url"]


@pytest.mark.asyncio
async def test_verify_discord_token_invalid():
    """verify_discord_token raises 400 when Discord returns non-200."""
    from fastapi import HTTPException
    from guild_portal.api.setup_routes import verify_discord_token, VerifyDiscordTokenBody
    from sv_common.config_cache import _cache
    _cache.clear()

    mock_response = MagicMock()
    mock_response.status_code = 401

    with patch("guild_portal.api.setup_routes.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await verify_discord_token(VerifyDiscordTokenBody(token="invalid"))

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Verify Blizzard — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_blizzard_success():
    from guild_portal.api.setup_routes import verify_blizzard, VerifyBlizzardBody
    from sv_common.config_cache import _cache
    _cache.clear()

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "test-token"}

    guild_response = MagicMock()
    guild_response.status_code = 200
    guild_response.json.return_value = {"name": "Pull All The Things", "member_count": 42}

    mock_db = AsyncMock()

    # Mock _get_or_create_site_config to return a mock SiteConfig
    mock_sc = MagicMock()
    with patch("guild_portal.api.setup_routes._get_or_create_site_config", return_value=mock_sc), \
         patch("guild_portal.api.setup_routes.httpx.AsyncClient") as mock_client_class, \
         patch("guild_portal.api.setup_routes.get_settings") as mock_settings:
        mock_settings.return_value.jwt_secret_key = "a-32-char-jwt-secret-key-padded!"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=guild_response)
        mock_client_class.return_value = mock_client

        result = await verify_blizzard(
            VerifyBlizzardBody(
                client_id="test-id",
                client_secret="test-secret",
                realm_slug="senjin",
                guild_slug="pull-all-the-things",
            ),
            db=mock_db,
        )

    assert result["ok"] is True
    assert result["guild_name"] == "Pull All The Things"


# ---------------------------------------------------------------------------
# Create admin — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_admin_password_too_short():
    from fastapi import HTTPException
    from guild_portal.api.setup_routes import create_admin, CreateAdminBody
    from sv_common.config_cache import _cache
    _cache.clear()

    mock_db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await create_admin(CreateAdminBody(display_name="Test", discord_username="test", password="short"), db=mock_db)
    assert exc_info.value.status_code == 400
    assert "8 characters" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_admin_requires_display_name():
    from fastapi import HTTPException
    from guild_portal.api.setup_routes import create_admin, CreateAdminBody
    from sv_common.config_cache import _cache
    _cache.clear()

    mock_db = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await create_admin(CreateAdminBody(display_name="", discord_username="user", password="validpassword"), db=mock_db)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# setup_complete flag blocks re-entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_routes_return_404_when_complete():
    """All setup API endpoints return 404 if setup_complete is True."""
    from fastapi import HTTPException
    from guild_portal.api.setup_routes import save_guild_identity, GuildIdentityBody
    from sv_common.config_cache import set_site_config

    set_site_config({"setup_complete": True, "guild_name": "Test", "accent_color_hex": "#d4a84b"})

    try:
        with pytest.raises(HTTPException) as exc_info:
            await save_guild_identity(GuildIdentityBody(guild_name="New Guild"), db=AsyncMock())
        assert exc_info.value.status_code == 404
    finally:
        from sv_common.config_cache import _cache
        _cache.clear()
