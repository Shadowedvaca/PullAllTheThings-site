"""Unit tests for sv_common.identity.members (player) service functions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildRank, Player, User
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
# Create player
# ---------------------------------------------------------------------------


async def test_create_player_default_rank(db_session: AsyncSession):
    initiate = await _make_rank(db_session, "Initiate_cp", 1)

    player = await member_service.create_player(db_session, display_name="NewPlayer")

    assert player.id is not None
    assert player.display_name == "NewPlayer"
    assert player.guild_rank_id == initiate.id


async def test_create_player_explicit_rank(db_session: AsyncSession):
    veteran = await _make_rank(db_session, "Veteran_cpe", 3)

    player = await member_service.create_player(
        db_session,
        display_name="VetPlayer",
        guild_rank_id=veteran.id,
    )

    assert player.guild_rank_id == veteran.id
    assert player.display_name == "VetPlayer"


async def test_create_player_no_initiate_rank_raises(db_session: AsyncSession):
    with pytest.raises(ValueError, match="Initiate rank"):
        await member_service.create_player(db_session, display_name="orphan_player")


# ---------------------------------------------------------------------------
# Fetch players
# ---------------------------------------------------------------------------


async def test_get_player_by_id(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_gpid", 2)
    player = Player(display_name="FindMe", guild_rank_id=rank.id)
    db_session.add(player)
    await db_session.flush()

    found = await member_service.get_player_by_id(db_session, player.id)

    assert found is not None
    assert found.display_name == "FindMe"


async def test_get_player_by_id_not_found(db_session: AsyncSession):
    found = await member_service.get_player_by_id(db_session, 99999)
    assert found is None


async def test_get_player_by_user_id(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_gpuid", 2)
    user = await _make_user(db_session, "linked@example.com")
    player = Player(
        display_name="LinkedPlayer",
        guild_rank_id=rank.id,
        website_user_id=user.id,
    )
    db_session.add(player)
    await db_session.flush()

    found = await member_service.get_player_by_user_id(db_session, user.id)

    assert found is not None
    assert found.display_name == "LinkedPlayer"


# ---------------------------------------------------------------------------
# get_eligible_voters
# ---------------------------------------------------------------------------


async def test_get_eligible_voters_excludes_unregistered(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Veteran_gev", 3)
    user = await _make_user(db_session, "registered_gev@example.com")

    registered = Player(
        display_name="Registered",
        guild_rank_id=rank.id,
        website_user_id=user.id,
    )
    unregistered = Player(
        display_name="Unregistered",
        guild_rank_id=rank.id,
    )
    db_session.add_all([registered, unregistered])
    await db_session.flush()

    voters = await member_service.get_eligible_voters(db_session, min_rank_level=3)

    names = [v.display_name for v in voters]
    assert "Registered" in names
    assert "Unregistered" not in names


async def test_get_eligible_voters_excludes_low_rank(db_session: AsyncSession):
    veteran_rank = await _make_rank(db_session, "Veteran_gel", 3)
    initiate_rank = await _make_rank(db_session, "Initiate_gel", 1)
    vet_user = await _make_user(db_session, "vet_gel@example.com")
    init_user = await _make_user(db_session, "init_gel@example.com")

    vet_player = Player(
        display_name="VetPlayer_gel",
        guild_rank_id=veteran_rank.id,
        website_user_id=vet_user.id,
    )
    init_player = Player(
        display_name="InitPlayer_gel",
        guild_rank_id=initiate_rank.id,
        website_user_id=init_user.id,
    )
    db_session.add_all([vet_player, init_player])
    await db_session.flush()

    voters = await member_service.get_eligible_voters(db_session, min_rank_level=3)

    names = [v.display_name for v in voters]
    assert "VetPlayer_gel" in names
    assert "InitPlayer_gel" not in names


# ---------------------------------------------------------------------------
# get_players_by_min_rank
# ---------------------------------------------------------------------------


async def test_get_players_by_min_rank(db_session: AsyncSession):
    initiate_rank = await _make_rank(db_session, "Initiate_gmbr", 1)
    officer_rank = await _make_rank(db_session, "Officer_gmbr", 4)

    low = Player(display_name="LowPlayer_gmbr", guild_rank_id=initiate_rank.id)
    high = Player(display_name="HighPlayer_gmbr", guild_rank_id=officer_rank.id)
    db_session.add_all([low, high])
    await db_session.flush()

    result = await member_service.get_players_by_min_rank(db_session, min_level=4)

    names = [p.display_name for p in result]
    assert "HighPlayer_gmbr" in names
    assert "LowPlayer_gmbr" not in names


# ---------------------------------------------------------------------------
# link_user_to_player
# ---------------------------------------------------------------------------


async def test_link_user_to_player(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_lutp", 2)
    user = await _make_user(db_session, "tolink@example.com")
    player = Player(display_name="ToLink", guild_rank_id=rank.id)
    db_session.add(player)
    await db_session.flush()

    updated = await member_service.link_user_to_player(db_session, player.id, user.id)

    assert updated.website_user_id == user.id
