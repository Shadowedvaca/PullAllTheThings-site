"""Unit contracts for Mike-approved revocable session policy in issue #58."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

from sv_common.auth.sessions import (
    SessionAuthenticationError,
    authenticate_session,
    issue_session_token,
    session_lifetime_minutes,
)
from sv_common.db.models import AuthSession, GuildRank, Player, User


def _auth_graph(*, rank_level: int = 3, active: bool = True, revoked: bool = False):
    rank = GuildRank(id=rank_level, name=f"Rank {rank_level}", level=rank_level)
    user = User(
        id=10,
        email="synthetic@test.invalid",
        password_hash="not-a-real-password-hash",
        is_active=active,
    )
    player = Player(
        id=20,
        display_name="Synthetic Member",
        website_user_id=user.id,
        guild_rank_id=rank.id,
    )
    player.guild_rank = rank
    user.player = player
    now = datetime.now(timezone.utc)
    session = AuthSession(
        id="11111111-1111-1111-1111-111111111111",
        user_id=user.id,
        rank_level_at_issue=rank_level,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        revoked_at=now if revoked else None,
        revoked_reason="logout" if revoked else None,
    )
    session.user = user
    return user, player, session


def _db_returning(value) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


def test_approved_absolute_lifetimes_and_privilege_boundary():
    assert session_lifetime_minutes(1) == 10080
    assert session_lifetime_minutes(3) == 10080
    assert session_lifetime_minutes(4) == 720
    assert session_lifetime_minutes(5) == 720


@pytest.mark.asyncio
async def test_issue_session_persists_jti_and_matching_member_expiry():
    db = _db_returning(None)
    issued = await issue_session_token(db, user_id=10, member_id=20, rank_level=3)

    persisted = db.add.call_args.args[0]
    payload = jwt.decode(
        issued.token,
        options={"verify_signature": False},
        algorithms=["HS256"],
    )
    assert persisted.id == issued.session_id == payload["jti"]
    assert persisted.user_id == payload["user_id"] == 10
    assert issued.max_age_seconds == 7 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_authenticate_session_accepts_active_current_identity():
    from sv_common.auth.jwt import create_access_token

    user, player, session = _auth_graph()
    token = create_access_token(
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
        expires_minutes=60,
        session_id=session.id,
    )
    authenticated = await authenticate_session(token, _db_returning(session))
    assert authenticated.user is user
    assert authenticated.player is player


@pytest.mark.asyncio
@pytest.mark.parametrize("active,revoked", [(False, False), (True, True)])
async def test_authenticate_session_fails_closed_for_inactive_or_revoked(
    active: bool, revoked: bool
):
    from sv_common.auth.jwt import create_access_token

    user, player, session = _auth_graph(active=active, revoked=revoked)
    token = create_access_token(
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
        expires_minutes=60,
        session_id=session.id,
    )
    with pytest.raises(SessionAuthenticationError):
        await authenticate_session(token, _db_returning(session))


@pytest.mark.asyncio
async def test_privilege_change_revokes_session_and_requires_login():
    from sv_common.auth.jwt import create_access_token

    user, player, session = _auth_graph(rank_level=3)
    player.guild_rank.level = 4
    token = create_access_token(
        user_id=user.id,
        member_id=player.id,
        rank_level=3,
        expires_minutes=60,
        session_id=session.id,
    )
    db = _db_returning(session)
    with pytest.raises(SessionAuthenticationError, match="Privilege changed"):
        await authenticate_session(token, db)
    assert db.execute.await_count == 2


def test_auth_session_migration_has_defense_in_depth_revocation_triggers():
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0182_auth_sessions.py"
    ).read_text(encoding="utf-8")
    assert "trg_users_revoke_auth_sessions" in migration
    assert "trg_players_revoke_auth_sessions" in migration
    assert "password_change" in migration
    assert "account_deactivated" in migration
    assert "privilege_change" in migration
