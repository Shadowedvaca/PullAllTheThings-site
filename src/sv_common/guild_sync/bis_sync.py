"""BIS list discovery + extraction pipeline.

Architecture:
  1. URL Discovery  — auto-generate scrape targets for all spec × source × hero talent combos
                      (config.bis_scrape_targets)
  2. Extraction     — fetch raw HTML/JSON per target; store in landing.bis_scrape_raw
  3. Enrichment     — rebuild_bis_from_landing() / rebuild_trinket_ratings_from_landing()
                      parse landing content and write to enrichment.bis_entries /
                      enrichment.trinket_ratings. Called by enrich-and-classify in bis_routes.
  4. Cross-reference— compare enrichment.bis_entries per spec to surface disagreements.

Schema separation:
  config.*          pipeline configuration (bis_scrape_targets)
  log.*             operational logs (bis_scrape_log)
  landing.*         raw API/scrape payloads (bis_scrape_raw)
  enrichment.*      parsed, structured BIS data (bis_entries, trinket_ratings)

Public functions
----------------
discover_targets(pool)                  — generate missing config.bis_scrape_targets rows
sync_source(pool, source_id, spec_ids)  — run extraction for one source (optionally filtered)
sync_all(pool)                          — run extraction for every active source
sync_target(pool, target_id)            — re-sync a single scrape target
rebuild_bis_from_landing(pool)          — rebuild enrichment.bis_entries from landing HTML
rebuild_trinket_ratings_from_landing(pool) — rebuild enrichment.trinket_ratings from landing HTML
cross_reference(pool, spec_id, ht_id)  — compare all sources per slot for one spec+hero
import_simc(pool, text, source_id,      — import a SimC BIS profile as enrichment.bis_entries
            spec_id, hero_talent_id)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

from .simc_parser import SimcSlot, parse_gear_slots
from .quality_track import SLOT_ORDER

logger = logging.getLogger(__name__)


@dataclass
class ExtractedTrinketRating:
    blizzard_item_id: int
    item_name: str
    tier: str        # 'S', 'A', 'B', 'C', 'D', 'F'
    sort_order: int  # position within tier group, 0-indexed

# ---------------------------------------------------------------------------
# Slug maps — (class_name, spec_name) → URL slugs per source
# ---------------------------------------------------------------------------
# Slug helper — mirrors guide_links._slug, uses separator from guide_sites


def _slug(name: str, sep: str = "-") -> str:
    """Convert a display name to a lowercase URL slug."""
    return name.lower().replace(" ", sep)

# u.gg slot names → our normalised internal keys
_UGG_SLOT_MAP: dict[str, str] = {
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
    "ugg":       ["json_embed"],
    "wowhead":   ["wh_gatherer"],
    "icy_veins": ["html_parse"],  # STUB — html_parse returns [] for IV; see _extract_icy_veins
    "manual":    ["manual"],
}

# HTTP timeouts for scraping
_HTTP_TIMEOUT = 20.0
_UGG_STATS_BASE = "https://stats2.u.gg/wow/builds/v29/all"
_WOWHEAD_TOOLTIP_BASE = "https://nether.wowhead.com/tooltip/item"

# Default headers to avoid obvious bot detection.
# Wowhead requires Sec-Fetch-* headers; without them it returns 403.
# Accept-Encoding is intentionally omitted — httpx manages encoding negotiation
# itself and supports gzip/deflate natively.  Explicitly advertising Brotli
# support causes u.gg to respond with Content-Encoding: br, which httpx cannot
# decompress without the optional 'brotli' package.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


# ---------------------------------------------------------------------------
# URL discovery
# ---------------------------------------------------------------------------


async def discover_targets(pool: asyncpg.Pool) -> dict:
    """Auto-generate scrape targets for all active spec × source combos.

    u.gg: one target per spec × hero talent (URLs embed the HT slug).
    Wowhead + Icy Veins: one target per spec with hero_talent_id=NULL.
      Wowhead's BIS page is not HT-specific — the same page/URL covers all builds.
      IV pages are also not HT-specific and are JS-rendered (extraction stubbed).

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
              FROM ref.bis_list_sources s
              LEFT JOIN common.guide_sites gs ON gs.id = s.guide_site_id
             WHERE s.is_active = TRUE
            """
        )

        # Load all specs with class and role names
        specs = await conn.fetch(
            """
            SELECT s.id AS spec_id, s.name AS spec_name, c.name AS class_name,
                   r.name AS role_name
              FROM ref.specializations s
              JOIN ref.classes c ON c.id = s.class_id
              LEFT JOIN guild_identity.roles r ON r.id = s.default_role_id
            ORDER BY c.name, s.name
            """
        )

        # Load all hero talents per spec
        hero_talents = await conn.fetch(
            "SELECT id, spec_id, name, slug FROM ref.hero_talents"
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

                if origin in ("icy_veins", "wowhead"):
                    # These sources have one page per spec — no HT variation in the URL.
                    # Wowhead: one combined BIS page per spec; sections toggle raid/M+/overall.
                    # IV: one page per spec, all content toggled client-side (extraction stubbed).
                    # Both get hero_talent_id=NULL ("applies to all builds").
                    if origin == "icy_veins":
                        url = _iv_base_url(class_name, spec_name, role_name)
                        technique = "html_parse"
                    else:
                        # Wowhead URL ignores ht_slug entirely
                        url = _build_url(origin, class_name, spec_name, "", content_type,
                                         source["slug_separator"])
                        technique = "wh_gatherer"
                    expected += 1
                    result = await conn.fetchrow(
                        """
                        INSERT INTO config.bis_scrape_targets
                            (source_id, spec_id, hero_talent_id, content_type,
                             url, preferred_technique, status)
                        VALUES ($1, $2, NULL, $3, $4, $5, 'pending')
                        ON CONFLICT (source_id, spec_id, url)
                        DO NOTHING
                        RETURNING id
                        """,
                        source_id, spec_id, content_type, url, technique,
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
                            INSERT INTO config.bis_scrape_targets
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

    if origin == "ugg":
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
              FROM config.bis_scrape_targets t
              JOIN ref.bis_list_sources s ON s.id = t.source_id
              JOIN ref.specializations sp ON sp.id = t.spec_id
              JOIN ref.classes c ON c.id = sp.class_id
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

    total_stats: dict = {"targets_run": 0, "items_found": 0, "errors": 0}

    for spec_id, spec_target_list in spec_targets.items():
        for target in spec_target_list:
            try:
                result = await sync_target(pool, target["id"], _target_row=target)
                total_stats["targets_run"] += 1
                total_stats["items_found"] += result.get("items_found", 0)
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
              FROM config.bis_scrape_targets t
              JOIN ref.bis_list_sources s ON s.id = t.source_id
             WHERE t.spec_id = $1
               AND s.is_active = TRUE
               AND s.origin != 'icy_veins'
               AND t.url IS NOT NULL
            """,
            spec_id,
        )

    stats = {"targets_run": 0, "items_found": 0, "errors": 0}
    for target in targets:
        target_dict = dict(target)
        try:
            result = await sync_target(pool, target_dict["id"], _target_row=target_dict)
            stats["targets_run"] += 1
            stats["items_found"] += result.get("items_found", 0)
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
            "SELECT origin FROM ref.bis_list_sources WHERE id = $1", source_id
        )
        if origin_row and origin_row["origin"] == "icy_veins":
            logger.info("sync_source skipping IV source %d", source_id)
            return {"targets_run": 0, "items_found": 0, "errors": 0,
                    "skipped": "icy_veins extraction not yet implemented"}

        query = """
            SELECT t.id, t.source_id, t.url, t.preferred_technique,
                   t.spec_id, t.hero_talent_id, t.content_type
              FROM config.bis_scrape_targets t
             WHERE t.source_id = $1
               AND t.url IS NOT NULL
        """
        args: list = [source_id]
        if spec_ids:
            query += " AND t.spec_id = ANY($2)"
            args.append(spec_ids)

        targets = await conn.fetch(query, *args)

    stats = {"targets_run": 0, "items_found": 0, "errors": 0}

    for target in targets:
        target_dict = dict(target)
        try:
            result = await sync_target(pool, target_dict["id"], _target_row=target_dict)
            stats["targets_run"] += 1
            stats["items_found"] += result.get("items_found", 0)
        except Exception as exc:
            logger.error("Error syncing target %d: %s", target_dict["id"], exc, exc_info=True)
            stats["errors"] += 1

        # Be polite to external servers
        await asyncio.sleep(1.5)

    return stats


async def sync_gaps(
    pool: asyncpg.Pool,
    stale_days: int = 7,
) -> dict:
    """Sync only BIS targets that are missing from or stale in landing.bis_scrape_raw.

    A target is eligible if:
      - it has no row at all in landing.bis_scrape_raw, OR
      - its most recent fetched_at is older than stale_days

    Targets are processed oldest-first (missing first, then by fetched_at ASC)
    so the biggest gaps are filled first.  Skips Icy Veins targets.

    Returns {targets_run, items_found, errors}.
    """
    from datetime import timedelta

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    async with pool.acquire() as conn:
        targets = await conn.fetch(
            """
            SELECT t.id, t.source_id, t.spec_id, t.hero_talent_id,
                   t.content_type, t.url, t.preferred_technique, s.origin,
                   latest.latest_at
              FROM config.bis_scrape_targets t
              JOIN ref.bis_list_sources s ON s.id = t.source_id
              LEFT JOIN (
                  SELECT target_id, MAX(fetched_at) AS latest_at
                    FROM landing.bis_scrape_raw
                   WHERE target_id IS NOT NULL
                   GROUP BY target_id
              ) latest ON latest.target_id = t.id
             WHERE s.is_active = TRUE
               AND s.origin != 'icy_veins'
               AND t.url IS NOT NULL
               AND (latest.latest_at IS NULL OR latest.latest_at < $1)
             ORDER BY latest.latest_at ASC NULLS FIRST
            """,
            stale_cutoff,
        )

    stats: dict = {"targets_run": 0, "items_found": 0, "errors": 0}

    for target in targets:
        target_dict = dict(target)
        try:
            result = await sync_target(pool, target_dict["id"], _target_row=target_dict)
            stats["targets_run"] += 1
            stats["items_found"] += result.get("items_found", 0)
            if result.get("status") == "failed":
                stats["errors"] += 1
        except Exception as exc:
            logger.error("sync_gaps: error on target %d: %s", target_dict["id"], exc, exc_info=True)
            stats["errors"] += 1
        await asyncio.sleep(1.5)

    return stats


async def sync_target(
    pool: asyncpg.Pool,
    target_id: int,
    _target_row: Optional[dict] = None,
) -> dict:
    """Re-sync a single scrape target.

    Fetches raw content from the BIS source and stores it in landing.bis_scrape_raw.
    Does NOT write to guild_identity.bis_list_entries or trinket_tier_ratings.
    Call rebuild_bis_from_landing() / rebuild_trinket_ratings_from_landing() after
    syncing all targets to populate enrichment.bis_entries and enrichment.trinket_ratings.

    Returns {items_found, technique, status}.
    """
    async with pool.acquire() as conn:
        if _target_row is None:
            row = await conn.fetchrow(
                """
                SELECT t.id, t.url, t.preferred_technique, t.source_id,
                       t.spec_id, t.hero_talent_id, t.content_type,
                       s.origin
                  FROM config.bis_scrape_targets t
                  JOIN ref.bis_list_sources s ON s.id = t.source_id
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
                "SELECT origin FROM ref.bis_list_sources WHERE id = $1",
                _target_row["source_id"],
            )
            _target_row = {**_target_row, "origin": origin_row["origin"] if origin_row else ""}

    # Skip IV targets — extraction not yet implemented; do not mark as failed
    if _target_row.get("origin") == "icy_veins":
        return {"items_found": 0, "technique": "html_parse", "status": "pending",
                "skipped": "icy_veins extraction not yet implemented"}

    url = _target_row.get("url")
    technique = _target_row.get("preferred_technique") or _TECHNIQUE_ORDER.get(
        _target_row.get("origin", ""), ["html_parse"]
    )[0]
    spec_id = _target_row["spec_id"]
    hero_talent_id = _target_row.get("hero_talent_id")
    source_id = _target_row["source_id"]

    if not url:
        return {"items_found": 0, "technique": technique, "status": "failed", "error": "No URL"}

    # Run extraction — fetches raw content, parses to determine status
    content_type = _target_row.get("content_type") or "overall"
    origin = _target_row.get("origin", "")
    slots, _trinket_ratings, error, raw_content = await _extract(url, technique, content_type=content_type)

    now = datetime.now(timezone.utc)

    # Determine status from slot coverage (no guild_identity writes — enrichment layer handles that)
    if slots:
        items_found = len(slots)
        extracted_slots = {s.slot for s in slots}
        missing = set(SLOT_ORDER) - extracted_slots
        # A spec using a 2H weapon never has off_hand — treat off_hand as the only
        # missing slot → success (green), not partial.
        if not missing or missing == {"off_hand"}:
            status = "success"
        else:
            status = "partial"
    else:
        items_found = 0
        status = "failed"

    # Log to log schema, stamp status on config.bis_scrape_targets,
    # and store raw content in landing for downstream enrichment rebuild.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO log.bis_scrape_log
                (target_id, technique, status, items_found, error_message, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            target_id, technique, status, items_found, error, now,
        )
        await conn.execute(
            """
            UPDATE config.bis_scrape_targets
               SET status = $1, items_found = $2, last_fetched = $3
             WHERE id = $4
            """,
            status, items_found, now, target_id,
        )
        if raw_content:
            try:
                await conn.execute(
                    """
                    INSERT INTO landing.bis_scrape_raw (source, url, content, target_id)
                    VALUES ($1, $2, $3, $4)
                    """,
                    origin, url, raw_content, target_id,
                )
            except Exception:
                pass  # landing write is best-effort

    return {
        "items_found": items_found,
        "technique": technique,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Extraction dispatcher
# ---------------------------------------------------------------------------


async def _extract(
    url: str, technique: str, content_type: str = "overall"
) -> tuple[list[SimcSlot], list[ExtractedTrinketRating], Optional[str], Optional[str]]:
    """Dispatch to the appropriate extractor.

    Returns (slots, trinket_ratings, error_message, raw_content).
    slots and trinket_ratings are empty lists on failure.
    trinket_ratings is only populated for wh_gatherer technique.
    raw_content is the raw HTML/JSON fetched from the source (for landing schema).
    """
    try:
        if technique == "json_embed":
            slots, raw_content = await _extract_ugg(url)
            return slots, [], None, raw_content
        elif technique == "wh_gatherer":
            slots, trinket_ratings, raw_content = await _extract_wowhead(url, content_type=content_type)
            return slots, trinket_ratings, None, raw_content
        elif technique == "html_parse":
            slots = await _extract_icy_veins(url)
            return slots, [], None, None
        elif technique == "manual":
            # Manual entries are written directly via the API — never scraped
            return [], [], "manual technique — use the API to enter items", None
        else:
            return [], [], f"unknown technique: {technique}", None
    except httpx.TimeoutException:
        return [], [], "request timed out", None
    except httpx.HTTPStatusError as exc:
        return [], [], f"HTTP {exc.response.status_code}", None
    except Exception as exc:
        logger.warning("Extraction failed for %s (%s): %s", url, technique, exc)
        return [], [], str(exc), None


# ---------------------------------------------------------------------------
# u.gg extractor  (json_embed)
# ---------------------------------------------------------------------------


def _parse_ugg_html(html: str, url: str) -> list[SimcSlot]:
    """Parse u.gg page HTML and extract BIS items from embedded SSR JSON.

    Pure function — no network calls.  Called by _extract_ugg() during live
    scraping and by rebuild_bis_from_landing() to re-parse stored HTML.

    u.gg embeds a large `window.__SSR_DATA__` JSON blob in the HTML which
    contains per-spec item data keyed by spec name (e.g. "DeathKnight-Blood").

    Returns a list of SimcSlot (empty list on parse failure).
    """
    # Extract window.__SSR_DATA__ using raw_decode so the nested JSON object is
    # parsed correctly regardless of size.  A regex with (\{.+?\}) is non-greedy
    # and stops at the first "}" in a 5 MB blob — it will never capture the full
    # object.  raw_decode(html, idx) parses exactly as much JSON as needed.
    marker = "window.__SSR_DATA__"
    ssr_idx = html.find(marker)
    if ssr_idx >= 0:
        obj_start = html.find("{", ssr_idx)
        if obj_start >= 0:
            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(html, obj_start)
                return _parse_ugg_ssr(data, url)
            except (json.JSONDecodeError, KeyError, ValueError):
                pass

    # SSR parsing failed — do NOT fall back to the stats2.u.gg endpoint.
    # The stats2 URL uses a versioned path (v29) that reflects a Dragonflight-era
    # snapshot.  Falling back to it would write outdated item IDs for specs whose
    # SSR blob failed to parse, silently contaminating BIS data with old items.
    # Return [] so the caller marks the target as "failed" — that failure is visible
    # in the scrape log and prompts investigation rather than silent corruption.
    logger.warning("_parse_ugg_html: SSR parse returned no items for %s", url)
    return []


async def _extract_ugg(url: str) -> tuple[list[SimcSlot], Optional[str]]:
    """Fetch u.gg page and extract BIS items.

    Returns (slots, raw_html) — raw_html written to landing.bis_scrape_raw.
    Parsing is delegated to _parse_ugg_html() for reuse in rebuild_bis_from_landing().
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    return _parse_ugg_html(html, url), html


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


def _ugg_url_to_spec_key(url: str) -> str:
    """Derive u.gg's internal spec key from the page URL.

    https://u.gg/wow/blood/death_knight/gear → "DeathKnight-Blood"
    https://u.gg/wow/frost/mage/gear          → "Mage-Frost"
    """
    m = re.search(r"u\.gg/wow/([^/]+)/([^/]+)/gear", url)
    if not m:
        return ""
    spec_pascal  = _slug_to_pascal(m.group(1))   # "blood" → "Blood"
    class_pascal = _slug_to_pascal(m.group(2))   # "death_knight" → "DeathKnight"
    return f"{class_pascal}-{spec_pascal}"


def _ugg_url_to_section(url: str) -> str:
    """Map the role= query param to the correct SSR data section.

    role=raid         → "raid"
    role=mythicdungeon→ "mythic"
    (no role)         → "single_target"
    """
    if "role=raid" in url:
        return "raid"
    if "role=mythicdungeon" in url:
        return "mythic"
    return "single_target"


def _slug_to_pascal(slug: str) -> str:
    """Convert snake_case or kebab-case slug to PascalCase.

    'demon_hunter' → 'DemonHunter', 'death-knight' → 'DeathKnight'
    """
    return "".join(word.capitalize() for word in re.split(r"[-_]", slug))


def _parse_ugg_ssr(data: dict, url: str = "") -> list[SimcSlot]:
    """Parse items from the window.__SSR_DATA__ blob.

    u.gg SSR format: the top-level dict is keyed by a stats2 URL.  Its value
    has a "data" dict with sections keyed by scenario type: "raid", "mythic",
    "single_target", "multi_target", "affixes".  Each section maps boss/dungeon
    IDs → spec keys → {"items": {...}, "items_table": {...}, "combos": {...}}.

    The correct path for BIS data is:
        data[section]["all"][spec_key]["items_table"]["items"]

    - "all" is the aggregate across all bosses/dungeons for that section.
    - "items_table" is the stable per-slot recommendation; "items.dps_item" etc.
      fluctuate per boss and can surface stale items from wrong specs or seasons.
    - section is derived from the URL's role= parameter.
    """
    spec_key = _ugg_url_to_spec_key(url)    # e.g. "DeathKnight-Blood"
    section  = _ugg_url_to_section(url)     # "raid", "mythic", or "single_target"

    try:
        for _url_key, table_data in data.items():
            if not isinstance(table_data, dict):
                continue
            inner = table_data.get("data") or table_data

            # Legacy items_table at top level (older u.gg format)
            items_by_slot = inner.get("items_table", {}).get("items", {})
            if items_by_slot:
                return _ugg_items_to_slots(items_by_slot)

            # Current format: section["all"][spec_key]["items_table"]["items"]
            sec = inner.get(section)
            if isinstance(sec, dict):
                all_data = sec.get("all")
                if isinstance(all_data, dict) and spec_key:
                    spec_data = all_data.get(spec_key)
                    if isinstance(spec_data, dict):
                        items_table = spec_data.get("items_table", {}).get("items", {})
                        if items_table:
                            logger.debug(
                                "_parse_ugg_ssr: using %s[all][%s][items_table] for %s",
                                section, spec_key, url,
                            )
                            return _ugg_items_to_slots(items_table)

            # Fallback: affixes (M+ data — mixes specs and may surface stale items)
            affixes = inner.get("affixes", {})
            if affixes:
                logger.warning(
                    "_parse_ugg_ssr: falling back to affixes for %s "
                    "(section=%s spec_key=%s not found)",
                    url, section, spec_key,
                )
                return _parse_ugg_combo_data(affixes)
    except (AttributeError, TypeError):
        pass
    return []


def _parse_ugg_combo_data(affixes: dict) -> list[SimcSlot]:
    """Extract most popular item per slot from u.gg's affixes data structure.

    Handles both the current format (items → slot → dps_item → {item_id})
    and the legacy format (combos → slot_pair → dps_item → [{first_item_id, count}]).
    Iterates all affixes and item levels, collects item_id votes per slot,
    picks the most frequent for each slot.
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

                # Current format: spec_data → items → {slot} → dps_item → {item_id: N}
                item_slots = spec_data.get("items") or {}
                for slot_key, slot_val in item_slots.items():
                    if not isinstance(slot_val, dict):
                        continue
                    dps_item = slot_val.get("dps_item") or {}
                    if not isinstance(dps_item, dict):
                        continue
                    item_id = dps_item.get("item_id")
                    if not item_id:
                        continue
                    try:
                        iid = int(item_id)
                    except (ValueError, TypeError):
                        continue
                    slot_counts.setdefault(slot_key, {})
                    slot_counts[slot_key][iid] = slot_counts[slot_key].get(iid, 0) + 1

                # Legacy format: spec_data → combos → {slot_pair} → dps_item: [{first_item_id, count}]
                combos = spec_data.get("combos") or {}
                for combo_key, combo_val in combos.items():
                    if not isinstance(combo_val, dict):
                        continue
                    items = combo_val.get("dps_item") or combo_val.get("items") or []
                    if not items or not isinstance(items, list):
                        continue
                    top = max(items, key=lambda i: int(i.get("count", 0) or 0), default=None)
                    if not top:
                        continue
                    parts = combo_key.split("_")
                    first_id = top.get("first_item_id")
                    second_id = top.get("second_item_id")
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
                            iid = int(item_id)
                        except (ValueError, TypeError):
                            continue
                        slot_counts.setdefault(sk, {})
                        slot_counts[sk][iid] = slot_counts[sk].get(iid, 0) + 1

    slots: list[SimcSlot] = []
    for ugg_slot, id_counts in slot_counts.items():
        normalised = _UGG_SLOT_MAP.get(ugg_slot.lower())
        if not normalised:
            continue
        best_id = max(id_counts, key=lambda k: id_counts[k])
        best_votes = id_counts[best_id]
        logger.debug(
            "u.gg extraction: slot '%s' → item %d (%d votes)",
            normalised, best_id, best_votes,
        )
        slots.append(SimcSlot(
            slot=normalised,
            blizzard_item_id=best_id,
            bonus_ids=[],
            enchant_id=None,
            gem_ids=[],
            quality_track=None,
        ))
    return slots


def _parse_ugg_items_table(data: dict) -> list[SimcSlot]:
    """Parse items from the stats2.u.gg direct JSON response (legacy format)."""
    try:
        items_by_slot = data.get("items_table", {}).get("items", {})
        return _ugg_items_to_slots(items_by_slot)
    except (AttributeError, TypeError):
        return []


def _ugg_items_to_slots(items_by_slot: dict) -> list[SimcSlot]:
    """Convert u.gg's per-slot items dict into SimcSlot list."""
    slots: list[SimcSlot] = []
    for ugg_slot, slot_data in items_by_slot.items():
        normalised = _UGG_SLOT_MAP.get(ugg_slot.lower())
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
# Raid/M+ "highlight" sections use [icon-badge=N] instead of [item=N]
_ICON_BADGE_RE = re.compile(r"\[icon-badge=(\d+)")

# Trinket tier list block patterns — match Wowhead BBCode tier-list markup.
# Note: raw_html is pre-normalised in _extract_trinket_tiers() to replace [\/ with [/,
# so these patterns use plain [/tag] form without any backslash escaping.
_TIER_LIST_BLOCK_RE = re.compile(r'\[tier-list[^\]]*\](.*?)\[/tier-list\]', re.DOTALL)
_TIER_BLOCK_RE = re.compile(r'\[tier\](.*?)\[/tier\]', re.DOTALL)
_TIER_LABEL_RE = re.compile(r'\[tier-label[^\]]*\]([SABCDF])\[/tier-label\]')
_TIER_BADGE_ITEM_RE = re.compile(r'\[icon-badge=(\d+)')

# slotbak inside jsonequip uses Blizzard API invtype IDs — NOT the old
# Wowhead-internal slot numbering.  The two systems agree for slots 1–12
# but diverge at 13+.
#
# Old Wowhead internal: 13=back, 14=mainhand, 15=offhand, 16=ring2, 17=trinket2
# Blizzard invtype IDs: 13=WEAPON(1H), 14=SHIELD, 16=CLOAK, 17=2HWEAPON,
#                       20=ROBE, 21=MAINHAND, 22=OFFHAND, 23=HOLDABLE
#
# Rings (11) and trinkets (12) share a single invtype for both slots; the
# extractor uses first/second occurrence order to assign _1 vs _2.
_WOWHEAD_SLOT_MAP: dict[int, str] = {
    1:  "head",
    2:  "neck",
    3:  "shoulder",
    5:  "chest",       # INVTYPE_CHEST
    6:  "waist",
    7:  "legs",
    8:  "feet",
    9:  "wrist",
    10: "hands",
    11: "ring",        # both ring slots — resolved by occurrence order below
    12: "trinket",     # both trinket slots — resolved by occurrence order below
    13: "main_hand",   # INVTYPE_WEAPON (1H, equips in main hand)
    14: "off_hand",    # INVTYPE_SHIELD
    15: "main_hand",   # INVTYPE_RANGED (bows, guns, crossbows — Hunter ranged weapon)
    16: "back",        # INVTYPE_CLOAK
    17: "main_hand",   # INVTYPE_2HWEAPON
    20: "chest",       # INVTYPE_ROBE (same equip slot as chest)
    21: "main_hand",   # INVTYPE_MAINHAND
    22: "off_hand",    # INVTYPE_OFFHAND
    23: "off_hand",    # INVTYPE_HOLDABLE (held in off hand)
}

# Wowhead BBCode section-header pattern.  Section titles live in the `toc`
# attribute, not between the tags.  The HTML is served inside a JSON string
# so double-quotes are escaped as \".
# Examples:
#   [h2 toc=\"Raid Drops\" type=bar]
#   [h3 toc=\"BiS Gear\"]
#   [h3 toc=false]        ← skip: value is not a string
_WH_SECTION_RE = re.compile(
    r'\[h([23])[^\]]*\btoc=(?:\\?")(.*?)(?:\\?")',
    re.IGNORECASE,
)

# Map content_type values to the section keywords we look for in toc attributes.
# Keys are the content_type values stored in bis_scrape_targets.
_WH_SECTION_KEYWORDS: dict[str, list[str]] = {
    "overall":     ["bis gear", "best in slot", "recommended gear", "overall"],
    "raid":        ["raid drops", "raid"],
    "mythic_plus": ["mythic+ drops", "mythic+", "mythic plus", "dungeon"],
}


def _wh_section_for_content_type(html: str, content_type: str) -> str:
    """Return the slice of HTML that belongs to the requested content_type section.

    Wowhead guides use [h2/h3 toc="Section Name"] BBCode headers to separate
    Overall, Raid, and Mythic+ sections on the same page.  The toc attribute
    holds the display title (not the inner text).  Double-quotes in the HTML
    are JSON-escaped as \\".

    Finds the first header whose toc value matches a keyword for the requested
    content_type and returns the HTML from that point to the start of the next
    h2-level header.  Falls back to the full page if no match is found.
    """
    keywords = _WH_SECTION_KEYWORDS.get(content_type, [])
    if not keywords:
        return html

    # Collect all h2/h3 positions with their toc labels
    sections: list[tuple[int, int, str]] = []  # (pos, level, toc_lower)
    for m in _WH_SECTION_RE.finditer(html):
        level = int(m.group(1))
        toc_text = m.group(2).strip().lower()
        sections.append((m.start(), level, toc_text))

    # Find first section matching our keywords
    target_start: Optional[int] = None
    target_level: Optional[int] = None
    target_idx: Optional[int] = None
    for i, (pos, level, text) in enumerate(sections):
        if any(kw in text for kw in keywords):
            target_start = pos
            target_level = level
            target_idx = i
            break

    if target_start is None:
        # No matching section found — fall back to full page
        return html

    # The section ends at the next header of equal or higher level (lower number)
    for pos, level, _text in sections[target_idx + 1:]:
        if level <= target_level:
            return html[target_start:pos]

    return html[target_start:]


def _extract_trinket_tiers(raw_html: str, item_meta: dict[int, dict]) -> list[ExtractedTrinketRating]:
    """Parse Wowhead BBCode tier-list blocks and return trinket tier ratings.

    Wowhead embeds tier lists as BBCode in the static HTML:
      [tier-list=rows grid]
        [tier][tier-label bg=q5]S[/tier-label][tier-content]
          [icon-badge=249346 quality=4 ...] ...
        [/tier-content][/tier]
        ...
      [/tier-list]

    Item names are resolved from item_meta (built from WH.Gatherer.addData()).
    If an item is not in item_meta, item_name is left as "" — it will be filled
    in by the item enrichment pipeline.

    Returns one ExtractedTrinketRating per (tier, item_id) pair found.
    """
    # Wowhead escapes closing-tag slashes as [\/ in the raw HTML (e.g. [\/tier-list]).
    # Normalise to standard [/ form before applying regexes.
    raw_html = raw_html.replace("[\\/", "[/")

    ratings: list[ExtractedTrinketRating] = []
    for tier_list_match in _TIER_LIST_BLOCK_RE.finditer(raw_html):
        block = tier_list_match.group(1)
        for tier_match in _TIER_BLOCK_RE.finditer(block):
            tier_block = tier_match.group(1)
            label_match = _TIER_LABEL_RE.search(tier_block)
            if not label_match:
                continue
            tier_letter = label_match.group(1)
            for pos, badge_match in enumerate(_TIER_BADGE_ITEM_RE.finditer(tier_block)):
                item_id = int(badge_match.group(1))
                meta = item_meta.get(item_id, {})
                item_name = meta.get("name", "")
                ratings.append(ExtractedTrinketRating(
                    blizzard_item_id=item_id,
                    item_name=item_name,
                    tier=tier_letter,
                    sort_order=pos,
                ))
    return ratings



# ---------------------------------------------------------------------------
# Enrichment rebuild from landing
# ---------------------------------------------------------------------------


async def rebuild_bis_from_landing(pool: asyncpg.Pool) -> dict:
    """Rebuild enrichment.bis_entries by re-parsing landing.bis_scrape_raw.

    For each target, takes the most recent raw HTML and parses it using
    _parse_ugg_html() or _parse_wowhead_html() — whichever matches the source.
    Only inserts items that already exist in enrichment.items (the FK requires it).

    Called by enrich-and-classify in bis_routes after sp_rebuild_all() so that
    enrichment.items is populated before we try to insert BIS references.

    Returns {bis_entries_inserted}.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH latest AS (
                SELECT
                    bsr.content, bsr.url, bsr.source,
                    t.source_id, t.spec_id, t.hero_talent_id, t.content_type,
                    ROW_NUMBER() OVER (
                        PARTITION BY bsr.target_id
                        ORDER BY bsr.fetched_at DESC
                    ) AS rn
                  FROM landing.bis_scrape_raw bsr
                  JOIN config.bis_scrape_targets t ON t.id = bsr.target_id
                 WHERE bsr.target_id IS NOT NULL
            )
            SELECT content, url, source, source_id, spec_id, hero_talent_id, content_type
              FROM latest
             WHERE rn = 1
        """)
        # TRUNCATE first — clean-slate rebuild.  enrichment.bis_entries has no
        # dependents so CASCADE is not needed.
        await conn.execute("TRUNCATE enrichment.bis_entries")

    total_inserted = 0
    for row in rows:
        html = row["content"]
        url = row["url"]
        source = row["source"]
        source_id = row["source_id"]
        spec_id = row["spec_id"]
        hero_talent_id = row["hero_talent_id"]
        content_type = row["content_type"] or "overall"

        if source == "ugg":
            slots = _parse_ugg_html(html, url)
        elif source == "wowhead":
            slots, _ = _parse_wowhead_html(html, url, content_type)
        else:
            continue  # icy_veins and others not yet parseable

        if not slots:
            continue

        async with pool.acquire() as conn:
            for slot_data in slots:
                # enrichment.bis_entries.blizzard_item_id FKs to enrichment.items —
                # skip items not yet in the enrichment layer.
                exists = await conn.fetchval(
                    "SELECT 1 FROM enrichment.items WHERE blizzard_item_id = $1",
                    slot_data.blizzard_item_id,
                )
                if not exists:
                    continue
                try:
                    await conn.execute(
                        """
                        INSERT INTO enrichment.bis_entries
                            (source_id, spec_id, hero_talent_id, slot, blizzard_item_id, priority)
                        VALUES ($1, $2, $3, $4, $5, 1)
                        """,
                        source_id, spec_id, hero_talent_id,
                        slot_data.slot, slot_data.blizzard_item_id,
                    )
                    total_inserted += 1
                except Exception:
                    pass  # duplicate within this rebuild — silently skip

    logger.info("rebuild_bis_from_landing: %d bis_entries inserted", total_inserted)
    return {"bis_entries_inserted": total_inserted}


async def rebuild_trinket_ratings_from_landing(pool: asyncpg.Pool) -> dict:
    """Rebuild enrichment.trinket_ratings by re-parsing landing.bis_scrape_raw.

    Re-parses Wowhead HTML for trinket tier lists and writes to
    enrichment.trinket_ratings.  Only inserts items that exist in enrichment.items.

    Deduplication is driven by bis_list_sources.trinket_ratings_by_content_type:

      FALSE (e.g. Wowhead) — ratings are identical across all content types,
            so we collapse Overall/Raid/M+ to one row per spec, picking the
            most-recently-fetched page regardless of which content type it was.
            Partition key: (spec_id, origin).

      TRUE  (e.g. u.gg if it publishes distinct lists per content type) — keep
            one row per (spec_id, source_id) so each content type is rebuilt
            independently.  Partition key: (spec_id, source_id).

    This is a per-source config decision, not a global style.  Adding a new
    trinket-ranking source requires setting trinket_ratings_by_content_type
    explicitly rather than assuming the Wowhead model.

    Called by enrich-and-classify in bis_routes after sp_rebuild_all().

    Returns {trinket_ratings_inserted}.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH ranked AS (
                SELECT
                    bsr.content, bsr.url,
                    t.source_id, t.spec_id, t.hero_talent_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            t.spec_id,
                            CASE WHEN sc.trinket_ratings_by_content_type
                                 THEN t.source_id::text
                                 ELSE sc.origin
                            END
                        ORDER BY bsr.fetched_at DESC
                    ) AS rn
                  FROM landing.bis_scrape_raw bsr
                  JOIN config.bis_scrape_targets t ON t.id = bsr.target_id
                  JOIN ref.bis_list_sources sc ON sc.id = t.source_id
                 WHERE bsr.target_id IS NOT NULL
                   AND bsr.source = 'wowhead'
            )
            SELECT content, url, source_id, spec_id, hero_talent_id
              FROM ranked
             WHERE rn = 1
        """)
        await conn.execute("TRUNCATE enrichment.trinket_ratings")

    total_inserted = 0
    for row in rows:
        html = row["content"]
        url = row["url"]
        source_id = row["source_id"]
        spec_id = row["spec_id"]
        hero_talent_id = row["hero_talent_id"]

        _, trinket_ratings = _parse_wowhead_html(html, url)

        if not trinket_ratings:
            continue

        async with pool.acquire() as conn:
            for rating in trinket_ratings:
                exists = await conn.fetchval(
                    "SELECT 1 FROM enrichment.items WHERE blizzard_item_id = $1",
                    rating.blizzard_item_id,
                )
                if not exists:
                    continue
                try:
                    await conn.execute(
                        """
                        INSERT INTO enrichment.trinket_ratings
                            (source_id, spec_id, hero_talent_id, blizzard_item_id,
                             tier, sort_order)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        source_id, spec_id, hero_talent_id,
                        rating.blizzard_item_id, rating.tier, rating.sort_order,
                    )
                    total_inserted += 1
                except Exception:
                    pass  # duplicate — silently skip

    logger.info(
        "rebuild_trinket_ratings_from_landing: %d ratings inserted", total_inserted
    )
    return {"trinket_ratings_inserted": total_inserted}


def _parse_wowhead_html(
    html: str, url: str = "", content_type: str = "overall"
) -> tuple[list[SimcSlot], list[ExtractedTrinketRating]]:
    """Parse Wowhead BIS guide HTML and extract items + trinket ratings.

    Pure function — no network calls.  Called by _extract_wowhead() during live
    scraping and by rebuild_bis_from_landing() / rebuild_trinket_ratings_from_landing()
    to re-parse stored HTML.

    content_type controls which page section is scanned:
      - "overall"     → Overall BiS section (default)
      - "raid"        → Raid Drops section
      - "mythic_plus" → Mythic+ Drops section

    Wowhead uses a single combined URL for all three; sections are delimited
    by [h2 type=bar] BBCode headers in the static HTML.

    Trinket ratings are always extracted from the full page regardless of
    content_type — tier lists are not section-scoped.

    Returns (slots, trinket_ratings).
    """
    # Build item metadata map from ALL WH.Gatherer.addData() calls in the page.
    # Metadata is declared globally (not per-section), so we always scan the
    # full HTML for this step.
    item_meta: dict[int, dict] = {}
    for m in _WH_GATHERER_RE.finditer(html):
        try:
            chunk = json.loads(m.group(1))
            for item_id_str, meta in chunk.items():
                try:
                    item_meta[int(item_id_str)] = meta
                except (ValueError, TypeError):
                    continue
        except json.JSONDecodeError:
            continue

    if not item_meta:
        return [], []

    # Extract items from the requested content section, then backfill any
    # missing slots from the overall BiS section.  Raid and M+ sections only
    # list the items specifically worth farming from that content; the overall
    # section provides the complete recommended set for all other slots.
    section_slots = _wh_slots_from_section(html, item_meta, content_type)

    if content_type != "overall":
        # Backfill missing slots with overall recommendations so every
        # content_type ends up with a full 16-slot BIS list.
        overall_slots = _wh_slots_from_section(html, item_meta, "overall")
        filled: dict[str, int] = {s.slot: s.blizzard_item_id for s in overall_slots}
        # Section-specific items take priority; they override overall
        filled.update({s.slot: s.blizzard_item_id for s in section_slots})
        section_slots = [
            SimcSlot(slot=slot, blizzard_item_id=iid, bonus_ids=[], enchant_id=None,
                     gem_ids=[], quality_track=None)
            for slot, iid in filled.items()
        ]

    # Extract trinket tier ratings from the full page (tier-list blocks are not
    # section-scoped — they appear once on the page regardless of content_type).
    trinket_ratings = _extract_trinket_tiers(html, item_meta)

    return section_slots, trinket_ratings


async def _extract_wowhead(
    url: str, content_type: str = "overall"
) -> tuple[list[SimcSlot], list[ExtractedTrinketRating], Optional[str]]:
    """Fetch Wowhead BIS guide and extract items via WH.Gatherer.addData() calls.

    Returns (slots, trinket_ratings, raw_html).  raw_html written to landing.bis_scrape_raw.
    Parsing is delegated to _parse_wowhead_html() for reuse in rebuild_*_from_landing().
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    slots, trinket_ratings = _parse_wowhead_html(html, url, content_type)
    return slots, trinket_ratings, html


def _wh_slots_from_section(
    html: str, item_meta: dict, content_type: str
) -> list[SimcSlot]:
    """Extract SimcSlot list from a single Wowhead page section.

    Scans both [item=N] and [icon-badge=N] markup within the section slice,
    resolves slot codes via _WOWHEAD_SLOT_MAP, and assigns ring_1/ring_2 and
    trinket_1/trinket_2 by document order.
    """
    section_html = _wh_section_for_content_type(html, content_type)

    # Collect item IDs in document order, deduped, across both markup patterns
    pos_map: dict[int, int] = {}
    for pat in (_ITEM_MARKUP_RE, _ICON_BADGE_RE):
        for m in pat.finditer(section_html):
            iid = int(m.group(1))
            if iid not in pos_map:
                pos_map[iid] = m.start()
    referenced_ids = sorted(pos_map.keys(), key=lambda iid: pos_map[iid])

    seen_slots: dict[str, int] = {}
    ring_count = 0
    trinket_count = 0

    for item_id in referenced_ids:
        meta = item_meta.get(item_id)
        if not meta:
            continue
        je = meta.get("jsonequip") or {}
        slot_code = je.get("slotbak") if isinstance(je, dict) else meta.get("slotbak")
        base_slot = _WOWHEAD_SLOT_MAP.get(slot_code)
        if not base_slot:
            continue

        if base_slot == "ring":
            if ring_count == 0:
                slot = "ring_1"
                ring_count += 1
            elif ring_count == 1:
                slot = "ring_2"
                ring_count += 1
            else:
                continue
        elif base_slot == "trinket":
            if trinket_count == 0:
                slot = "trinket_1"
                trinket_count += 1
            elif trinket_count == 1:
                slot = "trinket_2"
                trinket_count += 1
            else:
                continue
        else:
            slot = base_slot
            if slot in seen_slots:
                continue

        seen_slots[slot] = item_id

    return [
        SimcSlot(slot=slot, blizzard_item_id=iid, bonus_ids=[], enchant_id=None,
                 gem_ids=[], quality_track=None)
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
    """Import a SimC BIS profile directly into enrichment.bis_entries for a spec.

    Creates/updates a config.bis_scrape_targets row with technique='simc' and
    appends a row to log.bis_scrape_log.  Manual SimC imports are treated as
    'locked' — logged with status='success' so the matrix shows them clearly.

    Writes directly to enrichment.bis_entries (not guild_identity.bis_list_entries).
    Items that do not yet exist in enrichment.items are lazy-stubbed so they can be
    referenced — they will be enriched on the next Enrich & Classify run.

    Returns {items_upserted, status}.
    """
    slots = _extract_simc(text)
    items_upserted = 0
    now = datetime.now(timezone.utc)

    if slots:
        async with pool.acquire() as conn:
            # Clear existing SimC entries for this (source, spec, hero_talent)
            await conn.execute(
                """
                DELETE FROM enrichment.bis_entries
                 WHERE source_id = $1
                   AND spec_id = $2
                   AND (
                       ($3::int IS NULL AND hero_talent_id IS NULL)
                       OR hero_talent_id = $3
                   )
                """,
                source_id, spec_id, hero_talent_id,
            )
            for slot_data in slots:
                # Lazy-stub item in enrichment.items if it doesn't exist yet.
                # enrichment.items.blizzard_item_id is the PK so ON CONFLICT DO NOTHING is safe.
                await conn.execute(
                    """
                    INSERT INTO enrichment.items
                        (blizzard_item_id, name, slot_type, item_category, enriched_at)
                    VALUES ($1, '', $2, 'unclassified', NOW())
                    ON CONFLICT (blizzard_item_id) DO NOTHING
                    """,
                    slot_data.blizzard_item_id,
                    slot_data.slot,
                )
                await conn.execute(
                    """
                    INSERT INTO enrichment.bis_entries
                        (source_id, spec_id, hero_talent_id, slot, blizzard_item_id, priority)
                    VALUES ($1, $2, $3, $4, $5, 1)
                    """,
                    source_id, spec_id, hero_talent_id,
                    slot_data.slot, slot_data.blizzard_item_id,
                )
                items_upserted += 1

    status = "success" if slots else "failed"
    error_message = None if slots else "No gear slots found in SimC text"

    # Upsert config.bis_scrape_targets row + log entry
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id FROM config.bis_scrape_targets
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
                UPDATE config.bis_scrape_targets
                   SET status = $1, items_found = $2, last_fetched = $3
                 WHERE id = $4
                """,
                status, items_upserted, now, existing["id"],
            )
            target_id = existing["id"]
        else:
            target_row = await conn.fetchrow(
                """
                INSERT INTO config.bis_scrape_targets
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
            INSERT INTO log.bis_scrape_log
                (target_id, technique, status, items_found, error_message, created_at)
            VALUES ($1, 'simc', $2, $3, $4, $5)
            """,
            target_id, status, items_upserted, error_message, now,
        )

    return {"items_upserted": items_upserted, "status": status}


# ---------------------------------------------------------------------------
# Expansion mismatch detection
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Cross-reference
# ---------------------------------------------------------------------------


async def cross_reference(
    pool: asyncpg.Pool,
    spec_id: int,
    hero_talent_id: Optional[int],
) -> dict:
    """Compare BIS recommendations across all sources for one spec + hero talent.

    hero_talent_id=None (All builds) fetches every entry across all hero talents
    and picks the most common item per (source, slot) to show consensus.

    NOTE: `e.hero_talent_id = NULL` is always false in SQL — we handle None
    with a separate query branch to avoid the NULL equality trap.

    Returns a dict keyed by slot.  Each value is:
    {
        "consensus_blizzard_item_id": int | None,  # most common across sources
        "consensus_item_name": str,
        "all_agree": bool,
        "agree_count": int,      # sources whose item matches consensus
        "total_with_data": int,  # sources that have any data for this slot
        "sources": [
            {"source_id", "source_name", "blizzard_item_id", "item_name", "agrees"},
            ...
        ]
    }
    """
    from collections import Counter

    async with pool.acquire() as conn:
        if hero_talent_id is None:
            # All builds: aggregate across every hero talent.
            # GROUP BY item so we can count how many HTs of each source pick it;
            # ORDER DESC so the first row per (source, slot) is the winner.
            rows = await conn.fetch(
                """
                SELECT e.slot,
                       s.id AS source_id, s.name AS source_name, s.sort_order,
                       e.blizzard_item_id, i.name AS item_name,
                       COUNT(*) AS vote_count
                  FROM enrichment.bis_entries e
                  JOIN ref.bis_list_sources s ON s.id = e.source_id
                  LEFT JOIN enrichment.items i ON i.blizzard_item_id = e.blizzard_item_id
                 WHERE e.spec_id = $1 AND s.is_active = TRUE
                 GROUP BY e.slot, s.id, s.name, s.sort_order,
                          e.blizzard_item_id, i.name
                 ORDER BY e.slot, s.sort_order, vote_count DESC
                """,
                spec_id,
            )
            # Keep only the highest-voted item per (source_id, slot)
            by_slot: dict[str, list[dict]] = {}
            seen_src_slot: set = set()
            for r in rows:
                key = (r["source_id"], r["slot"])
                if key in seen_src_slot:
                    continue
                seen_src_slot.add(key)
                by_slot.setdefault(r["slot"], []).append({
                    "source_id": r["source_id"],
                    "source_name": r["source_name"],
                    "blizzard_item_id": r["blizzard_item_id"],
                    "item_name": r["item_name"] or "",
                })
        else:
            rows = await conn.fetch(
                """
                SELECT e.slot,
                       s.id AS source_id, s.name AS source_name,
                       e.blizzard_item_id, i.name AS item_name
                  FROM enrichment.bis_entries e
                  JOIN ref.bis_list_sources s ON s.id = e.source_id
                  LEFT JOIN enrichment.items i ON i.blizzard_item_id = e.blizzard_item_id
                 WHERE e.spec_id = $1
                   AND (e.hero_talent_id = $2 OR e.hero_talent_id IS NULL)
                   AND s.is_active = TRUE
                 ORDER BY e.slot, s.sort_order
                """,
                spec_id, hero_talent_id,
            )
            by_slot = {}
            for r in rows:
                by_slot.setdefault(r["slot"], []).append({
                    "source_id": r["source_id"],
                    "source_name": r["source_name"],
                    "blizzard_item_id": r["blizzard_item_id"],
                    "item_name": r["item_name"] or "",
                })

    # Build final result with consensus metadata per slot
    result: dict[str, dict] = {}
    for slot in SLOT_ORDER:
        entries = by_slot.get(slot, [])

        # Consensus = most common blizzard_item_id across sources with data
        item_counter: Counter = Counter(
            e["blizzard_item_id"] for e in entries if e.get("blizzard_item_id")
        )
        consensus_id: Optional[int] = None
        consensus_name = ""
        agree_count = 0
        if item_counter:
            consensus_id, agree_count = item_counter.most_common(1)[0]
            for e in entries:
                if e["blizzard_item_id"] == consensus_id and e.get("item_name"):
                    consensus_name = e["item_name"]
                    break

        source_entries = [
            {
                "source_id": e["source_id"],
                "source_name": e["source_name"],
                "blizzard_item_id": e.get("blizzard_item_id"),
                "item_name": e.get("item_name") or "",
                "agrees": e.get("blizzard_item_id") == consensus_id if consensus_id else False,
            }
            for e in entries
        ]

        result[slot] = {
            "consensus_blizzard_item_id": consensus_id,
            "consensus_item_name": consensus_name,
            "all_agree": bool(entries) and agree_count == len(entries),
            "agree_count": agree_count,
            "total_with_data": len(entries),
            "sources": source_entries,
        }

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
              FROM ref.bis_list_sources
             WHERE is_active = TRUE
             ORDER BY sort_order
            """
        )

        specs = await conn.fetch(
            """
            SELECT s.id, s.name AS spec_name, c.name AS class_name
              FROM ref.specializations s
              JOIN ref.classes c ON c.id = s.class_id
             ORDER BY c.name, s.name
            """
        )

        targets = await conn.fetch(
            """
            SELECT t.source_id, t.spec_id, t.hero_talent_id,
                   t.status, t.items_found, t.last_fetched, t.preferred_technique,
                   t.content_type, t.id AS target_id
              FROM config.bis_scrape_targets t
            """
        )

        hero_talents = await conn.fetch(
            "SELECT id, spec_id, name, slug FROM ref.hero_talents ORDER BY id"
        )

    ht_by_spec: dict[int, list[dict]] = {}
    for ht in hero_talents:
        ht_by_spec.setdefault(ht["spec_id"], []).append(dict(ht))

    # Build cell map: cells[spec_id][source_id][ht_key] → target data
    # ht_key is str(hero_talent_id) or "null" for targets that apply to all builds.
    # Each HT has its own entry so the UI can show accurate per-HT counts.
    cells: dict[str, dict[str, dict[str, dict]]] = {}
    for t in targets:
        spec_key = str(t["spec_id"])
        src_key = str(t["source_id"])
        ht_key = str(t["hero_talent_id"]) if t["hero_talent_id"] is not None else "null"
        cells.setdefault(spec_key, {}).setdefault(src_key, {})[ht_key] = {
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
