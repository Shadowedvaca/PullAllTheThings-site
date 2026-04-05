"""BIS list discovery + extraction pipeline.

Architecture (4 steps):
  1. URL Discovery  — auto-generate scrape targets for all spec × source × hero talent combos
  2. Extraction     — try multiple techniques per URL (Archon → Wowhead → Icy Veins → SimC)
  3. Auto-publish   — upsert extracted items into bis_list_entries immediately (no draft state)
  4. Cross-reference— compare sources per spec to surface disagreements for review

BIS data is centralised game data managed by Mike for the whole network.
All guild portals read from the same bis_list_entries table.

Public functions
----------------
discover_targets(pool)                  — generate missing scrape_targets rows for all specs
sync_source(pool, source_id, spec_ids)  — run extraction for one source (optionally filtered)
sync_all(pool)                          — run extraction for every active source
sync_target(pool, target_id)            — re-sync a single scrape target
cross_reference(pool, spec_id, ht_id)  — compare all sources per slot for one spec+hero
import_simc(pool, text, source_id,      — import a SimC BIS profile as bis_list_entries
            spec_id, hero_talent_id)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

from .simc_parser import SimcSlot, parse_gear_slots
from .quality_track import SLOT_ORDER

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slug maps — (class_name, spec_name) → URL slugs per source
# ---------------------------------------------------------------------------

# u.gg (Archon) URL: /wow/{spec_slug}/{class_slug}/gear?hero={hero_slug}&role={role}
_UGG_SLUG_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("Death Knight", "Blood"):        ("death-knight", "blood"),
    ("Death Knight", "Frost"):        ("death-knight", "frost"),
    ("Death Knight", "Unholy"):       ("death-knight", "unholy"),
    ("Demon Hunter", "Havoc"):        ("demon-hunter", "havoc"),
    ("Demon Hunter", "Vengeance"):    ("demon-hunter", "vengeance"),
    ("Druid",        "Balance"):      ("druid",         "balance"),
    ("Druid",        "Feral"):        ("druid",         "feral"),
    ("Druid",        "Guardian"):     ("druid",         "guardian"),
    ("Druid",        "Restoration"):  ("druid",         "restoration"),
    ("Evoker",       "Devastation"):  ("evoker",        "devastation"),
    ("Evoker",       "Preservation"): ("evoker",        "preservation"),
    ("Evoker",       "Augmentation"): ("evoker",        "augmentation"),
    ("Hunter",       "Beast Mastery"):("hunter",        "beastmastery"),
    ("Hunter",       "Marksmanship"): ("hunter",        "marksmanship"),
    ("Hunter",       "Survival"):     ("hunter",        "survival"),
    ("Mage",         "Arcane"):       ("mage",          "arcane"),
    ("Mage",         "Fire"):         ("mage",          "fire"),
    ("Mage",         "Frost"):        ("mage",          "frost"),
    ("Monk",         "Brewmaster"):   ("monk",          "brewmaster"),
    ("Monk",         "Mistweaver"):   ("monk",          "mistweaver"),
    ("Monk",         "Windwalker"):   ("monk",          "windwalker"),
    ("Paladin",      "Holy"):         ("paladin",       "holy"),
    ("Paladin",      "Protection"):   ("paladin",       "protection"),
    ("Paladin",      "Retribution"):  ("paladin",       "retribution"),
    ("Priest",       "Discipline"):   ("priest",        "discipline"),
    ("Priest",       "Holy"):         ("priest",        "holy"),
    ("Priest",       "Shadow"):       ("priest",        "shadow"),
    ("Rogue",        "Assassination"):("rogue",         "assassination"),
    ("Rogue",        "Outlaw"):       ("rogue",         "outlaw"),
    ("Rogue",        "Subtlety"):     ("rogue",         "subtlety"),
    ("Shaman",       "Elemental"):    ("shaman",        "elemental"),
    ("Shaman",       "Enhancement"):  ("shaman",        "enhancement"),
    ("Shaman",       "Restoration"):  ("shaman",        "restoration"),
    ("Warlock",      "Affliction"):   ("warlock",       "affliction"),
    ("Warlock",      "Demonology"):   ("warlock",       "demonology"),
    ("Warlock",      "Destruction"):  ("warlock",       "destruction"),
    ("Warrior",      "Arms"):         ("warrior",       "arms"),
    ("Warrior",      "Fury"):         ("warrior",       "fury"),
    ("Warrior",      "Protection"):   ("warrior",       "protection"),
}

# Wowhead BIS URL: /guide/classes/{class_slug}/{spec_slug}/best-in-slot
# Uses kebab-case for both class and spec
_WOWHEAD_SLUG_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("Death Knight", "Blood"):        ("death-knight",  "blood"),
    ("Death Knight", "Frost"):        ("death-knight",  "frost"),
    ("Death Knight", "Unholy"):       ("death-knight",  "unholy"),
    ("Demon Hunter", "Havoc"):        ("demon-hunter",  "havoc"),
    ("Demon Hunter", "Vengeance"):    ("demon-hunter",  "vengeance"),
    ("Druid",        "Balance"):      ("druid",          "balance"),
    ("Druid",        "Feral"):        ("druid",          "feral"),
    ("Druid",        "Guardian"):     ("druid",          "guardian"),
    ("Druid",        "Restoration"):  ("druid",          "restoration"),
    ("Evoker",       "Devastation"):  ("evoker",         "devastation"),
    ("Evoker",       "Preservation"): ("evoker",         "preservation"),
    ("Evoker",       "Augmentation"): ("evoker",         "augmentation"),
    ("Hunter",       "Beast Mastery"):("hunter",         "beast-mastery"),
    ("Hunter",       "Marksmanship"): ("hunter",         "marksmanship"),
    ("Hunter",       "Survival"):     ("hunter",         "survival"),
    ("Mage",         "Arcane"):       ("mage",           "arcane"),
    ("Mage",         "Fire"):         ("mage",           "fire"),
    ("Mage",         "Frost"):        ("mage",           "frost"),
    ("Monk",         "Brewmaster"):   ("monk",           "brewmaster"),
    ("Monk",         "Mistweaver"):   ("monk",           "mistweaver"),
    ("Monk",         "Windwalker"):   ("monk",           "windwalker"),
    ("Paladin",      "Holy"):         ("paladin",        "holy"),
    ("Paladin",      "Protection"):   ("paladin",        "protection"),
    ("Paladin",      "Retribution"):  ("paladin",        "retribution"),
    ("Priest",       "Discipline"):   ("priest",         "discipline"),
    ("Priest",       "Holy"):         ("priest",         "holy"),
    ("Priest",       "Shadow"):       ("priest",         "shadow"),
    ("Rogue",        "Assassination"):("rogue",          "assassination"),
    ("Rogue",        "Outlaw"):       ("rogue",          "outlaw"),
    ("Rogue",        "Subtlety"):     ("rogue",          "subtlety"),
    ("Shaman",       "Elemental"):    ("shaman",         "elemental"),
    ("Shaman",       "Enhancement"):  ("shaman",         "enhancement"),
    ("Shaman",       "Restoration"):  ("shaman",         "restoration"),
    ("Warlock",      "Affliction"):   ("warlock",        "affliction"),
    ("Warlock",      "Demonology"):   ("warlock",        "demonology"),
    ("Warlock",      "Destruction"):  ("warlock",        "destruction"),
    ("Warrior",      "Arms"):         ("warrior",        "arms"),
    ("Warrior",      "Fury"):         ("warrior",        "fury"),
    ("Warrior",      "Protection"):   ("warrior",        "protection"),
}

# Icy Veins BIS URL: /wow/{spec}-{class}-pve-best-in-slot
# Uses kebab-case combined slug
_ICYVEINS_SLUG_MAP: dict[tuple[str, str], str] = {
    ("Death Knight", "Blood"):        "blood-death-knight",
    ("Death Knight", "Frost"):        "frost-death-knight",
    ("Death Knight", "Unholy"):       "unholy-death-knight",
    ("Demon Hunter", "Havoc"):        "havoc-demon-hunter",
    ("Demon Hunter", "Vengeance"):    "vengeance-demon-hunter",
    ("Druid",        "Balance"):      "balance-druid",
    ("Druid",        "Feral"):        "feral-druid",
    ("Druid",        "Guardian"):     "guardian-druid",
    ("Druid",        "Restoration"):  "restoration-druid",
    ("Evoker",       "Devastation"):  "devastation-evoker",
    ("Evoker",       "Preservation"): "preservation-evoker",
    ("Evoker",       "Augmentation"): "augmentation-evoker",
    ("Hunter",       "Beast Mastery"):"beast-mastery-hunter",
    ("Hunter",       "Marksmanship"): "marksmanship-hunter",
    ("Hunter",       "Survival"):     "survival-hunter",
    ("Mage",         "Arcane"):       "arcane-mage",
    ("Mage",         "Fire"):         "fire-mage",
    ("Mage",         "Frost"):        "frost-mage",
    ("Monk",         "Brewmaster"):   "brewmaster-monk",
    ("Monk",         "Mistweaver"):   "mistweaver-monk",
    ("Monk",         "Windwalker"):   "windwalker-monk",
    ("Paladin",      "Holy"):         "holy-paladin",
    ("Paladin",      "Protection"):   "protection-paladin",
    ("Paladin",      "Retribution"):  "retribution-paladin",
    ("Priest",       "Discipline"):   "discipline-priest",
    ("Priest",       "Holy"):         "holy-priest",
    ("Priest",       "Shadow"):       "shadow-priest",
    ("Rogue",        "Assassination"):"assassination-rogue",
    ("Rogue",        "Outlaw"):       "outlaw-rogue",
    ("Rogue",        "Subtlety"):     "subtlety-rogue",
    ("Shaman",       "Elemental"):    "elemental-shaman",
    ("Shaman",       "Enhancement"):  "enhancement-shaman",
    ("Shaman",       "Restoration"):  "restoration-shaman",
    ("Warlock",      "Affliction"):   "affliction-warlock",
    ("Warlock",      "Demonology"):   "demonology-warlock",
    ("Warlock",      "Destruction"):  "destruction-warlock",
    ("Warrior",      "Arms"):         "arms-warrior",
    ("Warrior",      "Fury"):         "fury-warrior",
    ("Warrior",      "Protection"):   "protection-warrior",
}

# Archon slot names → our normalised internal keys
_ARCHON_SLOT_MAP: dict[str, str] = {
    "head":      "head",
    "neck":      "neck",
    "shoulder":  "shoulder",
    "back":      "back",
    "cape":      "back",
    "chest":     "chest",
    "wrist":     "wrist",
    "gloves":    "hands",
    "hands":     "hands",
    "belt":      "waist",
    "waist":     "waist",
    "legs":      "legs",
    "feet":      "feet",
    "ring1":     "ring_1",
    "ring2":     "ring_2",
    "trinket1":  "trinket_1",
    "trinket2":  "trinket_2",
    "weapon1":   "main_hand",
    "weapon2":   "off_hand",
    "main_hand": "main_hand",
    "off_hand":  "off_hand",
}

# Technique priority order for each BIS source origin
_TECHNIQUE_ORDER: dict[str, list[str]] = {
    "archon":    ["json_embed"],
    "wowhead":   ["wh_gatherer"],
    "icy_veins": ["html_parse"],
    "manual":    ["manual"],
}

# HTTP timeouts for scraping
_HTTP_TIMEOUT = 20.0
_UGG_STATS_BASE = "https://stats2.u.gg/wow/builds/v29/all"
_WOWHEAD_TOOLTIP_BASE = "https://nether.wowhead.com/tooltip/item"

# Default headers to avoid obvious bot detection
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ---------------------------------------------------------------------------
# URL discovery
# ---------------------------------------------------------------------------


async def discover_targets(pool: asyncpg.Pool) -> dict:
    """Auto-generate scrape targets for all active spec × source × hero talent combos.

    Inserts missing rows into bis_scrape_targets.  Does NOT overwrite existing rows
    (uses ON CONFLICT DO NOTHING) so manually-entered URLs are preserved.

    Returns a stats dict: {inserted, skipped, total_expected}.
    """
    async with pool.acquire() as conn:
        # Load all active sources
        sources = await conn.fetch(
            "SELECT id, name, origin, content_type, is_active "
            "FROM guild_identity.bis_list_sources WHERE is_active = TRUE"
        )

        # Load all specs with class names
        specs = await conn.fetch(
            """
            SELECT s.id AS spec_id, s.name AS spec_name, c.name AS class_name
              FROM guild_identity.specializations s
              JOIN guild_identity.classes c ON c.id = s.class_id
            ORDER BY c.name, s.name
            """
        )

        # Load all hero talents per spec
        hero_talents = await conn.fetch(
            "SELECT id, spec_id, name, slug FROM guild_identity.hero_talents"
        )

    ht_by_spec: dict[int, list[dict]] = {}
    for ht in hero_talents:
        ht_by_spec.setdefault(ht["spec_id"], []).append(dict(ht))

    inserted = 0
    skipped = 0
    expected = 0

    async with pool.acquire() as conn:
        for source in sources:
            source_id = source["id"]
            origin = source["origin"]

            for spec in specs:
                spec_id = spec["spec_id"]
                class_name = spec["class_name"]
                spec_name = spec["spec_name"]

                spec_hero_talents = ht_by_spec.get(spec_id, [])
                if not spec_hero_talents:
                    # No hero talents seeded for this spec — skip
                    continue

                for ht in spec_hero_talents:
                    ht_id = ht["id"]
                    ht_slug = ht["slug"]

                    # Each source has exactly one content_type — use it directly
                    content_type = source["content_type"] or "overall"
                    expected += 1
                    url = _build_url(
                        origin, class_name, spec_name, ht_slug, content_type
                    )
                    technique = _TECHNIQUE_ORDER.get(origin, ["html_parse"])[0]

                    result = await conn.fetchrow(
                        """
                        INSERT INTO guild_identity.bis_scrape_targets
                            (source_id, spec_id, hero_talent_id, content_type,
                             url, preferred_technique, status)
                        VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                        ON CONFLICT (source_id, spec_id, hero_talent_id, content_type)
                        DO NOTHING
                        RETURNING id
                        """,
                        source_id, spec_id, ht_id, content_type,
                        url, technique,
                    )
                    if result:
                        inserted += 1
                    else:
                        skipped += 1

    logger.info(
        "BIS target discovery: inserted=%d, already_existed=%d, total_expected=%d",
        inserted, skipped, expected,
    )
    return {"inserted": inserted, "skipped": skipped, "total_expected": expected}


def _build_url(
    origin: str,
    class_name: str,
    spec_name: str,
    hero_slug: str,
    content_type: str,
) -> Optional[str]:
    """Generate the BIS page URL for a given source origin + spec + hero + content type."""
    key = (class_name, spec_name)

    if origin == "archon":
        slugs = _UGG_SLUG_MAP.get(key)
        if not slugs:
            return None
        class_slug, spec_slug = slugs
        base = f"https://u.gg/wow/{spec_slug}/{class_slug}/gear?hero={hero_slug}"
        if content_type == "raid":
            return base + "&role=raid"
        elif content_type == "mythic_plus":
            return base + "&role=mythicdungeon"
        else:  # overall — no role param
            return base

    elif origin == "wowhead":
        slugs = _WOWHEAD_SLUG_MAP.get(key)
        if not slugs:
            return None
        class_slug, spec_slug = slugs
        base = f"https://www.wowhead.com/guide/classes/{class_slug}/{spec_slug}/best-in-slot"
        if content_type == "raid":
            anchor = "#raid-bis"
        elif content_type == "mythic_plus":
            anchor = "#mythic-plus-bis"
        else:
            anchor = ""
        return base + anchor

    elif origin == "icy_veins":
        combined = _ICYVEINS_SLUG_MAP.get(key)
        if not combined:
            return None
        area = "2" if content_type == "raid" else "3" if content_type == "mythic_plus" else "1"
        return (
            f"https://www.icy-veins.com/wow/{combined}-best-in-slot?area=area_{area}"
        )

    return None


# ---------------------------------------------------------------------------
# Main sync entry points
# ---------------------------------------------------------------------------


async def sync_all(pool: asyncpg.Pool) -> dict:
    """Run extraction for every active BIS source across all specs."""
    async with pool.acquire() as conn:
        sources = await conn.fetch(
            "SELECT id, name FROM guild_identity.bis_list_sources WHERE is_active = TRUE"
        )

    total_stats: dict = {"sources_run": 0, "items_upserted": 0, "targets_run": 0, "errors": 0}
    for source in sources:
        stats = await sync_source(pool, source["id"])
        total_stats["sources_run"] += 1
        total_stats["items_upserted"] += stats.get("items_upserted", 0)
        total_stats["targets_run"] += stats.get("targets_run", 0)
        total_stats["errors"] += stats.get("errors", 0)

    return total_stats


async def sync_source(
    pool: asyncpg.Pool,
    source_id: int,
    spec_ids: Optional[list[int]] = None,
) -> dict:
    """Run extraction for one BIS source, optionally filtered to specific specs.

    Returns a stats dict: {targets_run, items_upserted, errors}.
    """
    async with pool.acquire() as conn:
        query = """
            SELECT t.id, t.source_id, t.url, t.preferred_technique,
                   t.spec_id, t.hero_talent_id, t.content_type
              FROM guild_identity.bis_scrape_targets t
             WHERE t.source_id = $1
               AND t.url IS NOT NULL
        """
        args: list = [source_id]
        if spec_ids:
            query += " AND t.spec_id = ANY($2)"
            args.append(spec_ids)

        targets = await conn.fetch(query, *args)

    stats = {"targets_run": 0, "items_upserted": 0, "errors": 0}

    for target in targets:
        target_dict = dict(target)
        try:
            result = await sync_target(pool, target_dict["id"], _target_row=target_dict)
            stats["targets_run"] += 1
            stats["items_upserted"] += result.get("items_upserted", 0)
        except Exception as exc:
            logger.error("Error syncing target %d: %s", target_dict["id"], exc, exc_info=True)
            stats["errors"] += 1

        # Be polite to external servers
        await asyncio.sleep(1.5)

    return stats


async def sync_target(
    pool: asyncpg.Pool,
    target_id: int,
    _target_row: Optional[dict] = None,
) -> dict:
    """Re-sync a single scrape target.  Returns {items_upserted, technique, status}."""
    async with pool.acquire() as conn:
        if _target_row is None:
            row = await conn.fetchrow(
                """
                SELECT t.id, t.url, t.preferred_technique, t.source_id,
                       t.spec_id, t.hero_talent_id, t.content_type,
                       s.origin
                  FROM guild_identity.bis_scrape_targets t
                  JOIN guild_identity.bis_list_sources s ON s.id = t.source_id
                 WHERE t.id = $1
                """,
                target_id,
            )
            if row is None:
                raise ValueError(f"No scrape target with id={target_id}")
            _target_row = dict(row)
        else:
            # Need source origin
            origin_row = await conn.fetchrow(
                "SELECT origin FROM guild_identity.bis_list_sources WHERE id = $1",
                _target_row["source_id"],
            )
            _target_row = {**_target_row, "origin": origin_row["origin"] if origin_row else ""}

    url = _target_row.get("url")
    technique = _target_row.get("preferred_technique") or _TECHNIQUE_ORDER.get(
        _target_row.get("origin", ""), ["html_parse"]
    )[0]
    spec_id = _target_row["spec_id"]
    hero_talent_id = _target_row.get("hero_talent_id")
    source_id = _target_row["source_id"]

    if not url:
        return {"items_upserted": 0, "technique": technique, "status": "failed", "error": "No URL"}

    # Run extraction
    slots, error = await _extract(url, technique)

    now = datetime.now(timezone.utc)
    items_upserted = 0

    if slots:
        items_upserted = await _upsert_bis_entries(
            pool, source_id, spec_id, hero_talent_id, slots
        )
        status = "success" if items_upserted == len(SLOT_ORDER) else "partial"
    else:
        status = "failed"

    # Log and stamp
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_identity.bis_scrape_log
                (target_id, technique, status, items_found, error_message, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            target_id, technique, status, items_upserted, error, now,
        )
        await conn.execute(
            """
            UPDATE guild_identity.bis_scrape_targets
               SET status = $1, items_found = $2, last_fetched = $3
             WHERE id = $4
            """,
            status, items_upserted, now, target_id,
        )

    return {"items_upserted": items_upserted, "technique": technique, "status": status}


# ---------------------------------------------------------------------------
# Extraction dispatcher
# ---------------------------------------------------------------------------


async def _extract(
    url: str, technique: str
) -> tuple[list[SimcSlot], Optional[str]]:
    """Dispatch to the appropriate extractor.

    Returns (slots, error_message) — slots is empty list on failure.
    """
    try:
        if technique == "json_embed":
            slots = await _extract_archon(url)
        elif technique == "wh_gatherer":
            slots = await _extract_wowhead(url)
        elif technique == "html_parse":
            slots = await _extract_icy_veins(url)
        elif technique == "manual":
            # Manual entries are written directly via the API — never scraped
            return [], "manual technique — use the API to enter items"
        else:
            return [], f"unknown technique: {technique}"
        return slots, None
    except httpx.TimeoutException:
        return [], "request timed out"
    except httpx.HTTPStatusError as exc:
        return [], f"HTTP {exc.response.status_code}"
    except Exception as exc:
        logger.warning("Extraction failed for %s (%s): %s", url, technique, exc)
        return [], str(exc)


# ---------------------------------------------------------------------------
# Archon / u.gg extractor  (json_embed)
# ---------------------------------------------------------------------------


async def _extract_archon(url: str) -> list[SimcSlot]:
    """Fetch u.gg page and extract BIS items from embedded SSR JSON.

    u.gg embeds a large `window.__SSR_DATA__` JSON blob in the HTML which
    contains the items table with per-slot BIS data keyed by hero talent.
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    # Try to find window.__SSR_DATA__ = {...}; in the HTML
    ssr_match = re.search(
        r"window\.__SSR_DATA__\s*=\s*(\{.+?\});\s*(?:window|</script>)",
        html,
        re.DOTALL,
    )
    if not ssr_match:
        # Try alternate pattern (sometimes no trailing window)
        ssr_match = re.search(
            r"window\.__SSR_DATA__\s*=\s*(\{.+?\})\s*;",
            html,
            re.DOTALL,
        )

    if ssr_match:
        try:
            data = json.loads(ssr_match.group(1))
            return _parse_archon_ssr(data)
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: try the stats2 direct JSON endpoint
    # Build the stats2 URL from the u.gg page URL
    stats2_url = _ugg_to_stats2_url(url)
    if stats2_url:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
        ) as client:
            try:
                r2 = await client.get(stats2_url)
                r2.raise_for_status()
                data = r2.json()
                return _parse_archon_items_table(data)
            except Exception as exc:
                logger.debug("stats2 fallback failed for %s: %s", stats2_url, exc)

    return []


def _ugg_to_stats2_url(page_url: str) -> Optional[str]:
    """Attempt to derive a stats2.u.gg JSON URL from a u.gg page URL.

    Pattern: https://u.gg/wow/{spec}/{class}/gear?hero={hero}&role={role}
    → https://stats2.u.gg/wow/builds/v29/all/{Class}/{Class}_{spec}_itemsTable.json
    """
    m = re.search(r"u\.gg/wow/([^/]+)/([^/]+)/gear", page_url)
    if not m:
        return None
    spec_slug = m.group(1)
    class_slug = m.group(2).replace("-", "")  # death-knight → deathknight
    class_cap = class_slug.capitalize()
    return f"{_UGG_STATS_BASE}/{class_cap}/{class_cap}_{spec_slug}_itemsTable.json"


def _parse_archon_ssr(data: dict) -> list[SimcSlot]:
    """Parse items from the window.__SSR_DATA__ blob."""
    # Navigate: data → pageProps → wowData → items_table → items
    try:
        page_props = data.get("pageProps") or data
        wow_data = page_props.get("wowData") or page_props
        items_table = wow_data.get("items_table", {})
        items_by_slot = items_table.get("items", {})
        return _archon_items_to_slots(items_by_slot)
    except (AttributeError, TypeError):
        return []


def _parse_archon_items_table(data: dict) -> list[SimcSlot]:
    """Parse items from the stats2.u.gg direct JSON response."""
    try:
        items_by_slot = data.get("items_table", {}).get("items", {})
        return _archon_items_to_slots(items_by_slot)
    except (AttributeError, TypeError):
        return []


def _archon_items_to_slots(items_by_slot: dict) -> list[SimcSlot]:
    """Convert Archon's per-slot items dict into SimcSlot list."""
    slots: list[SimcSlot] = []
    for archon_slot, slot_data in items_by_slot.items():
        normalised = _ARCHON_SLOT_MAP.get(archon_slot.lower())
        if normalised is None:
            continue
        items = slot_data.get("items") or []
        if not items:
            continue
        # Take the most popular item (first by count/perc)
        top = max(items, key=lambda i: float(i.get("perc", 0) or 0), default=None)
        if top is None:
            continue
        item_id = top.get("item_id")
        if not item_id:
            continue
        slots.append(SimcSlot(
            slot=normalised,
            blizzard_item_id=int(item_id),
            bonus_ids=[],
            enchant_id=top.get("enchant_id"),
            gem_ids=[],
            quality_track=None,
        ))
    return slots


# ---------------------------------------------------------------------------
# Wowhead extractor  (wh_gatherer)
# ---------------------------------------------------------------------------

_WH_GATHERER_RE = re.compile(
    r"WH\.Gatherer\.addData\(\s*\d+\s*,\s*\d+\s*,\s*(\{.+?\})\s*\)",
    re.DOTALL,
)
_ITEM_MARKUP_RE = re.compile(r"\[item=(\d+)[^\]]*\]")
_WOWHEAD_SLOT_MAP = {
    1: "head", 2: "neck", 3: "shoulder", 5: "chest",
    6: "waist", 7: "legs", 8: "feet", 9: "wrist", 10: "hands",
    11: "ring_1", 16: "ring_2", 12: "trinket_1", 17: "trinket_2",
    13: "back", 14: "main_hand", 15: "off_hand", 22: "main_hand",
    23: "main_hand",
}


async def _extract_wowhead(url: str) -> list[SimcSlot]:
    """Fetch Wowhead BIS guide and extract items via WH.Gatherer.addData() calls."""
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    # Build item metadata map from WH.Gatherer.addData() calls
    item_meta: dict[int, dict] = {}
    for m in _WH_GATHERER_RE.finditer(html):
        try:
            chunk = json.loads(m.group(1))
            for item_id_str, meta in chunk.items():
                try:
                    iid = int(item_id_str)
                    item_meta[iid] = meta
                except (ValueError, TypeError):
                    continue
        except json.JSONDecodeError:
            continue

    if not item_meta:
        return []

    # Find item references in the BIS content — [item=ID] markup
    referenced_ids = [int(x) for x in _ITEM_MARKUP_RE.findall(html)]

    seen_slots: dict[str, int] = {}  # slot → first item_id found
    for item_id in referenced_ids:
        meta = item_meta.get(item_id)
        if not meta:
            continue
        slot_code = meta.get("slotbak")
        slot_name = _WOWHEAD_SLOT_MAP.get(slot_code)
        if slot_name and slot_name not in seen_slots:
            seen_slots[slot_name] = item_id

    return [
        SimcSlot(
            slot=slot,
            blizzard_item_id=iid,
            bonus_ids=[],
            enchant_id=None,
            gem_ids=[],
            quality_track=None,
        )
        for slot, iid in seen_slots.items()
    ]


# ---------------------------------------------------------------------------
# Icy Veins extractor  (html_parse)
# ---------------------------------------------------------------------------

_IV_ITEM_NAME_RE = re.compile(
    r'class="[^"]*recommended[^"]*"[^>]*>.*?<[^>]+>([^<]+)</[^>]+>',
    re.DOTALL | re.IGNORECASE,
)
_IV_ITEM_ID_RE = re.compile(r'data-item-id="(\d+)"')
_IV_ITEM_LINK_RE = re.compile(r'wowhead\.com/item=(\d+)')


async def _extract_icy_veins(url: str) -> list[SimcSlot]:
    """Fetch Icy Veins BIS page and extract item IDs from HTML.

    Icy Veins is the hardest to scrape (JS-heavy, no convenient JSON blob).
    This extractor looks for item IDs embedded as data attributes or Wowhead
    links within BIS table containers.  Results are best-effort.
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    # Collect all Wowhead item IDs mentioned on the page
    item_ids_from_links = [int(x) for x in _IV_ITEM_LINK_RE.findall(html)]
    item_ids_from_attrs = [int(x) for x in _IV_ITEM_ID_RE.findall(html)]

    # Union, dedup, preserving order
    seen: set[int] = set()
    candidate_ids: list[int] = []
    for iid in item_ids_from_links + item_ids_from_attrs:
        if iid not in seen:
            seen.add(iid)
            candidate_ids.append(iid)

    if not candidate_ids:
        return []

    # For each candidate item, fetch its Wowhead slot via tooltip API
    # Limit to first 50 candidates to avoid too many requests
    slots: list[SimcSlot] = []
    seen_internal_slots: set[str] = set()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for item_id in candidate_ids[:50]:
            try:
                r = await client.get(
                    f"{_WOWHEAD_TOOLTIP_BASE}/{item_id}?dataEnv=1&locale=0"
                )
                if r.status_code != 200:
                    continue
                tooltip = r.json()
                slot_code = tooltip.get("slotbak") or tooltip.get("slot")
                slot_name = _WOWHEAD_SLOT_MAP.get(slot_code)
                if slot_name and slot_name not in seen_internal_slots:
                    seen_internal_slots.add(slot_name)
                    slots.append(SimcSlot(
                        slot=slot_name,
                        blizzard_item_id=item_id,
                        bonus_ids=[],
                        enchant_id=None,
                        gem_ids=[],
                        quality_track=None,
                    ))
                await asyncio.sleep(0.3)
            except Exception:
                continue

    return slots


# ---------------------------------------------------------------------------
# SimC import extractor
# ---------------------------------------------------------------------------


def _extract_simc(text: str) -> list[SimcSlot]:
    """Parse a SimC profile text and return gear slots.

    Delegates to simc_parser.parse_gear_slots().  Used by import_simc().
    """
    return parse_gear_slots(text)


async def import_simc(
    pool: asyncpg.Pool,
    text: str,
    source_id: int,
    spec_id: int,
    hero_talent_id: Optional[int],
) -> dict:
    """Import a SimC BIS profile as bis_list_entries for a spec.

    Creates/updates a bis_scrape_targets row with technique='simc' and
    appends a row to bis_scrape_log.  Manual SimC imports are treated as
    'locked' — logged with status='success' so the matrix shows them clearly.

    Returns {items_upserted, status}.
    """
    slots = _extract_simc(text)
    items_upserted = 0
    now = datetime.now(timezone.utc)

    if slots:
        items_upserted = await _upsert_bis_entries(
            pool, source_id, spec_id, hero_talent_id, slots
        )

    status = "success" if slots else "failed"
    error_message = None if slots else "No gear slots found in SimC text"

    # Upsert scrape target (simc technique, no URL needed)
    async with pool.acquire() as conn:
        target_row = await conn.fetchrow(
            """
            INSERT INTO guild_identity.bis_scrape_targets
                (source_id, spec_id, hero_talent_id, content_type,
                 url, preferred_technique, status, items_found, last_fetched)
            VALUES ($1, $2, $3, 'overall', NULL, 'simc', $4, $5, $6)
            ON CONFLICT (source_id, spec_id, hero_talent_id, content_type)
            DO UPDATE
                SET preferred_technique = 'simc',
                    status = EXCLUDED.status,
                    items_found = EXCLUDED.items_found,
                    last_fetched = EXCLUDED.last_fetched
            RETURNING id
            """,
            source_id, spec_id, hero_talent_id, status, items_upserted, now,
        )
        target_id = target_row["id"]

        await conn.execute(
            """
            INSERT INTO guild_identity.bis_scrape_log
                (target_id, technique, status, items_found, error_message, created_at)
            VALUES ($1, 'simc', $2, $3, $4, $5)
            """,
            target_id, status, items_upserted, error_message, now,
        )

    return {"items_upserted": items_upserted, "status": status}


# ---------------------------------------------------------------------------
# BIS entry upsert
# ---------------------------------------------------------------------------


async def _upsert_bis_entries(
    pool: asyncpg.Pool,
    source_id: int,
    spec_id: int,
    hero_talent_id: Optional[int],
    slots: list[SimcSlot],
) -> int:
    """Upsert extracted BIS items into bis_list_entries.

    Ensures each item exists in wow_items (lazy-creates a stub if not) then
    upserts into bis_list_entries.  Returns the number of slots upserted.
    """
    upserted = 0
    async with pool.acquire() as conn:
        for slot_data in slots:
            # Ensure wow_items row exists (stub — full metadata fetched by item_service)
            await conn.execute(
                """
                INSERT INTO guild_identity.wow_items
                    (blizzard_item_id, name, slot_type)
                VALUES ($1, '', $2)
                ON CONFLICT (blizzard_item_id) DO NOTHING
                """,
                slot_data.blizzard_item_id,
                slot_data.slot,
            )

            item_row = await conn.fetchrow(
                "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
                slot_data.blizzard_item_id,
            )
            if item_row is None:
                continue
            item_id = item_row["id"]

            await conn.execute(
                """
                INSERT INTO guild_identity.bis_list_entries
                    (source_id, spec_id, hero_talent_id, slot, item_id, priority)
                VALUES ($1, $2, $3, $4, $5, 1)
                ON CONFLICT (source_id, spec_id, hero_talent_id, slot, item_id)
                DO UPDATE SET priority = 1
                """,
                source_id, spec_id, hero_talent_id, slot_data.slot, item_id,
            )
            upserted += 1

    return upserted


# ---------------------------------------------------------------------------
# Cross-reference
# ---------------------------------------------------------------------------


async def cross_reference(
    pool: asyncpg.Pool,
    spec_id: int,
    hero_talent_id: Optional[int],
) -> dict:
    """Compare BIS recommendations across all sources for one spec + hero talent.

    Returns a dict keyed by slot, where each value is a list of
    {source_name, source_id, item_id, blizzard_item_id, item_name, agrees}
    entries.  'agrees' is True when all active sources picked the same item.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                e.slot,
                s.id   AS source_id,
                s.name AS source_name,
                wi.id  AS item_id,
                wi.blizzard_item_id,
                wi.name AS item_name
              FROM guild_identity.bis_list_entries e
              JOIN guild_identity.bis_list_sources s ON s.id = e.source_id
              JOIN guild_identity.wow_items        wi ON wi.id = e.item_id
             WHERE e.spec_id = $1
               AND (e.hero_talent_id = $2 OR e.hero_talent_id IS NULL)
               AND s.is_active = TRUE
             ORDER BY e.slot, s.sort_order
            """,
            spec_id, hero_talent_id,
        )

    # Group by slot
    by_slot: dict[str, list[dict]] = {}
    for row in rows:
        slot = row["slot"]
        by_slot.setdefault(slot, []).append({
            "source_id": row["source_id"],
            "source_name": row["source_name"],
            "item_id": row["item_id"],
            "blizzard_item_id": row["blizzard_item_id"],
            "item_name": row["item_name"],
        })

    # Mark agreement
    result: dict[str, list[dict]] = {}
    for slot in SLOT_ORDER:
        entries = by_slot.get(slot, [])
        unique_items = {e["blizzard_item_id"] for e in entries}
        agree = len(unique_items) <= 1
        for entry in entries:
            entry["agrees"] = agree
        result[slot] = entries

    return result


# ---------------------------------------------------------------------------
# Matrix data for admin dashboard
# ---------------------------------------------------------------------------


async def get_matrix(pool: asyncpg.Pool) -> dict:
    """Return the spec × source status matrix for the admin BIS dashboard.

    Returns {sources: [...], specs: [...], cells: {spec_id: {source_id: {...}}}}
    """
    async with pool.acquire() as conn:
        sources = await conn.fetch(
            """
            SELECT id, name, short_label, origin, content_type, is_active, sort_order
              FROM guild_identity.bis_list_sources
             WHERE is_active = TRUE
             ORDER BY sort_order
            """
        )

        specs = await conn.fetch(
            """
            SELECT s.id, s.name AS spec_name, c.name AS class_name
              FROM guild_identity.specializations s
              JOIN guild_identity.classes c ON c.id = s.class_id
             ORDER BY c.name, s.name
            """
        )

        targets = await conn.fetch(
            """
            SELECT t.source_id, t.spec_id, t.hero_talent_id,
                   t.status, t.items_found, t.last_fetched, t.preferred_technique,
                   t.content_type, t.id AS target_id
              FROM guild_identity.bis_scrape_targets t
            """
        )

        hero_talents = await conn.fetch(
            "SELECT id, spec_id, name, slug FROM guild_identity.hero_talents ORDER BY id"
        )

    ht_by_spec: dict[int, list[dict]] = {}
    for ht in hero_talents:
        ht_by_spec.setdefault(ht["spec_id"], []).append(dict(ht))

    # Build cell map: (spec_id, source_id) → best/combined status
    cells: dict[str, dict[str, dict]] = {}
    for t in targets:
        spec_key = str(t["spec_id"])
        src_key = str(t["source_id"])
        cells.setdefault(spec_key, {})
        existing = cells[spec_key].get(src_key)
        # Prefer success over partial over pending
        status_rank = {"success": 3, "partial": 2, "pending": 1, "failed": 0}
        new_rank = status_rank.get(t["status"] or "pending", 0)
        if existing is None or new_rank > status_rank.get(existing.get("status", "pending"), 0):
            cells[spec_key][src_key] = {
                "status": t["status"],
                "items_found": t["items_found"],
                "last_fetched": t["last_fetched"].isoformat() if t["last_fetched"] else None,
                "technique": t["preferred_technique"],
                "target_id": t["target_id"],
            }

    return {
        "sources": [dict(s) for s in sources],
        "specs": [
            {
                "id": s["id"],
                "spec_name": s["spec_name"],
                "class_name": s["class_name"],
                "hero_talents": ht_by_spec.get(s["id"], []),
            }
            for s in specs
        ],
        "cells": cells,
    }
