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


_BLIZZARD_ARMOR_SUBCLASS: dict[str, str] = {
    "cloth": "cloth",
    "leather": "leather",
    "mail": "mail",
    "plate": "plate",
}

# Blizzard inventory_type.type → our slot_type key
_BLIZZARD_SLOT_MAP: dict[str, str] = {
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


async def enrich_blizzard_metadata(
    pool: asyncpg.Pool,
    blizzard_client,
    progress_cb=None,
) -> tuple[int, list[str]]:
    """Fetch armor_type + set-piece marker for tier-slot BIS items from Blizzard.

    Targets wow_items that appear in BIS lists with a tier slot
    (head/shoulder/chest/hands/legs) and have no wowhead_tooltip_html.
    Calls GET /data/wow/item/{id} and:
      - Sets armor_type from item_subclass (cloth/leather/mail/plate)
      - Sets wowhead_tooltip_html to '/item-set=ID' marker if item_set present
        (the view and gear_plan_service detect tier pieces via this pattern)
      - Also fixes slot_type if still 'other'

    Safe to re-run — uses COALESCE so existing data is never overwritten.
    Returns (updated_count, errors).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT wi.blizzard_item_id, wi.slot_type, wi.armor_type
              FROM guild_identity.wow_items wi
              JOIN guild_identity.bis_list_entries ble ON ble.item_id = wi.id
             WHERE wi.wowhead_tooltip_html IS NULL
               AND (
                     wi.slot_type IN ('head','shoulder','chest','hands','legs')
                     OR wi.slot_type = 'other'
                     OR wi.armor_type IS NULL
               )
             ORDER BY wi.blizzard_item_id
            """
        )

    if not rows:
        return 0, []

    logger.info(
        "Enriching %d tier-slot BIS items with Blizzard item metadata", len(rows)
    )

    updated = 0
    errors: list[str] = []
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(_BLIZZARD_ICON_CONCURRENCY)

    async def _process_one(blizzard_item_id: int, stagger_idx: int) -> None:
        nonlocal updated
        if stagger_idx < _BLIZZARD_ICON_CONCURRENCY:
            await asyncio.sleep(stagger_idx * _BLIZZARD_ICON_STAGGER)
        async with sem:
            try:
                data = await blizzard_client.get_item(blizzard_item_id)
                if not data:
                    async with lock:
                        if progress_cb:
                            progress_cb(updated, len(errors))
                    await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)
                    return

                # armor_type: from item_subclass name if item_class is Armor (id=4)
                armor_type = None
                item_class = data.get("item_class", {}) or {}
                if item_class.get("id") == 4:
                    subclass_name = (data.get("item_subclass", {}) or {}).get("name", "")
                    armor_type = _BLIZZARD_ARMOR_SUBCLASS.get(subclass_name.lower())

                # slot_type: from inventory_type if we need to fix 'other'
                slot_type = None
                inv_type = (data.get("inventory_type", {}) or {}).get("type", "")
                if inv_type:
                    slot_type = _BLIZZARD_SLOT_MAP.get(inv_type)

                # Synthetic tooltip marker if item is part of a set
                tooltip_marker = None
                item_set = data.get("item_set")
                if item_set:
                    set_id = item_set.get("id", 0)
                    tooltip_marker = f"/item-set={set_id}"

                if armor_type or slot_type or tooltip_marker:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE guild_identity.wow_items
                               SET armor_type           = COALESCE(armor_type, $2),
                                   slot_type            = CASE
                                       WHEN slot_type = 'other' AND $3::text IS NOT NULL
                                       THEN $3::text ELSE slot_type END,
                                   wowhead_tooltip_html = COALESCE(wowhead_tooltip_html, $4)
                             WHERE blizzard_item_id = $1
                            """,
                            blizzard_item_id, armor_type, slot_type, tooltip_marker,
                        )
                    async with lock:
                        updated += 1
                        if progress_cb:
                            progress_cb(updated, len(errors))
                else:
                    async with lock:
                        if progress_cb:
                            progress_cb(updated, len(errors))

            except Exception as exc:
                msg = f"Blizzard metadata fetch failed for item {blizzard_item_id}: {type(exc).__name__}: {exc}"
                logger.warning(msg)
                async with lock:
                    errors.append(msg)
                    if progress_cb:
                        progress_cb(updated, len(errors))

            await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)

    await asyncio.gather(*[
        _process_one(r["blizzard_item_id"], idx)
        for idx, r in enumerate(rows)
    ])

    logger.info(
        "Blizzard metadata enrichment complete — %d updated, %d errors",
        updated, len(errors),
    )
    return updated, errors


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
                               SET icon_url = CASE WHEN $2::text IS NOT NULL THEN $2::text ELSE icon_url END,
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

    # Backfill armor_type from Wowhead tooltip HTML for any item that has a
    # tooltip but still lacks armor_type (e.g. items enriched before this
    # field was populated, or items stubbed via BIS sync with slot set but
    # no armor_type).  Safe to run repeatedly — only touches NULL rows.
    backfilled = await backfill_armor_type_from_tooltip(pool)
    if backfilled:
        logger.info("backfill_armor_type_from_tooltip: updated %d rows", backfilled)

    return enriched, errors


async def backfill_armor_type_from_tooltip(pool: asyncpg.Pool) -> int:
    """Parse armor_type from existing Wowhead tooltip HTML for items missing it.

    Wowhead tooltips embed the armor subclass as:
      <!--scstart4:N--><span class="q1">Mail</span><!--scend-->
    where class 4 = Armor and N is the subclass index.

    Only updates rows in equippable armor slots (head/shoulder/chest/etc.) that
    have a tooltip but NULL armor_type.  Returns the number of rows updated.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE guild_identity.wow_items
               SET armor_type = CASE
                   WHEN wowhead_tooltip_html ~ $1 THEN 'cloth'
                   WHEN wowhead_tooltip_html ~ $2 THEN 'leather'
                   WHEN wowhead_tooltip_html ~ $3 THEN 'mail'
                   WHEN wowhead_tooltip_html ~ $4 THEN 'plate'
               END
             WHERE armor_type IS NULL
               AND wowhead_tooltip_html IS NOT NULL
               AND slot_type IS NOT NULL
               AND slot_type != 'other'
               AND slot_type NOT IN (
                   'neck', 'ring_1', 'ring_2',
                   'trinket_1', 'trinket_2',
                   'main_hand', 'off_hand', 'back'
               )
               AND (
                   wowhead_tooltip_html ~ $1
                   OR wowhead_tooltip_html ~ $2
                   OR wowhead_tooltip_html ~ $3
                   OR wowhead_tooltip_html ~ $4
               )
            """,
            r"<!--scstart4:[0-9]+--><span[^>]*>Cloth</span>",
            r"<!--scstart4:[0-9]+--><span[^>]*>Leather</span>",
            r"<!--scstart4:[0-9]+--><span[^>]*>Mail</span>",
            r"<!--scstart4:[0-9]+--><span[^>]*>Plate</span>",
        )
    return int(result.split()[-1])
