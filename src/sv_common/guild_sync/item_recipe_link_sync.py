"""Build item → recipe links for craftable gear.

Runs as a post-processing step after Sync Loot Tables.  Scans wow_items
rows whose Wowhead tooltip contains "Random Stat" (crafted items have
randomised secondary stats; raid/dungeon drops do not) and matches them
against the recipes table by name.

Confidence levels:
  100  exact_name       LOWER(item.name) == LOWER(recipe.name)
   90  prefix_stripped  Same after stripping common recipe prefixes
                        (Recipe:, Pattern:, Schematic:, Formula:, Design:,
                        Technique:, etc.)

Links are upserted — if a link already exists with a lower confidence it is
upgraded; if the same or higher confidence already exists it is left alone.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

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
        # All craftable items: wow_items with "Random Stat" in tooltip.
        craftable_rows = await conn.fetch(
            """
            SELECT id, name
              FROM guild_identity.wow_items
             WHERE wowhead_tooltip_html IS NOT NULL
               AND wowhead_tooltip_html LIKE '%Random Stat%'
            """
        )

        if not craftable_rows:
            logger.info("item_recipe_link_sync: no craftable items found")
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
