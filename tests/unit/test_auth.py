"""Unit tests for sv_common.auth — passwords, JWT, and invite code logic."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

# Ensure settings environment is populated before importing app modules
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-for-jwt")
os.environ.setdefault("APP_ENV", "testing")


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------


class TestPasswords:
    def test_hash_password_returns_hashed_string(self):
        from sv_common.auth.passwords import hash_password

        result = hash_password("hunter2")
        assert isinstance(result, str)
        assert result != "hunter2"

    def test_hash_password_returns_different_hash_each_time(self):
        from sv_common.auth.passwords import hash_password

        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2  # bcrypt uses random salt

    def test_verify_password_correct(self):
        from sv_common.auth.passwords import hash_password, verify_password

        hashed = hash_password("correct_horse_battery_staple")
        assert verify_password("correct_horse_battery_staple", hashed) is True

    def test_verify_password_incorrect(self):
        from sv_common.auth.passwords import hash_password, verify_password

        hashed = hash_password("real_password")
        assert verify_password("wrong_password", hashed) is False

    def test_verify_password_empty_string_not_allowed(self):
        from sv_common.auth.passwords import hash_password, verify_password

        hashed = hash_password("real_password")
        assert verify_password("", hashed) is False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TestJWT:
    def test_create_jwt_returns_string(self):
        from sv_common.auth.jwt import create_access_token

        token = create_access_token(user_id=1, member_id=2, rank_level=3)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_jwt_contains_expected_claims(self):
        from sv_common.auth.jwt import create_access_token, decode_access_token

        token = create_access_token(user_id=42, member_id=7, rank_level=4)
        payload = decode_access_token(token)
        assert payload["user_id"] == 42
        assert payload["member_id"] == 7
        assert payload["rank_level"] == 4

    def test_decode_jwt_valid_token(self):
        from sv_common.auth.jwt import create_access_token, decode_access_token

        token = create_access_token(user_id=1, member_id=1, rank_level=2)
        payload = decode_access_token(token)
        assert "exp" in payload
        assert "iat" in payload

    def test_decode_jwt_expired_token_raises(self):
        from sv_common.auth.jwt import create_access_token, decode_access_token

        # Create a token that expired 1 minute ago
        token = create_access_token(
            user_id=1, member_id=1, rank_level=1, expires_minutes=-1
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_access_token(token)

    def test_decode_jwt_invalid_token_raises(self):
        from sv_common.auth.jwt import decode_access_token

        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token("not.a.valid.token")

    def test_decode_jwt_tampered_token_raises(self):
        from sv_common.auth.jwt import create_access_token, decode_access_token

        token = create_access_token(user_id=1, member_id=1, rank_level=5)
        # Flip a char in the signature
        tampered = token[:-4] + "XXXX"
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(tampered)

    def test_custom_expiry_is_respected(self):
        from sv_common.auth.jwt import create_access_token, decode_access_token

        token = create_access_token(
            user_id=1, member_id=1, rank_level=1, expires_minutes=30
        )
        payload = decode_access_token(token)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        diff = (exp - iat).total_seconds()
        assert 29 * 60 <= diff <= 31 * 60  # roughly 30 minutes


# ---------------------------------------------------------------------------
# Invite code format (pure logic — no DB required)
# ---------------------------------------------------------------------------


_CHARSET = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
_AMBIGUOUS = set("0O1IL")


class TestInviteCodeFormat:
    def test_invite_code_generation_format(self):
        """Generated codes must be exactly 8 chars, uppercase, no ambiguous chars."""
        from sv_common.auth.invite_codes import _generate_code

        for _ in range(50):
            code = _generate_code()
            assert len(code) == 8, f"Code {code!r} is not 8 chars"
            assert code == code.upper(), f"Code {code!r} has lowercase"
            assert not _AMBIGUOUS.intersection(code), (
                f"Code {code!r} contains ambiguous chars"
            )
            assert all(c in _CHARSET for c in code), (
                f"Code {code!r} contains invalid chars"
            )

    def test_invite_code_uniqueness(self):
        """Codes should not repeat (statistically)."""
        from sv_common.auth.invite_codes import _generate_code

        codes = {_generate_code() for _ in range(100)}
        # With 32^8 possibilities, 100 codes should all be unique
        assert len(codes) == 100


class TestInviteCodeValidation:
    """Tests for validate_invite_code and consume_invite_code using mock DB."""

    def _make_invite(self, *, used_at=None, expires_at=None, member_id=1):
        invite = MagicMock()
        invite.code = "ABCD1234"
        invite.member_id = member_id
        invite.used_at = used_at
        invite.expires_at = expires_at
        return invite

    async def _mock_db_returning(self, invite):
        """Return a mock AsyncSession whose execute().scalar_one_or_none() returns invite."""
        scalar = MagicMock()
        scalar.scalar_one_or_none = MagicMock(return_value=invite)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=scalar)
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_invite_code_validation_valid(self):
        from sv_common.auth.invite_codes import validate_invite_code

        future_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
        invite = self._make_invite(expires_at=future_expiry)
        db = await self._mock_db_returning(invite)

        result = await validate_invite_code(db, "ABCD1234")
        assert result is invite

    @pytest.mark.asyncio
    async def test_invite_code_validation_already_used(self):
        from sv_common.auth.invite_codes import validate_invite_code

        invite = self._make_invite(used_at=datetime.now(timezone.utc))
        db = await self._mock_db_returning(invite)

        result = await validate_invite_code(db, "ABCD1234")
        assert result is None

    @pytest.mark.asyncio
    async def test_invite_code_validation_expired(self):
        from sv_common.auth.invite_codes import validate_invite_code

        past_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
        invite = self._make_invite(expires_at=past_expiry)
        db = await self._mock_db_returning(invite)

        result = await validate_invite_code(db, "ABCD1234")
        assert result is None

    @pytest.mark.asyncio
    async def test_invite_code_validation_not_found(self):
        from sv_common.auth.invite_codes import validate_invite_code

        db = await self._mock_db_returning(None)
        result = await validate_invite_code(db, "NOTFOUND")
        assert result is None

    @pytest.mark.asyncio
    async def test_consume_invalid_code_raises(self):
        from sv_common.auth.invite_codes import consume_invite_code

        db = await self._mock_db_returning(None)
        with pytest.raises(ValueError, match="invalid"):
            await consume_invite_code(db, "BADCODE1")
