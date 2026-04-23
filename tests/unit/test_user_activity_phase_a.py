"""Unit tests for Phase 1.8-A — user activity logging schema + login stamping.

Tests:
1. User model has last_active_at column
2. User model has last_login_at column
3. User model has login_count column with default 0
4. Migration 0178 exists with correct revision chain
5. login_count increments on each successful login call
6. last_login_at is stamped on successful login
7. login_count is NOT incremented on failed login (bad password)
8. login_count is NOT incremented on inactive account
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-for-jwt-32chars!!")
os.environ.setdefault("APP_ENV", "testing")


# ---------------------------------------------------------------------------
# Model column presence
# ---------------------------------------------------------------------------


class TestUserModelColumns:
    def test_user_has_last_active_at(self):
        from sv_common.db.models import User
        assert hasattr(User, "last_active_at")

    def test_user_has_last_login_at(self):
        from sv_common.db.models import User
        assert hasattr(User, "last_login_at")

    def test_user_has_login_count(self):
        from sv_common.db.models import User
        assert hasattr(User, "login_count")

    def test_user_login_count_column_name(self):
        from sv_common.db.models import User
        col = User.__table__.c.get("login_count")
        assert col is not None

    def test_user_last_login_at_nullable(self):
        from sv_common.db.models import User
        col = User.__table__.c.get("last_login_at")
        assert col is not None
        assert col.nullable is True

    def test_user_last_active_at_nullable(self):
        from sv_common.db.models import User
        col = User.__table__.c.get("last_active_at")
        assert col is not None
        assert col.nullable is True


# ---------------------------------------------------------------------------
# Migration file
# ---------------------------------------------------------------------------


class TestMigration0178:
    def test_migration_file_exists(self):
        import os
        base = os.path.dirname(__file__)
        migration = os.path.join(base, "..", "..", "alembic", "versions", "0178_user_activity_logging.py")
        assert os.path.isfile(migration), "Migration 0178 file not found"

    def test_migration_revision(self):
        import importlib.util, os
        base = os.path.dirname(__file__)
        path = os.path.join(base, "..", "..", "alembic", "versions", "0178_user_activity_logging.py")
        spec = importlib.util.spec_from_file_location("m0178", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "0178"
        assert m.down_revision == "0177"


# ---------------------------------------------------------------------------
# Login stamping
# ---------------------------------------------------------------------------


def _make_user(*, is_active=True, login_count=0, last_login_at=None):
    user = MagicMock()
    user.id = 1
    user.is_active = is_active
    user.password_hash = "$2b$12$placeholder"
    user.login_count = login_count
    user.last_login_at = last_login_at
    return user


def _make_player(*, rank_level=2):
    player = MagicMock()
    player.id = 10
    player.website_user_id = 1
    player.guild_rank = MagicMock()
    player.guild_rank.level = rank_level
    return player


def _make_db(user, player):
    """Return a mock AsyncSession that returns user then player from execute()."""
    user_scalar = MagicMock()
    user_scalar.scalar_one_or_none = MagicMock(return_value=user)
    player_scalar = MagicMock()
    player_scalar.scalar_one_or_none = MagicMock(return_value=player)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[user_scalar, player_scalar])
    db.flush = AsyncMock()
    return db


class TestLoginStamping:
    @pytest.mark.asyncio
    async def test_last_login_at_set_on_success(self):
        from guild_portal.api.auth_routes import login, LoginBody
        from sv_common.auth.passwords import hash_password

        user = _make_user()
        user.password_hash = hash_password("password123")
        player = _make_player()
        db = _make_db(user, player)

        body = LoginBody(discord_username="testuser", password="password123")
        result = await login(body, db)

        assert result["ok"] is True
        assert user.last_login_at is not None
        assert isinstance(user.last_login_at, datetime)

    @pytest.mark.asyncio
    async def test_login_count_incremented_on_success(self):
        from guild_portal.api.auth_routes import login, LoginBody
        from sv_common.auth.passwords import hash_password

        user = _make_user(login_count=5)
        user.password_hash = hash_password("password123")
        player = _make_player()
        db = _make_db(user, player)

        body = LoginBody(discord_username="testuser", password="password123")
        await login(body, db)

        assert user.login_count == 6

    @pytest.mark.asyncio
    async def test_login_count_starts_from_zero(self):
        from guild_portal.api.auth_routes import login, LoginBody
        from sv_common.auth.passwords import hash_password

        user = _make_user(login_count=0)
        user.password_hash = hash_password("password123")
        player = _make_player()
        db = _make_db(user, player)

        body = LoginBody(discord_username="testuser", password="password123")
        await login(body, db)

        assert user.login_count == 1

    @pytest.mark.asyncio
    async def test_db_flush_called_after_stamping(self):
        from guild_portal.api.auth_routes import login, LoginBody
        from sv_common.auth.passwords import hash_password

        user = _make_user()
        user.password_hash = hash_password("password123")
        player = _make_player()
        db = _make_db(user, player)

        body = LoginBody(discord_username="testuser", password="password123")
        await login(body, db)

        db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_login_count_not_incremented_on_bad_password(self):
        from guild_portal.api.auth_routes import login, LoginBody
        from sv_common.auth.passwords import hash_password
        from fastapi import HTTPException

        user = _make_user(login_count=3)
        user.password_hash = hash_password("correct_password")
        db = _make_db(user, _make_player())

        body = LoginBody(discord_username="testuser", password="wrong_password")
        with pytest.raises(HTTPException) as exc:
            await login(body, db)

        assert exc.value.status_code == 401
        assert user.login_count == 3  # unchanged

    @pytest.mark.asyncio
    async def test_login_count_not_incremented_on_unknown_user(self):
        from guild_portal.api.auth_routes import login, LoginBody
        from fastapi import HTTPException

        # User not found — scalar_one_or_none returns None
        scalar = MagicMock()
        scalar.scalar_one_or_none = MagicMock(return_value=None)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar)

        body = LoginBody(discord_username="ghost", password="any")
        with pytest.raises(HTTPException) as exc:
            await login(body, db)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_inactive_user_blocked_before_stamping(self):
        from guild_portal.api.auth_routes import login, LoginBody
        from sv_common.auth.passwords import hash_password
        from fastapi import HTTPException

        user = _make_user(is_active=False, login_count=0)
        user.password_hash = hash_password("password123")
        db = _make_db(user, _make_player())

        body = LoginBody(discord_username="testuser", password="password123")
        with pytest.raises(HTTPException) as exc:
            await login(body, db)

        assert exc.value.status_code == 403
        assert user.login_count == 0  # not incremented for inactive accounts
        assert user.last_login_at is None
