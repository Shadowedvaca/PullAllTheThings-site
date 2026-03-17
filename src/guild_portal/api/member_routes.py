"""Member-facing API routes — personal data for logged-in guild members."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from guild_portal.deps import get_current_player, get_db
from sv_common.db.models import (
    Player,
    PlayerCharacter,
    RaiderIOProfile,
    WowCharacter,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/me", tags=["member"])

# Class emoji mapping (matches public_pages.py)
_CLASS_EMOJIS: dict[str, str] = {
    "Druid": "🌿",
    "Paladin": "⚔️",
    "Warlock": "👁️",
    "Priest": "✨",
    "Mage": "🔮",
    "Hunter": "🏹",
    "Warrior": "⚔️",
    "Shaman": "⚡",
    "Monk": "☯️",
    "Death Knight": "💀",
    "Demon Hunter": "🦅",
    "Evoker": "🐉",
    "Rogue": "🗡️",
}


def _build_char_dict(
    pc: PlayerCharacter,
    player: Player,
    rio_by_char: dict[int, RaiderIOProfile],
) -> dict:
    """Build the character data dict for the API response."""
    char = pc.character
    class_name = char.wow_class.name if char.wow_class else None
    class_color = char.wow_class.color_hex if char.wow_class else None
    class_emoji = _CLASS_EMOJIS.get(class_name, "❓") if class_name else "❓"
    spec_name = char.active_spec.name if char.active_spec else None
    realm_slug = char.realm_slug
    realm_display = char.realm_name or realm_slug.replace("-", " ").title()
    char_name = char.character_name

    rio = rio_by_char.get(char.id)
    raiderio_url: str | None = None
    if rio:
        raiderio_url = rio.profile_url or (
            f"https://raider.io/characters/us/{realm_slug}/{char_name}"
        )

    last_synced_at: str | None = None
    if char.blizzard_last_sync:
        last_synced_at = char.blizzard_last_sync.isoformat()

    return {
        "id": char.id,
        "character_name": char_name,
        "realm_slug": realm_slug,
        "realm_display": realm_display,
        "class_name": class_name,
        "class_color": class_color,
        "class_emoji": class_emoji,
        "spec_name": spec_name,
        "avg_item_level": char.item_level,
        "last_login_ms": char.last_login_timestamp,
        "last_synced_at": last_synced_at,
        "is_main": char.id == player.main_character_id,
        "is_offspec": char.id == player.offspec_character_id,
        "link_source": pc.link_source,
        "armory_url": (
            f"https://worldofwarcraft.blizzard.com/en-us/character/us"
            f"/{realm_slug}/{char_name}"
        ),
        "raiderio_url": raiderio_url,
        "wcl_url": (
            f"https://www.warcraftlogs.com/character/us/{realm_slug}/{char_name}"
        ),
    }


def _pick_default_character_id(
    characters: list[dict],
    main_character_id: int | None,
    offspec_character_id: int | None,
) -> int | None:
    """Select the default character: main > offspec > first alphabetically."""
    if not characters:
        return None
    char_id_set = {c["id"] for c in characters}
    if main_character_id and main_character_id in char_id_set:
        return main_character_id
    if offspec_character_id and offspec_character_id in char_id_set:
        return offspec_character_id
    return characters[0]["id"]


@router.get("/characters")
async def get_my_characters(
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return all characters claimed by the current member with stat data."""
    # Load player characters with WoW class + spec relationships
    result = await db.execute(
        select(PlayerCharacter)
        .options(
            selectinload(PlayerCharacter.character).options(
                selectinload(WowCharacter.wow_class),
                selectinload(WowCharacter.active_spec),
            )
        )
        .where(PlayerCharacter.player_id == player.id)
    )
    player_chars = list(result.scalars().all())

    # Batch-load Raider.IO profiles for all claimed characters
    char_ids = [pc.character_id for pc in player_chars if pc.character]
    rio_by_char: dict[int, RaiderIOProfile] = {}
    if char_ids:
        rio_result = await db.execute(
            select(RaiderIOProfile).where(
                RaiderIOProfile.character_id.in_(char_ids),
                RaiderIOProfile.season == "current",
            )
        )
        for r in rio_result.scalars():
            rio_by_char[r.character_id] = r

    # Also reload player to get latest main/offspec IDs
    player_result = await db.execute(
        select(Player).where(Player.id == player.id)
    )
    fresh_player = player_result.scalar_one_or_none() or player

    # Build and sort character list
    characters: list[dict] = []
    for pc in player_chars:
        if not pc.character:
            continue
        characters.append(_build_char_dict(pc, fresh_player, rio_by_char))

    characters.sort(key=lambda c: f"{c['character_name']}-{c['realm_slug']}")

    default_id = _pick_default_character_id(
        characters,
        fresh_player.main_character_id,
        fresh_player.offspec_character_id,
    )

    return {
        "ok": True,
        "data": {
            "characters": characters,
            "default_character_id": default_id,
        },
    }
