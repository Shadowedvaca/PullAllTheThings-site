"""
Writes Blizzard API and addon data into the guild_identity PostgreSQL schema.

Handles:
- Upsert of character data (new characters, updated specs/levels, departures)
- Tracking of who left the guild vs. who's still present
- Marking characters as removed when they disappear from the roster
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from .blizzard_client import CharacterProfileData, RANK_NAME_MAP
from .migration import get_role_category

logger = logging.getLogger(__name__)


async def sync_blizzard_roster(
    pool: asyncpg.Pool,
    characters: list[CharacterProfileData],
) -> dict:
    """
    Sync a full Blizzard API roster pull into the database.

    Returns stats dict: {found, updated, new, removed}
    """
    now = datetime.now(timezone.utc)
    stats = {"found": len(characters), "updated": 0, "new": 0, "removed": 0}

    # Build set of current character keys
    current_keys = set()
    for char in characters:
        current_keys.add((char.character_name.lower(), char.realm_slug.lower()))

    async with pool.acquire() as conn:
        async with conn.transaction():

            # Upsert each character
            for char in characters:
                role_cat = get_role_category(
                    char.character_class,
                    char.active_spec or "",
                    "",
                )

                existing = await conn.fetchrow(
                    """SELECT id, active_spec, level, item_level, guild_rank, removed_at
                       FROM guild_identity.wow_characters
                       WHERE LOWER(character_name) = $1 AND LOWER(realm_slug) = $2""",
                    char.character_name.lower(), char.realm_slug.lower(),
                )

                if existing:
                    # Update existing character
                    await conn.execute(
                        """UPDATE guild_identity.wow_characters SET
                            character_class = $2,
                            active_spec = $3,
                            level = $4,
                            item_level = $5,
                            guild_rank = $6,
                            guild_rank_name = $7,
                            last_login_timestamp = $8,
                            role_category = $9,
                            blizzard_last_sync = $10,
                            removed_at = NULL,
                            realm_name = $11
                           WHERE id = $1""",
                        existing["id"],
                        char.character_class,
                        char.active_spec,
                        char.level,
                        char.item_level,
                        char.guild_rank,
                        RANK_NAME_MAP.get(char.guild_rank, f"Rank {char.guild_rank}"),
                        char.last_login_timestamp,
                        role_cat,
                        now,
                        char.realm_name,
                    )

                    if existing["removed_at"] is not None:
                        logger.info("Character %s has returned to the guild", char.character_name)

                    stats["updated"] += 1
                else:
                    # New character
                    await conn.execute(
                        """INSERT INTO guild_identity.wow_characters
                           (character_name, realm_slug, realm_name, character_class,
                            active_spec, level, item_level, guild_rank, guild_rank_name,
                            last_login_timestamp, role_category, blizzard_last_sync)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                        char.character_name, char.realm_slug, char.realm_name,
                        char.character_class, char.active_spec, char.level,
                        char.item_level, char.guild_rank,
                        RANK_NAME_MAP.get(char.guild_rank, f"Rank {char.guild_rank}"),
                        char.last_login_timestamp, role_cat, now,
                    )
                    logger.info("New guild member detected: %s (%s)", char.character_name, char.character_class)
                    stats["new"] += 1

            # Mark characters as removed if they're no longer in the roster
            all_active = await conn.fetch(
                """SELECT id, character_name, realm_slug
                   FROM guild_identity.wow_characters
                   WHERE removed_at IS NULL"""
            )

            for row in all_active:
                key = (row["character_name"].lower(), row["realm_slug"].lower())
                if key not in current_keys:
                    await conn.execute(
                        """UPDATE guild_identity.wow_characters
                           SET removed_at = $2
                           WHERE id = $1""",
                        row["id"], now,
                    )
                    logger.info("Character %s has left the guild", row["character_name"])
                    stats["removed"] += 1

    logger.info(
        "Blizzard sync stats: %d found, %d updated, %d new, %d removed",
        stats["found"], stats["updated"], stats["new"], stats["removed"],
    )
    return stats


async def sync_addon_data(
    pool: asyncpg.Pool,
    addon_characters: list[dict],
) -> dict:
    """
    Sync data from the WoW addon upload (guild notes, officer notes).

    addon_characters format:
    [
        {
            "name": "Trogmoon",
            "realm": "Sen'jin",
            "guild_note": "GM / Mike",
            "officer_note": "Discord: Trog",
            "rank": 0,
            "rank_name": "Guild Leader",
            "class": "Druid",
            "level": 80,
            "last_online": "0d 2h 15m",
        },
        ...
    ]
    """
    now = datetime.now(timezone.utc)
    stats = {"processed": 0, "updated": 0, "not_found": 0}

    async with pool.acquire() as conn:
        for char_data in addon_characters:
            name = char_data.get("name", "").strip()
            if not name:
                continue

            stats["processed"] += 1

            # Try to find the character in our DB
            # Use case-insensitive match since addon might have different casing
            row = await conn.fetchrow(
                """SELECT id FROM guild_identity.wow_characters
                   WHERE LOWER(character_name) = $1 AND removed_at IS NULL""",
                name.lower(),
            )

            if row:
                await conn.execute(
                    """UPDATE guild_identity.wow_characters SET
                        guild_note = $2,
                        officer_note = $3,
                        addon_last_sync = $4
                       WHERE id = $1""",
                    row["id"],
                    char_data.get("guild_note", ""),
                    char_data.get("officer_note", ""),
                    now,
                )
                stats["updated"] += 1
            else:
                logger.warning("Addon data for character '%s' not found in DB", name)
                stats["not_found"] += 1

    logger.info(
        "Addon sync stats: %d processed, %d updated, %d not found",
        stats["processed"], stats["updated"], stats["not_found"],
    )
    return stats
