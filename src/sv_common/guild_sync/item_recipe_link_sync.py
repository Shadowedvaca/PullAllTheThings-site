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
# PROFESSION_GEAR and all non-equip types are intentionally absent —
# profession hats (engineering goggles, alchemy hats, etc.) are excluded.
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

# Blizzard item_subclass → our armor_type.
# From get_item(): subclass["name"] e.g. "Leather".
# From search_items_by_name(): subclass is a locale dict; use ["en_US"].
_ARMOR_SUBCLASS_MAP: dict[str, str] = {
    "Cloth": "cloth",
    "Leather": "leather",
    "Mail": "mail",
    "Plate": "plate",
}


def _parse_slot_and_armor(item_data: dict) -> tuple[Optional[str], Optional[str]]:
    """Extract slot_type and armor_type from a Blizzard item data dict.

    Works for both get_item() and search_items_by_name() result dicts:
    - get_item()            → item_subclass["name"] = "Leather"
    - search_items_by_name() → item_subclass is a locale dict {"en_US": "Leather"}
    """
    inv_type  = (item_data.get("inventory_type") or {}).get("type", "")
    slot_type = _EQUIP_SLOT_MAP.get(inv_type)

    subclass = item_data.get("item_subclass") or {}
    subclass_name = ""
    if isinstance(subclass, dict):
        name_val = subclass.get("name")
        if isinstance(name_val, str):
            # get_item() format: {"name": "Leather", "id": 2}
            subclass_name = name_val
        elif isinstance(name_val, dict):
            # search API nested format: {"name": {"en_US": "Leather"}, "id": 2}
            subclass_name = name_val.get("en_US", "")
        else:
            # flat locale dict: {"en_US": "Leather", "en_GB": "Leather", ...}
            subclass_name = subclass.get("en_US", "")
    armor_type = _ARMOR_SUBCLASS_MAP.get(subclass_name)

    return slot_type, armor_type


async def _stub_and_link(
    pool: asyncpg.Pool,
    blizzard_item_id: int,
    item_name: str,
    slot_type: str,
    armor_type: Optional[str],
    recipe_db_id: int,
    match_type: str,
) -> tuple[bool, bool]:
    """Stub wow_items (if new) and insert item_recipe_links (if new).

    Returns (stubbed: bool, linked: bool).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO guild_identity.wow_items
                (blizzard_item_id, name, slot_type, armor_type)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (blizzard_item_id) DO NOTHING
            RETURNING id
            """,
            blizzard_item_id, item_name, slot_type, armor_type,
        )
        stubbed = row is not None
        if not row:
            row = await conn.fetchrow(
                "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
                blizzard_item_id,
            )
        item_db_id = row["id"]

        result = await conn.execute(
            """
            INSERT INTO guild_identity.item_recipe_links
                (item_id, recipe_id, confidence, match_type)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (item_id, recipe_id) DO NOTHING
            """,
            item_db_id, recipe_db_id, 100, match_type,
        )
        linked = result == "INSERT 0 1"

    return stubbed, linked


async def _get_unlinked_recipes(
    pool: asyncpg.Pool, expansion_name: str
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT rec.id AS recipe_db_id, rec.name AS recipe_name
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


async def discover_and_link_crafted_items(
    pool: asyncpg.Pool,
    client: Any,
) -> dict:
    """Discover craftable gear items and create item_recipe_links entries.

    Runs two phases in sequence:

    Phase 2a — character_equipment name match (pure DB, instant):
      For unlinked recipes whose name matches an item currently equipped by any
      guild member, stub wow_items using the equipped item's blizzard_item_id and
      create the link.  Covers items guild members have already crafted and worn.

    Phase 2b — Blizzard Item Search (API, ~1–3 min):
      For recipes still unlinked after Phase 2a, search the Blizzard Item API by
      recipe name.  On an exact en_US name match that maps to an equippable slot
      (PROFESSION_GEAR items are excluded), stub wow_items and create the link.
      This covers items that haven't been crafted/worn yet.

    Both phases are safe to re-run (ON CONFLICT DO NOTHING throughout).
    Run Enrich Items after to populate Wowhead tooltips + icons for new stubs.
    Returns combined stats dict.
    """
    async with pool.acquire() as conn:
        season_row = await conn.fetchrow(
            "SELECT expansion_name FROM patt.raid_seasons WHERE is_active = TRUE LIMIT 1"
        )
        if not season_row:
            logger.warning("discover_and_link_crafted_items: no active season found")
            return {"phase_2a_stubbed": 0, "phase_2a_linked": 0,
                    "phase_2b_checked": 0, "phase_2b_stubbed": 0,
                    "phase_2b_linked": 0, "phase_2b_skipped": 0, "phase_2b_errors": 0}
        expansion_name = season_row["expansion_name"]

    # ── Phase 2a: character_equipment name match ───────────────────────────────
    logger.info("discover_and_link_crafted_items: phase 2a — equipment match (%s)", expansion_name)
    async with pool.acquire() as conn:
        # Stub any wow_items not yet in the table
        stub_result = await conn.execute(
            """
            INSERT INTO guild_identity.wow_items (blizzard_item_id, name, slot_type)
            SELECT DISTINCT ON (ce.blizzard_item_id) ce.blizzard_item_id, ce.item_name, ce.slot
              FROM guild_identity.character_equipment ce
              JOIN guild_identity.recipes rec ON LOWER(rec.name) = LOWER(ce.item_name)
              JOIN guild_identity.profession_tiers pt ON pt.id = rec.tier_id
             WHERE pt.expansion_name = $1
               AND ce.blizzard_item_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM guild_identity.item_recipe_links irl
                    WHERE irl.recipe_id = rec.id
               )
            ON CONFLICT (blizzard_item_id) DO NOTHING
            """,
            expansion_name,
        )
        phase_2a_stubbed = int(stub_result.split()[-1])

        # Create links for all matching recipe↔item pairs
        link_result = await conn.execute(
            """
            INSERT INTO guild_identity.item_recipe_links (item_id, recipe_id, confidence, match_type)
            SELECT DISTINCT wi.id, rec.id, 100, 'equipment_name_match'
              FROM guild_identity.character_equipment ce
              JOIN guild_identity.recipes rec ON LOWER(rec.name) = LOWER(ce.item_name)
              JOIN guild_identity.profession_tiers pt ON pt.id = rec.tier_id
              JOIN guild_identity.wow_items wi ON wi.blizzard_item_id = ce.blizzard_item_id
             WHERE pt.expansion_name = $1
               AND ce.blizzard_item_id IS NOT NULL
            ON CONFLICT (item_id, recipe_id) DO NOTHING
            """,
            expansion_name,
        )
        phase_2a_linked = int(link_result.split()[-1])

    logger.info(
        "discover_and_link_crafted_items: phase 2a done — stubbed=%d linked=%d",
        phase_2a_stubbed, phase_2a_linked,
    )

    # ── Phase 2b: Blizzard Item Search for remaining unlinked recipes ──────────
    unlinked = await _get_unlinked_recipes(pool, expansion_name)
    logger.info(
        "discover_and_link_crafted_items: phase 2b — item search for %d remaining recipes",
        len(unlinked),
    )

    sem = asyncio.Semaphore(3)
    phase_2b_checked = phase_2b_stubbed = phase_2b_linked = 0
    phase_2b_skipped = phase_2b_errors = 0

    for recipe_row in unlinked:
        recipe_db_id  = recipe_row["recipe_db_id"]
        recipe_name   = recipe_row["recipe_name"]
        phase_2b_checked += 1

        try:
            async with sem:
                results = await client.search_items_by_name(recipe_name)

            # Find exact en_US name match
            match = None
            recipe_name_lower = recipe_name.lower()
            for r in results:
                r_name = (r.get("name") or {}).get("en_US", "")
                if r_name.lower() == recipe_name_lower:
                    match = r
                    break

            if not match:
                phase_2b_skipped += 1
                continue

            blizzard_item_id = match["id"]
            item_name        = (match.get("name") or {}).get("en_US", recipe_name)
            slot_type, armor_type = _parse_slot_and_armor(match)

            if not slot_type:
                phase_2b_skipped += 1  # PROFESSION_GEAR or non-equippable
                continue

            stubbed, linked = await _stub_and_link(
                pool, blizzard_item_id, item_name, slot_type, armor_type,
                recipe_db_id, "item_search",
            )
            if stubbed:
                phase_2b_stubbed += 1
            if linked:
                phase_2b_linked += 1

        except Exception as exc:
            logger.warning(
                "discover_and_link_crafted_items: phase 2b recipe %r failed: %s",
                recipe_name, exc,
            )
            phase_2b_errors += 1

    logger.info(
        "discover_and_link_crafted_items: phase 2b done — checked=%d stubbed=%d "
        "linked=%d skipped=%d errors=%d",
        phase_2b_checked, phase_2b_stubbed, phase_2b_linked,
        phase_2b_skipped, phase_2b_errors,
    )
    return {
        "phase_2a_stubbed":  phase_2a_stubbed,
        "phase_2a_linked":   phase_2a_linked,
        "phase_2b_checked":  phase_2b_checked,
        "phase_2b_stubbed":  phase_2b_stubbed,
        "phase_2b_linked":   phase_2b_linked,
        "phase_2b_skipped":  phase_2b_skipped,
        "phase_2b_errors":   phase_2b_errors,
    }
