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
insert_bis_items(ctx, items, note, start)— insert List[SimcSlot] via the shared insertion engine
rebuild_bis_from_landing(pool)          — rebuild enrichment.bis_entries from landing HTML
rebuild_trinket_ratings_from_landing(pool) — rebuild enrichment.trinket_ratings from landing HTML
rebuild_item_popularity_from_landing(pool) — rebuild enrichment.item_popularity from u.gg landing HTML
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
class BisInsertionContext:
    """Shared context passed to the BIS insertion engine for one target."""
    pool: asyncpg.Pool
    spec_id: int
    source_id: int
    hero_talent_id: Optional[int]
    content_type: str


@dataclass
class ExtractedTrinketRating:
    blizzard_item_id: int
    item_name: str
    tier: str        # 'S', 'A', 'B', 'C', 'D', 'F'
    sort_order: int  # position within tier group, 0-indexed


@dataclass
class UggPopularityItem:
    slot: str
    blizzard_item_id: int
    count: int   # players using this item in this slot
    total: int   # total players sampled for this source × spec × slot

# ---------------------------------------------------------------------------
# Slug maps — (class_name, spec_name) → URL slugs per source
# ---------------------------------------------------------------------------
# Slug helper — mirrors guide_links._slug, uses separator from guide_sites


def _slug(name: str, sep: str = "-") -> str:
    """Convert a display name to a lowercase URL slug."""
    return name.lower().replace(" ", sep)

# u.gg slot names → our normalised internal keys
# Technique priority order for each BIS source origin
_TECHNIQUE_ORDER: dict[str, list[str]] = {
    "ugg":       ["json_embed"],
    "wowhead":   ["wh_gatherer"],
    "icy_veins": ["html_parse"],
    "method":    ["html_parse_method"],
    "archon":    ["json_embed_archon"],
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

    u.gg: one target per spec with hero_talent_id=NULL (raid + M+ path-based URLs).
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

                if origin == "ugg":
                    # u.gg: one target per spec, no hero talent split.
                    # Path-based URLs (/gear/raid, /gear) — no overall content type.
                    if content_type == "overall":
                        continue
                    url = _build_url(origin, class_name, spec_name, "", content_type,
                                     source["slug_separator"])
                    if not url:
                        continue
                    technique = _TECHNIQUE_ORDER.get(origin, ["json_embed"])[0]
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

                elif origin in ("icy_veins", "wowhead", "method"):
                    # These sources have one page per spec — no HT variation in the URL.
                    # Wowhead/Method: one combined BIS page per spec; sections differ by content_type.
                    # IV: one page per spec, all content toggled client-side (extraction stubbed).
                    # All get hero_talent_id=NULL ("applies to all builds").
                    if origin == "icy_veins":
                        url = _iv_base_url(class_name, spec_name, role_name)
                        technique = "html_parse"
                    elif origin == "method":
                        url = _build_url(origin, class_name, spec_name, "", content_type,
                                         source["slug_separator"])
                        technique = "html_parse_method"
                    else:
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

                elif origin == "archon":
                    # One target per spec per content_type; no hero talent split.
                    # Archon has Raid and M+ (content_type='dungeon') only.
                    if content_type not in ("raid", "dungeon"):
                        continue
                    url = _build_url(origin, class_name, spec_name, "", content_type)
                    if not url:
                        continue
                    technique = _TECHNIQUE_ORDER["archon"][0]
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
        return "healing"
    return "dps"


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
        if content_type == "raid":
            return f"https://u.gg/wow/{spec}/{cls}/gear/raid"
        elif content_type == "mythic_plus":
            return f"https://u.gg/wow/{spec}/{cls}/gear"
        else:
            return None  # u.gg has no overall page

    elif origin == "wowhead":
        # Wowhead always uses hyphens regardless of guide_sites separator.
        # All content types point to the same bis-gear page — Wowhead has one
        # combined BIS section per spec with no raid/M+ split.
        cls_wh  = _slug(class_name, "-")
        spec_wh = _slug(spec_name,  "-")
        return f"https://www.wowhead.com/guide/classes/{cls_wh}/{spec_wh}/bis-gear#bis-gear"

    elif origin == "method":
        # Method always uses hyphens. All three content types (overall/raid/mythic_plus)
        # are on the same /gearing page — parser selects the correct table by index.
        cls_m  = _slug(class_name, "-")
        spec_m = _slug(spec_name,  "-")
        return f"https://www.method.gg/guides/{spec_m}-{cls_m}/gearing"

    elif origin == "archon":
        # Archon.gg — spec-first, class-second in the URL path.
        cls_a  = _slug(class_name, "-")
        spec_a = _slug(spec_name,  "-")
        if content_type in ("dungeon", "mythic_plus"):
            return (
                f"https://www.archon.gg/wow/builds/{spec_a}/{cls_a}"
                f"/mythic-plus/gear-and-tier-set/10/all-dungeons/this-week"
            )
        elif content_type == "raid":
            return (
                f"https://www.archon.gg/wow/builds/{spec_a}/{cls_a}"
                f"/raid/gear-and-tier-set/mythic/all-bosses"
            )
        return None

    # Icy Veins — targets come from _iv_base_url via discover_targets.
    # Other unknown origins fall through to None.

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

    Returns a stats dict: {targets_run, items_upserted, errors}.
    """
    async with pool.acquire() as conn:
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
    so the biggest gaps are filled first.

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
    slots, _trinket_ratings, error, raw_content = await _extract(
        url, technique, content_type=content_type, spec_id=spec_id,
        source_id=source_id, pool=pool
    )

    now = datetime.now(timezone.utc)

    # Determine status from slot coverage (no guild_identity writes — enrichment layer handles that)
    if slots:
        items_found = len(slots)
        extracted_slots = {s.slot for s in slots}
        # Normalize: any weapon slot (main_hand intermediate or resolved variant)
        # counts as covering both main_hand_1h and main_hand_2h for coverage purposes.
        if extracted_slots & {"main_hand", "main_hand_1h", "main_hand_2h"}:
            extracted_slots = (extracted_slots - {"main_hand"}) | {"main_hand_1h", "main_hand_2h"}
        missing = set(SLOT_ORDER) - extracted_slots
        # A spec using a 2H weapon never has off_hand; both main_hand variants may
        # legitimately be absent (spec uses only one build).  off_hand + either
        # weapon variant absent is expected and not a partial failure.
        if not missing or missing <= {"off_hand", "main_hand_1h", "main_hand_2h"}:
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
                # For archon: extract source_updated_at from the page JSON
                source_updated_at = None
                if origin == "archon":
                    try:
                        page_data = json.loads(raw_content)
                        ts_str = page_data.get("lastUpdated")
                        if ts_str:
                            source_updated_at = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            )
                    except Exception:
                        pass
                await conn.execute(
                    """
                    INSERT INTO landing.bis_scrape_raw
                        (source, url, content, target_id, source_updated_at)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    origin, url, raw_content, target_id, source_updated_at,
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
    url: str, technique: str, content_type: str = "overall",
    spec_id: int = 0, source_id: int = 0, pool: Optional[asyncpg.Pool] = None,
) -> tuple[list[SimcSlot], list[ExtractedTrinketRating], Optional[str], Optional[str]]:
    """Dispatch to the appropriate extractor.

    Returns (slots, trinket_ratings, error_message, raw_content).
    slots and trinket_ratings are empty lists on failure.
    trinket_ratings is only populated for wh_gatherer technique.
    raw_content is the raw HTML/JSON fetched from the source (for landing schema).
    """
    try:
        if technique == "json_embed":
            slots, raw_content = await _extract_ugg(url, pool=pool)
            return slots, [], None, raw_content
        elif technique == "wh_gatherer":
            slots, trinket_ratings, raw_content = await _extract_wowhead(url, content_type=content_type, pool=pool)
            return slots, trinket_ratings, None, raw_content
        elif technique == "html_parse":
            slots, raw_content = await _extract_icy_veins(
                url, content_type=content_type, spec_id=spec_id, source_id=source_id, pool=pool
            )
            return slots, [], None, raw_content
        elif technique == "html_parse_method":
            slots, raw_content = await _extract_method(url, content_type, spec_id=spec_id, source_id=source_id, pool=pool)
            return slots, [], None, raw_content
        elif technique == "json_embed_archon":
            slots, raw_content = await _extract_archon(url, pool=pool)
            return slots, [], None, raw_content
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


def _parse_ugg_html(html: str, url: str, slot_map: dict[str, str | None] | None = None) -> list[SimcSlot]:
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
                return _parse_ugg_ssr(data, url, slot_map)
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


async def _extract_ugg(
    url: str, pool: Optional[asyncpg.Pool] = None
) -> tuple[list[SimcSlot], Optional[str]]:
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

    slot_map: dict[str, str | None] = {}
    if pool:
        async with pool.acquire() as conn:
            slot_map = await _load_slot_labels(conn)

    return _parse_ugg_html(html, url, slot_map), html


# ---------------------------------------------------------------------------
# Archon.gg extractor  (json_embed_archon)
# ---------------------------------------------------------------------------


def _parse_archon_page(
    page: dict,
    slot_map: dict[str, str | None],
    total_parses: int,
) -> tuple[list[SimcSlot], list[UggPopularityItem]]:
    """Parse Archon __NEXT_DATA__ page object → BIS slots + popularity rows.

    Finds the BuildsGearTablesSection (navigationId='gear-tables') in
    page['sections'].  For each table: extracts the slot label, item IDs, and
    popularity percentages from the JSX strings embedded in the data rows.

    Paired slots where slot_map returns None (trinket → trinket_1+trinket_2,
    rings → ring_1+ring_2) are expanded: every item in the table is written for
    both paired slots with the same guide_order.

    Returns (slots, popularity_items).  Slots are ordered so that insert_bis_items()
    assigns guide_order=1 to the most popular item (row index 0) per slot.
    """
    slots: list[SimcSlot] = []
    popularity_items: list[UggPopularityItem] = []

    # Find the gear tables section
    gear_section: dict | None = None
    for sec in (page.get("sections") or []):
        if sec.get("navigationId") == "gear-tables":
            gear_section = sec
            break

    if gear_section is None:
        logger.warning("_parse_archon_page: no 'gear-tables' section found in page")
        return slots, popularity_items

    tables = ((gear_section.get("props") or {}).get("tables")) or []

    for table in tables:
        columns = table.get("columns") or {}
        item_col = columns.get("item") or {}
        raw_label = item_col.get("header") or ""
        if not raw_label:
            continue

        # Header may be a JSX string: "<ImageIcon ...>Head</ImageIcon>" — strip tags
        raw_label = re.sub(r"<[^>]+>", "", raw_label).strip()
        if not raw_label:
            continue

        label_lower = raw_label.lower()
        # Look up in slot_map (try lowercased first, then original)
        if label_lower in slot_map:
            slot_key = slot_map[label_lower]
            known = True
        elif raw_label in slot_map:
            slot_key = slot_map[raw_label]
            known = True
        else:
            slot_key = None
            known = False

        # Determine the target slot(s) for this table
        if slot_key is not None:
            target_slots = [slot_key]
        elif known:
            # NULL in slot_map = paired slot; expand to both
            if "trinket" in label_lower:
                target_slots = ["trinket_1", "trinket_2"]
            elif "ring" in label_lower:
                target_slots = ["ring_1", "ring_2"]
            else:
                logger.warning(
                    "_parse_archon_page: NULL slot_key for unexpected label %r", raw_label
                )
                continue
        else:
            logger.warning("_parse_archon_page: unrecognised slot label %r", raw_label)
            continue

        for row in (table.get("data") or []):
            item_jsx = row.get("item") or ""
            pop_jsx  = row.get("popularity") or ""

            m_id = re.search(r"id=\{(\d+)\}", item_jsx)
            if not m_id:
                continue
            item_id = int(m_id.group(1))
            if item_id == 0:
                continue

            m_pct = re.search(r"([\d.]+)%", pop_jsx)
            pct = float(m_pct.group(1)) if m_pct else 0.0
            count = round(pct / 100.0 * total_parses)

            for slot in target_slots:
                slots.append(SimcSlot(slot=slot, blizzard_item_id=item_id))
                popularity_items.append(
                    UggPopularityItem(
                        slot=slot,
                        blizzard_item_id=item_id,
                        count=count,
                        total=total_parses,
                    )
                )

    return slots, popularity_items


async def _extract_archon(
    url: str,
    pool: Optional[asyncpg.Pool] = None,
) -> tuple[list[SimcSlot], Optional[str]]:
    """Fetch Archon.gg gear page and extract BIS items.

    Archon embeds all item data as JSON in a <script id="__NEXT_DATA__"> block —
    no Playwright or SSR workaround needed; a plain httpx GET returns everything.

    Returns (slots, raw_json) where raw_json is json.dumps(page) — the extracted
    page object from __NEXT_DATA__, suitable for storage in landing.bis_scrape_raw.
    raw_json is None on extraction failure.

    Parsing is delegated to _parse_archon_page() for reuse in
    rebuild_bis_from_landing().
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        logger.warning("_extract_archon: __NEXT_DATA__ not found in %s", url)
        return [], None

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("_extract_archon: JSON parse error for %s: %s", url, exc)
        return [], None

    try:
        page = data["props"]["pageProps"]["page"]
    except (KeyError, TypeError) as exc:
        logger.warning("_extract_archon: unexpected page structure for %s: %s", url, exc)
        return [], None

    slot_map: dict[str, str | None] = {}
    if pool:
        async with pool.acquire() as conn:
            slot_map = await _load_slot_labels(conn)

    total_parses = page.get("totalParses", 0)
    slots, _ = _parse_archon_page(page, slot_map, total_parses)

    return slots, json.dumps(page)


def _ugg_to_stats2_url(page_url: str) -> Optional[str]:
    """Attempt to derive a stats2.u.gg JSON URL from a u.gg page URL.

    Pattern: https://u.gg/wow/{spec}/{class}/gear[/raid]
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

    https://u.gg/wow/blood/death_knight/gear/raid → "DeathKnight-Blood"
    https://u.gg/wow/frost/mage/gear              → "Mage-Frost"
    """
    m = re.search(r"u\.gg/wow/([^/]+)/([^/]+)/gear", url)
    if not m:
        return ""
    spec_pascal  = _slug_to_pascal(m.group(1))   # "blood" → "Blood"
    class_pascal = _slug_to_pascal(m.group(2))   # "death_knight" → "DeathKnight"
    return f"{class_pascal}-{spec_pascal}"


def _ugg_url_to_section(url: str) -> str:
    """Map the u.gg URL path to the correct SSR data section.

    /gear/raid → "raid"
    /gear      → "mythic"  (base gear page is M+)
    """
    if "/gear/raid" in url:
        return "raid"
    return "mythic"


def _slug_to_pascal(slug: str) -> str:
    """Convert snake_case or kebab-case slug to PascalCase.

    'demon_hunter' → 'DemonHunter', 'death-knight' → 'DeathKnight'
    """
    return "".join(word.capitalize() for word in re.split(r"[-_]", slug))


def _parse_ugg_ssr(data: dict, url: str = "", slot_map: dict[str, str | None] | None = None) -> list[SimcSlot]:
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
    sm: dict[str, str | None] = slot_map or {}
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
                return _ugg_items_to_slots(items_by_slot, sm)

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
                            return _ugg_items_to_slots(items_table, sm)

            # Fallback: affixes (M+ data — mixes specs and may surface stale items)
            affixes = inner.get("affixes", {})
            if affixes:
                logger.warning(
                    "_parse_ugg_ssr: falling back to affixes for %s "
                    "(section=%s spec_key=%s not found)",
                    url, section, spec_key,
                )
                return _parse_ugg_combo_data(affixes, sm)
    except (AttributeError, TypeError):
        pass
    return []


def _parse_ugg_combo_data(affixes: dict, slot_map: dict[str, str | None]) -> list[SimcSlot]:
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
        normalised = slot_map.get(ugg_slot.lower())
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


def _parse_ugg_items_table(data: dict, slot_map: dict[str, str | None]) -> list[SimcSlot]:
    """Parse items from the stats2.u.gg direct JSON response (legacy format)."""
    try:
        items_by_slot = data.get("items_table", {}).get("items", {})
        return _ugg_items_to_slots(items_by_slot, slot_map)
    except (AttributeError, TypeError):
        return []


def _ugg_items_to_slots(items_by_slot: dict, slot_map: dict[str, str | None]) -> list[SimcSlot]:
    """Convert u.gg's per-slot items dict into SimcSlot list."""
    slots: list[SimcSlot] = []
    for ugg_slot, slot_data in items_by_slot.items():
        normalised = slot_map.get(ugg_slot.lower())
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


def _ugg_items_to_popularity(items_by_slot: dict, slot_map: dict[str, str | None]) -> list[UggPopularityItem]:
    """Extract per-item count/total from u.gg's items_table dict.

    Returns one UggPopularityItem per (slot, item_id) pair that has non-zero count.
    total is derived from the slot_data or per-item fields; falls back to
    count/perc derivation if neither is directly available.
    """
    result: list[UggPopularityItem] = []
    for ugg_slot, slot_data in items_by_slot.items():
        normalised = slot_map.get(ugg_slot.lower())
        if normalised is None:
            continue
        items = slot_data.get("items") or []
        if not items:
            continue

        # Derive slot-level total (same for all items in this slot)
        slot_total: int = int(slot_data.get("total") or 0)
        if not slot_total:
            # Try per-item total (take first non-zero value)
            for item in items:
                t = item.get("total") or 0
                if t:
                    slot_total = int(t)
                    break
        if not slot_total:
            # Last resort: derive from count + perc of the first item
            for item in items:
                count = int(item.get("count") or 0)
                perc = float(item.get("perc") or 0)
                if count and perc > 0:
                    slot_total = round(count / perc)
                    break
        if not slot_total:
            continue

        for item in items:
            item_id = item.get("item_id")
            if not item_id:
                continue
            try:
                iid = int(item_id)
            except (ValueError, TypeError):
                continue
            if iid == 0:
                continue
            count = int(item.get("count") or 0)
            if count == 0:
                # No count field — derive from perc × total
                perc = float(item.get("perc") or 0)
                if perc > 0:
                    count = round(perc * slot_total)
            if count == 0:
                continue
            # Weapon1 maps to generic "main_hand" — emit both typed slots so
            # popularity shows correctly regardless of 2H vs 1H build mode.
            emit_slots = (
                ["main_hand_2h", "main_hand_1h"]
                if normalised == "main_hand"
                else [normalised]
            )
            for emit_slot in emit_slots:
                result.append(UggPopularityItem(
                    slot=emit_slot,
                    blizzard_item_id=iid,
                    count=count,
                    total=slot_total,
                ))
    return result


def _parse_ugg_popularity(html: str, url: str, slot_map: dict[str, str | None] | None = None) -> list[UggPopularityItem]:
    """Parse u.gg SSR HTML and return per-item popularity data for all slots.

    Pure function — no network calls.  Extracts the full items list (all items,
    not just the top one) with count and total from the SSR JSON blob.
    """
    marker = "window.__SSR_DATA__"
    ssr_idx = html.find(marker)
    if ssr_idx < 0:
        return []
    obj_start = html.find("{", ssr_idx)
    if obj_start < 0:
        return []
    try:
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(html, obj_start)
    except (json.JSONDecodeError, ValueError):
        return []

    spec_key = _ugg_url_to_spec_key(url)
    section  = _ugg_url_to_section(url)

    try:
        for _url_key, table_data in data.items():
            if not isinstance(table_data, dict):
                continue
            inner = table_data.get("data") or table_data
            sec = inner.get(section)
            if not isinstance(sec, dict):
                continue
            all_data = sec.get("all")
            if not isinstance(all_data, dict) or not spec_key:
                continue
            spec_data = all_data.get(spec_key)
            if not isinstance(spec_data, dict):
                continue
            items_by_slot = spec_data.get("items_table", {}).get("items", {})
            if items_by_slot:
                return _ugg_items_to_popularity(items_by_slot, slot_map or {})
    except (AttributeError, TypeError):
        pass

    return []


async def rebuild_item_popularity_from_landing(pool: asyncpg.Pool) -> dict:
    """Rebuild enrichment.item_popularity by re-parsing u.gg landing HTML.

    Reads the most recent u.gg HTML for each scrape target (one per spec ×
    content_type), extracts all items with count/total from the SSR items_table,
    and TRUNCATE-rebuilds enrichment.item_popularity.

    Called by enrich-and-classify after rebuild_bis_from_landing so the
    popularity table reflects the same set of scraped pages.

    Returns {rows_inserted, specs_processed}.
    """
    async with pool.acquire() as conn:
        ugg_slot_map = await _load_slot_labels(conn)
        rows = await conn.fetch("""
            WITH latest AS (
                SELECT
                    bsr.content, bsr.url, bsr.source,
                    t.source_id, t.spec_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY bsr.target_id
                        ORDER BY bsr.fetched_at DESC
                    ) AS rn
                  FROM landing.bis_scrape_raw bsr
                  JOIN config.bis_scrape_targets t ON t.id = bsr.target_id
                 WHERE bsr.target_id IS NOT NULL
                   AND bsr.source IN ('ugg', 'archon')
            )
            SELECT content, url, source, source_id, spec_id
              FROM latest
             WHERE rn = 1
        """)
        await conn.execute("TRUNCATE enrichment.item_popularity")

    total_inserted = 0
    specs_processed = 0

    for row in rows:
        source = row["source"]
        if source == "ugg":
            items = _parse_ugg_popularity(row["content"], row["url"], ugg_slot_map)
        elif source == "archon":
            page = json.loads(row["content"])
            total_parses = page.get("totalParses", 0)
            _, items = _parse_archon_page(page, ugg_slot_map, total_parses)
        else:
            continue
        if not items:
            continue

        specs_processed += 1
        async with pool.acquire() as conn:
            for item in items:
                try:
                    await conn.execute(
                        """
                        INSERT INTO enrichment.item_popularity
                            (source_id, spec_id, slot, blizzard_item_id, count, total)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (source_id, spec_id, slot, blizzard_item_id)
                        DO UPDATE SET
                            count      = EXCLUDED.count,
                            total      = EXCLUDED.total,
                            scraped_at = NOW()
                        """,
                        row["source_id"], row["spec_id"],
                        item.slot, item.blizzard_item_id,
                        item.count, item.total,
                    )
                    total_inserted += 1
                except Exception:
                    pass

    logger.info(
        "rebuild_item_popularity_from_landing: %d rows inserted from %d targets",
        total_inserted, specs_processed,
    )
    return {"rows_inserted": total_inserted, "specs_processed": specs_processed}


# ---------------------------------------------------------------------------
# Wowhead extractor  (wh_gatherer)
# ---------------------------------------------------------------------------

_WH_GATHERER_RE = re.compile(
    r"WH\.Gatherer\.addData\(\s*\d+\s*,\s*\d+\s*,\s*(\{.+?\})\s*\)",
    re.DOTALL,
)
_ITEM_MARKUP_RE = re.compile(r"\[item=(\d+)[^\]]*\]")
# Wowhead BBcode table row where slot label is "Offhand" or "Off Hand".
# Wowhead stores BBcode with escaped forward slashes ([\/td]), so we accept
# both [/td] and [\/td].  Captures the full cell content so all items in
# multi-option rows (e.g. "item A or item B") are collected.
_WH_OFFHAND_ROW_RE = re.compile(
    r"\[td\]off[\s\-]?hand\[\\?/td\]\[td\](.*?)\[\\?/td\]",
    re.IGNORECASE | re.DOTALL,
)
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


async def reparse_method_sections(pool: asyncpg.Pool) -> dict:
    """Re-run section extraction on existing Method HTML in landing.bis_scrape_raw.

    Does NOT re-fetch from Method.gg — reads stored HTML and re-classifies
    headings using the current classifier.  Use this after updating keyword
    rules in _classify_method_heading to apply them without waiting for stale
    targets to be re-scraped by gap fill.

    Returns {specs_processed, sections_upserted}.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH latest AS (
                SELECT bsr.content, t.spec_id, t.source_id, t.url AS page_url,
                    ROW_NUMBER() OVER (
                        PARTITION BY t.spec_id, t.source_id
                        ORDER BY bsr.fetched_at DESC
                    ) AS rn
                  FROM landing.bis_scrape_raw bsr
                  JOIN config.bis_scrape_targets t ON t.id = bsr.target_id
                  JOIN ref.bis_list_sources s ON s.id = t.source_id
                 WHERE s.origin = 'method'
                   AND bsr.content IS NOT NULL
            )
            SELECT content, spec_id, source_id, page_url FROM latest WHERE rn = 1
        """)
        slot_map = await _load_slot_labels(conn)

    specs_processed = 0
    sections_upserted = 0
    for row in rows:
        sections = _extract_method_sections(row["content"], slot_map)
        if not sections:
            continue
        async with pool.acquire() as conn:
            await _upsert_method_sections(conn, row["spec_id"], row["source_id"], row["page_url"], sections)
        specs_processed += 1
        sections_upserted += len(sections)

    logger.info(
        "reparse_method_sections: %d specs, %d sections upserted",
        specs_processed, sections_upserted,
    )
    return {"specs_processed": specs_processed, "sections_upserted": sections_upserted}


async def insert_bis_items(
    ctx: BisInsertionContext,
    items: list[SimcSlot],
    note: str | None = None,
    guide_order_start: int = 1,
) -> dict:
    """Insert a List[SimcSlot] into enrichment.bis_entries for one target.

    Handles weapon variant resolution (main_hand → main_hand_1h/2h),
    guide_order assignment per slot, FK validation against enrichment.items,
    and optional bis_note stamping.  Returns {"inserted": N, "skipped": N}.
    """
    if not items:
        return {"inserted": 0, "skipped": 0}

    inserted = 0
    skipped = 0

    async with ctx.pool.acquire() as conn:
        weapon_counter = guide_order_start - 1
        slot_counters: dict[str, int] = {}

        for slot_data in items:
            # Resolve main_hand intermediate slot → main_hand_1h or main_hand_2h
            if slot_data.slot == "main_hand":
                weapon_counter += 1
                resolved_slot = await _resolve_weapon_slot(conn, slot_data.blizzard_item_id)
                if resolved_slot is None:
                    skipped += 1
                    continue
                actual_slot = resolved_slot
                guide_order = weapon_counter
            else:
                actual_slot = slot_data.slot
                slot_counters[actual_slot] = slot_counters.get(actual_slot, guide_order_start - 1) + 1
                guide_order = slot_counters[actual_slot]

            # enrichment.bis_entries.blizzard_item_id FKs to enrichment.items —
            # skip items not yet in the enrichment layer.
            exists = await conn.fetchval(
                "SELECT 1 FROM enrichment.items WHERE blizzard_item_id = $1",
                slot_data.blizzard_item_id,
            )
            if not exists:
                skipped += 1
                continue

            try:
                await conn.execute(
                    """
                    INSERT INTO enrichment.bis_entries
                        (source_id, spec_id, hero_talent_id, slot, blizzard_item_id, guide_order, bis_note)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    ctx.source_id, ctx.spec_id, ctx.hero_talent_id,
                    actual_slot, slot_data.blizzard_item_id, guide_order, note,
                )
                inserted += 1
            except Exception:
                skipped += 1  # duplicate within rebuild — silently skip

    return {"inserted": inserted, "skipped": skipped}


async def rebuild_bis_from_landing(pool: asyncpg.Pool) -> dict:
    """Rebuild enrichment.bis_entries by re-parsing landing.bis_scrape_raw.

    Two-pass rebuild:
      Pass 1 — normal targets: parse per-source HTML and call insert_bis_items().
               Targets that have a merge override with secondary_section_key are
               skipped here and handled in pass 2.
      Pass 2 — merge targets: for each override row with secondary_section_key set,
               fetch both sections, call merge_bis_sections() to fold them together.

    Only inserts items that already exist in enrichment.items (the FK requires it).

    Called by enrich-and-classify in bis_routes after sp_rebuild_all() so that
    enrichment.items is populated before we try to insert BIS references.

    Returns {bis_entries_inserted}.
    """
    async with pool.acquire() as conn:
        slot_map = await _load_slot_labels(conn)
        wh_invtype_map = await _load_wowhead_invtypes(conn)
        raid_instance_names = await _load_raid_instance_names(conn)
        rows = await conn.fetch("""
            WITH latest AS (
                SELECT
                    bsr.content, bsr.url, bsr.source,
                    t.id AS target_id,
                    t.source_id, t.spec_id, t.hero_talent_id, t.content_type,
                    ROW_NUMBER() OVER (
                        PARTITION BY bsr.target_id
                        ORDER BY bsr.fetched_at DESC
                    ) AS rn
                  FROM landing.bis_scrape_raw bsr
                  JOIN config.bis_scrape_targets t ON t.id = bsr.target_id
                 WHERE bsr.target_id IS NOT NULL
            )
            SELECT content, url, source, target_id, source_id, spec_id, hero_talent_id, content_type
              FROM latest
             WHERE rn = 1
        """)
        # Load merge override rows for pass 2 before the TRUNCATE.
        merge_override_rows = await conn.fetch("""
            SELECT
                o.spec_id, o.source_id, o.content_type,
                o.section_key, o.secondary_section_key,
                o.primary_note, o.match_note, o.secondary_note,
                s.origin,
                t.id            AS target_id,
                t.hero_talent_id
              FROM config.bis_section_overrides o
              JOIN ref.bis_list_sources s ON s.id = o.source_id
              LEFT JOIN config.bis_scrape_targets t
                     ON t.spec_id = o.spec_id
                    AND t.source_id = o.source_id
                    AND COALESCE(t.content_type, 'overall') = o.content_type
             WHERE o.secondary_section_key IS NOT NULL
        """)
        # TRUNCATE first — clean-slate rebuild.  enrichment.bis_entries has no
        # dependents so CASCADE is not needed.
        await conn.execute("TRUNCATE enrichment.bis_entries")

    merge_keys: set[tuple[int, int, str]] = {
        (r["spec_id"], r["source_id"], r["content_type"])
        for r in merge_override_rows
    }

    total_inserted = 0
    now = datetime.now(timezone.utc)

    # Pass 1: normal targets
    for row in rows:
        html = row["content"]
        url = row["url"]
        source = row["source"]
        target_id = row["target_id"]
        source_id = row["source_id"]
        spec_id = row["spec_id"]
        hero_talent_id = row["hero_talent_id"]
        content_type = row["content_type"] or "overall"

        if (spec_id, source_id, content_type) in merge_keys:
            continue

        if source == "ugg":
            slots = _parse_ugg_html(html, url, slot_map)
        elif source == "wowhead":
            slots, _ = _parse_wowhead_html(html, url, content_type, wh_invtype_map)
        elif source == "method":
            slots = await _resolve_method_bis_from_db(pool, spec_id, source_id, content_type)
        elif source == "icy_veins":
            sections = _iv_parse_sections(html, slot_map, raid_instance_names)
            slots = await _resolve_iv_section(pool, sections, spec_id, source_id, content_type)
        elif source == "archon":
            page = json.loads(html)
            total_parses = page.get("totalParses", 0)
            slots, _ = _parse_archon_page(page, slot_map, total_parses)
        else:
            continue

        ctx = BisInsertionContext(
            pool=pool,
            spec_id=spec_id,
            source_id=source_id,
            hero_talent_id=hero_talent_id,
            content_type=content_type,
        )
        result = await insert_bis_items(ctx, slots or [])
        target_inserted = result["inserted"]
        total_inserted += target_inserted

        # Determine status from coverage and stamp back onto scrape target
        if target_inserted == 0:
            rebuild_status = "failed"
        else:
            extracted_slots = {s.slot for s in slots} if slots else set()
            # Normalize weapon slots for coverage check
            if extracted_slots & {"main_hand", "main_hand_1h", "main_hand_2h"}:
                extracted_slots = (extracted_slots - {"main_hand"}) | {"main_hand_1h", "main_hand_2h"}
            missing = set(SLOT_ORDER) - extracted_slots
            rebuild_status = "success" if missing <= {"off_hand", "main_hand_1h", "main_hand_2h"} else "partial"

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE config.bis_scrape_targets
                   SET items_found = $1, status = $2, last_fetched = $3
                 WHERE id = $4
                """,
                target_inserted, rebuild_status, now, target_id,
            )

    # Pass 2: merge targets
    for mo in merge_override_rows:
        spec_id = mo["spec_id"]
        source_id = mo["source_id"]
        content_type = mo["content_type"]
        origin = mo["origin"]

        async with pool.acquire() as conn:
            primary_items = await _fetch_section_items(
                conn, spec_id, source_id, origin, mo["section_key"], slot_map, raid_instance_names,
            )
            secondary_items = await _fetch_section_items(
                conn, spec_id, source_id, origin, mo["secondary_section_key"], slot_map, raid_instance_names,
            )

        ctx = BisInsertionContext(
            pool=pool,
            spec_id=spec_id,
            source_id=source_id,
            hero_talent_id=mo["hero_talent_id"],
            content_type=content_type,
        )
        result = await merge_bis_sections(ctx, primary_items, secondary_items, dict(mo))
        total_inserted += result["inserted"]

        # Status update for the corresponding scrape target
        target_id = mo["target_id"]
        if target_id:
            target_inserted = result["inserted"]
            if target_inserted == 0:
                rebuild_status = "failed"
            else:
                all_items = list(primary_items) + list(secondary_items)
                extracted_slots = {s.slot for s in all_items}
                if extracted_slots & {"main_hand", "main_hand_1h", "main_hand_2h"}:
                    extracted_slots = (extracted_slots - {"main_hand"}) | {"main_hand_1h", "main_hand_2h"}
                missing = set(SLOT_ORDER) - extracted_slots
                rebuild_status = "success" if missing <= {"off_hand", "main_hand_1h", "main_hand_2h"} else "partial"

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE config.bis_scrape_targets
                       SET items_found = $1, status = $2, last_fetched = $3
                     WHERE id = $4
                    """,
                    target_inserted, rebuild_status, now, target_id,
                )
        logger.info(
            "rebuild_bis_from_landing merge: spec %d source %d %s → %d inserted",
            spec_id, source_id, content_type, result["inserted"],
        )

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
                    bsr.content, bsr.url, bsr.source AS bsr_source,
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
                   AND bsr.source IN ('wowhead', 'icy_veins')
            )
            SELECT content, url, bsr_source, source_id, spec_id, hero_talent_id
              FROM ranked
             WHERE rn = 1
        """)
        wh_invtype_map = await _load_wowhead_invtypes(conn)
        await conn.execute("TRUNCATE enrichment.trinket_ratings")

    total_inserted = 0
    for row in rows:
        html = row["content"]
        url = row["url"]
        bsr_source = row["bsr_source"]
        source_id = row["source_id"]
        spec_id = row["spec_id"]
        hero_talent_id = row["hero_talent_id"]

        if bsr_source == "wowhead":
            _, trinket_ratings = _parse_wowhead_html(html, url, slot_map=wh_invtype_map)
        elif bsr_source == "icy_veins":
            raw_rows = _iv_parse_trinkets_from_raw(html)
            trinket_ratings = [
                ExtractedTrinketRating(
                    blizzard_item_id=r["item_id"],
                    item_name="",
                    tier=r["tier"],
                    sort_order=r["sort_order"],
                )
                for r in raw_rows
            ]
        else:
            continue

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
    html: str, url: str = "", content_type: str = "overall",
    slot_map: dict[int, str] | None = None,
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
    sm: dict[int, str] = slot_map or {}
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
    section_slots = _wh_slots_from_section(html, item_meta, content_type, sm)

    if content_type != "overall":
        # Backfill missing slots with overall recommendations so every
        # content_type ends up with a full 16-slot BIS list.
        overall_slots = _wh_slots_from_section(html, item_meta, "overall", sm)
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
    url: str, content_type: str = "overall",
    pool: Optional[asyncpg.Pool] = None,
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

    invtype_map: dict[int, str] = {}
    if pool:
        async with pool.acquire() as conn:
            invtype_map = await _load_wowhead_invtypes(conn)

    slots, trinket_ratings = _parse_wowhead_html(html, url, content_type, invtype_map)
    return slots, trinket_ratings, html


# ---------------------------------------------------------------------------
# Method.gg extractor  (html_parse_method)
# ---------------------------------------------------------------------------


@dataclass
class MethodSection:
    heading: str
    table_index: int
    row_count: int
    slots: list[SimcSlot]
    inferred_content_type: Optional[str]
    is_outlier: bool
    outlier_reason: Optional[str]


async def _load_slot_labels(conn: asyncpg.Connection) -> dict[str, str | None]:
    """Load universal text label → slot_key mapping from config.slot_labels."""
    rows = await conn.fetch("SELECT page_label, slot_key FROM config.slot_labels")
    return {r["page_label"]: r["slot_key"] for r in rows}


async def _load_wowhead_invtypes(conn: asyncpg.Connection) -> dict[int, str]:
    """Load Blizzard inventory_type code → slot_key from config.wowhead_invtypes."""
    rows = await conn.fetch("SELECT invtype_id, slot_key FROM config.wowhead_invtypes")
    return {r["invtype_id"]: r["slot_key"] for r in rows}


async def _load_raid_instance_names(conn: asyncpg.Connection) -> frozenset[str]:
    """Load current-season raid instance names from landing.blizzard_journal_instances.

    Used by the IV classifier to detect tab labels that name raid wings instead
    of using the generic keyword "raid".  Returns empty frozenset if the table
    has no raid rows (e.g. before the first Blizzard API sync).
    """
    rows = await conn.fetch(
        "SELECT instance_name FROM landing.blizzard_journal_instances WHERE instance_type = 'raid'"
    )
    return frozenset(r["instance_name"] for r in rows)


def _resolve_text_slot(
    raw_label: str,
    slot_map: dict[str, str | None],
    ring_count: int = 0,
    trinket_count: int = 0,
) -> tuple[str | None, int, int]:
    """Resolve a text BIS slot label to a canonical slot key.

    Shared by all text-label BIS parsers (Method.gg, Icy Veins, etc.).

    Returns (slot_key, ring_count, trinket_count).  slot_key is None for
    unrecognised labels; counters are updated when a ring or trinket slot
    is positionally assigned.

    Resolution rules (in order):
      1. Label in map with non-NULL value → return it directly.
      2. Label in map with NULL value     → positional ring_1/ring_2 or trinket_1/trinket_2.
      3. Label absent, contains "ring" or "trinket" → positional (unknown variant).
      4. Label absent and unrelated       → None (caller should skip and log).
    """
    slot_key = slot_map.get(raw_label)
    known = raw_label in slot_map

    if slot_key is not None:
        return slot_key, ring_count, trinket_count

    if not known and "ring" not in raw_label and "trinket" not in raw_label:
        return None, ring_count, trinket_count

    if "ring" in raw_label:
        ring_count += 1
        return ("ring_1" if ring_count % 2 == 1 else "ring_2"), ring_count, trinket_count
    trinket_count += 1
    return ("trinket_1" if trinket_count % 2 == 1 else "trinket_2"), ring_count, trinket_count


def _classify_method_heading(heading: str) -> Optional[str]:
    """Infer content_type from a Method.gg section heading.

    Returns 'overall', 'raid', 'mythic_plus', or None if unrecognised.
    """
    h = heading.lower()
    if "mythic" in h or "dungeon" in h:
        return "mythic_plus"
    if "raid" in h:
        return "raid"
    if "overall" in h:
        return "overall"
    if "best in slot" in h or "bis" in h:
        return "overall"
    return None


def _parse_method_table(
    table_el, slot_map: dict[str, str | None]
) -> list[SimcSlot]:
    """Parse a single Method.gg BIS table element into SimcSlot list."""
    results: list[SimcSlot] = []
    ring_count = 0
    trinket_count = 0
    weapon_count = 0  # cap at 2: guide_order=1 (preferred build) and guide_order=2 (alt build)

    for row in table_el.find_all("tr")[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        raw_slot = cells[0].get_text(strip=True).lower()

        # Peek at the map without consuming ring/trinket counts.
        direct_key = slot_map.get(raw_slot)
        known = raw_slot in slot_map
        # NULL in map or absent ring/trinket variant → positional pool row
        is_positional = direct_key is None and (known or "ring" in raw_slot or "trinket" in raw_slot)

        if direct_key is None and not is_positional:
            logger.debug("_parse_method_table: unrecognised slot %r, skipping", raw_slot)
            continue

        # Ring/trinket pool rows ("Rings (any 2 of these)") can have multiple
        # item links in one cell — emit one SimcSlot per link.  All other rows
        # use only the first link (named slots never have multi-item cells).
        if is_positional:
            candidate_links = cells[1].find_all("a", href=True)
        else:
            first = cells[1].find("a", href=True)
            candidate_links = [first] if first else []

        if not candidate_links:
            continue

        # Limit weapon slots to 2: guide_order=1 (preferred build) and =2 (alt build).
        if direct_key == "main_hand":
            weapon_count += 1
            if weapon_count > 2:
                continue

        for link in candidate_links:
            m = re.search(r"item=(\d+)", link["href"])
            if not m:
                continue
            item_id = int(m.group(1))

            bonus_ids: list[int] = []
            bm = re.search(r"bonus=([0-9:]+)", link["href"])
            if bm:
                bonus_ids = [int(b) for b in bm.group(1).split(":") if b]

            current_slot, ring_count, trinket_count = _resolve_text_slot(
                raw_slot, slot_map, ring_count, trinket_count
            )
            if current_slot is None:
                continue

            results.append(SimcSlot(
                slot=current_slot,
                blizzard_item_id=item_id,
                bonus_ids=bonus_ids,
                enchant_id=None,
                gem_ids=[],
                quality_track=None,
            ))

    return results


def _extract_method_sections(
    html: str, slot_map: dict[str, str | None]
) -> list[MethodSection]:
    """Parse Method.gg gearing page HTML into a list of MethodSection objects.

    Walks document elements in order, pairing each h3 with the table that
    immediately follows it.  Sections without a preceding h3 are skipped.

    After parsing, sections are classified by heading keyword and any that
    produce duplicate or unrecognised inferred_content_type values are flagged
    as outliers so the admin can configure overrides.

    Pure function — no network calls or DB access.
    slot_map is loaded from config.method_slot_labels by async callers.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("_extract_method_sections: BeautifulSoup not available")
        return []

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Walk all heading (h2/h3/h4) and table elements in document order.
    # Each heading sets the pending heading; the next table consumes it.
    _HEADING_TAGS = {"h2", "h3", "h4"}
    pairs: list[tuple[str, object]] = []  # (heading, table_el)
    pending_heading: Optional[str] = None

    for elem in soup.find_all(["h2", "h3", "h4", "table"]):
        if elem.name in _HEADING_TAGS:
            pending_heading = elem.get_text(strip=True)
        elif elem.name == "table" and pending_heading is not None:
            pairs.append((pending_heading, elem))
            pending_heading = None

    # Build sections with initial classification
    sections: list[MethodSection] = []
    for i, (heading, table_el) in enumerate(pairs):
        slots = _parse_method_table(table_el, slot_map)
        inferred = _classify_method_heading(heading)
        sections.append(MethodSection(
            heading=heading,
            table_index=i,
            row_count=len(slots),
            slots=slots,
            inferred_content_type=inferred,
            is_outlier=False,
            outlier_reason=None,
        ))

    # Single-section rule: if only one section exists and its heading contains
    # "overall", treat it as overall regardless of other conflicting keywords
    # (e.g. "Overall / Raid Best Gear" on a spec with no separate Raid list).
    if len(sections) == 1 and "overall" in sections[0].heading.lower():
        sections[0].inferred_content_type = "overall"
        sections[0].is_outlier = False
        sections[0].outlier_reason = None
        return sections

    # Detect outliers: same inferred CT more than once, or unrecognised heading
    ct_counts: dict[str, int] = {}
    for s in sections:
        if s.inferred_content_type:
            ct_counts[s.inferred_content_type] = ct_counts.get(s.inferred_content_type, 0) + 1

    for s in sections:
        if s.inferred_content_type is None:
            s.is_outlier = True
            s.outlier_reason = "unrecognised heading"
        elif ct_counts.get(s.inferred_content_type, 0) > 1:
            s.is_outlier = True
            s.outlier_reason = f"duplicate classification for {s.inferred_content_type!r}"

    return sections


def _resolve_method_section_local(
    sections: list[MethodSection], content_type: str
) -> list[SimcSlot]:
    """Return slots for content_type using only auto-classification.

    Picks the first non-outlier section whose inferred_content_type matches.
    Returns empty list if no match found.  Pure — no DB access.
    """
    for s in sections:
        if s.inferred_content_type == content_type and not s.is_outlier:
            return s.slots
    return []


async def _upsert_method_sections(
    conn: asyncpg.Connection,
    spec_id: int,
    source_id: int,
    page_url: str,
    sections: list[MethodSection],
) -> None:
    """Upsert section heading metadata to landing.bis_page_sections.

    Only stores metadata used by the override UI (heading, classification,
    outlier status).  Slots are NOT stored here — they live in
    enrichment.bis_entries, populated by rebuild_bis_from_landing() which
    re-parses raw HTML from landing.bis_scrape_raw.
    """
    now = datetime.now(timezone.utc)
    for s in sections:
        await conn.execute(
            """
            INSERT INTO landing.bis_page_sections
                (spec_id, source_id, page_url, section_key, section_title,
                 sort_order, content_type, is_trinket_section, row_count,
                 is_outlier, outlier_reason, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, $8, $9, $10, $11)
            ON CONFLICT (spec_id, source_id, section_key) DO UPDATE SET
                page_url      = EXCLUDED.page_url,
                section_title = EXCLUDED.section_title,
                sort_order    = EXCLUDED.sort_order,
                content_type  = EXCLUDED.content_type,
                row_count     = EXCLUDED.row_count,
                is_outlier    = EXCLUDED.is_outlier,
                outlier_reason = EXCLUDED.outlier_reason,
                scraped_at    = EXCLUDED.scraped_at
            """,
            spec_id, source_id, page_url,
            s.heading, s.heading,
            s.table_index, s.inferred_content_type,
            s.row_count, s.is_outlier, s.outlier_reason, now,
        )


async def _resolve_method_section(
    pool: asyncpg.Pool,
    sections: list[MethodSection],
    spec_id: int,
    source_id: int,
    content_type: str,
) -> list[SimcSlot]:
    """Resolve which section to use for content_type, consulting DB overrides.

    If an override row exists in config.bis_section_overrides for this
    (spec_id, source_id, content_type), use the section matching that heading.
    Otherwise fall back to auto-classification via _resolve_method_section_local.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT section_key
              FROM config.bis_section_overrides
             WHERE spec_id = $1 AND source_id = $2 AND content_type = $3
            """,
            spec_id, source_id, content_type,
        )
    if row:
        target_heading = row["section_key"]
        for s in sections:
            if s.heading == target_heading:
                return s.slots
        logger.warning(
            "_resolve_method_section: override heading %r not found in sections for spec %d source %d / %s",
            target_heading, spec_id, source_id, content_type,
        )
        return []
    return _resolve_method_section_local(sections, content_type)


async def _resolve_method_bis_from_db(
    pool: asyncpg.Pool,
    spec_id: int,
    source_id: int,
    content_type: str,
) -> list[SimcSlot]:
    """Resolve BIS slots for a Method target by re-parsing raw HTML.

    Used by rebuild_bis_from_landing() — reads the latest raw HTML for this
    spec from landing.bis_scrape_raw, re-parses sections with the current
    classifier, then resolves via config.bis_section_overrides or
    auto-classification.  No pre-parsed slot data is read from landing.
    """
    async with pool.acquire() as conn:
        raw_row = await conn.fetchrow(
            """
            SELECT bsr.content
              FROM landing.bis_scrape_raw bsr
              JOIN config.bis_scrape_targets t  ON t.id = bsr.target_id
              JOIN ref.bis_list_sources s        ON s.id = t.source_id
             WHERE s.origin = 'method'
               AND t.spec_id = $1
               AND bsr.content IS NOT NULL
             ORDER BY bsr.fetched_at DESC
             LIMIT 1
            """,
            spec_id,
        )
        if not raw_row:
            return []

        override = await conn.fetchrow(
            """
            SELECT section_key
              FROM config.bis_section_overrides
             WHERE spec_id = $1 AND source_id = $2 AND content_type = $3
            """,
            spec_id, source_id, content_type,
        )
        slot_map = await _load_slot_labels(conn)

    sections = _extract_method_sections(raw_row["content"], slot_map)
    if not sections:
        return []

    if override:
        target_heading = override["section_key"]
        for s in sections:
            if s.heading == target_heading:
                return s.slots
        logger.warning(
            "_resolve_method_bis_from_db: override heading %r not found for spec %d source %d / %s",
            target_heading, spec_id, source_id, content_type,
        )
        return []

    return _resolve_method_section_local(sections, content_type)


async def _extract_method(
    url: str,
    content_type: str = "overall",
    spec_id: int = 0,
    source_id: int = 0,
    pool: Optional[asyncpg.Pool] = None,
) -> tuple[list[SimcSlot], Optional[str]]:
    """Fetch Method.gg gearing page and extract BIS items.

    Returns (slots, raw_html).  raw_html written to landing.bis_scrape_raw.

    When spec_id, source_id, and pool are provided, parsed sections are upserted
    to landing.bis_page_sections and DB overrides are consulted for resolution.
    Otherwise falls back to pure auto-classification (used in tests).
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    if pool and spec_id and source_id:
        async with pool.acquire() as conn:
            slot_map = await _load_slot_labels(conn)
            sections = _extract_method_sections(html, slot_map)
            await _upsert_method_sections(conn, spec_id, source_id, url, sections)
        slots = await _resolve_method_section(pool, sections, spec_id, source_id, content_type)
    else:
        sections = _extract_method_sections(html, {})
        slots = _resolve_method_section_local(sections, content_type)

    return slots, html


async def _resolve_weapon_slot(conn: asyncpg.Connection, blizzard_item_id: int) -> Optional[str]:
    """Resolve a main_hand item to main_hand_2h or main_hand_1h via enrichment.items.slot_type.

    Returns None and logs ERROR if the item is not in enrichment.items — this
    indicates sp_rebuild_items has not yet run, which is a pipeline ordering bug.
    """
    slot_type = await conn.fetchval(
        "SELECT slot_type FROM enrichment.items WHERE blizzard_item_id = $1",
        blizzard_item_id,
    )
    if slot_type is None:
        logger.error(
            "_resolve_weapon_slot: item %d not found in enrichment.items — "
            "ensure sp_rebuild_items ran before rebuild_bis_from_landing",
            blizzard_item_id,
        )
        return None
    if slot_type in ("two_hand", "ranged"):
        return "main_hand_2h"
    if slot_type == "one_hand":
        return "main_hand_1h"
    logger.error(
        "_resolve_weapon_slot: item %d has unexpected slot_type=%r for a weapon slot — "
        "defaulting to main_hand_2h",
        blizzard_item_id, slot_type,
    )
    return "main_hand_2h"


def _wh_slots_from_section(
    html: str, item_meta: dict, content_type: str,
    slot_map: dict[int, str] | None = None,
) -> list[SimcSlot]:
    """Extract SimcSlot list from a single Wowhead page section.

    Scans both [item=N] and [icon-badge=N] markup within the section slice,
    resolves invtype codes via config.wowhead_invtypes, and assigns ring_1/ring_2
    and trinket_1/trinket_2 by document order.
    """
    section_html = _wh_section_for_content_type(html, content_type)

    # Pre-pass: detect items explicitly labeled "Offhand" in the BBcode table.
    # Wowhead invtype for glaives/one-handers is 13 (INVTYPE_WEAPON = main_hand),
    # so off-hand dual-wield items must be identified by their row label instead.
    # A cell may list multiple options ("item A or item B"), so scan all [item=N]
    # within the matched cell content.
    explicit_offhand_ids: set[int] = set()
    for row_m in _WH_OFFHAND_ROW_RE.finditer(section_html):
        for item_m in _ITEM_MARKUP_RE.finditer(row_m.group(1)):
            explicit_offhand_ids.add(int(item_m.group(1)))

    # Collect item IDs in document order, deduped, across both markup patterns
    pos_map: dict[int, int] = {}
    for pat in (_ITEM_MARKUP_RE, _ICON_BADGE_RE):
        for m in pat.finditer(section_html):
            iid = int(m.group(1))
            if iid not in pos_map:
                pos_map[iid] = m.start()
    referenced_ids = sorted(pos_map.keys(), key=lambda iid: pos_map[iid])

    seen_slots: dict[str, int] = {}
    weapon_slots: list[int] = []   # item_ids of main_hand weapons in document order
    ring_count = 0
    trinket_count = 0

    for item_id in referenced_ids:
        meta = item_meta.get(item_id)
        if not meta:
            continue
        je = meta.get("jsonequip") or {}
        slot_code = je.get("slotbak") if isinstance(je, dict) else meta.get("slotbak")
        base_slot = (slot_map or {}).get(slot_code)
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
        elif base_slot == "main_hand":
            if item_id in explicit_offhand_ids:
                # Explicit off-hand label in BBcode table overrides invtype classification.
                if "off_hand" not in seen_slots:
                    seen_slots["off_hand"] = item_id
            else:
                # Collect up to 2 weapon items; type resolved to main_hand_1h/2h downstream.
                if len(weapon_slots) < 2:
                    weapon_slots.append(item_id)
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
    ] + [
        SimcSlot(slot="main_hand", blizzard_item_id=iid, bonus_ids=[], enchant_id=None,
                 gem_ids=[], quality_track=None)
        for iid in weapon_slots
    ]


# ---------------------------------------------------------------------------
# Icy Veins extractor  (html_parse)
# ---------------------------------------------------------------------------


@dataclass
class IVSection:
    h3_id: str
    section_title: str
    content_type: Optional[str]   # 'overall', 'raid', 'mythic_plus', or None if unrecognised
    is_trinket_section: bool
    row_count: int
    slots: list[SimcSlot]         # non-trinket sections only
    trinket_rows: list[dict]      # trinket sections only: [{tier, item_id, sort_order}]
    is_outlier: bool
    outlier_reason: Optional[str]


def _iv_classify_section(h3_id: str) -> tuple[Optional[str], bool]:
    """Map an IV h3 id to (content_type, is_trinket_section).

    Uses keyword matching because IV uses many h3 id variants across specs:
      contains "mythic"            → mythic_plus  (checked first)
      contains "raid"              → raid
      contains "bis" or "overall"  → overall
      none of the above            → None (flagged as outlier)

    is_trinket_section is set by the caller based on DOM structure, not the id.
    """
    h = h3_id.lower()
    if "mythic" in h:
        return "mythic_plus", False
    if "raid" in h:
        return "raid", False
    if "bis" in h or "overall" in h:
        return "overall", False
    return None, False


def _iv_extract_regular_rows(
    table_el,
    slot_map: dict[str, str | None],
) -> list[SimcSlot]:
    """Parse a standard IV BIS table into SimcSlot list.

    Each row: td[0] = slot label, td[1] contains span[data-wowhead=item=N].
    Calls _resolve_text_slot for ring/trinket positional assignment.
    """
    results: list[SimcSlot] = []
    ring_count = 0
    trinket_count = 0

    for row in table_el.find_all("tr")[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        raw_slot = cells[0].get_text(strip=True).lower()

        span = cells[1].find("span", attrs={"data-wowhead": True})
        if span is None:
            continue

        wh_attr = span.get("data-wowhead", "")
        m = re.search(r"item=(\d+)", wh_attr)
        if not m:
            continue
        item_id = int(m.group(1))
        if item_id == 0:
            continue

        slot_key, ring_count, trinket_count = _resolve_text_slot(
            raw_slot, slot_map, ring_count, trinket_count
        )
        if slot_key is None:
            logger.debug("_iv_extract_regular_rows: unrecognised slot %r, skipping", raw_slot)
            continue

        results.append(SimcSlot(
            slot=slot_key,
            blizzard_item_id=item_id,
            bonus_ids=[],
            enchant_id=None,
            gem_ids=[],
            quality_track=None,
        ))

    return results


def _iv_extract_trinket_rows(details_el) -> list[dict]:
    """Parse an IV trinket-dropdown <details> element.

    Returns [{tier, item_id, sort_order}] — one entry per item per tier row.
    tier is the cleaned tier label (S/A/B/C/D).  sort_order is 0-indexed
    within each tier.
    """
    results: list[dict] = []
    tier_sort: dict[str, int] = {}

    for row in details_el.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        raw_tier = cells[0].get_text(strip=True)
        tier = raw_tier.replace(" Tier", "").replace(" tier", "").strip()
        if not tier:
            continue

        for span in cells[1].find_all("span", attrs={"data-wowhead": True}):
            wh_attr = span.get("data-wowhead", "")
            m = re.search(r"item=(\d+)", wh_attr)
            if not m:
                continue
            item_id = int(m.group(1))
            if item_id == 0:
                continue

            sort_order = tier_sort.get(tier, 0)
            tier_sort[tier] = sort_order + 1

            results.append({"tier": tier, "item_id": item_id, "sort_order": sort_order})

    return results


def _iv_is_outlier(section: "IVSection") -> tuple[bool, Optional[str]]:
    """Return (is_outlier, reason) for an IVSection."""
    if section.content_type is None:
        return True, "unrecognised h3 id prefix"
    if section.row_count == 0:
        return True, "no rows extracted"
    if section.row_count < 5:
        return True, f"suspiciously short ({section.row_count} rows)"
    if section.is_trinket_section and not section.trinket_rows:
        return True, "trinket section has no tier labels"
    return False, None


def _iv_classify_tab_label(
    label: str,
    raid_instance_names: frozenset[str] = frozenset(),
) -> Optional[str]:
    """Map an image_block tab button label → content_type.

    Uses keyword matching on the human-readable label text, which is more
    reliable than h3 id strings (which vary wildly across spec pages).

    raid_instance_names — frozenset of current-season raid instance names from
    landing.blizzard_journal_instances.  When provided, labels that contain a
    known raid instance name are classified as 'raid' even if the word "raid"
    doesn't appear (e.g. "Dreamrift, Voidspire, and March on Quel'Danas BiS List").
    The instance name check runs after the "mythic"/"raid" fast paths but before
    the generic "bis"/"overall" fallback so that raid-named sections aren't
    misclassified as overall.
    """
    l = label.lower()
    if "mythic" in l:
        return "mythic_plus"
    if "raid" in l:
        return "raid"
    for name in raid_instance_names:
        if name.lower() in l:
            return "raid"
    if "overall" in l or "bis" in l or "best" in l:
        return "overall"
    return None


def _iv_parse_from_image_blocks(
    soup,
    slot_map: dict[str, str | None],
    raid_instance_names: frozenset[str] = frozenset(),
) -> list["IVSection"]:
    """Parse IV BIS sections from image_block tab structure.

    IV pages wrap each BIS table in a div.image_block with tab buttons
    (div.image_block_header_buttons) and tab content panes
    (div.image_block_content[id="area_N"]).  Button labels provide reliable
    content_type classification.  The h3 id inside each pane is used as the
    section identifier when present for backward compatibility; otherwise the
    area_N id is used.

    raid_instance_names — passed through to _iv_classify_tab_label for
    season-specific raid wing name detection.
    """
    sections: list[IVSection] = []

    for image_block in soup.find_all("div", class_="image_block"):
        buttons_div = image_block.find("div", class_="image_block_header_buttons")
        if not buttons_div:
            continue

        # Build area_id → (content_type, label) from button spans.
        # Unclassified tabs (ct=None) are kept so they appear in Section Inventory
        # as outliers and can be targeted by section overrides.  The block is
        # skipped only when *no* tab is classifiable — that guards against
        # non-BIS image_blocks (talent trees, skill grids, etc.).
        area_map: dict[str, tuple[Optional[str], str]] = {}
        has_classified = False
        for span in buttons_div.find_all(
            "span", id=lambda x: x and x.endswith("_button")
        ):
            area_id = span["id"][:-7]  # strip "_button"
            label = span.get_text(strip=True)
            ct = _iv_classify_tab_label(label, raid_instance_names)
            area_map[area_id] = (ct, label)
            if ct:
                has_classified = True

        if not has_classified:
            continue

        for content_div in image_block.find_all("div", class_="image_block_content"):
            area_id = content_div.get("id", "")
            if area_id not in area_map:
                continue
            ct, label = area_map[area_id]

            # Prefer h3 id as section identifier (backward compat with existing rows)
            h3 = content_div.find("h3")
            h3_id = h3.get("id", "") if h3 else area_id
            section_title = h3.get_text(strip=True) if h3 else label

            table = content_div.find("table")
            details = content_div.find("details", class_="trinket-dropdown")

            if details:
                is_trinket = True
                trinket_rows = _iv_extract_trinket_rows(details)
                row_count = len(trinket_rows)
                slots: list[SimcSlot] = []
            elif table:
                is_trinket = False
                slots = _iv_extract_regular_rows(table, slot_map)
                trinket_rows = []
                row_count = len(slots)
            else:
                continue

            section = IVSection(
                h3_id=h3_id,
                section_title=section_title,
                content_type=ct,
                is_trinket_section=is_trinket,
                row_count=row_count,
                slots=slots,
                trinket_rows=trinket_rows,
                is_outlier=False,
                outlier_reason=None,
            )
            is_out, reason = _iv_is_outlier(section)
            section.is_outlier = is_out
            section.outlier_reason = reason
            sections.append(section)

    return sections


def _iv_parse_sections(
    html: str,
    slot_map: dict[str, str | None],
    raid_instance_names: frozenset[str] = frozenset(),
) -> list["IVSection"]:
    """Parse IV BIS page HTML into a list of IVSection objects.

    Primary path: image_block tab structure (universal across IV spec pages).
    Fallback path: heading_container divs wrapping h3 tags (used for tests
    and any edge-case pages that lack the image_block wrapper).

    raid_instance_names — passed through to the tab classifier for season-specific
    raid wing name detection (Phase 5).
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("_iv_parse_sections: BeautifulSoup not available")
        return []

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # PRIMARY: image_block tab structure
    sections = _iv_parse_from_image_blocks(soup, slot_map, raid_instance_names)
    if sections:
        return sections

    # FALLBACK: heading_container / sibling-table approach
    sections = []
    for container in soup.find_all("div", class_="heading_container"):
        h3 = container.find("h3")
        if not h3:
            continue

        h3_id = h3.get("id", "")
        section_title = h3.get_text(strip=True)
        content_type, _ = _iv_classify_section(h3_id)

        sibling = container.find_next_sibling()
        while sibling and sibling.name not in ("table", "details"):
            sibling = sibling.find_next_sibling()

        if sibling is None:
            continue

        is_trinket = (
            sibling.name == "details"
            and "trinket-dropdown" in (sibling.get("class") or [])
        )

        if is_trinket:
            trinket_rows = _iv_extract_trinket_rows(sibling)
            row_count = len(trinket_rows)
            slots: list[SimcSlot] = []
        else:
            slots = _iv_extract_regular_rows(sibling, slot_map)
            trinket_rows = []
            row_count = len(slots)

        section = IVSection(
            h3_id=h3_id,
            section_title=section_title,
            content_type=content_type,
            is_trinket_section=is_trinket,
            row_count=row_count,
            slots=slots,
            trinket_rows=trinket_rows,
            is_outlier=False,
            outlier_reason=None,
        )
        is_outlier, reason = _iv_is_outlier(section)
        section.is_outlier = is_outlier
        section.outlier_reason = reason

        sections.append(section)

    return sections


def _iv_parse_bis_from_raw(
    html: str,
    content_type: str,
    slot_map: dict[str, str | None],
    raid_instance_names: frozenset[str] = frozenset(),
) -> list[SimcSlot]:
    """Parse IV BIS slots from stored raw HTML for a specific content_type.

    Pure function — no DB access.  Override support requires _resolve_iv_section.
    Returns the first non-outlier, non-trinket section matching content_type,
    or an empty list if none found.
    """
    sections = _iv_parse_sections(html, slot_map, raid_instance_names)
    for section in sections:
        if (
            section.content_type == content_type
            and not section.is_trinket_section
            and not section.is_outlier
        ):
            return section.slots
    return []


def _iv_parse_trinkets_from_raw(html: str) -> list[dict]:
    """Extract all trinket tier rows from IV raw HTML.

    Finds every <details class="trinket-dropdown"> element and collects
    [{tier, item_id, sort_order}] dicts.  Pure function — no DB access.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for details in soup.find_all("details", class_="trinket-dropdown"):
        results.extend(_iv_extract_trinket_rows(details))
    return results


async def _upsert_iv_sections(
    conn: asyncpg.Connection,
    spec_id: int,
    source_id: int,
    page_url: str,
    sections: list["IVSection"],
) -> None:
    """Upsert section metadata to landing.bis_page_sections."""
    now = datetime.now(timezone.utc)
    for s in sections:
        await conn.execute(
            """
            INSERT INTO landing.bis_page_sections
                (spec_id, source_id, page_url, section_key, section_title,
                 sort_order, content_type, is_trinket_section, row_count,
                 is_outlier, outlier_reason, scraped_at)
            VALUES ($1, $2, $3, $4, $5, NULL, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (spec_id, source_id, section_key) DO UPDATE SET
                page_url           = EXCLUDED.page_url,
                section_title      = EXCLUDED.section_title,
                content_type       = EXCLUDED.content_type,
                is_trinket_section = EXCLUDED.is_trinket_section,
                row_count          = EXCLUDED.row_count,
                is_outlier         = EXCLUDED.is_outlier,
                outlier_reason     = EXCLUDED.outlier_reason,
                scraped_at         = EXCLUDED.scraped_at
            """,
            spec_id, source_id, page_url, s.h3_id, s.section_title,
            s.content_type, s.is_trinket_section, s.row_count,
            s.is_outlier, s.outlier_reason, now,
        )


async def _resolve_iv_section(
    pool: asyncpg.Pool,
    sections: list["IVSection"],
    spec_id: int,
    source_id: int,
    content_type: str,
) -> list[SimcSlot]:
    """Resolve which IV section to use for content_type, consulting DB overrides.

    If an override row exists in config.bis_section_overrides for this
    (spec_id, source_id, content_type), use the section matching that section_key.
    Otherwise falls back to the first non-outlier, non-trinket section with
    matching content_type (auto-classification).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT section_key
              FROM config.bis_section_overrides
             WHERE spec_id = $1 AND source_id = $2 AND content_type = $3
            """,
            spec_id, source_id, content_type,
        )
    if row:
        target_key = row["section_key"]
        for s in sections:
            if s.h3_id == target_key and not s.is_trinket_section:
                return s.slots
        logger.warning(
            "_resolve_iv_section: override key %r not found in sections for spec %d source %d / %s",
            target_key, spec_id, source_id, content_type,
        )
        return []
    for section in sections:
        if section.content_type == content_type and not section.is_trinket_section and not section.is_outlier:
            return section.slots
    return []


async def _fetch_section_items(
    conn: asyncpg.Connection,
    spec_id: int,
    source_id: int,
    origin: str,
    section_key: str,
    slot_map: dict,
    raid_instance_names: frozenset[str] = frozenset(),
) -> list[SimcSlot]:
    """Return SimcSlot list for a named section, fetching raw HTML from landing.

    Used by the merge pass in rebuild_bis_from_landing() to resolve both the
    primary and secondary section for a merge override row.  The function reads
    the latest bis_scrape_raw entry for the given spec/source, re-parses it, and
    returns the slots for the section identified by section_key.

    For 'icy_veins':  section_key matches IVSection.h3_id (e.g. "area_1").
    For 'method':     section_key matches MethodSection.heading (e.g. "Overall").
    """
    if origin == "icy_veins":
        raw_row = await conn.fetchrow(
            """
            SELECT bsr.content
              FROM landing.bis_scrape_raw bsr
              JOIN config.bis_scrape_targets t ON t.id = bsr.target_id
             WHERE t.spec_id = $1 AND t.source_id = $2
               AND bsr.content IS NOT NULL
             ORDER BY bsr.fetched_at DESC
             LIMIT 1
            """,
            spec_id, source_id,
        )
        if not raw_row:
            logger.warning(
                "_fetch_section_items: no IV raw HTML for spec %d source %d",
                spec_id, source_id,
            )
            return []
        sections = _iv_parse_sections(raw_row["content"], slot_map, raid_instance_names)
        for s in sections:
            if s.h3_id == section_key and not s.is_trinket_section:
                return s.slots

    elif origin == "method":
        raw_row = await conn.fetchrow(
            """
            SELECT bsr.content
              FROM landing.bis_scrape_raw bsr
              JOIN config.bis_scrape_targets t ON t.id = bsr.target_id
              JOIN ref.bis_list_sources s ON s.id = t.source_id
             WHERE s.origin = 'method' AND t.spec_id = $1
               AND bsr.content IS NOT NULL
             ORDER BY bsr.fetched_at DESC
             LIMIT 1
            """,
            spec_id,
        )
        if not raw_row:
            logger.warning(
                "_fetch_section_items: no Method raw HTML for spec %d", spec_id,
            )
            return []
        sections = _extract_method_sections(raw_row["content"], slot_map)
        for s in sections:
            if s.heading == section_key:
                return s.slots

    logger.warning(
        "_fetch_section_items: section_key %r not found for spec %d source %d origin %s",
        section_key, spec_id, source_id, origin,
    )
    return []


async def merge_bis_sections(
    ctx: BisInsertionContext,
    primary_items: list[SimcSlot],
    secondary_items: list[SimcSlot],
    override_row: dict,
) -> dict:
    """Merge primary + secondary guide sections into enrichment.bis_entries.

    Calls insert_bis_items() for primary items with primary_note, then walks
    secondary items and for each:
      - Already present in the same slot family for this target → stamp match_note
        on the existing entry (if match_note is not None); count as skipped.
      - Not present → INSERT at the next available guide_order with secondary_note.

    Paired slots (ring_1/ring_2, trinket_1/trinket_2) are treated as a family —
    a secondary ring item is considered "present" if it appears in either ring slot.

    Returns {"inserted": N, "skipped": N} summing both primary and secondary passes.
    """
    primary_note = override_row.get("primary_note")
    match_note = override_row.get("match_note")
    secondary_note = override_row.get("secondary_note")

    primary_result = await insert_bis_items(ctx, primary_items, note=primary_note)

    if not secondary_items:
        return primary_result

    sec_inserted = 0
    sec_skipped = 0

    async with ctx.pool.acquire() as conn:
        for slot_data in secondary_items:
            # Resolve main_hand → main_hand_1h / main_hand_2h
            if slot_data.slot == "main_hand":
                resolved = await _resolve_weapon_slot(conn, slot_data.blizzard_item_id)
                if resolved is None:
                    sec_skipped += 1
                    continue
                actual_slot = resolved
            else:
                actual_slot = slot_data.slot

            # FK gate — item must exist in enrichment.items
            exists = await conn.fetchval(
                "SELECT 1 FROM enrichment.items WHERE blizzard_item_id = $1",
                slot_data.blizzard_item_id,
            )
            if not exists:
                sec_skipped += 1
                continue

            # Check whether item is already present for this target.
            # Ring/trinket use a prefix LIKE so both paired slots are covered.
            base = actual_slot.split("_")[0]
            if base in ("ring", "trinket"):
                existing = await conn.fetchrow(
                    """
                    SELECT slot FROM enrichment.bis_entries
                     WHERE source_id = $1 AND spec_id = $2
                       AND hero_talent_id IS NOT DISTINCT FROM $3
                       AND blizzard_item_id = $4
                       AND slot LIKE $5
                    """,
                    ctx.source_id, ctx.spec_id, ctx.hero_talent_id,
                    slot_data.blizzard_item_id, base + "%",
                )
            else:
                existing = await conn.fetchrow(
                    """
                    SELECT slot FROM enrichment.bis_entries
                     WHERE source_id = $1 AND spec_id = $2
                       AND hero_talent_id IS NOT DISTINCT FROM $3
                       AND blizzard_item_id = $4
                       AND slot = $5
                    """,
                    ctx.source_id, ctx.spec_id, ctx.hero_talent_id,
                    slot_data.blizzard_item_id, actual_slot,
                )

            if existing:
                # Item already present — always stamp match_note (clears primary_note when None)
                await conn.execute(
                    """
                    UPDATE enrichment.bis_entries
                       SET bis_note = $1
                     WHERE source_id = $2 AND spec_id = $3
                       AND hero_talent_id IS NOT DISTINCT FROM $4
                       AND blizzard_item_id = $5
                       AND slot = $6
                    """,
                    match_note, ctx.source_id, ctx.spec_id, ctx.hero_talent_id,
                    slot_data.blizzard_item_id, existing["slot"],
                )
                sec_skipped += 1
            else:
                # Item is new — find next guide_order and insert
                if base in ("ring", "trinket"):
                    max_order = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(guide_order), 0)
                          FROM enrichment.bis_entries
                         WHERE source_id = $1 AND spec_id = $2
                           AND hero_talent_id IS NOT DISTINCT FROM $3
                           AND slot LIKE $4
                        """,
                        ctx.source_id, ctx.spec_id, ctx.hero_talent_id, base + "%",
                    )
                else:
                    max_order = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(guide_order), 0)
                          FROM enrichment.bis_entries
                         WHERE source_id = $1 AND spec_id = $2
                           AND hero_talent_id IS NOT DISTINCT FROM $3
                           AND slot = $4
                        """,
                        ctx.source_id, ctx.spec_id, ctx.hero_talent_id, actual_slot,
                    )
                try:
                    await conn.execute(
                        """
                        INSERT INTO enrichment.bis_entries
                            (source_id, spec_id, hero_talent_id, slot,
                             blizzard_item_id, guide_order, bis_note)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        ctx.source_id, ctx.spec_id, ctx.hero_talent_id,
                        actual_slot, slot_data.blizzard_item_id,
                        (max_order or 0) + 1, secondary_note,
                    )
                    sec_inserted += 1
                except Exception:
                    sec_skipped += 1

    return {
        "inserted": primary_result["inserted"] + sec_inserted,
        "skipped": primary_result["skipped"] + sec_skipped,
    }


async def _extract_icy_veins(
    url: str,
    content_type: str = "overall",
    spec_id: int = 0,
    source_id: int = 0,
    pool: Optional[asyncpg.Pool] = None,
) -> tuple[list[SimcSlot], Optional[str]]:
    """Fetch one IV BIS page, extract all sections, upsert metadata to
    landing.bis_page_sections, store raw HTML in landing.bis_scrape_raw.

    Returns (slots_for_this_content_type, raw_html).
    """
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_HTTP_TIMEOUT, headers=_HEADERS
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    if pool and spec_id and source_id:
        async with pool.acquire() as conn:
            slot_map = await _load_slot_labels(conn)
            raid_instance_names = await _load_raid_instance_names(conn)
            sections = _iv_parse_sections(html, slot_map, raid_instance_names)
            await _upsert_iv_sections(conn, spec_id, source_id, url, sections)
        return await _resolve_iv_section(pool, sections, spec_id, source_id, content_type), html

    slot_map: dict[str, str | None] = {}
    sections = _iv_parse_sections(html, slot_map)
    for section in sections:
        if section.content_type == content_type and not section.is_trinket_section and not section.is_outlier:
            return section.slots, html
    return [], html


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
                        (source_id, spec_id, hero_talent_id, slot, blizzard_item_id, guide_order)
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
                   t.content_type, t.id AS target_id,
                   (SELECT MAX(r.source_updated_at)
                      FROM landing.bis_scrape_raw r
                     WHERE r.target_id = t.id) AS source_updated_at
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
            "source_updated_at": t["source_updated_at"].isoformat() if t["source_updated_at"] else None,
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
