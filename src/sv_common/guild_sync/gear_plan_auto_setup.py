"""Auto-setup default Wowhead BIS gear plan for newly-discovered in-guild characters.

Called from equipment_sync.py after a successful per-character equipment sync.
Contains raw asyncpg SQL so sv_common has no dependency on guild_portal.services.
"""

import logging

import asyncpg

logger = logging.getLogger(__name__)

# Mirrors WOW_SLOTS in guild_portal/services/gear_plan_service.py
_WOW_SLOTS = frozenset([
    "head", "neck", "shoulder", "back", "chest", "wrist",
    "hands", "waist", "legs", "feet",
    "ring_1", "ring_2", "trinket_1", "trinket_2",
    "main_hand", "off_hand",
])


async def auto_setup_gear_plan(pool: asyncpg.Pool, character_id: int) -> bool:
    """Create a default Wowhead Overall BIS plan for a character if one does not exist.

    Steps:
      1. Resolve player_id via player_characters — skip if no link yet.
      2. Skip if gear_plan already exists for (player_id, character_id).
      3. Create gear_plan using the character's active_spec and Wowhead Overall source.
      4. Populate gear_plan_slots from bis_list_entries (unlocked, priority-ordered).

    Returns True if a new plan was created and populated, False otherwise.
    """
    async with pool.acquire() as conn:
        # Step 1: resolve player_id from player_characters bridge
        pc_row = await conn.fetchrow(
            """
            SELECT player_id
              FROM guild_identity.player_characters
             WHERE character_id = $1
             LIMIT 1
            """,
            character_id,
        )
        if not pc_row:
            logger.debug(
                "auto_setup_gear_plan: character %d has no player_characters link — skipping",
                character_id,
            )
            return False

        player_id = pc_row["player_id"]

        # Step 2: skip if plan already exists
        existing = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if existing:
            return False

        # Step 3: resolve spec and Wowhead Overall source
        char_row = await conn.fetchrow(
            "SELECT active_spec_id FROM guild_identity.wow_characters WHERE id=$1",
            character_id,
        )
        spec_id = char_row["active_spec_id"] if char_row else None

        src_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.bis_list_sources WHERE name = 'Wowhead Overall' LIMIT 1"
        )
        if not src_row:
            logger.warning(
                "auto_setup_gear_plan: 'Wowhead Overall' source not found — skipping character %d",
                character_id,
            )
            return False
        source_id = src_row["id"]

        # Create gear_plan row
        async with conn.transaction():
            plan_row = await conn.fetchrow(
                """
                INSERT INTO guild_identity.gear_plans
                    (player_id, character_id, spec_id, hero_talent_id, bis_source_id, is_active)
                VALUES ($1, $2, $3, NULL, $4, TRUE)
                RETURNING id
                """,
                player_id, character_id, spec_id, source_id,
            )
            plan_id = plan_row["id"]

            # Step 4: populate slots from BIS entries (skip if no spec)
            populated = 0
            if spec_id:
                bis_rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (ble.slot)
                           ble.slot, ble.item_id, wi.blizzard_item_id, wi.name AS item_name
                      FROM guild_identity.bis_list_entries ble
                      JOIN guild_identity.wow_items wi ON wi.id = ble.item_id
                     WHERE ble.source_id = $1
                       AND ble.spec_id = $2
                       AND ble.hero_talent_id IS NULL
                     ORDER BY ble.slot, ble.priority
                    """,
                    source_id, spec_id,
                )

                for row in bis_rows:
                    if row["slot"] not in _WOW_SLOTS:
                        continue
                    await conn.execute(
                        """
                        INSERT INTO guild_identity.gear_plan_slots
                            (plan_id, slot, desired_item_id, blizzard_item_id, item_name, is_locked)
                        VALUES ($1, $2, $3, $4, $5, FALSE)
                        ON CONFLICT (plan_id, slot) DO NOTHING
                        """,
                        plan_id, row["slot"], row["item_id"],
                        row["blizzard_item_id"], row["item_name"],
                    )
                    populated += 1

    logger.info(
        "auto_setup_gear_plan: created plan for player=%d character=%d spec=%s — %d slots populated",
        player_id, character_id, spec_id, populated,
    )
    return True
