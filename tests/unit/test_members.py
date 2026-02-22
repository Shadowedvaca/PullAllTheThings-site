"""Unit tests for sv_common.identity.members service functions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildMember, GuildRank, User
from sv_common.identity import members as member_service


async def _make_rank(db: AsyncSession, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _make_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="hashed")
    db.add(user)
    await db.flush()
    return user


# ---------------------------------------------------------------------------
# Create member
# ---------------------------------------------------------------------------


async def test_create_member_default_rank(db_session: AsyncSession):
    initiate = await _make_rank(db_session, "Initiate_cmd", 1)

    member = await member_service.create_member(
        db_session, discord_username="newbie_cmd"
    )

    assert member.id is not None
    assert member.discord_username == "newbie_cmd"
    assert member.rank_id == initiate.id


async def test_create_member_explicit_rank(db_session: AsyncSession):
    veteran = await _make_rank(db_session, "Veteran_cme", 3)

    member = await member_service.create_member(
        db_session,
        discord_username="vet_user_cme",
        display_name="Vet",
        rank_id=veteran.id,
    )

    assert member.rank_id == veteran.id
    assert member.display_name == "Vet"


async def test_create_member_no_initiate_rank_raises(db_session: AsyncSession):
    with pytest.raises(ValueError, match="Initiate rank"):
        await member_service.create_member(
            db_session, discord_username="orphan_user"
        )


# ---------------------------------------------------------------------------
# Fetch members
# ---------------------------------------------------------------------------


async def test_get_member_by_discord_username(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_gmu", 2)
    db_session.add(GuildMember(discord_username="findme_gmu", rank_id=rank.id))
    await db_session.flush()

    found = await member_service.get_member_by_discord_username(
        db_session, "findme_gmu"
    )

    assert found is not None
    assert found.discord_username == "findme_gmu"


async def test_get_member_by_discord_id(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_gmdi", 2)
    db_session.add(
        GuildMember(
            discord_username="discord_user_gmdi",
            discord_id="999888777666555444",
            rank_id=rank.id,
        )
    )
    await db_session.flush()

    found = await member_service.get_member_by_discord_id(
        db_session, "999888777666555444"
    )

    assert found is not None
    assert found.discord_id == "999888777666555444"


async def test_get_member_by_discord_id_not_found(db_session: AsyncSession):
    found = await member_service.get_member_by_discord_id(db_session, "0000000000")
    assert found is None


# ---------------------------------------------------------------------------
# get_eligible_voters
# ---------------------------------------------------------------------------


async def test_get_eligible_voters_excludes_unregistered(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Veteran_gev", 3)
    user = await _make_user(db_session, "registered@example.com")

    # Registered member
    registered = GuildMember(
        discord_username="registered_gev",
        rank_id=rank.id,
        user_id=user.id,
    )
    # Unregistered member
    unregistered = GuildMember(
        discord_username="unregistered_gev",
        rank_id=rank.id,
    )
    db_session.add_all([registered, unregistered])
    await db_session.flush()

    voters = await member_service.get_eligible_voters(db_session, min_rank_level=3)

    usernames = [v.discord_username for v in voters]
    assert "registered_gev" in usernames
    assert "unregistered_gev" not in usernames


async def test_get_eligible_voters_excludes_low_rank(db_session: AsyncSession):
    veteran_rank = await _make_rank(db_session, "Veteran_gel", 3)
    initiate_rank = await _make_rank(db_session, "Initiate_gel", 1)
    vet_user = await _make_user(db_session, "vet@example.com")
    init_user = await _make_user(db_session, "init@example.com")

    vet_member = GuildMember(
        discord_username="vet_member_gel",
        rank_id=veteran_rank.id,
        user_id=vet_user.id,
    )
    init_member = GuildMember(
        discord_username="init_member_gel",
        rank_id=initiate_rank.id,
        user_id=init_user.id,
    )
    db_session.add_all([vet_member, init_member])
    await db_session.flush()

    voters = await member_service.get_eligible_voters(db_session, min_rank_level=3)

    usernames = [v.discord_username for v in voters]
    assert "vet_member_gel" in usernames
    assert "init_member_gel" not in usernames


# ---------------------------------------------------------------------------
# get_members_by_min_rank
# ---------------------------------------------------------------------------


async def test_get_members_by_min_rank(db_session: AsyncSession):
    initiate_rank = await _make_rank(db_session, "Initiate_gmbr", 1)
    officer_rank = await _make_rank(db_session, "Officer_gmbr", 4)

    low = GuildMember(discord_username="low_user_gmbr", rank_id=initiate_rank.id)
    high = GuildMember(discord_username="high_user_gmbr", rank_id=officer_rank.id)
    db_session.add_all([low, high])
    await db_session.flush()

    result = await member_service.get_members_by_min_rank(db_session, min_level=4)

    usernames = [m.discord_username for m in result]
    assert "high_user_gmbr" in usernames
    assert "low_user_gmbr" not in usernames
