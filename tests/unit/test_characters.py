"""Unit tests for sv_common.identity.characters service functions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildMember, GuildRank
from sv_common.identity import characters as char_service
from sv_common.identity.characters import build_armory_url


# ---------------------------------------------------------------------------
# build_armory_url (pure function — no DB needed)
# ---------------------------------------------------------------------------


def test_build_armory_url_basic():
    url = build_armory_url("Trogmoon", "Stormrage")
    assert url == "https://worldofwarcraft.blizzard.com/en-us/character/us/stormrage/trogmoon"


def test_build_armory_url_senjin_apostrophe_handling():
    url = build_armory_url("Trogmoon", "Sen'jin")
    assert "senjin" in url
    assert "'" not in url


def test_build_armory_url_realm_with_spaces():
    url = build_armory_url("Mychar", "Area 52")
    assert "area-52" in url


def test_build_armory_url_lowercase_name():
    url = build_armory_url("BIGNAME", "Stormrage")
    assert "/bigname" in url


# ---------------------------------------------------------------------------
# Validation (pure — no DB needed)
# ---------------------------------------------------------------------------


def test_invalid_role_rejected_sync():
    """Validate that VALID_ROLES does not include garbage."""
    from sv_common.identity.characters import VALID_ROLES

    assert "tank" in VALID_ROLES
    assert "healer" in VALID_ROLES
    assert "melee_dps" in VALID_ROLES
    assert "ranged_dps" in VALID_ROLES
    assert "wizard" not in VALID_ROLES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_member(db: AsyncSession) -> GuildMember:
    rank = GuildRank(name=f"Member_char", level=2)
    db.add(rank)
    await db.flush()
    member = GuildMember(discord_username="char_owner", rank_id=rank.id)
    db.add(member)
    await db.flush()
    return member


# ---------------------------------------------------------------------------
# create_character
# ---------------------------------------------------------------------------


async def test_create_character_builds_armory_url(db_session: AsyncSession):
    member = await _setup_member(db_session)

    char = await char_service.create_character(
        db_session,
        member_id=member.id,
        name="Trogmoon",
        realm="Stormrage",
        wow_class="Druid",
        spec="Balance",
        role="ranged_dps",
    )

    assert char.armory_url is not None
    assert "stormrage" in char.armory_url
    assert "trogmoon" in char.armory_url


async def test_create_character_senjin_apostrophe_handling(db_session: AsyncSession):
    member = await _setup_member(db_session)

    char = await char_service.create_character(
        db_session,
        member_id=member.id,
        name="Trogmoon",
        realm="Sen'jin",
        wow_class="Druid",
    )

    assert "senjin" in char.armory_url
    assert "'" not in char.armory_url


async def test_get_main_character(db_session: AsyncSession):
    member = await _setup_member(db_session)

    await char_service.create_character(
        db_session,
        member_id=member.id,
        name="MainChar",
        realm="Stormrage",
        wow_class="Druid",
        main_alt="main",
    )
    await char_service.create_character(
        db_session,
        member_id=member.id,
        name="AltChar",
        realm="Stormrage",
        wow_class="Warrior",
        main_alt="alt",
    )

    main = await char_service.get_main_character(db_session, member.id)

    assert main is not None
    assert main.name == "MainChar"
    assert main.main_alt == "main"


async def test_invalid_role_rejected(db_session: AsyncSession):
    member = await _setup_member(db_session)

    with pytest.raises(ValueError, match="Invalid role"):
        await char_service.create_character(
            db_session,
            member_id=member.id,
            name="BadRole",
            realm="Stormrage",
            wow_class="Druid",
            role="wizard",
        )


async def test_invalid_main_alt_rejected(db_session: AsyncSession):
    member = await _setup_member(db_session)

    with pytest.raises(ValueError, match="Invalid main_alt"):
        await char_service.create_character(
            db_session,
            member_id=member.id,
            name="BadAlt",
            realm="Stormrage",
            wow_class="Druid",
            main_alt="primary",
        )


async def test_duplicate_name_realm_rejected(db_session: AsyncSession):
    member = await _setup_member(db_session)

    await char_service.create_character(
        db_session,
        member_id=member.id,
        name="Dupchar",
        realm="Stormrage",
        wow_class="Druid",
    )

    with pytest.raises(ValueError, match="already exists"):
        await char_service.create_character(
            db_session,
            member_id=member.id,
            name="Dupchar",
            realm="Stormrage",
            wow_class="Warrior",
        )
