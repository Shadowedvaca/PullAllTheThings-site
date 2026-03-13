"""Tests for Phase 4.4.1 — Battle.net OAuth account linking."""

import os
import pytest
from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_bnet_token_roundtrip(monkeypatch):
    """encrypt_bnet_token / decrypt_bnet_token are inverse operations."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("BNET_TOKEN_ENCRYPTION_KEY", key)

    from sv_common.crypto import encrypt_bnet_token, decrypt_bnet_token

    original = "test-access-token-abc123"
    encrypted = encrypt_bnet_token(original)
    assert encrypted != original
    assert decrypt_bnet_token(encrypted) == original


def test_encrypt_bnet_token_different_each_call(monkeypatch):
    """Each encrypt call produces a unique ciphertext (Fernet adds random IV)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("BNET_TOKEN_ENCRYPTION_KEY", key)

    from sv_common.crypto import encrypt_bnet_token

    t1 = encrypt_bnet_token("same-token")
    t2 = encrypt_bnet_token("same-token")
    assert t1 != t2  # Different IVs


def test_get_bnet_fernet_raises_if_key_missing(monkeypatch):
    """get_bnet_fernet raises RuntimeError when the env var is absent."""
    monkeypatch.delenv("BNET_TOKEN_ENCRYPTION_KEY", raising=False)

    # Reload to clear cached Fernet instance from any previous test
    import importlib
    import sv_common.crypto as crypto_mod
    importlib.reload(crypto_mod)

    with pytest.raises(RuntimeError, match="BNET_TOKEN_ENCRYPTION_KEY"):
        crypto_mod.get_bnet_fernet()


def test_bnet_crypto_independent_from_jwt_crypto():
    """Tokens encrypted with JWT-derived key cannot be decrypted with bnet key and vice versa."""
    from sv_common.crypto import encrypt_secret, decrypt_bnet_token, encrypt_bnet_token

    key = Fernet.generate_key().decode()
    os.environ["BNET_TOKEN_ENCRYPTION_KEY"] = key

    jwt_encrypted = encrypt_secret("my-secret", "jwt-secret-key")
    bnet_encrypted = encrypt_bnet_token("my-secret")

    # These should not cross-decrypt
    with pytest.raises(Exception):
        decrypt_bnet_token(jwt_encrypted)

    os.environ.pop("BNET_TOKEN_ENCRYPTION_KEY", None)


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


def test_battlenet_account_model_exists():
    """BattlenetAccount model is importable."""
    from sv_common.db.models import BattlenetAccount
    assert BattlenetAccount.__tablename__ == "battlenet_accounts"
    assert BattlenetAccount.__table_args__["schema"] == "guild_identity"


def test_battlenet_account_has_required_columns():
    """BattlenetAccount has all Phase 4.4.1 columns."""
    from sv_common.db.models import BattlenetAccount
    cols = {c.name for c in BattlenetAccount.__table__.columns}
    required = {
        "id", "player_id", "bnet_id", "battletag",
        "access_token_encrypted", "refresh_token_encrypted",
        "token_expires_at", "linked_at", "last_refreshed", "last_character_sync",
    }
    assert required.issubset(cols), f"Missing columns: {required - cols}"


def test_player_has_battlenet_account_relationship():
    """Player model has battlenet_account relationship."""
    from sv_common.db.models import Player
    assert hasattr(Player, "battlenet_account")


# ---------------------------------------------------------------------------
# Route module
# ---------------------------------------------------------------------------


def test_bnet_auth_routes_importable():
    """bnet_auth_routes module imports cleanly."""
    from guild_portal.api import bnet_auth_routes  # noqa: F401


def test_bnet_auth_router_exists():
    """Router object is exported."""
    from guild_portal.api.bnet_auth_routes import router
    assert router is not None


def test_bnet_auth_router_paths():
    """Router contains the expected route paths."""
    from guild_portal.api.bnet_auth_routes import router
    paths = {route.path for route in router.routes}
    assert "/auth/battlenet" in paths
    assert "/auth/battlenet/callback" in paths
    assert "/api/v1/auth/battlenet" in paths


def test_bnet_delete_route_method():
    """DELETE /api/v1/auth/battlenet route exists with DELETE method."""
    from guild_portal.api.bnet_auth_routes import router
    delete_routes = [r for r in router.routes if r.path == "/api/v1/auth/battlenet"]
    assert len(delete_routes) == 1
    assert "DELETE" in delete_routes[0].methods


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_settings_has_bnet_token_encryption_key():
    """Settings model has bnet_token_encryption_key field."""
    from guild_portal.config import Settings
    settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        jwt_secret_key="test-secret",
    )
    # Defaults to empty string when not set
    assert hasattr(settings, "bnet_token_encryption_key")
    assert isinstance(settings.bnet_token_encryption_key, str)


# ---------------------------------------------------------------------------
# App registration
# ---------------------------------------------------------------------------


def test_app_includes_bnet_routes():
    """create_app registers Battle.net auth routes."""
    from guild_portal.app import create_app
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/auth/battlenet" in paths
    assert "/auth/battlenet/callback" in paths
    assert "/api/v1/auth/battlenet" in paths


# ---------------------------------------------------------------------------
# Profile pages
# ---------------------------------------------------------------------------


def test_profile_pages_loads_bnet_data():
    """_load_profile_data returns bnet_account and bnet_char_count keys."""
    import inspect
    from guild_portal.pages.profile_pages import _load_profile_data
    src = inspect.getsource(_load_profile_data)
    assert "bnet_account" in src
    assert "bnet_char_count" in src


def test_profile_settings_template_has_bnet_section():
    """settings.html contains the Battle.net connection section."""
    from pathlib import Path
    template_path = (
        Path(__file__).parent.parent.parent
        / "src" / "guild_portal" / "templates" / "profile" / "settings.html"
    )
    content = template_path.read_text(encoding="utf-8")
    assert "Battle.net Account" in content
    assert "/auth/battlenet" in content
    assert "bnet_account" in content
    assert "bnet-unlink-modal" in content


def test_profile_settings_template_has_unlink_js():
    """settings.html contains the unlink confirmation modal JS."""
    from pathlib import Path
    template_path = (
        Path(__file__).parent.parent.parent
        / "src" / "guild_portal" / "templates" / "profile" / "settings.html"
    )
    content = template_path.read_text(encoding="utf-8")
    assert "confirmUnlink" in content
    assert "doUnlink" in content
    assert "/api/v1/auth/battlenet" in content
