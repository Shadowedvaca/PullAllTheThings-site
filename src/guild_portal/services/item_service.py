"""Item metadata service — fetch and cache WoW item data from Wowhead.

Provides a thin wrapper around the public Wowhead tooltip API:
    GET https://nether.wowhead.com/tooltip/item/{itemId}?dataEnv=1&locale=0

Results are written to guild_identity.wow_items and served from cache on
subsequent lookups.  No auth required — the endpoint is publicly accessible.
"""

import asyncio
import json
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

    Returns a dict with keys: blizzard_item_id, name, icon_url, slot_type,
    armor_type, weapon_type.  Returns None if the item cannot be resolved.
    """
    # 1. Check cache in enrichment.items (rebuilt by sp_rebuild_items)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT blizzard_item_id, name, icon_url, slot_type, armor_type
              FROM enrichment.items
             WHERE blizzard_item_id = $1
            """,
            blizzard_item_id,
        )
    if row:
        return {**dict(row), "weapon_type": None}

    # 2. Fetch from Wowhead tooltip API
    data = await _fetch_wowhead_tooltip(blizzard_item_id, http_client)
    if not data:
        return None

    # Write raw Wowhead payload to landing schema for future enrichment runs
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO landing.wowhead_tooltips (blizzard_item_id, payload)
                VALUES ($1, $2::jsonb)
                """,
                blizzard_item_id, json.dumps(data),
            )
        except Exception:
            pass  # landing write is best-effort

    name = data.get("name", "")
    icon_name = data.get("icon", "")
    icon_url = (
        f"https://wow.zamimg.com/images/wow/icons/medium/{icon_name}.jpg"
        if icon_name else None
    )
    tooltip_html = data.get("tooltip")
    slot_type = _slot_from_tooltip(tooltip_html or "")
    jsonequip = data.get("jsonequip", {}) or {}
    armor_type = jsonequip.get("subclass") if isinstance(jsonequip, dict) else None
    weapon_type = None
    if data.get("weaponinfo"):
        weapon_type = data.get("subclassname")

    return {
        "blizzard_item_id": blizzard_item_id,
        "name": name,
        "icon_url": icon_url,
        "slot_type": slot_type,
        "armor_type": str(armor_type) if armor_type is not None else None,
        "weapon_type": weapon_type,
    }


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

    Targets enrichment.items that appear in BIS lists with a tier slot
    (head/shoulder/chest/hands/legs) and have no wowhead_tooltip entry in landing.
    Calls GET /data/wow/item/{id} and:
      - Writes the full payload to landing.blizzard_items (sp_rebuild_items derives
        armor_type and slot_type from it on next rebuild)
      - Writes a synthetic '/item-set=ID' marker to landing.wowhead_tooltips so
        process_tier_tokens can detect tier set membership

    Safe to re-run — landing.blizzard_items is append-only; landing.wowhead_tooltips
    insert is best-effort (duplicate rows are harmless).
    Returns (updated_count, errors).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ei.blizzard_item_id, ei.slot_type, ei.armor_type
              FROM enrichment.items ei
              JOIN enrichment.bis_entries be ON be.blizzard_item_id = ei.blizzard_item_id
             WHERE NOT EXISTS (
                     SELECT 1 FROM landing.wowhead_tooltips wt
                      WHERE wt.blizzard_item_id = ei.blizzard_item_id
                   )
               AND (
                     ei.slot_type IN ('head','shoulder','chest','hands','legs')
                     OR ei.slot_type = 'other'
                     OR ei.armor_type IS NULL
               )
             ORDER BY ei.blizzard_item_id
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

                # Phase A: dual-write raw Blizzard item payload to landing schema
                try:
                    async with pool.acquire() as _lconn:
                        await _lconn.execute(
                            """
                            INSERT INTO landing.blizzard_items (blizzard_item_id, payload)
                            VALUES ($1, $2::jsonb)
                            """,
                            blizzard_item_id, json.dumps(data),
                        )
                except Exception:
                    pass  # landing write is best-effort; don't fail enrichment

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

                # Synthetic tooltip marker if item is part of a set.
                # Write to landing.wowhead_tooltips so process_tier_tokens can detect
                # tier set membership (reads wt.payload->>'tooltip' LIKE '%/item-set=%').
                tooltip_marker = None
                item_set = data.get("item_set")
                if item_set:
                    set_id = item_set.get("id", 0)
                    tooltip_marker = f"/item-set={set_id}"

                if armor_type or slot_type or tooltip_marker:
                    if tooltip_marker:
                        try:
                            async with pool.acquire() as conn:
                                await conn.execute(
                                    """
                                    INSERT INTO landing.wowhead_tooltips (blizzard_item_id, payload)
                                    VALUES ($1, $2::jsonb)
                                    """,
                                    blizzard_item_id,
                                    json.dumps({"tooltip": tooltip_marker}),
                                )
                        except Exception:
                            pass  # landing write is best-effort
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
    """Fetch icon URLs from Blizzard Item APIs for enriched items missing icon data.

    Targets enrichment.items rows where icon_url IS NULL — these are items too new
    for Wowhead to have indexed (common at the start of a new expansion).  Calls:
      - GET /data/wow/media/item/{id}  → written to landing.blizzard_item_icons

    sp_rebuild_items reads landing.blizzard_item_icons for icon_url, so icons
    become visible in enrichment.items after the next "Enrich & Classify" rebuild.

    Uses the same concurrency limit as enrich_unenriched_items.
    progress_cb(updated, error_count) is called after each item if provided.
    Returns (updated_count, errors).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT blizzard_item_id, name
              FROM enrichment.items
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

                if icon_url is not None:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            INSERT INTO landing.blizzard_item_icons
                                (blizzard_item_id, icon_url)
                            VALUES ($1, $2)
                            ON CONFLICT (blizzard_item_id)
                            DO UPDATE SET icon_url = EXCLUDED.icon_url,
                                          fetched_at = NOW()
                            """,
                            blizzard_item_id, icon_url,
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
    """Fetch Wowhead data for items missing tooltip data in landing.wowhead_tooltips.

    Targets two categories:
    1. enrichment.items rows with slot_type='other' — stubs not yet enriched.
    2. Items in item_recipe_links with no landing.wowhead_tooltips entry — crafted
       item stubs from Sync Crafted Items that need a Wowhead tooltip so the quality
       filter (class="q4") can pass.

    Writes fetched payloads to landing.wowhead_tooltips.  sp_rebuild_items will
    derive slot_type/armor_type from the payloads on the next Enrich & Classify run.

    Uses up to _WOWHEAD_ENRICH_CONCURRENCY concurrent connections so 4k+ items
    complete in ~45 seconds instead of 15 minutes.

    progress_cb(enriched, error_count) is called after each item if provided.
    Returns (enriched_count, errors).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ei.blizzard_item_id
              FROM enrichment.items ei
             WHERE ei.slot_type = 'other'
                OR (
                    NOT EXISTS (
                        SELECT 1 FROM landing.wowhead_tooltips wt
                         WHERE wt.blizzard_item_id = ei.blizzard_item_id
                    )
                    AND EXISTS (
                        SELECT 1 FROM guild_identity.item_recipe_links irl
                         WHERE irl.blizzard_item_id = ei.blizzard_item_id
                    )
                )
             ORDER BY ei.blizzard_item_id
            """
        )

    if not rows:
        return 0, []

    item_ids = [r["blizzard_item_id"] for r in rows]
    logger.info(
        "Enriching %d wow_items (slot_type='other' or crafted stubs) from Wowhead",
        len(item_ids),
    )

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

            # Write raw Wowhead payload to landing.wowhead_tooltips.
            # sp_rebuild_items derives slot_type/armor_type from the payload on
            # the next Enrich & Classify run.
            try:
                async with pool.acquire() as _lconn:
                    await _lconn.execute(
                        """
                        INSERT INTO landing.wowhead_tooltips (blizzard_item_id, payload)
                        VALUES ($1, $2::jsonb)
                        """,
                        blizzard_item_id, json.dumps(data),
                    )
                async with lock:
                    enriched += 1
                    if progress_cb:
                        progress_cb(enriched, len(errors))
            except Exception as exc:
                async with lock:
                    errors.append(f"DB error storing tooltip for item {blizzard_item_id}: {exc}")
                    if progress_cb:
                        progress_cb(enriched, len(errors))

            await asyncio.sleep(_WOWHEAD_ENRICH_DELAY)

    async with httpx.AsyncClient(timeout=10) as http_client:
        await asyncio.gather(*[_process_one(iid, http_client) for iid in item_ids])

    logger.info("Enriched %d items (%d errors)", enriched, len(errors))
    return enriched, errors


