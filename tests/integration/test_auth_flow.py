"""Integration tests for the full authentication flow.

Requires TEST_DATABASE_URL pointing to a running PostgreSQL instance.
All tests skip gracefully if the database is unavailable.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildMember, GuildRank, InviteCode, User
from sv_common.auth.passwords import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_rank(db: AsyncSession, *, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _create_member(
    db: AsyncSession, *, discord_username: str, rank_id: int, discord_id: str = "000000000000000001"
) -> GuildMember:
    member = GuildMember(
        discord_username=discord_username,
        display_name=discord_username,
        discord_id=discord_id,
        rank_id=rank_id,
    )
    db.add(member)
    await db.flush()
    return member


async def _create_registered_member(
    db: AsyncSession, *, discord_username: str, rank_id: int, password: str, discord_id: str = "000000000000000002"
):
    """Create a member with a linked user account (already registered)."""
    user = User(password_hash=hash_password(password))
    db.add(user)
    await db.flush()

    from datetime import datetime, timezone
    member = GuildMember(
        discord_username=discord_username,
        display_name=discord_username,
        discord_id=discord_id,
        rank_id=rank_id,
        user_id=user.id,
        registered_at=datetime.now(timezone.utc),
    )
    db.add(member)
    await db.flush()
    return member, user


async def _create_invite(
    db: AsyncSession,
    *,
    member_id: int,
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
        member_id=member_id,
        created_by=created_by_id,
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
        member = await _create_member(
            db_session, discord_username="test_reg_user", rank_id=rank.id, discord_id="100000000000000001"
        )
        code = await _create_invite(
            db_session, member_id=member.id, created_by_id=member.id
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
        assert me["discord_username"] == "test_reg_user"

    async def test_register_with_invalid_code_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_inv", level=2)
        await _create_member(
            db_session, discord_username="user_invalid_code", rank_id=rank.id, discord_id="100000000000000002"
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": "ZZZZZZZZ", "discord_username": "user_invalid_code", "password": "pw"},
        )
        assert resp.status_code == 400

    async def test_register_with_expired_code_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_exp", level=2)
        member = await _create_member(
            db_session, discord_username="user_expired_code", rank_id=rank.id, discord_id="100000000000000003"
        )
        code = await _create_invite(
            db_session, member_id=member.id, created_by_id=member.id, expired=True
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": code, "discord_username": "user_expired_code", "password": "pw"},
        )
        assert resp.status_code == 400

    async def test_register_with_wrong_username_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Code was generated for member A; user registers claiming to be member B."""
        rank = await _create_rank(db_session, name="Member_wrong", level=2)
        member = await _create_member(
            db_session, discord_username="real_user", rank_id=rank.id, discord_id="100000000000000004"
        )
        code = await _create_invite(
            db_session, member_id=member.id, created_by_id=member.id
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"code": code, "discord_username": "wrong_user", "password": "pw"},
        )
        assert resp.status_code == 400

    async def test_register_already_registered_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_rereg", level=2)
        member, _ = await _create_registered_member(
            db_session, discord_username="already_regged", rank_id=rank.id, password="pw", discord_id="100000000000000005"
        )
        code = await _create_invite(
            db_session, member_id=member.id, created_by_id=member.id
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
        await _create_registered_member(
            db_session,
            discord_username="login_user",
            rank_id=rank.id,
            password="correct_pw",
            discord_id="100000000000000006",
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
        await _create_registered_member(
            db_session,
            discord_username="badpw_user",
            rank_id=rank.id,
            password="real_pw",
            discord_id="100000000000000007",
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"discord_username": "badpw_user", "password": "wrong_pw"},
        )
        assert resp.status_code == 401

    async def test_login_unregistered_member_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        rank = await _create_rank(db_session, name="Member_unreg", level=2)
        await _create_member(
            db_session, discord_username="unreg_user", rank_id=rank.id, discord_id="100000000000000008"
        )
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
        member, _ = await _create_registered_member(
            db_session,
            discord_username="low_rank_member",
            rank_id=rank.id,
            password="pw",
            discord_id="100000000000000009",
        )
        from sv_common.auth.jwt import create_access_token
        token = create_access_token(
            user_id=member.user_id, member_id=member.id, rank_level=2
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
        member, _ = await _create_registered_member(
            db_session,
            discord_username="officer_access",
            rank_id=rank.id,
            password="pw",
            discord_id="100000000000000010",
        )
        from sv_common.auth.jwt import create_access_token
        token = create_access_token(
            user_id=member.user_id, member_id=member.id, rank_level=4
        )
        resp = await client.get(
            "/api/v1/admin/ranks", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
