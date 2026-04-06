"""Item metadata service — fetch and cache WoW item data from Wowhead.

Provides a thin wrapper around the public Wowhead tooltip API:
    GET https://nether.wowhead.com/tooltip/item/{itemId}?dataEnv=1&locale=0

Results are written to guild_identity.wow_items and served from cache on
subsequent lookups.  No auth required — the endpoint is publicly accessible.
"""

import asyncio
import logging
from typing import Optional

import asyncpg
import httpx

logger = logging.getLogger(__name__)

_WOWHEAD_ENRICH_DELAY = 0.05  # seconds between requests during batch enrichment

WOWHEAD_TOOLTIP_URL = "https://nether.wowhead.com/tooltip/item/{item_id}"

# Map Wowhead numeric slot codes (slotbak) to our normalised slot names.
# Incomplete items (cosmetics, etc.) map to None and are silently ignored.
_WOWHEAD_SLOT_MAP: dict[int, str] = {
    1: "head",
    2: "neck",
    3: "shoulder",
    5: "chest",
    6: "waist",
    7: "legs",
    8: "feet",
    9: "wrist",
    10: "hands",
    11: "ring_1",    # first ring slot
    12: "trinket_1", # first trinket
    13: "back",
    14: "main_hand",
    15: "off_hand",
    16: "ring_2",
    17: "trinket_2",
    22: "main_hand",  # two-hand
    23: "main_hand",  # ranged
}


async def get_or_fetch_item(
    pool: asyncpg.Pool,
    blizzard_item_id: int,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict]:
    """Return cached item metadata, fetching from Wowhead if not cached.

    Returns a dict with keys: id, blizzard_item_id, name, icon_url, slot_type,
    armor_type, weapon_type.  Returns None if the item cannot be resolved.
    """
    # 1. Check cache
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, blizzard_item_id, name, icon_url, slot_type, armor_type, weapon_type"
            "  FROM guild_identity.wow_items"
            " WHERE blizzard_item_id = $1",
            blizzard_item_id,
        )
    if row:
        return dict(row)

    # 2. Fetch from Wowhead tooltip API
    data = await _fetch_wowhead_tooltip(blizzard_item_id, http_client)
    if not data:
        return None

    name = data.get("name", "")
    icon_name = data.get("icon", "")
    icon_url = (
        f"https://wow.zamimg.com/images/wow/icons/medium/{icon_name}.jpg"
        if icon_name else None
    )

    # Derive slot from slotbak
    slot_code = data.get("slotbak")
    slot_type = _WOWHEAD_SLOT_MAP.get(slot_code, "other") if slot_code else "other"

    # Armor / weapon type from json equip data
    jsonequip = data.get("jsonequip", {}) or {}
    armor_type = jsonequip.get("subclass") if isinstance(jsonequip, dict) else None
    weapon_type = None
    if data.get("weaponinfo"):
        weapon_type = data.get("subclassname")

    tooltip_html = data.get("tooltip")

    # 3. Upsert into cache
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO guild_identity.wow_items
                (blizzard_item_id, name, icon_url, slot_type, armor_type,
                 weapon_type, wowhead_tooltip_html)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (blizzard_item_id) DO UPDATE
                SET name                 = EXCLUDED.name,
                    icon_url             = EXCLUDED.icon_url,
                    slot_type            = EXCLUDED.slot_type,
                    armor_type           = EXCLUDED.armor_type,
                    weapon_type          = EXCLUDED.weapon_type,
                    wowhead_tooltip_html = EXCLUDED.wowhead_tooltip_html,
                    fetched_at           = NOW()
            RETURNING id, blizzard_item_id, name, icon_url, slot_type, armor_type, weapon_type
            """,
            blizzard_item_id, name, icon_url, slot_type,
            str(armor_type) if armor_type is not None else None,
            weapon_type, tooltip_html,
        )
    return dict(row) if row else None


async def _fetch_wowhead_tooltip(
    blizzard_item_id: int,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict]:
    """Fetch raw tooltip JSON from Wowhead's public tooltip API."""
    url = WOWHEAD_TOOLTIP_URL.format(item_id=blizzard_item_id)
    params = {"dataEnv": "1", "locale": "0"}

    own_client = http_client is None
    if own_client:
        http_client = httpx.AsyncClient(timeout=10)

    try:
        response = await http_client.get(url, params=params)
        if response.status_code == 404:
            logger.warning("Wowhead tooltip 404 for item %d", blizzard_item_id)
            return None
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.warning("Wowhead tooltip fetch failed for item %d: %s", blizzard_item_id, exc)
        return None
    finally:
        if own_client:
            await http_client.aclose()


async def enrich_unenriched_items(
    pool: asyncpg.Pool,
) -> tuple[int, list[str]]:
    """Fetch Wowhead data for all wow_items rows that have no icon_url yet.

    Called after a Journal API sync to populate slot_type, icon_url, and
    tooltip for newly-created stub rows.  Returns (enriched_count, errors).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT blizzard_item_id FROM guild_identity.wow_items"
            " WHERE icon_url IS NULL ORDER BY blizzard_item_id"
        )

    if not rows:
        return 0, []

    item_ids = [r["blizzard_item_id"] for r in rows]
    logger.info("Enriching %d unenriched wow_items from Wowhead", len(item_ids))

    enriched = 0
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=10) as http_client:
        for blizzard_item_id in item_ids:
            data = await _fetch_wowhead_tooltip(blizzard_item_id, http_client)
            if not data:
                errors.append(f"Wowhead fetch failed for item {blizzard_item_id}")
                await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)
                continue

            name = data.get("name", "")
            icon_name = data.get("icon", "")
            icon_url = (
                f"https://wow.zamimg.com/images/wow/icons/medium/{icon_name}.jpg"
                if icon_name else None
            )
            slot_code = data.get("slotbak")
            slot_type = _WOWHEAD_SLOT_MAP.get(slot_code, "other") if slot_code else "other"
            jsonequip = data.get("jsonequip", {}) or {}
            armor_type = jsonequip.get("subclass") if isinstance(jsonequip, dict) else None
            weapon_type = data.get("subclassname") if data.get("weaponinfo") else None
            tooltip_html = data.get("tooltip")

            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE guild_identity.wow_items
                           SET name                 = CASE WHEN name = '' OR name IS NULL
                                                          THEN $2 ELSE name END,
                               icon_url             = $3,
                               slot_type            = $4,
                               armor_type           = $5,
                               weapon_type          = $6,
                               wowhead_tooltip_html = $7,
                               fetched_at           = NOW()
                         WHERE blizzard_item_id = $1
                        """,
                        blizzard_item_id, name, icon_url, slot_type,
                        str(armor_type) if armor_type is not None else None,
                        weapon_type, tooltip_html,
                    )
                enriched += 1
            except Exception as exc:
                errors.append(f"DB error enriching item {blizzard_item_id}: {exc}")

            await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)

    logger.info("Enriched %d items (%d errors)", enriched, len(errors))
    return enriched, errors
