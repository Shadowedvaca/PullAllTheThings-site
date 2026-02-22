"""Unit tests for sv_common.identity.ranks service functions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildMember, GuildRank
from sv_common.identity import ranks as rank_service


async def _make_rank(db: AsyncSession, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _make_member(db: AsyncSession, rank_id: int, username: str) -> GuildMember:
    member = GuildMember(discord_username=username, rank_id=rank_id)
    db.add(member)
    await db.flush()
    return member


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


async def test_get_all_ranks_returns_seeded_data(db_session: AsyncSession):
    await _make_rank(db_session, "Initiate", 1)
    await _make_rank(db_session, "Member", 2)
    await _make_rank(db_session, "Veteran", 3)

    result = await rank_service.get_all_ranks(db_session)

    assert len(result) == 3
    assert [r.level for r in result] == [1, 2, 3]


async def test_get_rank_by_level_found(db_session: AsyncSession):
    await _make_rank(db_session, "Officer", 4)

    rank = await rank_service.get_rank_by_level(db_session, 4)

    assert rank is not None
    assert rank.name == "Officer"


async def test_get_rank_by_level_not_found(db_session: AsyncSession):
    rank = await rank_service.get_rank_by_level(db_session, 99)
    assert rank is None


async def test_create_rank_with_all_fields(db_session: AsyncSession):
    rank = await rank_service.create_rank(
        db_session,
        name="Legend",
        level=10,
        description="The best",
        discord_role_id="123456789",
    )

    assert rank.id is not None
    assert rank.name == "Legend"
    assert rank.level == 10
    assert rank.description == "The best"
    assert rank.discord_role_id == "123456789"


async def test_create_rank_duplicate_level_rejected(db_session: AsyncSession):
    await rank_service.create_rank(db_session, name="Alpha", level=20)

    with pytest.raises(ValueError, match="already exists"):
        await rank_service.create_rank(db_session, name="Beta", level=20)


# ---------------------------------------------------------------------------
# member_meets_rank_requirement
# ---------------------------------------------------------------------------


async def test_member_meets_rank_veteran_at_veteran_level(db_session: AsyncSession):
    veteran_rank = await _make_rank(db_session, "Veteran_mrv", 3)
    member = await _make_member(db_session, veteran_rank.id, "vet_user_mrv")

    result = await rank_service.member_meets_rank_requirement(
        db_session, member.id, required_level=3
    )

    assert result is True


async def test_member_meets_rank_initiate_at_veteran_level(db_session: AsyncSession):
    initiate_rank = await _make_rank(db_session, "Initiate_mri", 1)
    member = await _make_member(db_session, initiate_rank.id, "init_user_mri")

    result = await rank_service.member_meets_rank_requirement(
        db_session, member.id, required_level=3
    )

    assert result is False


async def test_member_meets_rank_officer_at_veteran_level(db_session: AsyncSession):
    officer_rank = await _make_rank(db_session, "Officer_mro", 4)
    member = await _make_member(db_session, officer_rank.id, "off_user_mro")

    result = await rank_service.member_meets_rank_requirement(
        db_session, member.id, required_level=3
    )

    assert result is True


async def test_member_meets_rank_nonexistent_member(db_session: AsyncSession):
    result = await rank_service.member_meets_rank_requirement(
        db_session, member_id=999999, required_level=1
    )
    assert result is False
