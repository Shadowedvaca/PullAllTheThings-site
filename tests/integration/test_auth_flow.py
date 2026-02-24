"""Integration tests for the full authentication flow.

Requires TEST_DATABASE_URL pointing to a running PostgreSQL instance.
All tests skip gracefully if the database is unavailable.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildRank, InviteCode, Player, User
from sv_common.auth.passwords import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_rank(db: AsyncSession, *, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _create_player(
    db: AsyncSession, *, display_name: str, rank_id: int
) -> Player:
    """Create an unregistered player (no website account)."""
    player = Player(display_name=display_name, guild_rank_id=rank_id)
    db.add(player)
    await db.flush()
    return player


async def _create_registered_player(
    db: AsyncSession, *, discord_username: str, rank_id: int, password: str
):
    """Create a player with a linked website account (already registered)."""
    user = User(
        email=discord_username.lower(),
        password_hash=hash_password(password),
    )
    db.add(user)
    await db.flush()

    player = Player(
        display_name=discord_username,
        guild_rank_id=rank_id,
        website_user_id=user.id,
    )
    db.add(player)
    await db.flush()
    return player, user


async def _create_invite(
    db: AsyncSession,
    *,
    player_id: int,
    created_by_id: int,
    hours: int = 72,
    used: bool = False,
    expired: bool = False,
) -> str:
    from datetime import datetime, timedelta, timezone
    from sv_common.auth.invite_codes import _generate_code

    code = _generate_code()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=hours) if not expired else now - timedelta(hours=1)
    used_at = now if used else None

    invite = InviteCode(
        code=code,
        player_id=player_id,
        created_by_player_id=created_by_id,
        expires_at=expires_at,
        used_at=used_at,
    )
    db.add(invite)
    await db.flush()
    return code


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_full_registration_flow(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """generate code → register → login → access /me"""
        rank = await _create_rank(db_session, name="Member_reg", level=2)
        player = await _create_player(
            db_session, display_name="test_reg_user", rank_id=rank.id
        )
        code = await _create_invite(
            db_session, player_id=player.id, created_by_id=player.id
        )

        # Register
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": code, "discord_username": "test_reg_user", "password": "securepw1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        token = data["data"]["token"]
        assert token

        # Login
        resp = await client.post(
            "/api/v1/auth/login",
            json={"discord_username": "test_reg_user", "password": "securepw1"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Access /me
        resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        me = resp.json()["data"]
        assert me["display_name"] == "test_reg_user"

    async def test_register_with_invalid_code_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": "ZZZZZZZZ", "discord_username": "user_invalid_code", "password": "pw"},
        )
        assert resp.status_code == 400

    async def test_register_with_expired_code_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_exp", level=2)
        player = await _create_player(
            db_session, display_name="user_expired_code", rank_id=rank.id
        )
        code = await _create_invite(
            db_session, player_id=player.id, created_by_id=player.id, expired=True
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": code, "discord_username": "user_expired_code", "password": "pw"},
        )
        assert resp.status_code == 400

    async def test_register_with_used_code_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_used", level=2)
        player = await _create_player(
            db_session, display_name="user_used_code", rank_id=rank.id
        )
        code = await _create_invite(
            db_session, player_id=player.id, created_by_id=player.id, used=True
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": code, "discord_username": "user_used_code", "password": "pw"},
        )
        assert resp.status_code == 400

    async def test_register_already_registered_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_rereg", level=2)
        player, _ = await _create_registered_player(
            db_session,
            discord_username="already_regged",
            rank_id=rank.id,
            password="pw",
        )
        code = await _create_invite(
            db_session, player_id=player.id, created_by_id=player.id
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": code, "discord_username": "already_regged", "password": "newpw"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Login tests
# ---------------------------------------------------------------------------


class TestLogin:
    async def test_login_with_correct_credentials(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_login", level=2)
        await _create_registered_player(
            db_session,
            discord_username="login_user",
            rank_id=rank.id,
            password="correct_pw",
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"discord_username": "login_user", "password": "correct_pw"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["data"]["token"]

    async def test_login_with_wrong_password_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_badpw", level=2)
        await _create_registered_player(
            db_session,
            discord_username="badpw_user",
            rank_id=rank.id,
            password="real_pw",
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"discord_username": "badpw_user", "password": "wrong_pw"},
        )
        assert resp.status_code == 401

    async def test_login_unregistered_player_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A player with no website_user_id cannot log in."""
        rank = await _create_rank(db_session, name="Member_unreg", level=2)
        await _create_player(db_session, display_name="unreg_user", rank_id=rank.id)
        resp = await client.post(
            "/api/v1/auth/login",
            json={"discord_username": "unreg_user", "password": "whatever"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"discord_username": "ghost_user_xyz", "password": "pw"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Protected route tests
# ---------------------------------------------------------------------------


class TestProtectedRoutes:
    async def test_protected_route_without_token_returns_401(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_protected_route_with_invalid_token_returns_401(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        resp = await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.real.token"}
        )
        assert resp.status_code == 401

    async def test_admin_route_blocked_without_auth(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        resp = await client.get("/api/v1/admin/ranks")
        assert resp.status_code == 401

    async def test_admin_route_blocked_for_member_rank(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A registered Member (rank 2) should get 403 on admin routes."""
        rank = await _create_rank(db_session, name="Member_admblk", level=2)
        player, user = await _create_registered_player(
            db_session,
            discord_username="low_rank_member",
            rank_id=rank.id,
            password="pw",
        )
        from sv_common.auth.jwt import create_access_token
        token = create_access_token(
            user_id=user.id, member_id=player.id, rank_level=2
        )
        resp = await client.get(
            "/api/v1/admin/ranks", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403

    async def test_admin_route_accessible_by_officer(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """A registered Officer (rank 4) should access admin routes."""
        rank = await _create_rank(db_session, name="Officer_adm", level=4)
        player, user = await _create_registered_player(
            db_session,
            discord_username="officer_access",
            rank_id=rank.id,
            password="pw",
        )
        from sv_common.auth.jwt import create_access_token
        token = create_access_token(
            user_id=user.id, member_id=player.id, rank_level=4
        )
        resp = await client.get(
            "/api/v1/admin/ranks", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
