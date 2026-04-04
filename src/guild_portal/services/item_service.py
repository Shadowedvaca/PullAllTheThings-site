"""Item metadata service — fetch and cache WoW item data from Wowhead.

Provides a thin wrapper around the public Wowhead tooltip API:
    GET https://nether.wowhead.com/tooltip/item/{itemId}?dataEnv=1&locale=0

Results are written to guild_identity.wow_items and served from cache on
subsequent lookups.  No auth required — the endpoint is publicly accessible.
"""

import logging
from typing import Optional

import asyncpg
import httpx

logger = logging.getLogger(__name__)

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
