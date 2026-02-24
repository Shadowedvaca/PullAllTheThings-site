"""
Writes Blizzard API and addon data into the guild_identity PostgreSQL schema.

Uses the Phase 2.7 schema: class_id/active_spec_id/guild_rank_id FKs on wow_characters.
"""

import logging
from datetime import datetime, timezone

import asyncpg

from .blizzard_client import CharacterProfileData, RANK_NAME_MAP

logger = logging.getLogger(__name__)


async def sync_blizzard_roster(
    pool: asyncpg.Pool,
    characters: list[CharacterProfileData],
) -> dict:
    """Sync a full Blizzard API roster pull into the database.

    Returns stats dict: {found, updated, new, removed}
    """
    now = datetime.now(timezone.utc)
    stats = {"found": len(characters), "updated": 0, "new": 0, "removed": 0}

    current_keys = set()
    for char in characters:
        current_keys.add((char.character_name.lower(), char.realm_slug.lower()))

    async with pool.acquire() as conn:
        async with conn.transaction():

            for char in characters:
                existing = await conn.fetchrow(
                    """SELECT id, removed_at
                       FROM guild_identity.wow_characters
                       WHERE LOWER(character_name) = $1 AND LOWER(realm_slug) = $2""",
                    char.character_name.lower(), char.realm_slug.lower(),
                )

                # Resolve class_id and active_spec_id from reference tables
                class_row = await conn.fetchrow(
                    "SELECT id FROM guild_identity.classes WHERE LOWER(name) = LOWER($1)",
                    char.character_class or "",
                )
                class_id = class_row["id"] if class_row else None

                spec_id = None
                if class_id and char.active_spec:
                    spec_row = await conn.fetchrow(
                        """SELECT id FROM guild_identity.specializations
                           WHERE class_id = $1 AND LOWER(name) = LOWER($2)""",
                        class_id, char.active_spec,
                    )
                    spec_id = spec_row["id"] if spec_row else None

                rank_row = await conn.fetchrow(
                    "SELECT id FROM common.guild_ranks WHERE level = $1",
                    char.guild_rank,
                )
                guild_rank_id = rank_row["id"] if rank_row else None

                if existing:
                    await conn.execute(
                        """UPDATE guild_identity.wow_characters SET
                            class_id = $2,
                            active_spec_id = $3,
                            level = $4,
                            item_level = $5,
                            guild_rank_id = $6,
                            last_login_timestamp = $7,
                            blizzard_last_sync = $8,
                            removed_at = NULL,
                            realm_name = $9
                           WHERE id = $1""",
                        existing["id"],
                        class_id,
                        spec_id,
                        char.level,
                        char.item_level,
                        guild_rank_id,
                        char.last_login_timestamp,
                        now,
                        char.realm_name,
                    )

                    if existing["removed_at"] is not None:
                        logger.info(
                            "Character %s has returned to the guild", char.character_name
                        )

                    stats["updated"] += 1
                else:
                    await conn.execute(
                        """INSERT INTO guild_identity.wow_characters
                           (character_name, realm_slug, realm_name, class_id,
                            active_spec_id, level, item_level, guild_rank_id,
                            last_login_timestamp, blizzard_last_sync)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                        char.character_name, char.realm_slug, char.realm_name,
                        class_id, spec_id, char.level, char.item_level,
                        guild_rank_id, char.last_login_timestamp, now,
                    )
                    logger.info(
                        "New guild member detected: %s (%s)",
                        char.character_name, char.character_class,
                    )
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
    """Sync data from the WoW addon upload (guild notes, officer notes)."""
    now = datetime.now(timezone.utc)
    stats = {"processed": 0, "updated": 0, "not_found": 0}

    async with pool.acquire() as conn:
        for char_data in addon_characters:
            name = char_data.get("name", "").strip()
            if not name:
                continue

            stats["processed"] += 1

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
