"""Item metadata service — fetch and cache WoW item data from Wowhead.

Provides a thin wrapper around the public Wowhead tooltip API:
    GET https://nether.wowhead.com/tooltip/item/{itemId}?dataEnv=1&locale=0

Results are written to guild_identity.wow_items and served from cache on
subsequent lookups.  No auth required — the endpoint is publicly accessible.
"""

import asyncio
import logging
import re
from typing import Optional

import asyncpg
import httpx

logger = logging.getLogger(__name__)

_WOWHEAD_ENRICH_DELAY = 0.05        # seconds between requests per concurrent worker
_WOWHEAD_ENRICH_CONCURRENCY = 20   # concurrent Wowhead connections during batch enrichment

# Blizzard Game Data API has a tighter burst limit than Wowhead — use lower
# concurrency and stagger the initial requests to avoid 429s.
_BLIZZARD_ICON_CONCURRENCY = 5     # concurrent Blizzard media API connections
_BLIZZARD_ICON_STAGGER = 0.2       # seconds to wait before each worker's first request

WOWHEAD_TOOLTIP_URL = "https://nether.wowhead.com/tooltip/item/{item_id}"

# Wowhead's nether tooltip API no longer returns the numeric `slotbak` field.
# Slot is instead parsed from the tooltip HTML (the <td>SLOT</td><th>TYPE</th>
# table that appears after "Binds when picked up").
_TOOLTIP_SLOT_MAP: dict[str, str] = {
    "head":              "head",
    "neck":              "neck",
    "shoulder":          "shoulder",
    "shoulders":         "shoulder",
    "back":              "back",
    "chest":             "chest",
    "waist":             "waist",
    "legs":              "legs",
    "feet":              "feet",
    "wrist":             "wrist",
    "wrists":            "wrist",
    "hands":             "hands",
    "finger":            "ring_1",
    "trinket":           "trinket_1",
    "main hand":         "main_hand",
    "one-hand":          "main_hand",
    "two-hand":          "main_hand",
    "off hand":          "off_hand",
    "held in off-hand":  "off_hand",
    "ranged":            "main_hand",
}


def _slot_from_tooltip(tooltip_html: str) -> str:
    """Extract slot type from Wowhead tooltip HTML.

    Wowhead embeds the slot as plain text in a stats table:
        <table width="100%"><tr><td>Hands</td><th>...Plate...</th></tr></table>
    This table always appears after "Binds when" in the tooltip.
    """
    if not tooltip_html:
        return "other"
    bwpu = tooltip_html.find("Binds when")
    search_str = tooltip_html[bwpu:] if bwpu >= 0 else tooltip_html
    m = re.search(r'<table width="100%"><tr><td>([^<]+)</td>', search_str)
    if m:
        return _TOOLTIP_SLOT_MAP.get(m.group(1).strip().lower(), "other")
    return "other"


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

    tooltip_html = data.get("tooltip")
    slot_type = _slot_from_tooltip(tooltip_html or "")

    # Armor / weapon type from json equip data
    jsonequip = data.get("jsonequip", {}) or {}
    armor_type = jsonequip.get("subclass") if isinstance(jsonequip, dict) else None
    weapon_type = None
    if data.get("weaponinfo"):
        weapon_type = data.get("subclassname")

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


async def enrich_null_icons(
    pool: asyncpg.Pool,
    blizzard_client,
    progress_cb=None,
) -> tuple[int, list[str]]:
    """Fetch icon URLs (and names) from Blizzard Item APIs for items where
    Wowhead returned no icon data.

    Targets rows where icon_url IS NULL — these are items too new for Wowhead
    to have indexed (common at the start of a new expansion).  Calls:
      - GET /data/wow/media/item/{id}  → icon_url
      - GET /data/wow/item/{id}        → name (if name is empty)

    Uses the same concurrency limit as enrich_unenriched_items.
    progress_cb(updated, error_count) is called after each item if provided.
    Returns (updated_count, errors).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT blizzard_item_id, name
              FROM guild_identity.wow_items
             WHERE icon_url IS NULL
             ORDER BY blizzard_item_id
            """
        )

    if not rows:
        return 0, []

    logger.info(
        "Enriching %d wow_items with null icon_url from Blizzard APIs", len(rows)
    )

    updated = 0
    errors: list[str] = []
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(_BLIZZARD_ICON_CONCURRENCY)

    async def _process_one(
        blizzard_item_id: int, existing_name: str, stagger_idx: int
    ) -> None:
        nonlocal updated
        # Stagger only the first CONCURRENCY workers so they don't all fire
        # simultaneously; the rest are blocked by the semaphore anyway.
        if stagger_idx < _BLIZZARD_ICON_CONCURRENCY:
            await asyncio.sleep(stagger_idx * _BLIZZARD_ICON_STAGGER)
        async with sem:
            try:
                icon_url = await blizzard_client.get_item_media(blizzard_item_id)

                # Also fetch name from Blizzard if Wowhead left it empty
                name = existing_name or ""
                if not name:
                    item_data = await blizzard_client.get_item(blizzard_item_id)
                    if item_data:
                        name = item_data.get("name") or ""

                if icon_url is not None or name:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE guild_identity.wow_items
                               SET icon_url = CASE WHEN $2 IS NOT NULL THEN $2 ELSE icon_url END,
                                   name     = CASE WHEN (name IS NULL OR name = '') AND $3 != ''
                                                   THEN $3 ELSE name END
                             WHERE blizzard_item_id = $1
                            """,
                            blizzard_item_id, icon_url, name,
                        )
                    if icon_url is not None:
                        async with lock:
                            updated += 1
                            if progress_cb:
                                progress_cb(updated, len(errors))
                    else:
                        async with lock:
                            if progress_cb:
                                progress_cb(updated, len(errors))
                else:
                    async with lock:
                        if progress_cb:
                            progress_cb(updated, len(errors))

            except Exception as exc:
                msg = f"Blizzard icon fetch failed for item {blizzard_item_id}: {type(exc).__name__}: {exc}"
                logger.warning(msg)
                async with lock:
                    errors.append(msg)
                    if progress_cb:
                        progress_cb(updated, len(errors))

            await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)

    await asyncio.gather(*[
        _process_one(r["blizzard_item_id"], r["name"] or "", idx)
        for idx, r in enumerate(rows)
    ])

    logger.info(
        "Blizzard icon enrichment complete — %d updated, %d errors",
        updated, len(errors),
    )
    return updated, errors


async def enrich_unenriched_items(
    pool: asyncpg.Pool,
    progress_cb=None,
) -> tuple[int, list[str]]:
    """Fetch Wowhead data for all wow_items rows that still have slot_type='other'.

    Uses up to _WOWHEAD_ENRICH_CONCURRENCY concurrent connections so 4k+ items
    complete in ~45 seconds instead of 15 minutes.

    progress_cb(enriched, error_count) is called after each item if provided.
    Returns (enriched_count, errors).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT blizzard_item_id FROM guild_identity.wow_items"
            " WHERE slot_type = 'other' ORDER BY blizzard_item_id"
        )

    if not rows:
        return 0, []

    item_ids = [r["blizzard_item_id"] for r in rows]
    logger.info("Enriching %d stub wow_items (slot_type='other') from Wowhead", len(item_ids))

    enriched = 0
    errors: list[str] = []
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(_WOWHEAD_ENRICH_CONCURRENCY)

    async def _process_one(blizzard_item_id: int, http_client: httpx.AsyncClient) -> None:
        nonlocal enriched
        async with sem:
            data = await _fetch_wowhead_tooltip(blizzard_item_id, http_client)
            if not data:
                async with lock:
                    errors.append(f"Wowhead fetch failed for item {blizzard_item_id}")
                    if progress_cb:
                        progress_cb(enriched, len(errors))
                await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)
                return

            name = data.get("name", "")
            icon_name = data.get("icon", "")
            icon_url = (
                f"https://wow.zamimg.com/images/wow/icons/medium/{icon_name}.jpg"
                if icon_name else None
            )
            slot_type = _slot_from_tooltip(data.get("tooltip") or "")
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
                async with lock:
                    enriched += 1
                    if progress_cb:
                        progress_cb(enriched, len(errors))
            except Exception as exc:
                async with lock:
                    errors.append(f"DB error enriching item {blizzard_item_id}: {exc}")
                    if progress_cb:
                        progress_cb(enriched, len(errors))

            await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)

    async with httpx.AsyncClient(timeout=10) as http_client:
        await asyncio.gather(*[_process_one(iid, http_client) for iid in item_ids])

    logger.info("Enriched %d items (%d errors)", enriched, len(errors))
    return enriched, errors
