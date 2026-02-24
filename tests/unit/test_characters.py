"""Unit tests for sv_common.identity.characters service functions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildRank, Player, WowCharacter
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
# Helpers
# ---------------------------------------------------------------------------


async def _make_rank(db: AsyncSession, name: str, level: int) -> GuildRank:
    rank = GuildRank(name=name, level=level)
    db.add(rank)
    await db.flush()
    return rank


async def _make_player(db: AsyncSession, rank_id: int, name: str) -> Player:
    player = Player(display_name=name, guild_rank_id=rank_id)
    db.add(player)
    await db.flush()
    return player


async def _make_wow_char(db: AsyncSession, char_name: str, realm: str = "senjin") -> WowCharacter:
    char = WowCharacter(character_name=char_name, realm_slug=realm)
    db.add(char)
    await db.flush()
    return char


# ---------------------------------------------------------------------------
# link / unlink / get operations
# ---------------------------------------------------------------------------


async def test_link_character_to_player(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_lcp", 2)
    player = await _make_player(db_session, rank.id, "LinkPlayer")
    char = await _make_wow_char(db_session, "LinkChar_lcp")

    bridge = await char_service.link_character_to_player(db_session, player.id, char.id)

    assert bridge.player_id == player.id
    assert bridge.character_id == char.id


async def test_get_characters_for_player(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_gcfp", 2)
    player = await _make_player(db_session, rank.id, "MultiPlayer_gcfp")
    char1 = await _make_wow_char(db_session, "CharA_gcfp")
    char2 = await _make_wow_char(db_session, "CharB_gcfp")

    await char_service.link_character_to_player(db_session, player.id, char1.id)
    await char_service.link_character_to_player(db_session, player.id, char2.id)

    chars = await char_service.get_characters_for_player(db_session, player.id)
    names = [c.character_name for c in chars]
    assert "CharA_gcfp" in names
    assert "CharB_gcfp" in names


async def test_get_characters_for_player_empty(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_gcfpe", 2)
    player = await _make_player(db_session, rank.id, "EmptyPlayer_gcfpe")

    chars = await char_service.get_characters_for_player(db_session, player.id)
    assert chars == []


async def test_get_player_for_character(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_gpfc", 2)
    player = await _make_player(db_session, rank.id, "OwnerPlayer_gpfc")
    char = await _make_wow_char(db_session, "OwnedChar_gpfc")

    await char_service.link_character_to_player(db_session, player.id, char.id)

    found_player_id = await char_service.get_player_for_character(db_session, char.id)
    assert found_player_id == player.id


async def test_get_player_for_character_not_linked(db_session: AsyncSession):
    char = await _make_wow_char(db_session, "UnlinkedChar_gpfc")

    found = await char_service.get_player_for_character(db_session, char.id)
    assert found is None


async def test_unlink_character_from_player(db_session: AsyncSession):
    rank = await _make_rank(db_session, "Member_ucfp", 2)
    player = await _make_player(db_session, rank.id, "UnlinkPlayer_ucfp")
    char = await _make_wow_char(db_session, "UnlinkChar_ucfp")

    await char_service.link_character_to_player(db_session, player.id, char.id)

    removed = await char_service.unlink_character_from_player(db_session, char.id)
    assert removed is True

    found = await char_service.get_player_for_character(db_session, char.id)
    assert found is None


async def test_unlink_character_not_linked_returns_false(db_session: AsyncSession):
    char = await _make_wow_char(db_session, "FreeChar_ucfp")

    removed = await char_service.unlink_character_from_player(db_session, char.id)
    assert removed is False


async def test_get_wow_character_by_name(db_session: AsyncSession):
    await _make_wow_char(db_session, "Trogmoon_gwcbn", "senjin")

    found = await char_service.get_wow_character_by_name(db_session, "Trogmoon_gwcbn", "senjin")
    assert found is not None
    assert found.character_name == "Trogmoon_gwcbn"


async def test_get_wow_character_by_name_not_found(db_session: AsyncSession):
    found = await char_service.get_wow_character_by_name(db_session, "GhostChar_nf", "senjin")
    assert found is None


async def test_get_wow_character_by_id(db_session: AsyncSession):
    char = await _make_wow_char(db_session, "IdChar_gwcbi", "senjin")

    found = await char_service.get_wow_character_by_id(db_session, char.id)
    assert found is not None
    assert found.id == char.id


async def test_character_unique_per_player(db_session: AsyncSession):
    """A character can only be linked to one player at a time (UNIQUE on character_id)."""
    from sqlalchemy.exc import IntegrityError

    rank = await _make_rank(db_session, "Member_cupp", 2)
    player1 = await _make_player(db_session, rank.id, "Player1_cupp")
    player2 = await _make_player(db_session, rank.id, "Player2_cupp")
    char = await _make_wow_char(db_session, "SharedChar_cupp")

    await char_service.link_character_to_player(db_session, player1.id, char.id)

    with pytest.raises(IntegrityError):
        await char_service.link_character_to_player(db_session, player2.id, char.id)
