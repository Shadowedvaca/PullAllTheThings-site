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
# Slug helper — mirrors guide_links._slug, uses separator from guide_sites


def _slug(name: str, sep: str = "-") -> str:
    """Convert a display name to a lowercase URL slug."""
    return name.lower().replace(" ", sep)

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
    "icy_veins": ["html_parse"],  # STUB — html_parse returns [] for IV; see _extract_icy_veins
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
    """Auto-generate scrape targets for all active spec × source combos.

    Archon + Wowhead: one target per spec × hero talent (URLs embed the HT slug).
    Icy Veins: one target per spec (IV pages are not HT-specific; all content is
               on a single page at a role-derived URL with no HT variation).

    Inserts missing rows into bis_scrape_targets.  Does NOT overwrite existing rows
    (uses ON CONFLICT DO NOTHING) so manually-entered URLs are preserved.

    Returns a stats dict: {inserted, skipped, total_expected}.
    """
    async with pool.acquire() as conn:
        # Load all active sources, joining guide_sites for the slug_separator
        sources = await conn.fetch(
            """
            SELECT s.id, s.name, s.origin, s.content_type, s.is_active,
                   COALESCE(gs.slug_separator, '-') AS slug_separator
              FROM guild_identity.bis_list_sources s
              LEFT JOIN common.guide_sites gs ON gs.id = s.guide_site_id
             WHERE s.is_active = TRUE
            """
        )

        # Load all specs with class and role names
        specs = await conn.fetch(
            """
            SELECT s.id AS spec_id, s.name AS spec_name, c.name AS class_name,
                   r.name AS role_name
              FROM guild_identity.specializations s
              JOIN guild_identity.classes c ON c.id = s.class_id
              LEFT JOIN guild_identity.roles r ON r.id = s.default_role_id
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
            content_type = source["content_type"] or "overall"

            for spec in specs:
                spec_id = spec["spec_id"]
                class_name = spec["class_name"]
                spec_name = spec["spec_name"]
                role_name = spec["role_name"] or "dps"

                if origin == "icy_veins":
                    # IV has one page per spec — no HT variation in the URL.
                    # All content types (raid/m+/overall) live on the same page,
                    # toggled client-side.  We insert one row per spec per IV source
                    # with hero_talent_id=NULL so it applies to all builds.
                    url = _iv_base_url(class_name, spec_name, role_name)
                    expected += 1
                    result = await conn.fetchrow(
                        """
                        INSERT INTO guild_identity.bis_scrape_targets
                            (source_id, spec_id, hero_talent_id, content_type,
                             url, preferred_technique, status)
                        VALUES ($1, $2, NULL, $3, $4, 'html_parse', 'pending')
                        ON CONFLICT (source_id, spec_id, url)
                        DO NOTHING
                        RETURNING id
                        """,
                        source_id, spec_id, content_type, url,
                    )
                    if result:
                        inserted += 1
                    else:
                        skipped += 1

                else:
                    spec_hero_talents = ht_by_spec.get(spec_id, [])
                    if not spec_hero_talents:
                        # No hero talents seeded for this spec — skip
                        continue

                    for ht in spec_hero_talents:
                        ht_id = ht["id"]
                        ht_slug = ht["slug"]
                        slug_sep = source["slug_separator"]
                        expected += 1
                        url = _build_url(
                            origin, class_name, spec_name, ht_slug, content_type, slug_sep
                        )
                        technique = _TECHNIQUE_ORDER.get(origin, ["html_parse"])[0]

                        result = await conn.fetchrow(
                            """
                            INSERT INTO guild_identity.bis_scrape_targets
                                (source_id, spec_id, hero_talent_id, content_type,
                                 url, preferred_technique, status)
                            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                            ON CONFLICT (source_id, spec_id, url)
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


async def discover_iv_areas(pool: asyncpg.Pool) -> dict:
    """No-op stub — IV discovery is now part of discover_targets.

    Icy Veins pages don't use URL-based area parameters; all content is
    client-side toggled.  IV targets (one per spec per source) are now
    generated directly in discover_targets using _iv_base_url().
    """
    logger.info("discover_iv_areas called — IV targets now generated by discover_targets; no-op")
    return {"inserted": 0, "skipped": 0, "failed": 0, "total_specs": 0}


# IV area tab regex — written assuming IV tabs used ?area=area_N URL params.
# IV does NOT use URL parameters — tabs are CSS class toggles driven by JS.
# This regex never matched anything on a live IV page.
# Kept for context; see reference/PHASE_Z_ICY_VEINS_SCRAPE-idea-only.md.
_IV_AREA_LINK_RE = re.compile(
    r'href="[^"]*\?area=(area_\d+)"[^>]*>\s*(.*?)\s*</a>',
    re.DOTALL | re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


async def _fetch_iv_areas(base_url: str) -> dict[str, str]:
    """Orphaned helper — discover_iv_areas is now a no-op stub.

    Originally fetched IV pages to find ?area=area_N tab links.
    IV doesn't use URL-based tabs; this always returned an empty dict.
    Kept in place for reference; safe to remove in a future cleanup.
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(base_url)
        response.raise_for_status()
        html = response.text

    areas: dict[str, str] = {}
    seen_labels: set[str] = set()
    for m in _IV_AREA_LINK_RE.finditer(html):
        area_key = m.group(1)
        raw_label = m.group(2)
        label = _HTML_TAG_RE.sub("", raw_label).strip()
        if not label or area_key in areas:
            continue
        # Skip duplicate labels (same tab text appearing multiple times in nav/footer)
        if label in seen_labels:
            continue
        seen_labels.add(label)
        areas[area_key] = label

    return areas


def _iv_base_url(class_name: str, spec_name: str, role_name: str) -> str:
    """Build the Icy Veins BIS base URL for a spec (without area param)."""
    cls  = _slug(class_name, "-")
    spec = _slug(spec_name,  "-")
    role = _iv_bis_role(role_name)
    return f"https://www.icy-veins.com/wow/{spec}-{cls}-pve-{role}-gear-best-in-slot"


def _iv_bis_role(role_name: str) -> str:
    """Map a WoW combat role name to the Icy Veins URL role slug."""
    r = (role_name or "").lower()
    if "tank" in r:
        return "tank"
    if "heal" in r:
        return "healer"
    return "dps"


def _categorize_iv_area(
    label: str, hero_talent_names: list[str]
) -> tuple[str, Optional[str]]:
    """Fuzzy-match an IV area tab label to (content_type, hero_talent_name).

    Rules (checked in order):
      1. "mythic" in label → mythic_plus, no HT (M+ list applies to all builds)
      2. "raid" in label (without HT name) → raid, no HT (raid list applies to all builds)
      3. Hero talent name in label → overall, matched HT (this is the per-HT overall list)
      4. Otherwise → overall, no HT (generic overall, applies to all builds)

    The key insight: Icy Veins gives each hero talent its own "overall" BIS list,
    while raid-only and M+-only lists are shared across both hero talents for that spec.
    If no HT is specified in the label, hero_talent_id stays NULL which means
    "applies to all builds" — the fallback when no HT-specific data exists.
    """
    low = label.lower()
    if "mythic" in low:
        return "mythic_plus", None
    if "raid" in low:
        return "raid", None
    for ht_name in hero_talent_names:
        if ht_name.lower() in low:
            return "overall", ht_name
    return "overall", None


def _build_url(
    origin: str,
    class_name: str,
    spec_name: str,
    hero_slug: str,
    content_type: str,
    slug_sep: str = "-",
) -> Optional[str]:
    """Generate the BIS page URL for a given source origin + spec + hero + content type.

    slug_sep comes from common.guide_sites.slug_separator for the linked site
    (e.g. '_' for u.gg, '-' for Wowhead/Icy Veins).  This lets the admin fix
    class/spec slug issues in the Reference Tables → Guide Sites UI.
    """
    cls  = _slug(class_name, slug_sep)
    spec = _slug(spec_name,  slug_sep)

    if origin == "archon":
        base = f"https://u.gg/wow/{spec}/{cls}/gear?hero={hero_slug}"
        if content_type == "raid":
            return base + "&role=raid"
        elif content_type == "mythic_plus":
            return base + "&role=mythicdungeon"
        else:
            return base

    elif origin == "wowhead":
        # Wowhead always uses hyphens regardless of guide_sites separator.
        # All content types point to the same bis-gear page — Wowhead has one
        # combined BIS section per spec with no raid/M+ split.
        cls_wh  = _slug(class_name, "-")
        spec_wh = _slug(spec_name,  "-")
        return f"https://www.wowhead.com/guide/classes/{cls_wh}/{spec_wh}/bis-gear#bis-gear"

    # Icy Veins — targets come from discover_iv_areas (which uses _iv_base_url with
    # the real role name).  discover_targets skips icy_veins, so this should never
    # be reached; return None to surface any accidental call clearly.

    return None


# ---------------------------------------------------------------------------
# Main sync entry points
# ---------------------------------------------------------------------------


async def sync_all(pool: asyncpg.Pool) -> dict:
    """Run extraction for every active non-IV source, grouped by spec.

    Processes all sources for one spec before moving to the next so that:
      - requests to each site are naturally spaced across all specs
      - per-spec data is complete before moving on
      - Icy Veins targets are skipped (extraction not yet implemented)
    """
    async with pool.acquire() as conn:
        targets = await conn.fetch(
            """
            SELECT t.id, t.source_id, t.spec_id, t.hero_talent_id, t.content_type,
                   t.url, t.preferred_technique, s.origin
              FROM guild_identity.bis_scrape_targets t
              JOIN guild_identity.bis_list_sources s ON s.id = t.source_id
              JOIN guild_identity.specializations sp ON sp.id = t.spec_id
              JOIN guild_identity.classes c ON c.id = sp.class_id
             WHERE s.is_active = TRUE
               AND s.origin != 'icy_veins'
               AND t.url IS NOT NULL
             ORDER BY c.name, sp.name, t.hero_talent_id, s.sort_order
            """
        )

    # Group targets by spec so we process all sources for one spec together
    spec_targets: dict[int, list[dict]] = {}
    for t in targets:
        spec_targets.setdefault(t["spec_id"], []).append(dict(t))

    total_stats: dict = {"targets_run": 0, "items_upserted": 0, "errors": 0}

    for spec_id, spec_target_list in spec_targets.items():
        for target in spec_target_list:
            try:
                result = await sync_target(pool, target["id"], _target_row=target)
                total_stats["targets_run"] += 1
                total_stats["items_upserted"] += result.get("items_upserted", 0)
                if result.get("status") == "failed":
                    total_stats["errors"] += 1
            except Exception as exc:
                logger.error("Error syncing target %d: %s", target["id"], exc, exc_info=True)
                total_stats["errors"] += 1
            await asyncio.sleep(1.5)

        # Brief pause between specs to spread load across sites
        await asyncio.sleep(1.0)

    return total_stats


async def sync_spec(pool: asyncpg.Pool, spec_id: int) -> dict:
    """Sync all active non-IV targets for one spec (synchronous).

    Used by the frontend to drive per-spec progress updates — each call
    handles one spec and returns immediately with results so the UI can
    show live progress without polling.
    """
    async with pool.acquire() as conn:
        targets = await conn.fetch(
            """
            SELECT t.id, t.source_id, t.spec_id, t.hero_talent_id, t.content_type,
                   t.url, t.preferred_technique, s.origin
              FROM guild_identity.bis_scrape_targets t
              JOIN guild_identity.bis_list_sources s ON s.id = t.source_id
             WHERE t.spec_id = $1
               AND s.is_active = TRUE
               AND s.origin != 'icy_veins'
               AND t.url IS NOT NULL
            """,
            spec_id,
        )

    stats = {"targets_run": 0, "items_upserted": 0, "errors": 0}
    for target in targets:
        target_dict = dict(target)
        try:
            result = await sync_target(pool, target_dict["id"], _target_row=target_dict)
            stats["targets_run"] += 1
            stats["items_upserted"] += result.get("items_upserted", 0)
            if result.get("status") == "failed":
                stats["errors"] += 1
        except Exception as exc:
            logger.error("Error syncing target %d: %s", target_dict["id"], exc, exc_info=True)
            stats["errors"] += 1
        await asyncio.sleep(1.0)

    return stats


async def sync_source(
    pool: asyncpg.Pool,
    source_id: int,
    spec_ids: Optional[list[int]] = None,
) -> dict:
    """Run extraction for one BIS source, optionally filtered to specific specs.

    Skips IV sources (extraction not yet implemented).
    Returns a stats dict: {targets_run, items_upserted, errors}.
    """
    async with pool.acquire() as conn:
        # Check if this source is IV — skip if so
        origin_row = await conn.fetchrow(
            "SELECT origin FROM guild_identity.bis_list_sources WHERE id = $1", source_id
        )
        if origin_row and origin_row["origin"] == "icy_veins":
            logger.info("sync_source skipping IV source %d", source_id)
            return {"targets_run": 0, "items_upserted": 0, "errors": 0,
                    "skipped": "icy_veins extraction not yet implemented"}

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

    # Skip IV targets — extraction not yet implemented; do not mark as failed
    if _target_row.get("origin") == "icy_veins":
        return {"items_upserted": 0, "technique": "html_parse", "status": "pending",
                "skipped": "icy_veins extraction not yet implemented"}

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

    u.gg uses underscore slugs (demon_hunter), stats2 expects PascalCase (DemonHunter).
    """
    m = re.search(r"u\.gg/wow/([^/]+)/([^/]+)/gear", page_url)
    if not m:
        return None
    spec_slug = m.group(1)
    class_pascal = _slug_to_pascal(m.group(2))
    return f"{_UGG_STATS_BASE}/{class_pascal}/{class_pascal}_{spec_slug}_itemsTable.json"


def _slug_to_pascal(slug: str) -> str:
    """Convert snake_case or kebab-case slug to PascalCase.

    'demon_hunter' → 'DemonHunter', 'death-knight' → 'DeathKnight'
    """
    return "".join(word.capitalize() for word in re.split(r"[-_]", slug))


def _parse_archon_ssr(data: dict) -> list[SimcSlot]:
    """Parse items from the window.__SSR_DATA__ blob.

    u.gg SSR format (current): the top-level dict is keyed by the stats2 URL.
    The value has structure:  {data: {affixes: {affix_name: {item_level: {spec: {combos: {...}}}}}}}
    This is M+ affix/combo performance data, not a simple per-slot BIS list.
    We attempt best-effort extraction by looking at the most popular combo
    per slot across all affixes and item levels.
    """
    try:
        # Outer key is the stats2 URL — iterate to get the first value
        for _url_key, table_data in data.items():
            if not isinstance(table_data, dict):
                continue
            inner = table_data.get("data") or table_data
            # Try legacy items_table format first
            items_by_slot = inner.get("items_table", {}).get("items", {})
            if items_by_slot:
                return _archon_items_to_slots(items_by_slot)
            # Current format: data → affixes → {affix} → {item_level} → {spec} → combos
            affixes = inner.get("affixes", {})
            if affixes:
                return _parse_archon_combo_data(affixes)
    except (AttributeError, TypeError):
        pass
    return []


def _parse_archon_combo_data(affixes: dict) -> list[SimcSlot]:
    """Extract most popular item per slot from u.gg's affix+combo data structure.

    Iterates all affixes and item levels, collects item IDs per slot key,
    picks the most frequent item_id for each slot.
    """
    slot_counts: dict[str, dict[int, int]] = {}  # slot_key → {item_id: count}

    for _affix, affix_data in affixes.items():
        if not isinstance(affix_data, dict):
            continue
        for _item_level, level_data in affix_data.items():
            if not isinstance(level_data, dict):
                continue
            for _spec_key, spec_data in level_data.items():
                if not isinstance(spec_data, dict):
                    continue
                combos = spec_data.get("combos", {})
                for combo_key, combo_val in combos.items():
                    if not isinstance(combo_val, dict):
                        continue
                    # combo_key looks like "head", "ring1_ring2", "trinket1_trinket2", etc.
                    # combo_val has "dps_item": [{first_item_id, second_item_id, count, ...}]
                    items = combo_val.get("dps_item") or combo_val.get("items") or []
                    if not items:
                        continue
                    top = max(items, key=lambda i: int(i.get("count", 0) or 0), default=None)
                    if not top:
                        continue
                    # Extract slot(s) from the combo key
                    parts = combo_key.split("_")
                    first_id = top.get("first_item_id")
                    second_id = top.get("second_item_id")
                    # Map combo parts back to archon slot keys
                    slot_keys = [combo_key] if "_" not in combo_key else [
                        parts[0] if len(parts) >= 1 else None,
                        "_".join(parts[1:]) if len(parts) >= 2 else None,
                    ]
                    for i, sk in enumerate(slot_keys):
                        if not sk:
                            continue
                        item_id = first_id if i == 0 else second_id
                        if not item_id:
                            continue
                        try:
                            item_id_int = int(item_id)
                        except (ValueError, TypeError):
                            continue
                        slot_counts.setdefault(sk, {})
                        slot_counts[sk][item_id_int] = slot_counts[sk].get(item_id_int, 0) + 1

    slots: list[SimcSlot] = []
    for archon_slot, id_counts in slot_counts.items():
        normalised = _ARCHON_SLOT_MAP.get(archon_slot.lower())
        if not normalised:
            continue
        best_id = max(id_counts, key=lambda k: id_counts[k])
        slots.append(SimcSlot(
            slot=normalised,
            blizzard_item_id=best_id,
            bonus_ids=[],
            enchant_id=None,
            gem_ids=[],
            quality_track=None,
        ))
    return slots


def _parse_archon_items_table(data: dict) -> list[SimcSlot]:
    """Parse items from the stats2.u.gg direct JSON response (legacy format)."""
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
# Icy Veins extractor  (html_parse) — STUBBED, not yet implemented
#
# IV BIS pages are fully client-side rendered.  A plain httpx GET returns the
# page shell only — all gear data is injected by a compiled JS bundle after
# page load.  The regexes below were written against expected HTML patterns
# but cannot match anything in the static response IV actually delivers.
#
# See reference/PHASE_Z_ICY_VEINS_SCRAPE-idea-only.md for full context,
# what was tried, and what the next phase needs to solve before implementing.
# ---------------------------------------------------------------------------

# Kept for reference — these patterns assume item IDs are present in static
# HTML, which IV's JS-rendered pages do not provide.
_IV_ITEM_NAME_RE = re.compile(
    r'class="[^"]*recommended[^"]*"[^>]*>.*?<[^>]+>([^<]+)</[^>]+>',
    re.DOTALL | re.IGNORECASE,
)
_IV_ITEM_ID_RE = re.compile(r'data-item-id="(\d+)"')    # never matches IV static HTML
_IV_ITEM_LINK_RE = re.compile(r'wowhead\.com/item=(\d+)')  # never matches IV static HTML


async def _extract_icy_veins(url: str) -> list[SimcSlot]:
    """STUB — Icy Veins item extraction is not yet implemented.

    IV BIS pages are fully client-side rendered.  The static HTML returned by
    a plain httpx GET contains no item data — gear recommendations are injected
    by a compiled JavaScript bundle after page load.  The regex approach below
    (_IV_ITEM_LINK_RE, _IV_ITEM_ID_RE) was written assuming item IDs would be
    present in static HTML; they are not.

    What would be needed:
      Option A — Headless browser (Playwright): render the full page, parse DOM.
                 Heavy infrastructure, slow, executes all their JS (ads/trackers).
      Option B — Reverse-engineer their JS bundle to find the private API call.
                 Their private backend, not public, likely violates ToS.

    Both are intentionally deferred.  See:
        reference/PHASE_Z_ICY_VEINS_SCRAPE-idea-only.md

    URL discovery IS fully functional — correct IV URLs are stored in
    bis_scrape_targets (one per spec per source).  Only this extraction step
    is a stub.  When this is implemented, no schema or URL changes are needed.
    """
    logger.info("IV extraction stubbed — skipping %s (not yet implemented)", url)
    return []


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

    # Upsert scrape target (simc technique, no URL — find existing by technique)
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id FROM guild_identity.bis_scrape_targets
             WHERE source_id = $1
               AND spec_id = $2
               AND ($3::int IS NULL AND hero_talent_id IS NULL
                    OR hero_talent_id = $3)
               AND preferred_technique = 'simc'
            LIMIT 1
            """,
            source_id, spec_id, hero_talent_id,
        )
        if existing:
            await conn.execute(
                """
                UPDATE guild_identity.bis_scrape_targets
                   SET status = $1, items_found = $2, last_fetched = $3
                 WHERE id = $4
                """,
                status, items_upserted, now, existing["id"],
            )
            target_id = existing["id"]
        else:
            target_row = await conn.fetchrow(
                """
                INSERT INTO guild_identity.bis_scrape_targets
                    (source_id, spec_id, hero_talent_id, content_type,
                     url, preferred_technique, status, items_found, last_fetched)
                VALUES ($1, $2, $3, 'overall', NULL, 'simc', $4, $5, $6)
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
