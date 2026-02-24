"""WoW character service functions.

Operates on guild_identity.wow_characters and guild_identity.player_characters.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import PlayerCharacter, WowCharacter


def build_armory_url(name: str, realm: str) -> str:
    """Build Blizzard armory URL."""
    clean_realm = realm.lower().replace("'", "").replace(" ", "-")
    return (
        f"https://worldofwarcraft.blizzard.com/en-us/character/us"
        f"/{clean_realm}/{name.lower()}"
    )


async def get_characters_for_player(
    db: AsyncSession, player_id: int
) -> list[WowCharacter]:
    """Return all WoW characters owned by a player (via player_characters bridge)."""
    result = await db.execute(
        select(WowCharacter)
        .join(PlayerCharacter, PlayerCharacter.character_id == WowCharacter.id)
        .where(PlayerCharacter.player_id == player_id)
        .order_by(WowCharacter.character_name)
    )
    return list(result.scalars().all())


async def get_player_for_character(
    db: AsyncSession, character_id: int
) -> int | None:
    """Return the player_id that owns this character, or None."""
    result = await db.execute(
        select(PlayerCharacter.player_id).where(
            PlayerCharacter.character_id == character_id
        )
    )
    row = result.scalar_one_or_none()
    return row


async def link_character_to_player(
    db: AsyncSession, player_id: int, character_id: int
) -> PlayerCharacter:
    """Create a player_characters bridge row (link character to player)."""
    bridge = PlayerCharacter(player_id=player_id, character_id=character_id)
    db.add(bridge)
    await db.flush()
    await db.refresh(bridge)
    return bridge


async def unlink_character_from_player(
    db: AsyncSession, character_id: int
) -> bool:
    """Remove the player_characters link for this character."""
    result = await db.execute(
        select(PlayerCharacter).where(PlayerCharacter.character_id == character_id)
    )
    bridge = result.scalar_one_or_none()
    if bridge is None:
        return False
    await db.delete(bridge)
    await db.flush()
    return True


async def get_wow_character_by_id(
    db: AsyncSession, character_id: int
) -> WowCharacter | None:
    result = await db.execute(
        select(WowCharacter).where(WowCharacter.id == character_id)
    )
    return result.scalar_one_or_none()


async def get_wow_character_by_name(
    db: AsyncSession, character_name: str, realm_slug: str
) -> WowCharacter | None:
    result = await db.execute(
        select(WowCharacter).where(
            WowCharacter.character_name.ilike(character_name),
            WowCharacter.realm_slug.ilike(realm_slug),
        )
    )
    return result.scalar_one_or_none()
