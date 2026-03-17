"""Tests for Phase 4.4.2 — Battle.net character auto-claim."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(rows=None, fetchrow_side_effects=None):
    """Return a mock asyncpg pool whose acquire() context manager yields a mock conn."""
    conn = AsyncMock()
    if fetchrow_side_effects:
        conn.fetchrow.side_effect = fetchrow_side_effects
    else:
        conn.fetchrow.return_value = rows  # rows=None → returns None
    conn.fetch.return_value = []
    conn.execute.return_value = None

    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = cm
    return pool, conn


def _setup_bnet_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("BNET_TOKEN_ENCRYPTION_KEY", key)
    return key


# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------


def test_module_importable():
    from sv_common.guild_sync import bnet_character_sync  # noqa: F401


def test_functions_exist():
    from sv_common.guild_sync.bnet_character_sync import (
        get_valid_access_token,
        sync_bnet_characters,
    )
    assert callable(get_valid_access_token)
    assert callable(sync_bnet_characters)


# ---------------------------------------------------------------------------
# get_valid_access_token — valid token returned without refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_token_when_valid(monkeypatch):
    """Valid (non-expired) token is decrypted and returned without refresh."""
    _setup_bnet_key(monkeypatch)
    from sv_common.crypto import encrypt_bnet_token
    from sv_common.guild_sync.bnet_character_sync import get_valid_access_token

    original_token = "my-valid-access-token"
    encrypted = encrypt_bnet_token(original_token)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    pool, conn = _make_pool(rows={
        "access_token_encrypted": encrypted,
        "refresh_token_encrypted": None,
        "token_expires_at": future,
    })

    result = await get_valid_access_token(pool, player_id=1)
    assert result == original_token


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_none_when_no_account(monkeypatch):
    """Returns None when player has no battlenet_accounts row."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import get_valid_access_token

    pool, conn = _make_pool(rows=None)
    result = await get_valid_access_token(pool, player_id=99)
    assert result is None


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_none_when_no_refresh_token(monkeypatch):
    """Expired token with no refresh token returns None."""
    _setup_bnet_key(monkeypatch)
    from sv_common.crypto import encrypt_bnet_token
    from sv_common.guild_sync.bnet_character_sync import get_valid_access_token

    encrypted = encrypt_bnet_token("old-token")
    past = datetime.now(timezone.utc) - timedelta(hours=1)

    pool, conn = _make_pool(rows={
        "access_token_encrypted": encrypted,
        "refresh_token_encrypted": None,
        "token_expires_at": past,
    })

    mock_report_result = {"id": 1, "is_first_occurrence": True, "occurrence_count": 1}
    with patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)):
        result = await get_valid_access_token(pool, player_id=1)
    assert result is None


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_expired_token(monkeypatch):
    """Expired token triggers refresh; new tokens are stored and returned."""
    _setup_bnet_key(monkeypatch)
    from sv_common.crypto import encrypt_bnet_token
    from sv_common.guild_sync.bnet_character_sync import get_valid_access_token

    old_access = encrypt_bnet_token("old-access")
    old_refresh = encrypt_bnet_token("my-refresh-token")
    past = datetime.now(timezone.utc) - timedelta(hours=1)

    pool, conn = _make_pool(rows={
        "access_token_encrypted": old_access,
        "refresh_token_encrypted": old_refresh,
        "token_expires_at": past,
    })

    new_token_response = {
        "access_token": "new-shiny-access",
        "refresh_token": "new-refresh",
        "expires_in": 3600,
    }

    monkeypatch.setenv("BLIZZARD_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("BLIZZARD_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-key-32-bytes-long-here!")

    # Patch config_cache to return empty (fall back to env vars)
    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {},
    )

    import httpx
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = new_token_response

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_valid_access_token(pool, player_id=1)

    assert result == "new-shiny-access"
    # Verify that execute was called to update the stored tokens
    conn.execute.assert_called()


# ---------------------------------------------------------------------------
# sync_bnet_characters — filtering and upsert logic
# ---------------------------------------------------------------------------


def _make_blizzard_profile(characters):
    return {"wow_accounts": [{"id": 1, "characters": characters}]}


def _char(name, realm_slug, level, class_name="Druid"):
    return {
        "name": name,
        "realm": {"slug": realm_slug, "name": realm_slug.capitalize()},
        "level": level,
        "playable_class": {"id": 11, "name": class_name},
        "playable_race": {"id": 4, "name": "Night Elf"},
    }


@pytest.mark.asyncio
async def test_sync_bnet_characters_correct_realm_and_level_filtering(monkeypatch):
    """Home-realm chars at level >= 10 are linked; low-level and unknown off-realm chars skipped."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import sync_bnet_characters

    profile = _make_blizzard_profile([
        _char("Trogmoon", "senjin", 80),       # ✓ home realm, level ok
        _char("Bankalt", "senjin", 5),          # ✗ level too low
        _char("Transfered", "illidan", 80),     # ✗ unknown char on unrelated realm
        _char("NewChar", "senjin", 10),         # ✓ exactly level 10
    ])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = profile

    async def fake_fetchrow(query, *args):
        if "classes" in query:
            return {"id": 5}  # class_id
        if "SELECT id FROM guild_identity.wow_characters" in query:
            # Trogmoon (senjin) and NewChar (senjin) exist; Transfered (illidan) does NOT
            char_name = args[0]
            realm = args[1]
            if realm == "senjin":
                return {"id": 100}
            return None  # illidan char not in guild DB
        if "INSERT INTO guild_identity.wow_characters" in query:
            return {"id": 100}
        if "player_characters" in query and "SELECT" in query.upper():
            return None  # no existing link
        return None

    pool, conn = _make_pool()
    conn.fetchrow.side_effect = fake_fetchrow

    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {"home_realm_slug": "senjin"},
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        stats = await sync_bnet_characters(pool, player_id=1, access_token="tok")

    assert stats["linked"] == 2  # Trogmoon + NewChar
    assert stats["skipped"] == 2  # Bankalt + Transfered


@pytest.mark.asyncio
async def test_sync_bnet_characters_links_connected_realm_chars(monkeypatch):
    """Characters on connected realms that exist in wow_characters are linked."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import sync_bnet_characters

    # Bladefist and Malganis are connected to Sen'jin — chars already in wow_characters from roster sync
    profile = _make_blizzard_profile([
        _char("Shamlee", "bladefist", 82),      # ✓ known connected-realm char
        _char("Bullstorms", "malganis", 90),    # ✓ known connected-realm char
        _char("Bankalt", "area-52", 5),         # ✗ level too low
        _char("Stranger", "area-52", 80),       # ✗ unknown char on unrelated realm
    ])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = profile

    async def fake_fetchrow(query, *args):
        if "classes" in query:
            return {"id": 7}
        if "SELECT id FROM guild_identity.wow_characters" in query:
            char_name = args[0]
            realm = args[1]
            # Shamlee/Bullstorms were imported by guild roster sync
            if (char_name, realm) in [("Shamlee", "bladefist"), ("Bullstorms", "malganis")]:
                return {"id": 50}
            return None  # Stranger on area-52 is unknown
        if "INSERT INTO guild_identity.wow_characters" in query:
            return {"id": 50}
        if "player_characters" in query and "SELECT" in query.upper():
            return None
        return None

    pool, conn = _make_pool()
    conn.fetchrow.side_effect = fake_fetchrow

    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {"home_realm_slug": "senjin"},
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        stats = await sync_bnet_characters(pool, player_id=1, access_token="tok")

    assert stats["linked"] == 2   # Shamlee + Bullstorms
    assert stats["skipped"] == 2  # Bankalt (low level) + Stranger (unknown off-realm)


@pytest.mark.asyncio
async def test_sync_bnet_characters_skips_wrong_realm(monkeypatch):
    """Characters on a different realm are skipped entirely."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import sync_bnet_characters

    profile = _make_blizzard_profile([
        _char("Offserver", "illidan", 80),
        _char("AlsoOff", "stormrage", 80),
    ])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = profile

    pool, conn = _make_pool()

    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {"home_realm_slug": "senjin"},
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        stats = await sync_bnet_characters(pool, player_id=1, access_token="tok")

    assert stats["linked"] == 0
    assert stats["skipped"] == 2


@pytest.mark.asyncio
async def test_sync_bnet_characters_skips_low_level(monkeypatch):
    """Characters below level 10 are skipped."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import sync_bnet_characters

    profile = _make_blizzard_profile([
        _char("Lvl1", "senjin", 1),
        _char("Lvl9", "senjin", 9),
    ])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = profile

    pool, conn = _make_pool()

    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {"home_realm_slug": "senjin"},
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        stats = await sync_bnet_characters(pool, player_id=1, access_token="tok")

    assert stats["linked"] == 0
    assert stats["skipped"] == 2


@pytest.mark.asyncio
async def test_sync_bnet_characters_creates_new_wow_character(monkeypatch):
    """A character not in wow_characters is upserted (created) and linked."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import sync_bnet_characters

    profile = _make_blizzard_profile([_char("Brandnew", "senjin", 80)])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = profile

    call_sequence = []

    async def fake_fetchrow(query, *args):
        if "classes" in query:
            return {"id": 5}
        if "SELECT id FROM guild_identity.wow_characters" in query:
            call_sequence.append("pre_check")
            return None  # Not found → new character
        if "INSERT INTO guild_identity.wow_characters" in query:
            call_sequence.append("insert")
            return {"id": 200}
        if "player_characters" in query and ("SELECT" in query.upper() or "player_id" in query.lower()):
            return None
        return None

    pool, conn = _make_pool()
    conn.fetchrow.side_effect = fake_fetchrow

    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {"home_realm_slug": "senjin"},
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        stats = await sync_bnet_characters(pool, player_id=1, access_token="tok")

    assert stats["linked"] == 1
    assert stats["new_characters"] == 1


@pytest.mark.asyncio
async def test_sync_bnet_characters_upgrades_existing_link(monkeypatch):
    """An existing link with a different source is upgraded to battlenet_oauth."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import sync_bnet_characters

    profile = _make_blizzard_profile([_char("Trogmoon", "senjin", 80)])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = profile

    async def fake_fetchrow(query, *args):
        if "classes" in query:
            return {"id": 5}
        if "SELECT id FROM guild_identity.wow_characters" in query:
            return {"id": 42}  # char exists
        if "INSERT INTO guild_identity.wow_characters" in query:
            return {"id": 42}
        if "player_characters" in query and "character_id" in query:
            # Same player, different source
            return {"player_id": 1, "link_source": "guild_note"}
        return None

    pool, conn = _make_pool()
    conn.fetchrow.side_effect = fake_fetchrow

    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {"home_realm_slug": "senjin"},
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        stats = await sync_bnet_characters(pool, player_id=1, access_token="tok")

    assert stats["linked"] == 1
    # Verify INSERT INTO player_characters was called with battlenet_oauth
    insert_calls = [
        call for call in conn.execute.call_args_list
        if "player_characters" in str(call) and "battlenet_oauth" in str(call)
    ]
    assert len(insert_calls) >= 1


@pytest.mark.asyncio
async def test_sync_bnet_characters_no_home_realm_configured(monkeypatch):
    """Returns zeros without calling Blizzard API when home_realm_slug is missing."""
    _setup_bnet_key(monkeypatch)
    from sv_common.guild_sync.bnet_character_sync import sync_bnet_characters

    monkeypatch.delenv("GUILD_REALM_SLUG", raising=False)
    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config",
        lambda: {},
    )

    pool, conn = _make_pool()

    with patch("httpx.AsyncClient") as mock_client_cls:
        stats = await sync_bnet_characters(pool, player_id=1, access_token="tok")
        mock_client_cls.assert_not_called()

    assert stats == {"linked": 0, "new_characters": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Player Manager API — link_source in response
# ---------------------------------------------------------------------------


def test_admin_players_data_query_includes_link_source():
    """The players-data endpoint query selects pc.link_source."""
    import inspect
    from guild_portal.pages.admin_pages import admin_players_data
    src = inspect.getsource(admin_players_data)
    assert "link_source" in src


def test_admin_players_data_response_includes_link_source():
    """The characters dict in the players-data response includes link_source."""
    import inspect
    from guild_portal.pages.admin_pages import admin_players_data
    src = inspect.getsource(admin_players_data)
    assert '"link_source"' in src or "'link_source'" in src


# ---------------------------------------------------------------------------
# Settings template — verification badge for OAuth chars
# ---------------------------------------------------------------------------


def test_settings_template_shows_bnet_verified_badge():
    """settings.html shows a Battle.net Verified badge for OAuth-linked characters."""
    from pathlib import Path
    template_path = (
        Path(__file__).parent.parent.parent
        / "src" / "guild_portal" / "templates" / "profile" / "settings.html"
    )
    content = template_path.read_text(encoding="utf-8")
    assert "battlenet_oauth" in content
    assert "Battle.net Verified" in content


def test_settings_template_hides_unclaim_for_oauth():
    """settings.html hides the Unclaim button for battlenet_oauth characters."""
    from pathlib import Path
    template_path = (
        Path(__file__).parent.parent.parent
        / "src" / "guild_portal" / "templates" / "profile" / "settings.html"
    )
    content = template_path.read_text(encoding="utf-8")
    # The unclaim form should be inside an else block conditioned on is_bnet
    assert "is_bnet" in content
    assert "Locked" in content


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def test_scheduler_has_bnet_refresh_job():
    """GuildSyncScheduler registers the bnet_character_refresh job."""
    import inspect
    from sv_common.guild_sync.scheduler import GuildSyncScheduler
    src = inspect.getsource(GuildSyncScheduler.start)
    assert "bnet_character_refresh" in src


def test_scheduler_has_run_bnet_character_refresh_method():
    """GuildSyncScheduler has a run_bnet_character_refresh method."""
    from sv_common.guild_sync.scheduler import GuildSyncScheduler
    assert hasattr(GuildSyncScheduler, "run_bnet_character_refresh")
    assert callable(GuildSyncScheduler.run_bnet_character_refresh)


# ---------------------------------------------------------------------------
# Profile pages — OAuth unclaim block
# ---------------------------------------------------------------------------


def test_profile_unclaim_blocks_oauth_characters():
    """profile_unclaim_character route blocks unclaiming battlenet_oauth characters."""
    import inspect
    from guild_portal.pages.profile_pages import profile_unclaim_character
    src = inspect.getsource(profile_unclaim_character)
    assert "battlenet_oauth" in src


# ---------------------------------------------------------------------------
# players.js — lock icon for OAuth characters
# ---------------------------------------------------------------------------


def test_players_js_handles_battlenet_oauth():
    """players.js adds lock badge and non-draggable for battlenet_oauth characters."""
    from pathlib import Path
    js_path = (
        Path(__file__).parent.parent.parent
        / "src" / "guild_portal" / "static" / "js" / "players.js"
    )
    content = js_path.read_text(encoding="utf-8")
    assert "battlenet_oauth" in content
    assert "draggable" in content
    assert "🔒" in content


# ---------------------------------------------------------------------------
# Phase 6.4 — _refresh_token error reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_reports_error_on_no_refresh_token(monkeypatch):
    """_refresh_token calls report_error when no refresh token is stored."""
    _setup_bnet_key(monkeypatch)
    from sv_common.crypto import encrypt_bnet_token
    from sv_common.guild_sync.bnet_character_sync import get_valid_access_token
    from datetime import datetime, timedelta, timezone

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    pool, conn = _make_pool(rows={
        "access_token_encrypted": encrypt_bnet_token("old"),
        "refresh_token_encrypted": None,  # no refresh token
        "token_expires_at": past,
    })

    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config", lambda: {}
    )

    mock_report_result = {"id": 1, "is_first_occurrence": True, "occurrence_count": 1}
    with patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)) as mock_report:
        result = await get_valid_access_token(pool, player_id=42)

    assert result is None
    mock_report.assert_awaited_once()
    call_args = mock_report.await_args[0]
    assert call_args[1] == "bnet_token_expired"
    call_kwargs = mock_report.await_args[1]
    assert call_kwargs.get("identifier") == "42"


@pytest.mark.asyncio
async def test_refresh_token_reports_error_on_http_failure(monkeypatch):
    """_refresh_token calls report_error when the Blizzard HTTP request fails."""
    _setup_bnet_key(monkeypatch)
    from sv_common.crypto import encrypt_bnet_token
    from sv_common.guild_sync.bnet_character_sync import get_valid_access_token
    from datetime import datetime, timedelta, timezone
    import httpx

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    pool, conn = _make_pool(rows={
        "access_token_encrypted": encrypt_bnet_token("old"),
        "refresh_token_encrypted": encrypt_bnet_token("refresh-tok"),
        "token_expires_at": past,
    })

    monkeypatch.setenv("BLIZZARD_CLIENT_ID", "test-id")
    monkeypatch.setenv("BLIZZARD_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-key-32-bytes-long-here!")
    monkeypatch.setattr(
        "sv_common.guild_sync.bnet_character_sync.get_site_config", lambda: {}
    )

    mock_report_result = {"id": 1, "is_first_occurrence": True, "occurrence_count": 1}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("sv_common.errors.report_error", new=AsyncMock(return_value=mock_report_result)) as mock_report:
            result = await get_valid_access_token(pool, player_id=42)

    assert result is None
    mock_report.assert_awaited_once()
    call_args = mock_report.await_args[0]
    assert call_args[1] == "bnet_token_expired"
