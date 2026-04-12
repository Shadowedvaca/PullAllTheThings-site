"""Build item → recipe links for craftable gear.

Two discovery strategies:

Phase 1 — name match (build_item_recipe_links):
  Scans wow_items rows and matches them against the recipes table by name.
  Confidence 100 = exact, 90 = prefix-stripped (Pattern:, Recipe:, etc.).
  Only works for items already in wow_items (BIS or Journal-sourced).

Phase 2 — Blizzard-backed discovery (discover_and_link_crafted_items):
  For recipes in the active expansion with no item_recipe_links entry, calls
  GET /data/wow/recipe/{id} to get the crafted_item ID, then GET /data/wow/item/{id}
  to confirm it is equippable and get slot_type + armor_type.  Stubs the item
  into wow_items and creates the link.  Confidence 100, match_type 'blizzard_recipe_api'.
  Run after Phase 1 or standalone; safe to re-run.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

import asyncpg

logger = logging.getLogger(__name__)

# Prefixes Blizzard adds to recipe item names that do not appear on the
# crafted item itself.  All are followed by optional whitespace.
_RECIPE_PREFIXES = re.compile(
    r"^(Recipe|Pattern|Schematic|Formula|Design|Technique|Plans|Scroll of):\s*",
    re.IGNORECASE,
)


def _strip_prefix(name: str) -> str:
    """Remove a known recipe prefix from a recipe name, if present."""
    return _RECIPE_PREFIXES.sub("", name).strip()


async def build_item_recipe_links(pool: asyncpg.Pool) -> dict:
    """Match craftable wow_items to recipes and upsert into item_recipe_links.

    Returns a stats dict: {scanned, linked, updated, skipped}.
    """
    async with pool.acquire() as conn:
        # Scan all items with a name — the name match against recipes is
        # specific enough to avoid false positives, and we can't rely on
        # the Wowhead "Random Stat" tooltip marker for new expansion items
        # where Wowhead has not yet indexed the tooltip HTML.
        craftable_rows = await conn.fetch(
            """
            SELECT id, name
              FROM guild_identity.wow_items
             WHERE name IS NOT NULL AND name != ''
            """
        )

        if not craftable_rows:
            logger.info("item_recipe_link_sync: no items with names found")
            return {"scanned": 0, "linked": 0, "updated": 0, "skipped": 0}

        # Load all recipes with their normalised names for matching.
        recipe_rows = await conn.fetch(
            """
            SELECT id, name, LOWER(name) AS name_lower
              FROM guild_identity.recipes
            """
        )

    # Build lookup dicts: normalised_name → recipe_id
    exact_map: dict[str, list[int]] = {}
    prefix_map: dict[str, list[int]] = {}

    for r in recipe_rows:
        exact_map.setdefault(r["name_lower"], []).append(r["id"])
        stripped = _strip_prefix(r["name"]).lower()
        if stripped != r["name_lower"]:
            prefix_map.setdefault(stripped, []).append(r["id"])

    scanned = len(craftable_rows)
    linked = 0
    updated = 0
    skipped = 0

    async with pool.acquire() as conn:
        for item in craftable_rows:
            item_id = item["id"]
            item_name_lower = item["name"].lower()

            candidates: list[tuple[int, int, str]] = []  # (recipe_id, confidence, match_type)

            exact_ids = exact_map.get(item_name_lower, [])
            for rid in exact_ids:
                candidates.append((rid, 100, "exact_name"))

            if not candidates:
                prefix_ids = prefix_map.get(item_name_lower, [])
                for rid in prefix_ids:
                    candidates.append((rid, 90, "prefix_stripped"))

            if not candidates:
                skipped += 1
                continue

            for recipe_id, confidence, match_type in candidates:
                result = await conn.execute(
                    """
                    INSERT INTO guild_identity.item_recipe_links
                        (item_id, recipe_id, confidence, match_type)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (item_id, recipe_id) DO UPDATE
                        SET confidence  = GREATEST(
                                guild_identity.item_recipe_links.confidence,
                                EXCLUDED.confidence
                            ),
                            match_type  = CASE
                                WHEN EXCLUDED.confidence
                                     > guild_identity.item_recipe_links.confidence
                                THEN EXCLUDED.match_type
                                ELSE guild_identity.item_recipe_links.match_type
                            END
                    """,
                    item_id, recipe_id, confidence, match_type,
                )
                # asyncpg returns "INSERT 0 1" or "UPDATE 1"
                if result.startswith("INSERT"):
                    linked += 1
                else:
                    updated += 1

    logger.info(
        "item_recipe_link_sync: scanned=%d linked=%d updated=%d skipped=%d",
        scanned, linked, updated, skipped,
    )
    return {"scanned": scanned, "linked": linked, "updated": updated, "skipped": skipped}


# Blizzard inventory_type.type → our slot_type (equippable gear only).
# Non-equippable types (NON_EQUIP, NON_EQUIP_IGNORE, BAG, QUIVER, etc.) are
# intentionally absent so we skip consumables, enchant scrolls, and materials.
_EQUIP_SLOT_MAP: dict[str, str] = {
    "HEAD": "head",
    "NECK": "neck",
    "SHOULDER": "shoulder",
    "BACK": "back",
    "CHEST": "chest",
    "WAIST": "waist",
    "LEGS": "legs",
    "FEET": "feet",
    "WRIST": "wrist",
    "HAND": "hands",
    "FINGER": "ring_1",
    "TRINKET": "trinket_1",
    "WEAPON": "main_hand",
    "TWOHWEAPON": "main_hand",
    "RANGED": "main_hand",
    "OFFHAND": "off_hand",
    "HOLDABLE": "off_hand",
    "SHIELD": "off_hand",
}

# Blizzard item_subclass.name → our armor_type value (armor slots only).
_ARMOR_SUBCLASS_MAP: dict[str, str] = {
    "Cloth": "cloth",
    "Leather": "leather",
    "Mail": "mail",
    "Plate": "plate",
}


async def discover_and_link_crafted_items(
    pool: asyncpg.Pool,
    client: Any,
) -> dict:
    """Phase 2 discovery: call Blizzard Recipe + Item APIs for unlinked recipes.

    For each recipe in the active expansion that has no item_recipe_links entry:
      1. GET /data/wow/recipe/{id} → crafted_item.id + crafted_item.name
      2. GET /data/wow/item/{id}   → inventory_type (slot) + item_subclass (armor)
      3. Skip non-equippable items (consumables, enchant scrolls, materials, etc.)
      4. Stub equippable items into wow_items (ON CONFLICT DO NOTHING)
      5. GET /data/wow/media/item/{id} → icon_url
      6. Insert item_recipe_links with confidence=100, match_type='blizzard_recipe_api'

    Safe to re-run — existing wow_items rows and links are never overwritten.
    Returns {recipes_checked, discovered, stubbed, linked, skipped, errors}.
    """
    async with pool.acquire() as conn:
        season_row = await conn.fetchrow(
            "SELECT expansion_name FROM patt.raid_seasons WHERE is_active = TRUE LIMIT 1"
        )
        if not season_row:
            logger.warning("discover_and_link_crafted_items: no active season found")
            return {"recipes_checked": 0, "discovered": 0, "stubbed": 0,
                    "linked": 0, "skipped": 0, "errors": 0}

        expansion_name = season_row["expansion_name"]

        unlinked = await conn.fetch(
            """
            SELECT rec.id AS recipe_db_id, rec.blizzard_recipe_id, rec.name AS recipe_name
              FROM guild_identity.recipes rec
              JOIN guild_identity.profession_tiers pt ON pt.id = rec.tier_id
             WHERE pt.expansion_name = $1
               AND NOT EXISTS (
                   SELECT 1 FROM guild_identity.item_recipe_links irl
                    WHERE irl.recipe_id = rec.id
               )
             ORDER BY rec.id
            """,
            expansion_name,
        )

    if not unlinked:
        logger.info("discover_and_link_crafted_items: no unlinked %s recipes", expansion_name)
        return {"recipes_checked": 0, "discovered": 0, "stubbed": 0,
                "linked": 0, "skipped": 0, "errors": 0}

    logger.info(
        "discover_and_link_crafted_items: processing %d unlinked %s recipes",
        len(unlinked), expansion_name,
    )

    sem = asyncio.Semaphore(3)
    recipes_checked = discovered = stubbed = linked = skipped = errors = 0

    for recipe_row in unlinked:
        recipe_db_id      = recipe_row["recipe_db_id"]
        blizzard_recipe_id = recipe_row["blizzard_recipe_id"]
        recipes_checked   += 1

        try:
            async with sem:
                detail = await client.get_recipe_detail(blizzard_recipe_id)

            if not detail:
                skipped += 1
                continue

            crafted = detail.get("crafted_item")
            if not crafted:
                skipped += 1
                continue

            blizzard_item_id = crafted["id"]
            item_name        = crafted.get("name") or ""
            discovered       += 1

            # Fetch inventory_type and item_subclass to confirm equippable
            async with sem:
                item_data = await client.get_item(blizzard_item_id)

            if not item_data:
                skipped += 1
                continue

            inv_type  = (item_data.get("inventory_type") or {}).get("type", "")
            slot_type = _EQUIP_SLOT_MAP.get(inv_type)
            if not slot_type:
                skipped += 1   # consumable, enchant scroll, material, etc.
                continue

            subclass_name = (item_data.get("item_subclass") or {}).get("name", "")
            armor_type    = _ARMOR_SUBCLASS_MAP.get(subclass_name)

            # Fetch icon
            async with sem:
                icon_url = await client.get_item_media(blizzard_item_id)

            # Stub into wow_items, retrieve DB id
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO guild_identity.wow_items
                        (blizzard_item_id, name, icon_url, slot_type, armor_type)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (blizzard_item_id) DO NOTHING
                    RETURNING id
                    """,
                    blizzard_item_id, item_name, icon_url, slot_type, armor_type,
                )
                if not row:
                    row = await conn.fetchrow(
                        "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
                        blizzard_item_id,
                    )
                else:
                    stubbed += 1

                item_db_id = row["id"]

                result = await conn.execute(
                    """
                    INSERT INTO guild_identity.item_recipe_links
                        (item_id, recipe_id, confidence, match_type)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (item_id, recipe_id) DO NOTHING
                    """,
                    item_db_id, recipe_db_id, 100, "blizzard_recipe_api",
                )
                if result == "INSERT 0 1":
                    linked += 1

        except Exception as exc:
            logger.warning(
                "discover_and_link_crafted_items: recipe %d failed: %s",
                blizzard_recipe_id, exc,
            )
            errors += 1

    logger.info(
        "discover_and_link_crafted_items: checked=%d discovered=%d stubbed=%d "
        "linked=%d skipped=%d errors=%d",
        recipes_checked, discovered, stubbed, linked, skipped, errors,
    )
    return {
        "recipes_checked": recipes_checked,
        "discovered":      discovered,
        "stubbed":         stubbed,
        "linked":          linked,
        "skipped":         skipped,
        "errors":          errors,
    }
