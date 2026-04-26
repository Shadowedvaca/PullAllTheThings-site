"""
Writes Blizzard API and addon data into the guild_identity PostgreSQL schema.

Uses the Phase 2.7 schema: class_id/active_spec_id/guild_rank_id FKs on wow_characters.
"""

import logging
from datetime import datetime, timezone

import asyncpg

from .blizzard_client import CharacterProfileData

logger = logging.getLogger(__name__)


async def _build_rank_index_map(conn: asyncpg.Connection) -> dict[int, int]:
    """Return {wow_rank_index: guild_rank_id} from the guild_ranks table."""
    rows = await conn.fetch(
        "SELECT id, wow_rank_index FROM common.guild_ranks WHERE wow_rank_index IS NOT NULL"
    )
    return {row["wow_rank_index"]: row["id"] for row in rows}


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

            # Build WoW rank index → guild_rank_id map from DB (replaces RANK_NAME_MAP)
            rank_index_map = await _build_rank_index_map(conn)

            for char in characters:
                # --- Resolve existing row: stable ID first, name+realm fallback ---
                existing = None
                renamed_from = None

                if char.blizzard_character_id:
                    existing = await conn.fetchrow(
                        """SELECT id, character_name, realm_slug, removed_at
                           FROM guild_identity.wow_characters
                           WHERE blizzard_character_id = $1""",
                        char.blizzard_character_id,
                    )
                    if existing and existing["character_name"].lower() != char.character_name.lower():
                        # Rename detected: same stable ID, different name
                        renamed_from = existing["character_name"]

                if existing is None:
                    # Fall back to name+realm (handles rows that predate stable ID tracking)
                    existing = await conn.fetchrow(
                        """SELECT id, character_name, realm_slug, removed_at
                           FROM guild_identity.wow_characters
                           WHERE LOWER(character_name) = $1 AND LOWER(realm_slug) = $2""",
                        char.character_name.lower(), char.realm_slug.lower(),
                    )

                # Resolve class_id and active_spec_id from reference tables
                class_row = await conn.fetchrow(
                    "SELECT id FROM ref.classes WHERE LOWER(name) = LOWER($1)",
                    char.character_class or "",
                )
                class_id = class_row["id"] if class_row else None

                spec_id = None
                if class_id and char.active_spec:
                    spec_row = await conn.fetchrow(
                        """SELECT id FROM ref.specializations
                           WHERE class_id = $1 AND LOWER(name) = LOWER($2)""",
                        class_id, char.active_spec,
                    )
                    spec_id = spec_row["id"] if spec_row else None

                guild_rank_id = rank_index_map.get(char.guild_rank)
                if guild_rank_id is None and char.guild_rank is not None:
                    logger.warning(
                        "No guild_rank mapping for WoW rank index %d — "
                        "set wow_rank_index on the matching rank in Reference Tables",
                        char.guild_rank,
                    )

                if existing:
                    if renamed_from:
                        # Record old name in history before updating
                        await conn.execute(
                            """INSERT INTO guild_identity.character_name_history
                               (wow_character_id, character_name, realm_slug)
                               VALUES ($1, $2, $3)""",
                            existing["id"], renamed_from, existing["realm_slug"],
                        )
                        logger.info(
                            "Character rename detected: %s → %s (blizzard_id=%s)",
                            renamed_from, char.character_name, char.blizzard_character_id,
                        )
                        # current_keys was built from new names, so update it to keep
                        # removal detection accurate (remove the old name key if it snuck in)
                        old_key = (renamed_from.lower(), existing["realm_slug"].lower())
                        current_keys.discard(old_key)

                    # If character_name or realm_slug is changing (rename or realm transfer),
                    # evict any row that already holds the target (name, realm) combination.
                    # Without this, the UPDATE below raises a unique constraint violation when
                    # a soft-deleted row from a prior stint exists with the same name+realm.
                    target_name_changed = char.character_name.lower() != existing["character_name"].lower()
                    target_realm_changed = char.realm_slug.lower() != existing["realm_slug"].lower()
                    if target_name_changed or target_realm_changed:
                        conflict = await conn.fetchrow(
                            """SELECT id, removed_at FROM guild_identity.wow_characters
                               WHERE LOWER(character_name) = $1 AND LOWER(realm_slug) = $2
                                 AND id != $3""",
                            char.character_name.lower(), char.realm_slug.lower(), existing["id"],
                        )
                        if conflict:
                            change_type = "rename" if target_name_changed else "realm transfer"
                            if conflict["removed_at"] is not None:
                                logger.info(
                                    "Evicting stale removed row id=%s (%s/%s) ahead of %s update",
                                    conflict["id"], char.character_name, char.realm_slug, change_type,
                                )
                            else:
                                logger.warning(
                                    "Evicting unexpected live duplicate row id=%s (%s/%s) "
                                    "ahead of %s; stable-ID row id=%s takes precedence",
                                    conflict["id"], char.character_name, char.realm_slug,
                                    change_type, existing["id"],
                                )
                            await conn.execute(
                                "DELETE FROM guild_identity.wow_characters WHERE id = $1",
                                conflict["id"],
                            )

                    await conn.execute(
                        """UPDATE guild_identity.wow_characters SET
                            character_name = $2,
                            realm_slug = $3,
                            blizzard_character_id = COALESCE($4, blizzard_character_id),
                            class_id = $5,
                            active_spec_id = $6,
                            level = $7,
                            item_level = $8,
                            guild_rank_id = $9,
                            last_login_timestamp = $10,
                            blizzard_last_sync = $11,
                            removed_at = NULL,
                            in_guild = TRUE,
                            realm_name = $12
                           WHERE id = $1""",
                        existing["id"],
                        char.character_name,
                        char.realm_slug,
                        char.blizzard_character_id,
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
                           (character_name, realm_slug, realm_name, blizzard_character_id,
                            class_id, active_spec_id, level, item_level, guild_rank_id,
                            last_login_timestamp, blizzard_last_sync, in_guild)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, TRUE)""",
                        char.character_name, char.realm_slug, char.realm_name,
                        char.blizzard_character_id,
                        class_id, spec_id, char.level, char.item_level,
                        guild_rank_id, char.last_login_timestamp, now,
                    )
                    logger.info(
                        "New guild member detected: %s (%s)",
                        char.character_name, char.character_class,
                    )
                    stats["new"] += 1

            # Mark characters as removed if they're no longer in the roster.
            # Only consider in_guild=TRUE characters — BNet-discovered non-guild
            # characters (in_guild=FALSE) are not guild members and should never
            # be marked removed by the Blizzard roster sync.
            all_active = await conn.fetch(
                """SELECT id, character_name, realm_slug
                   FROM guild_identity.wow_characters
                   WHERE removed_at IS NULL AND in_guild = TRUE"""
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
    stats: dict = {"processed": 0, "updated": 0, "not_found": 0}

    async with pool.acquire() as conn:
        for char_data in addon_characters:
            name = char_data.get("name", "").strip()
            if not name:
                continue

            stats["processed"] += 1
            new_note = char_data.get("guild_note", "")

            row = await conn.fetchrow(
                """SELECT wc.id, wc.guild_note,
                          (SELECT player_id FROM guild_identity.player_characters
                           WHERE character_id = wc.id LIMIT 1) AS player_id
                   FROM guild_identity.wow_characters wc
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
                    new_note,
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
