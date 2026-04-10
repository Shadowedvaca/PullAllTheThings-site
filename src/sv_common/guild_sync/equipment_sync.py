"""Equipment sync — fetch full per-slot gear from Blizzard API and store it.

Follows the same batch pattern as progression_sync.py:
  - Load characters that have logged in since last_equipment_sync
  - Call BlizzardClient.get_character_equipment() for each
  - UPSERT into guild_identity.character_equipment
  - Stamp last_equipment_sync on the character row

Runs as a step inside run_blizzard_sync() (after progression sync).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from .blizzard_client import BlizzardClient, should_sync_character
from .gear_plan_auto_setup import auto_setup_gear_plan

logger = logging.getLogger(__name__)

# Batch size and delay between batches (mirror progression_sync.py)
_BATCH_SIZE = 10
_BATCH_DELAY = 0.5


async def load_characters_for_equipment_sync(
    pool: asyncpg.Pool,
    force_full: bool = False,
) -> tuple[list[dict], int]:
    """Return characters that need an equipment sync.

    Filters to characters that have logged in since their last equipment sync
    (or have never been synced) — same last-login-filtered approach as
    progression_sync.

    Returns (characters_to_sync, total_character_count).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, character_name, realm_slug,
                   last_login_timestamp, last_equipment_sync
              FROM guild_identity.wow_characters
             WHERE in_guild = TRUE
               AND removed_at IS NULL
            ORDER BY id
            """
        )

    total = len(rows)
    if force_full:
        return [dict(r) for r in rows], total

    to_sync = [
        dict(r) for r in rows
        if should_sync_character(
            r["last_login_timestamp"],
            r["last_equipment_sync"],
        )
    ]
    return to_sync, total


async def sync_equipment(
    pool: asyncpg.Pool,
    blizzard_client: BlizzardClient,
    characters: list[dict],
) -> dict:
    """Sync equipment for the supplied character list.

    Returns a stats dict: {synced, skipped, errors}.
    """
    synced = 0
    skipped = 0
    errors = 0
    gear_plan_errors = 0
    now = datetime.now(timezone.utc)

    # Build batches
    batches = [
        characters[i : i + _BATCH_SIZE]
        for i in range(0, len(characters), _BATCH_SIZE)
    ]

    for batch in batches:
        tasks = [
            _sync_one_character(pool, blizzard_client, char, now)
            for char in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for char, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.error(
                    "Equipment sync error for %s: %s",
                    char["character_name"], result, exc_info=result,
                )
                errors += 1
            elif result:
                synced += 1
                # Auto-setup a default gear plan for this character if one doesn't exist.
                # Runs after each successful equipment sync; no-op for existing plans.
                try:
                    await auto_setup_gear_plan(pool, char["id"])
                except Exception as plan_exc:
                    gear_plan_errors += 1
                    logger.error(
                        "Gear plan auto-setup failed for %s (id=%d): %s",
                        char["character_name"], char["id"], plan_exc, exc_info=plan_exc,
                    )
            else:
                skipped += 1

        if len(batches) > 1:
            await asyncio.sleep(_BATCH_DELAY)

    logger.info(
        "Equipment sync complete — synced: %d, skipped: %d, errors: %d, plan_setup_errors: %d",
        synced, skipped, errors, gear_plan_errors,
    )
    return {
        "equipment_synced": synced,
        "equipment_skipped": skipped,
        "equipment_errors": errors,
        "gear_plan_setup_errors": gear_plan_errors,
    }


async def _sync_one_character(
    pool: asyncpg.Pool,
    blizzard_client: BlizzardClient,
    char: dict,
    now: datetime,
) -> bool:
    """Sync equipment for a single character.  Returns True on success."""
    char_id = char["id"]
    char_name = char["character_name"]
    realm_slug = char["realm_slug"]

    slots = await blizzard_client.get_character_equipment(realm_slug, char_name)
    if slots is None:
        logger.debug("No equipment data for %s/%s", realm_slug, char_name)
        return False

    async with pool.acquire() as conn:
        async with conn.transaction():
            for slot_data in slots:
                # Stub wow_items row so icon enrichment can pick it up later.
                # ON CONFLICT DO NOTHING — never overwrite richer existing data.
                await conn.execute(
                    """
                    INSERT INTO guild_identity.wow_items
                        (blizzard_item_id, name, slot_type)
                    VALUES ($1, $2, 'other')
                    ON CONFLICT (blizzard_item_id) DO NOTHING
                    """,
                    slot_data.blizzard_item_id, slot_data.item_name,
                )

                # Resolve the wow_items PK so character_equipment.item_id is set.
                item_row = await conn.fetchrow(
                    "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
                    slot_data.blizzard_item_id,
                )
                wow_item_id = item_row["id"] if item_row else None

                await conn.execute(
                    """
                    INSERT INTO guild_identity.character_equipment
                        (character_id, slot, blizzard_item_id, item_id, item_name,
                         item_level, quality_track, bonus_ids, enchant_id,
                         gem_ids, synced_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (character_id, slot) DO UPDATE
                        SET blizzard_item_id = EXCLUDED.blizzard_item_id,
                            item_id          = EXCLUDED.item_id,
                            item_name        = EXCLUDED.item_name,
                            item_level       = EXCLUDED.item_level,
                            quality_track    = EXCLUDED.quality_track,
                            bonus_ids        = EXCLUDED.bonus_ids,
                            enchant_id       = EXCLUDED.enchant_id,
                            gem_ids          = EXCLUDED.gem_ids,
                            synced_at        = EXCLUDED.synced_at
                    """,
                    char_id,
                    slot_data.slot,
                    slot_data.blizzard_item_id,
                    wow_item_id,
                    slot_data.item_name,
                    slot_data.item_level,
                    slot_data.quality_track,
                    slot_data.bonus_ids,
                    slot_data.enchant_id,
                    slot_data.gem_ids,
                    now,
                )

            # Stamp last_equipment_sync
            await conn.execute(
                """
                UPDATE guild_identity.wow_characters
                   SET last_equipment_sync = $1
                 WHERE id = $2
                """,
                now, char_id,
            )

    return True
