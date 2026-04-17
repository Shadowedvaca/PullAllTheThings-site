"""Admin BIS (Best-In-Slot) API routes.

All routes require Officer+ (level 4).  BIS import/sync routes additionally
check for GL rank (level 5) since they modify shared game data.

Endpoints:
  GET  /api/v1/admin/bis/sources
  POST /api/v1/admin/bis/sources
  PUT  /api/v1/admin/bis/sources/{id}
  GET  /api/v1/admin/bis/entries
  POST /api/v1/admin/bis/entries
  DELETE /api/v1/admin/bis/entries/{id}
  GET  /api/v1/admin/bis/targets
  GET  /api/v1/admin/bis/matrix
  POST /api/v1/admin/bis/targets/discover
  PUT  /api/v1/admin/bis/targets/{id}
  POST /api/v1/admin/bis/sync
  POST /api/v1/admin/bis/sync/{source_id}
  POST /api/v1/admin/bis/sync/target/{target_id}
  GET  /api/v1/admin/bis/scrape-log
  GET  /api/v1/admin/bis/cross-reference
  POST /api/v1/admin/bis/import-simc
  GET  /api/v1/admin/bis/item-sources
  POST /api/v1/admin/bis/flag-junk-sources
  POST /api/v1/admin/bis/process-tier-tokens
  DELETE /api/v1/admin/bis/item-sources/{id}
  GET  /api/v1/admin/bis/enrich-items        (poll status)
  POST /api/v1/admin/bis/enrich-items        (start job)
  GET  /api/v1/admin/bis/sync-crafted-items  (poll status)
  POST /api/v1/admin/bis/sync-crafted-items  (start job)
  GET  /api/v1/admin/bis/trinket-ratings-status
  POST /api/v1/admin/bis/rebuild-enrichment
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from guild_portal.deps import get_db, require_rank
from sv_common.db.models import Player

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/bis",
    tags=["admin-bis"],
    dependencies=[Depends(require_rank(4))],  # Officer+ for all routes
)


def _pool(request: Request):
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool unavailable")
    return pool


# ---------------------------------------------------------------------------
# Enrich-items job state (module-level, one job at a time)
# ---------------------------------------------------------------------------

_enrich_status: dict = {
    "running": False,
    "phase": 0,          # 1 = Wowhead pass, 2 = Blizzard icon pass
    "phase_label": "",
    "total": 0,
    "enriched": 0,
    "error_count": 0,
    "started_at": None,
    "finished_at": None,
}

_crafted_sync_status: dict = {
    "running": False,
    "phase_label": "",
    "phase_2a_stubbed": 0,
    "phase_2a_linked": 0,
    "phase_2b_checked": 0,
    "phase_2b_stubbed": 0,
    "phase_2b_linked": 0,
    "phase_2b_errors": 0,
    "started_at": None,
    "finished_at": None,
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SourceUpdate(BaseModel):
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    short_label: Optional[str] = None


class EntryCreate(BaseModel):
    source_id: int
    spec_id: int
    hero_talent_id: Optional[int] = None
    slot: str
    blizzard_item_id: int
    priority: int = 1
    notes: Optional[str] = None


class TargetUpdate(BaseModel):
    url: Optional[str] = None
    preferred_technique: Optional[str] = None
    hero_talent_id: Optional[int] = None
    content_type: Optional[str] = None
    area_label: Optional[str] = None


class SimcImport(BaseModel):
    simc_text: str
    source_id: int
    spec_id: int
    hero_talent_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@router.get("/sources")
async def list_sources(request: Request):
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, short_label, origin, content_type,
                   is_default, is_active, sort_order, last_synced
              FROM ref.bis_list_sources
             ORDER BY sort_order, id
            """
        )
    return {"ok": True, "sources": [dict(r) for r in rows]}


@router.put("/sources/{source_id}")
async def update_source(source_id: int, body: SourceUpdate, request: Request):
    pool = _pool(request)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    values = list(updates.values())
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE ref.bis_list_sources SET {set_clauses} WHERE id = $1",
            source_id, *values,
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


@router.get("/entries")
async def list_entries(
    request: Request,
    source_id: Optional[int] = None,
    spec_id: Optional[int] = None,
    hero_talent_id: Optional[int] = None,
):
    pool = _pool(request)
    conditions = []
    args: list = []

    if source_id is not None:
        args.append(source_id)
        conditions.append(f"e.source_id = ${len(args)}")
    if spec_id is not None:
        args.append(spec_id)
        conditions.append(f"e.spec_id = ${len(args)}")
    if hero_talent_id is not None:
        args.append(hero_talent_id)
        conditions.append(f"(e.hero_talent_id = ${len(args)} OR e.hero_talent_id IS NULL)")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT e.id, e.source_id, e.spec_id, e.hero_talent_id, e.slot,
                   e.blizzard_item_id, e.priority,
                   i.name AS item_name, i.icon_url
              FROM enrichment.bis_entries e
              LEFT JOIN enrichment.items i ON i.blizzard_item_id = e.blizzard_item_id
             {where}
             ORDER BY e.slot, e.priority
            """,
            *args,
        )
    return {"ok": True, "entries": [dict(r) for r in rows]}


@router.post("/entries")
async def create_entry(body: EntryCreate, request: Request):
    """Add or update a single BIS entry (manual override)."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        # Upsert into enrichment.bis_entries — hero_talent_id NULL handled via DELETE+INSERT
        # because PostgreSQL UNIQUE treats NULLs as distinct (no ON CONFLICT match).
        await conn.execute(
            """
            DELETE FROM enrichment.bis_entries
             WHERE source_id = $1
               AND spec_id = $2
               AND slot = $3
               AND blizzard_item_id = $4
               AND (
                   ($5::int IS NULL AND hero_talent_id IS NULL)
                   OR hero_talent_id = $5
               )
            """,
            body.source_id, body.spec_id, body.slot, body.blizzard_item_id, body.hero_talent_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO enrichment.bis_entries
                (source_id, spec_id, hero_talent_id, slot, blizzard_item_id, priority)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            body.source_id, body.spec_id, body.hero_talent_id,
            body.slot, body.blizzard_item_id, body.priority,
        )
    return {"ok": True, "id": row["id"]}


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: int, request: Request):
    pool = _pool(request)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM enrichment.bis_entries WHERE id = $1",
            entry_id,
        )
    deleted = result.split()[-1] if result else "0"
    return {"ok": True, "deleted": int(deleted)}


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


@router.get("/targets")
async def list_targets(
    request: Request,
    source_id: Optional[int] = None,
    spec_id: Optional[int] = None,
):
    pool = _pool(request)
    conditions = ["1=1"]
    args: list = []

    if source_id is not None:
        args.append(source_id)
        conditions.append(f"t.source_id = ${len(args)}")
    if spec_id is not None:
        args.append(spec_id)
        conditions.append(f"t.spec_id = ${len(args)}")

    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT t.id, t.source_id, t.spec_id, t.hero_talent_id,
                   t.content_type, t.url, t.area_label, t.preferred_technique,
                   t.status, t.items_found, t.last_fetched,
                   s.name AS source_name, s.origin,
                   ht.name AS hero_talent_name, ht.slug AS hero_talent_slug,
                   sp.name AS spec_name, c.name AS class_name
              FROM config.bis_scrape_targets t
              JOIN ref.bis_list_sources s ON s.id = t.source_id
              JOIN ref.specializations sp ON sp.id = t.spec_id
              JOIN ref.classes c ON c.id = sp.class_id
              LEFT JOIN ref.hero_talents ht ON ht.id = t.hero_talent_id
             WHERE {where}
             ORDER BY c.name, sp.name, s.sort_order
            """,
            *args,
        )
    return {"ok": True, "targets": [dict(r) for r in rows]}


@router.get("/matrix")
async def get_matrix(request: Request):
    """Return spec × source status matrix for the admin BIS dashboard."""
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import get_matrix as _get_matrix
    matrix = await _get_matrix(pool)
    # Serialise datetimes
    return {"ok": True, **matrix}


@router.post("/targets/discover")
async def discover_targets(request: Request, player: Player = Depends(require_rank(5))):
    """Generate scrape targets for all sources (Archon, Wowhead, Icy Veins).

    All targets are built synchronously from URL patterns — no background tasks.
    IV targets use a role-derived base URL (one per spec per IV source).
    """
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import discover_targets as _discover
    stats = await _discover(pool)
    return {"ok": True, **stats}


@router.put("/targets/{target_id}")
async def update_target(target_id: int, body: TargetUpdate, request: Request):
    pool = _pool(request)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    values = list(updates.values())
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE config.bis_scrape_targets SET {set_clauses} WHERE id = $1",
            target_id, *values,
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sync (GL-only)
# ---------------------------------------------------------------------------


@router.post("/sync")
async def sync_all(request: Request, player: Player = Depends(require_rank(5))):
    """Trigger full BIS pipeline across all active non-IV sources (GL only).

    Runs in background — prefer the frontend per-spec loop for live progress.
    """
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import sync_all as _sync_all
    import asyncio
    asyncio.create_task(_sync_all(pool))
    return {"ok": True, "message": "Full BIS sync started in background"}


@router.post("/sync/spec/{spec_id}")
async def sync_spec(
    spec_id: int, request: Request, player: Player = Depends(require_rank(5))
):
    """Sync all active non-IV targets for one spec (synchronous, GL only).

    Returns immediately with results so the frontend can drive per-spec
    progress updates without long-lived HTTP connections or polling.
    """
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import sync_spec as _sync_spec
    result = await _sync_spec(pool, spec_id)
    return {"ok": True, **result}


@router.post("/sync-gaps")
async def sync_gaps(request: Request, player: Player = Depends(require_rank(5))):
    """Sync only BIS targets missing from or stale in landing.bis_scrape_raw (GL only).

    A target is eligible if it has no raw row or its last fetch is older than
    7 days.  Useful for keeping the landing layer fresh without a full re-scrape.
    Runs synchronously and returns when complete.
    """
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import sync_gaps as _sync_gaps
    result = await _sync_gaps(pool)
    return {"ok": True, **result}


@router.post("/sync/{source_id}")
async def sync_source(
    source_id: int, request: Request, player: Player = Depends(require_rank(5))
):
    """Sync one source for all specs, spec by spec (synchronous, GL only).

    Skips IV sources. Returns when complete.
    """
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import sync_source as _sync_source
    result = await _sync_source(pool, source_id)
    return {"ok": True, **result}


@router.post("/sync/target/{target_id}")
async def sync_target(
    target_id: int, request: Request, player: Player = Depends(require_rank(5))
):
    """Re-sync a single scrape target (GL only)."""
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import sync_target as _sync_target
    result = await _sync_target(pool, target_id)
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Scrape log
# ---------------------------------------------------------------------------


@router.get("/scrape-log")
async def get_scrape_log(
    request: Request,
    target_id: Optional[int] = None,
    limit: int = 50,
):
    pool = _pool(request)
    conditions = ["1=1"]
    args: list = []

    if target_id is not None:
        args.append(target_id)
        conditions.append(f"l.target_id = ${len(args)}")

    args.append(limit)
    where = " AND ".join(conditions)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT l.id, l.target_id, l.technique, l.status,
                   l.items_found, l.error_message, l.created_at,
                   t.spec_id, t.source_id, t.content_type,
                   sp.name AS spec_name, c.name AS class_name,
                   s.name AS source_name
              FROM log.bis_scrape_log l
              JOIN config.bis_scrape_targets t ON t.id = l.target_id
              JOIN ref.bis_list_sources s ON s.id = t.source_id
              JOIN ref.specializations sp ON sp.id = t.spec_id
              JOIN ref.classes c ON c.id = sp.class_id
             WHERE {where}
             ORDER BY l.created_at DESC
             LIMIT ${len(args)}
            """,
            *args,
        )
    return {"ok": True, "log": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Cross-reference
# ---------------------------------------------------------------------------


@router.get("/cross-reference")
async def cross_reference(
    request: Request,
    spec_id: int,
    hero_talent_id: Optional[int] = None,
):
    """Compare BIS recommendations across all sources for one spec + hero talent."""
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import cross_reference as _xref
    result = await _xref(pool, spec_id, hero_talent_id)
    return {"ok": True, "by_slot": result}


# ---------------------------------------------------------------------------
# SimC import
# ---------------------------------------------------------------------------


@router.post("/import-simc")
async def import_simc(
    body: SimcImport, request: Request, player: Player = Depends(require_rank(5))
):
    """Import a SimC BIS profile as enrichment.bis_entries for a spec (GL only)."""
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import import_simc as _import
    result = await _import(
        pool,
        body.simc_text,
        body.source_id,
        body.spec_id,
        body.hero_talent_id,
    )
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Item Sources (Journal API loot tables)
# ---------------------------------------------------------------------------


async def _get_blizzard_client(request: Request):
    """Return a BlizzardClient — scheduler's shared instance if available,
    otherwise a temporary one built from env vars / site_config.

    The temporary path supports admin operations (like Journal API sync) even
    when the scheduler hasn't started (e.g. audit channel not yet configured).
    """
    import os
    from sv_common.guild_sync.blizzard_client import BlizzardClient
    from sv_common.config_cache import get_site_config

    # Prefer the already-initialised scheduler client (no extra token fetch).
    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    if scheduler is not None:
        client = getattr(scheduler, "blizzard_client", None)
        if client is not None:
            return client

    # Fallback: build a temporary client from env vars / site_config.
    cfg = get_site_config() or {}
    client_id = os.environ.get("BLIZZARD_CLIENT_ID") or cfg.get("blizzard_client_id") or ""
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET", "")
    if not client_secret and cfg.get("blizzard_client_secret_encrypted"):
        from sv_common.crypto import decrypt_secret
        import os as _os
        jwt_secret = _os.environ.get("JWT_SECRET_KEY", "")
        try:
            client_secret = decrypt_secret(cfg["blizzard_client_secret_encrypted"], jwt_secret)
        except Exception:
            pass

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Blizzard API credentials not configured — set them in Admin → Site Config",
        )

    realm_slug = cfg.get("home_realm_slug") or os.environ.get("GUILD_REALM_SLUG", "senjin")
    guild_slug = cfg.get("guild_name_slug") or os.environ.get("GUILD_NAME_SLUG", "pull-all-the-things")

    client = BlizzardClient(
        client_id=client_id,
        client_secret=client_secret,
        realm_slug=realm_slug,
        guild_slug=guild_slug,
    )
    await client.initialize()
    return client


@router.post("/sync-item-sources")
async def sync_item_sources(
    request: Request,
    expansion_id: Optional[int] = None,
    player: Player = Depends(require_rank(5)),
):
    """Trigger Journal API item source sync for the current (or given) expansion (GL only).

    Populates guild_identity.item_sources with boss/dungeon loot tables.
    Quality tracks: raid boss → V/C/H/M, dungeon → C/H/M (Midnight S1).
    """
    pool = _pool(request)
    client = await _get_blizzard_client(request)
    from sv_common.guild_sync.item_source_sync import sync_item_sources as _sync
    from guild_portal.services.item_service import enrich_unenriched_items
    from sv_common.guild_sync.item_recipe_link_sync import build_item_recipe_links
    result = await _sync(pool, client, expansion_id=expansion_id)
    enriched, enrich_errors = await enrich_unenriched_items(pool)
    result["items_enriched"] = enriched
    result.setdefault("errors", []).extend(enrich_errors)
    link_stats = await build_item_recipe_links(pool)
    result["recipe_links_linked"] = link_stats["linked"]
    result["recipe_links_updated"] = link_stats["updated"]
    result["recipe_links_skipped"] = link_stats["skipped"]
    return {"ok": True, **result}


@router.get("/sync-crafted-items")
async def sync_crafted_items_status():
    """Poll the current sync-crafted-items job state."""
    s = _crafted_sync_status.copy()
    s["started_at"] = s["started_at"].isoformat() if s["started_at"] else None
    s["finished_at"] = s["finished_at"].isoformat() if s["finished_at"] else None
    return {"ok": True, **s}


@router.post("/sync-crafted-items")
async def sync_crafted_items(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """Discover craftable gear items and populate item_recipe_links.

    Runs two phases:
      2a — character_equipment name match (pure DB, instant)
      2b — Blizzard Item Search API (~1–3 min for a full expansion)

    Returns immediately; poll GET /sync-crafted-items for live progress.
    Run Enrich Items (Step 2) afterwards.
    """
    import asyncio
    global _crafted_sync_status
    from sv_common.guild_sync.item_recipe_link_sync import discover_and_link_crafted_items

    if _crafted_sync_status["running"]:
        return {"ok": False, "error": "Crafted item sync already in progress — check status."}

    pool   = _pool(request)
    client = await _get_blizzard_client(request)

    _crafted_sync_status.update({
        "running": True,
        "phase_label": "Equipment match",
        "phase_2a_stubbed": 0,
        "phase_2a_linked": 0,
        "phase_2b_checked": 0,
        "phase_2b_stubbed": 0,
        "phase_2b_linked": 0,
        "phase_2b_errors": 0,
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
    })

    async def _run():
        global _crafted_sync_status
        try:
            _crafted_sync_status["phase_label"] = "Equipment match"
            stats = await discover_and_link_crafted_items(pool, client)
            _crafted_sync_status.update({
                "running": False,
                "phase_label": "Done",
                "phase_2a_stubbed": stats.get("phase_2a_stubbed", 0),
                "phase_2a_linked": stats.get("phase_2a_linked", 0),
                "phase_2b_checked": stats.get("phase_2b_checked", 0),
                "phase_2b_stubbed": stats.get("phase_2b_stubbed", 0),
                "phase_2b_linked": stats.get("phase_2b_linked", 0),
                "phase_2b_errors": stats.get("phase_2b_errors", 0),
                "finished_at": datetime.now(timezone.utc),
            })
            logger.info("Crafted item discovery complete: %s", stats)
        except Exception as exc:
            _crafted_sync_status.update({
                "running": False,
                "phase_label": "Error",
                "finished_at": datetime.now(timezone.utc),
            })
            logger.error("Crafted item discovery background task failed: %s", exc, exc_info=True)

    asyncio.create_task(_run())
    return {"ok": True, "started": True}


@router.post("/sync-legacy-dungeons")
async def sync_legacy_dungeons(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """Fire-and-forget sync of dungeon instances from all prior expansions (GL only).

    Returns immediately — the sync runs in the background (takes several
    minutes).  Progress is logged server-side; refresh Item Sources when done.

    Raids and world bosses from prior expansions are intentionally skipped —
    they don't drop current-season loot.
    """
    import asyncio
    from sv_common.guild_sync.item_source_sync import sync_legacy_expansion_dungeons
    from guild_portal.services.item_service import enrich_unenriched_items
    from sv_common.guild_sync.item_recipe_link_sync import build_item_recipe_links

    pool = _pool(request)
    client = await _get_blizzard_client(request)

    async def _run_in_background():
        try:
            result = await sync_legacy_expansion_dungeons(pool, client)
            enriched, enrich_errors = await enrich_unenriched_items(pool)
            link_stats = await build_item_recipe_links(pool)
            logger.info(
                "Legacy dungeon sync complete — %d expansions, %d dungeons, "
                "%d encounters, %d items, %d enriched, %d links. Errors: %s",
                result.get("expansions_checked", 0),
                result.get("instances_synced", 0),
                result.get("encounters_synced", 0),
                result.get("items_upserted", 0),
                enriched,
                link_stats.get("linked", 0),
                result.get("errors", []) + enrich_errors,
            )
        except Exception as exc:
            logger.error("Legacy dungeon sync background task failed: %s", exc, exc_info=True)

    asyncio.create_task(_run_in_background())
    return {
        "ok": True,
        "message": (
            "Legacy dungeon sync started in background. "
            "This takes several minutes — watch server logs for progress. "
            "Refresh Item Sources when done."
        ),
    }


@router.get("/item-sources")
async def list_item_sources(
    request: Request,
    instance_name: Optional[str] = None,
    instance_id: Optional[int] = None,
    instance_type: Optional[str] = None,
    show_junk: bool = False,
    limit: int = 500,
):
    """List item→source mappings, optionally filtered by instance or type.

    Junk rows are hidden by default; pass show_junk=true to reveal them.
    Always returns junk_hidden_count so the UI can display "N junk hidden".
    """
    pool = _pool(request)
    from sv_common.guild_sync.item_source_sync import get_item_sources, get_instance_names
    sources = await get_item_sources(
        pool,
        instance_name=instance_name,
        instance_id=instance_id,
        instance_type=instance_type,
        show_junk=show_junk,
        limit=limit,
    )
    instances = await get_instance_names(pool, show_junk=show_junk)

    # Always surface junk count so the frontend can show "N junk hidden"
    junk_hidden_count = 0
    if not show_junk:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM guild_identity.item_sources WHERE is_suspected_junk = TRUE"
            )
            junk_hidden_count = int(row["cnt"])

    return {
        "ok": True,
        "sources": sources,
        "instances": instances,
        "junk_hidden_count": junk_hidden_count,
    }


@router.get("/enrich-items")
async def enrich_items_status():
    """Poll the current enrich-items job state."""
    s = _enrich_status.copy()
    s["started_at"] = s["started_at"].isoformat() if s["started_at"] else None
    s["finished_at"] = s["finished_at"].isoformat() if s["finished_at"] else None
    return {"ok": True, **s}


@router.post("/enrich-items")
async def enrich_items(
    request: Request, player: Player = Depends(require_rank(5))
):
    """Fetch Wowhead tooltips for unenriched wow_items rows (GL only).

    Processes all rows where slot_type='other' using concurrent workers.
    Returns immediately; poll GET /enrich-items for live progress.
    Safe to re-run — already-enriched rows are skipped.
    """
    import asyncio
    global _enrich_status

    if _enrich_status["running"]:
        return {"ok": False, "error": "Enrichment already in progress — check status."}

    pool = _pool(request)
    from guild_portal.services.item_service import enrich_unenriched_items

    # Count pending items for Phase 1 so the UI can show a denominator immediately
    async with pool.acquire() as conn:
        p1_total = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.wow_items WHERE slot_type = 'other'"
        )

    _enrich_status = {
        "running": True,
        "phase": 1,
        "phase_label": "Wowhead",
        "total": p1_total,
        "enriched": 0,
        "error_count": 0,
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
    }

    def _progress(enriched: int, error_count: int) -> None:
        _enrich_status["enriched"] = enriched
        _enrich_status["error_count"] = error_count

    async def _run():
        from guild_portal.services.item_service import enrich_null_icons, enrich_blizzard_metadata
        from sv_common.guild_sync.item_recipe_link_sync import build_item_recipe_links
        try:
            blizzard_client = await _get_blizzard_client(request)

            # --- Phase 1: Wowhead tooltip enrichment ---
            wh_enriched, wh_errors = await enrich_unenriched_items(pool, progress_cb=_progress)

            # --- Phase 2: Blizzard icon fallback for items still missing icons ---
            async with pool.acquire() as conn:
                p2_total = await conn.fetchval(
                    "SELECT COUNT(*) FROM guild_identity.wow_items WHERE icon_url IS NULL"
                )
            _enrich_status.update({
                "phase": 2, "phase_label": "Icons",
                "total": p2_total, "enriched": 0, "error_count": 0,
            })
            bl_enriched, bl_errors = await enrich_null_icons(
                pool, blizzard_client, progress_cb=_progress
            )

            # --- Phase 3: Blizzard item metadata for tier-slot BIS items ---
            async with pool.acquire() as conn:
                p3_total = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT wi.blizzard_item_id)
                      FROM guild_identity.wow_items wi
                      JOIN enrichment.bis_entries be ON be.blizzard_item_id = wi.blizzard_item_id
                     WHERE wi.wowhead_tooltip_html IS NULL
                       AND (wi.slot_type IN ('head','shoulder','chest','hands','legs')
                            OR wi.slot_type = 'other' OR wi.armor_type IS NULL)
                    """
                )
            _enrich_status.update({
                "phase": 3, "phase_label": "Metadata",
                "total": p3_total, "enriched": 0, "error_count": 0,
            })
            meta_enriched, meta_errors = await enrich_blizzard_metadata(
                pool, blizzard_client, progress_cb=_progress
            )

            # --- Phase 4: Rebuild item → recipe links for crafted items ---
            _enrich_status.update({
                "phase": 4, "phase_label": "Recipes",
                "total": 0, "enriched": 0, "error_count": 0,
            })
            recipe_stats = await build_item_recipe_links(pool)

            all_errors = len(wh_errors) + len(bl_errors) + len(meta_errors)
            _enrich_status.update({
                "running": False,
                "enriched": recipe_stats.get("linked", 0),
                "error_count": all_errors,
                "finished_at": datetime.now(timezone.utc),
            })
            logger.info(
                "Enrich items complete — Wowhead: %d; Icons: %d; Metadata: %d; "
                "Recipe links: %d linked, %d updated; Errors: %d",
                wh_enriched, bl_enriched, meta_enriched,
                recipe_stats.get("linked", 0), recipe_stats.get("updated", 0),
                all_errors,
            )
        except Exception as exc:
            logger.error("Enrich items background task failed: %s", exc, exc_info=True)
            _enrich_status.update({
                "running": False,
                "finished_at": datetime.now(timezone.utc),
            })

    asyncio.create_task(_run())
    return {"ok": True, "running": True, "total": p1_total}


@router.post("/flag-junk-sources")
async def flag_junk_sources(
    request: Request, player: Player = Depends(require_rank(5))
):
    """Flag suspected-junk rows in item_sources (GL only).

    Marks null-ID world boss rows and tier piece direct-source rows as
    is_suspected_junk = TRUE.  Safe to re-run — clears and re-applies all
    flags each time.
    """
    pool = _pool(request)
    from sv_common.guild_sync.item_source_sync import flag_junk_sources as _flag
    result = await _flag(pool)
    return {"ok": True, **result}


@router.post("/process-tier-tokens")
async def process_tier_tokens(
    request: Request, player: Player = Depends(require_rank(5))
):
    """Parse tier token tooltips, populate tier_token_attrs, and flag junk sources (GL only).

    Detects tier tokens from wow_items tooltips (slot_type='other' + 'Synthesize
    a soulbound set' text), upserts their parsed slot/armor type into
    tier_token_attrs, then runs flag_junk_sources(flag_tier_pieces=True) so
    stale direct-drop rows for tier pieces are suppressed in the gear plan.

    Safe to re-run — rows with is_manual_override=TRUE are never overwritten.
    """
    pool = _pool(request)
    from sv_common.guild_sync.item_source_sync import process_tier_tokens as _process
    result = await _process(pool)
    return {"ok": True, **result}


@router.post("/rebuild-enrichment")
async def rebuild_enrichment(
    request: Request, player: Player = Depends(require_rank(5))
):
    """Rebuild enrichment schema tables from landing data via sp_rebuild_all() (GL only).

    sp_rebuild_all() runs: items → item_sources → item_recipes →
    update_item_categories → item_seasons → flag_junk_sources.

    BIS entries and trinket ratings are NOT rebuilt by this endpoint — use
    Enrich & Classify (POST /enrich-and-classify) which calls the Python
    rebuild_bis_from_landing() and rebuild_trinket_ratings_from_landing() functions.

    Safe to re-run.  Takes a few seconds on a populated database.
    """
    pool = _pool(request)
    async with pool.acquire() as conn:
        await conn.execute("CALL enrichment.sp_rebuild_all()")
        counts = await conn.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM enrichment.items)            AS items,
                (SELECT count(*) FROM enrichment.item_sources)     AS item_sources,
                (SELECT count(*) FROM enrichment.item_recipes)     AS item_recipes,
                (SELECT count(*) FROM enrichment.bis_entries)      AS bis_entries,
                (SELECT count(*) FROM enrichment.trinket_ratings)  AS trinket_ratings,
                (SELECT count(*) FROM enrichment.items
                  WHERE item_category = 'crafted')                 AS crafted,
                (SELECT count(*) FROM enrichment.items
                  WHERE item_category = 'catalyst')                AS catalyst,
                (SELECT count(*) FROM enrichment.items
                  WHERE item_category = 'tier')                    AS tier,
                (SELECT count(*) FROM enrichment.items
                  WHERE item_category = 'raid')                    AS raid,
                (SELECT count(*) FROM enrichment.items
                  WHERE item_category = 'dungeon')                 AS dungeon,
                (SELECT count(*) FROM enrichment.items
                  WHERE item_category = 'world_boss')              AS world_boss,
                (SELECT count(*) FROM enrichment.items
                  WHERE item_category = 'unclassified')            AS unclassified
            """
        )
    return {
        "ok": True,
        "counts": dict(counts),
    }


@router.delete("/item-sources/{source_id}")
async def delete_item_source(
    source_id: int, request: Request, player: Player = Depends(require_rank(5))
):
    """Delete an item source entry (GL only)."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM guild_identity.item_sources WHERE id = $1",
            source_id,
        )
    deleted = result.split()[-1] if result else "0"
    if deleted == "0":
        raise HTTPException(status_code=404, detail="Source entry not found")
    return {"ok": True}


@router.post("/bulk-populate-plans")
async def bulk_populate_plans(
    request: Request, player: Player = Depends(require_rank(5))
):
    """Populate unlocked BIS slots for all in-guild characters (GL only).

    For every in-guild character linked to any player, creates a gear plan if one
    doesn't exist (using the character's active spec) then fills all unlocked slots
    from the Wowhead Overall BIS source.  Returns counts of characters processed
    and total slots populated.
    """
    from guild_portal.services import gear_plan_service as svc

    pool = _pool(request)

    async with pool.acquire() as conn:
        wowhead_src = await conn.fetchrow(
            "SELECT id FROM ref.bis_list_sources WHERE name = 'Wowhead Overall' LIMIT 1"
        )
        wowhead_source_id = wowhead_src["id"] if wowhead_src else None

        rows = await conn.fetch(
            """
            SELECT DISTINCT p.id AS player_id, wc.id AS character_id
              FROM guild_identity.players p
              JOIN guild_identity.player_characters pc ON pc.player_id = p.id
              JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
             WHERE wc.in_guild = TRUE
               AND wc.removed_at IS NULL
             ORDER BY p.id, wc.id
            """
        )

    characters_processed = 0
    slots_populated = 0

    for row in rows:
        player_id = row["player_id"]
        character_id = row["character_id"]

        await svc.get_or_create_plan(pool, player_id, character_id)
        populated = await svc.populate_from_bis(
            pool, player_id, character_id, source_id=wowhead_source_id
        )
        slots_populated += populated
        characters_processed += 1

    return JSONResponse({
        "ok": True,
        "data": {
            "characters_processed": characters_processed,
            "slots_populated": slots_populated,
        },
    })


# ---------------------------------------------------------------------------
# Gear Plan Admin — New pipeline endpoints
# ---------------------------------------------------------------------------

_landing_status: dict = {
    "running": False,
    "phase_label": "Idle",
    "step": 0,
    "total_steps": 3,
    "detail": "",
    "started_at": None,
    "finished_at": None,
    "error": None,
}

_enrich_classify_status: dict = {
    "running": False,
    "phase_label": "Idle",
    "step": 0,
    "total_steps": 2,
    "detail": "",
    "started_at": None,
    "finished_at": None,
    "error": None,
}


@router.get("/landing-status")
async def landing_status():
    """Poll the landing table fill status."""
    s = _landing_status.copy()
    s["started_at"] = s["started_at"].isoformat() if s["started_at"] else None
    s["finished_at"] = s["finished_at"].isoformat() if s["finished_at"] else None
    return {"ok": True, **s}


async def _run_landing_fill(pool, blizzard_client, flush: bool):
    """Store raw Blizzard API payloads in landing tables.

    No guild_identity writes.  No enrichment.  Pure raw data collection.

    flush=True  — TRUNCATE all landing tables first, then fetch everything.
    flush=False — Gap-fill: skip instances/encounters already present;
                  for items, only fetch missing ones or those stale (>30 days).

    Steps:
      1. (flush only) TRUNCATE landing tables
      2. Fetch expansion → instances → encounters; use ON CONFLICT DO NOTHING
         so gap-fill mode adds only new instances/encounters
      3. Fetch item sets (index + each set) → store item IDs for tier pieces
      4. Fetch item records — missing always; stale (>30d) refreshed in catch-up

    Run Enrich & Classify after this completes.
    """
    import json as _json
    from datetime import timedelta

    global _landing_status

    _STALE_DAYS = 30  # re-fetch items older than this in catch-up mode

    try:
        step = 0

        # ── Step 1 (flush only): truncate ──────────────────────────────────────
        if flush:
            step = 1
            _landing_status.update(step=step, phase_label="Truncating landing tables", detail="")
            async with pool.acquire() as conn:
                await conn.execute("""
                    TRUNCATE landing.blizzard_items,
                             landing.blizzard_journal_encounters,
                             landing.blizzard_journal_instances,
                             landing.blizzard_item_sets,
                             landing.blizzard_item_icons,
                             landing.blizzard_appearances,
                             landing.blizzard_item_quality_tracks,
                             landing.wowhead_tooltips
                """)
            logger.info("landing fill: landing tables truncated")

        # ── Step 2: expansion → instances → encounters ─────────────────────────
        # Current expansion: raids + dungeons + world boss
        # Prior expansions: dungeons only (M+ seasons include legacy dungeons)
        step += 1
        _landing_status.update(
            step=step,
            phase_label="Fetching expansion instances and encounters from Blizzard API",
            detail="",
        )

        tiers = await blizzard_client.get_journal_expansion_index()
        if not tiers:
            raise RuntimeError("Could not fetch expansion index from Blizzard API")

        sorted_tiers   = sorted(tiers, key=lambda t: t.get("id", 0))
        current_tier   = sorted_tiers[-1]
        legacy_tiers   = sorted_tiers[:-1]
        expansion_id   = current_tier["id"]
        expansion_name = current_tier.get("name", f"Expansion {expansion_id}")

        exp_data = await blizzard_client.get_journal_expansion(expansion_id)
        if not exp_data:
            raise RuntimeError(f"Could not fetch expansion {expansion_id} from Blizzard API")
        expansion_name = exp_data.get("name", expansion_name)

        instances: list[dict] = []
        for inst in exp_data.get("dungeons", []):
            instances.append({"id": inst["id"], "name": inst.get("name", ""), "type": "dungeon", "exp_id": expansion_id})
        for inst in exp_data.get("raids", []):
            inst_name = inst.get("name", "")
            inst_type = "world_boss" if inst_name == expansion_name else "raid"
            instances.append({"id": inst["id"], "name": inst_name, "type": inst_type, "exp_id": expansion_id})

        for tier in legacy_tiers:
            leg_exp_id = tier["id"]
            leg_exp_data = await blizzard_client.get_journal_expansion(leg_exp_id)
            if not leg_exp_data:
                logger.warning("landing fill: could not fetch legacy expansion %d", leg_exp_id)
                continue
            for inst in leg_exp_data.get("dungeons", []):
                inst_id = inst.get("id")
                if inst_id:
                    instances.append({"id": inst_id, "name": inst.get("name", ""), "type": "dungeon", "exp_id": leg_exp_id})

        if not instances:
            raise RuntimeError("No instances found — Blizzard API may be unavailable")

        all_item_ids:      set[int] = set()
        instances_stored  = 0
        instances_skipped = 0
        encounters_stored  = 0
        encounters_skipped = 0

        for inst in instances:
            inst_id   = inst["id"]
            inst_name = inst["name"]
            inst_type = inst["type"]
            inst_exp  = inst["exp_id"]

            inst_data = await blizzard_client.get_journal_instance(inst_id)
            if not inst_data:
                logger.warning("landing fill: no data for instance %d (%s)", inst_id, inst_name)
                continue

            # ON CONFLICT DO NOTHING — unique constraint on instance_id (migration 0115)
            async with pool.acquire() as conn:
                result = await conn.execute(
                    """
                    INSERT INTO landing.blizzard_journal_instances
                        (instance_id, instance_name, instance_type, expansion_id)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (instance_id) DO NOTHING
                    """,
                    inst_id, inst_name, inst_type, inst_exp,
                )
            if result == "INSERT 0 1":
                instances_stored += 1
            else:
                instances_skipped += 1

            enc_section = inst_data.get("encounters", {})
            if isinstance(enc_section, dict):
                enc_list = enc_section.get("encounters", [])
            else:
                enc_list = enc_section if isinstance(enc_section, list) else []

            for enc_ref in enc_list:
                enc_id = enc_ref.get("id")
                if not enc_id:
                    continue

                try:
                    enc_data = await blizzard_client.get_journal_encounter(enc_id)
                except Exception as enc_exc:
                    logger.warning("landing fill: skipping encounter %d — %s", enc_id, enc_exc)
                    continue
                if not enc_data:
                    logger.warning("landing fill: no data for encounter %d", enc_id)
                    continue

                # ON CONFLICT DO NOTHING — unique constraint on encounter_id (migration 0115)
                async with pool.acquire() as conn:
                    result = await conn.execute(
                        """
                        INSERT INTO landing.blizzard_journal_encounters
                            (encounter_id, instance_id, payload)
                        VALUES ($1, $2, $3::jsonb)
                        ON CONFLICT (encounter_id) DO NOTHING
                        """,
                        enc_id, inst_id, _json.dumps(enc_data),
                    )
                if result == "INSERT 0 1":
                    encounters_stored += 1
                else:
                    encounters_skipped += 1

                for item_entry in enc_data.get("items", []):
                    item_id = (item_entry.get("item") or {}).get("id")
                    if item_id:
                        all_item_ids.add(item_id)

            _landing_status.update(
                detail=(
                    f"{instances_stored} new instances ({instances_skipped} existing), "
                    f"{encounters_stored} new encounters, "
                    f"{len(all_item_ids)} unique items found"
                )
            )

        # Add crafted item IDs from item_recipe_links — not in journal encounter tables.
        async with pool.acquire() as conn:
            crafted_rows = await conn.fetch(
                """
                SELECT DISTINCT irl.blizzard_item_id
                  FROM guild_identity.item_recipe_links irl
                 WHERE irl.blizzard_item_id IS NOT NULL
                """
            )
        crafted_ids = {r["blizzard_item_id"] for r in crafted_rows}
        all_item_ids.update(crafted_ids)
        logger.info("landing fill: added %d crafted item IDs to fetch queue", len(crafted_ids))

        # Add catalyst item IDs from quality_tracks table — populated by the
        # appearance crawl (Sync Loot Tables); not in journal encounter data.
        async with pool.acquire() as conn:
            qt_rows = await conn.fetch(
                "SELECT blizzard_item_id FROM landing.blizzard_item_quality_tracks"
            )
        qt_ids = {r["blizzard_item_id"] for r in qt_rows}
        all_item_ids.update(qt_ids)
        logger.info("landing fill: added %d quality-track item IDs to fetch queue", len(qt_ids))

        # ── Step 3: item sets (tier piece item IDs) ────────────────────────────
        # Tier pieces don't drop directly from encounters — they come from token
        # exchanges.  The Blizzard Item Set API gives us the exact item IDs for
        # every tier set across all expansions.
        step += 1
        _landing_status.update(
            step=step,
            phase_label="Fetching item sets from Blizzard API",
            detail="",
        )

        set_index = await blizzard_client.get_item_set_index()
        set_refs = (set_index or {}).get("item_sets", [])
        sets_stored  = 0
        sets_skipped = 0

        for set_ref in set_refs:
            set_id = set_ref.get("id")
            if not set_id:
                continue
            try:
                set_data = await blizzard_client.get_item_set(set_id)
            except Exception as set_exc:
                logger.warning("landing fill: skipping item set %d — %s", set_id, set_exc)
                continue
            if not set_data:
                continue

            set_name = set_data.get("name") or set_ref.get("name") or f"Set {set_id}"
            item_ids = [
                entry["id"]
                for entry in set_data.get("items", [])
                if entry.get("id")
            ]
            all_item_ids.update(item_ids)

            async with pool.acquire() as conn:
                result = await conn.execute(
                    """
                    INSERT INTO landing.blizzard_item_sets
                        (set_id, set_name, item_ids, payload, fetched_at)
                    VALUES ($1, $2, $3, $4::jsonb, NOW())
                    ON CONFLICT (set_id) DO UPDATE SET
                        set_name   = EXCLUDED.set_name,
                        item_ids   = EXCLUDED.item_ids,
                        payload    = EXCLUDED.payload,
                        fetched_at = EXCLUDED.fetched_at
                    """,
                    set_id, set_name, item_ids, _json.dumps(set_data),
                )
            if result.startswith("INSERT"):
                sets_stored += 1
            else:
                sets_skipped += 1

        logger.info(
            "landing fill: %d item sets stored, %d updated, %d total item IDs queued",
            sets_stored, sets_skipped, len(all_item_ids),
        )
        _landing_status.update(
            detail=f"{sets_stored + sets_skipped} item sets processed, "
                   f"{len(all_item_ids)} unique item IDs queued"
        )

        # ── Step 4: appearance crawl (catalyst quality tracks) ─────────────────
        # Discovers back/wrist/waist/feet catalyst tier pieces via the Blizzard
        # Item Appearance API.  Derives tier set name suffixes from enrichment.items,
        # matches appearance sets, and writes quality_track mappings to
        # landing.blizzard_item_quality_tracks.  Item IDs are added to all_item_ids
        # so they are fetched in Step 5.
        step += 1
        _landing_status.update(
            step=step,
            phase_label="Crawling appearance sets for catalyst items",
            detail="",
        )

        _TIER_SLOTS = ("head", "shoulder", "chest", "hands", "legs")
        _CATALYST_SLOTS = ("back", "wrist", "waist", "feet")

        async with pool.acquire() as conn:
            tier_name_rows = await conn.fetch("""
                SELECT DISTINCT ei.name
                  FROM enrichment.items ei
                  JOIN enrichment.item_sources es
                    ON es.blizzard_item_id = ei.blizzard_item_id
                 WHERE ei.slot_type = ANY($1::text[])
                   AND ei.name LIKE '%% of %%'
                   AND es.instance_type = 'raid'
                   AND EXISTS (
                       SELECT 1 FROM enrichment.bis_entries be
                        WHERE be.blizzard_item_id = ei.blizzard_item_id
                   )
            """, list(_TIER_SLOTS))

        tier_suffixes: set[str] = set()
        for row in tier_name_rows:
            name = row["name"] or ""
            idx = name.find(" of ")
            if idx >= 0:
                tier_suffixes.add(name[idx:])

        qt_registered = 0
        if not tier_suffixes:
            logger.info("landing fill: no tier suffixes found — skipping appearance crawl")
        else:
            logger.info(
                "landing fill: appearance crawl — %d suffix(es): %s",
                len(tier_suffixes), sorted(tier_suffixes),
            )
            all_app_sets = await blizzard_client.get_item_appearance_set_index()
            if not all_app_sets:
                logger.warning("landing fill: appearance set index unavailable — skipping crawl")
            else:
                # Inline quality-track derivation (avoids cross-package import)
                def _qt(name: str):
                    lower = name.strip().lower()
                    if lower.endswith("(mythic)"):    return "M"
                    if lower.endswith("(heroic)"):    return "H"
                    if "(raid finder)" in lower or "(lfr)" in lower: return "V"
                    return "C"

                matching_sets: list[tuple[int, str]] = []
                for app_set in all_app_sets:
                    set_name = app_set.get("name", "")
                    set_id   = app_set.get("id")
                    if not set_id:
                        continue
                    for suffix in tier_suffixes:
                        if suffix.strip().lower() in set_name.lower():
                            matching_sets.append((set_id, set_name))
                            break

                logger.info(
                    "landing fill: appearance crawl — %d matching set(s)", len(matching_sets)
                )

                for set_id, set_name in matching_sets:
                    quality_track = _qt(set_name)
                    set_data = await blizzard_client.get_item_appearance_set(set_id)
                    if not set_data:
                        logger.warning(
                            "landing fill: could not fetch appearance set %d", set_id
                        )
                        continue

                    appearances = set_data.get("appearances", [])
                    app_ids = [a.get("id") for a in appearances if a.get("id")]

                    app_results = await asyncio.gather(
                        *[blizzard_client.get_item_appearance(aid) for aid in app_ids],
                        return_exceptions=True,
                    )

                    async with pool.acquire() as conn:
                        for app_id, app_data in zip(app_ids, app_results):
                            if isinstance(app_data, Exception) or not app_data:
                                continue
                            await conn.execute(
                                """
                                INSERT INTO landing.blizzard_appearances
                                       (appearance_id, payload)
                                VALUES ($1, $2::jsonb)
                                ON CONFLICT DO NOTHING
                                """,
                                app_id, _json.dumps(app_data),
                            )
                            for item in app_data.get("items", []):
                                item_id = item.get("id")
                                if not item_id:
                                    continue
                                await conn.execute(
                                    """
                                    INSERT INTO landing.blizzard_item_quality_tracks
                                           (blizzard_item_id, quality_track)
                                    VALUES ($1, $2)
                                    ON CONFLICT (blizzard_item_id) DO UPDATE SET
                                        quality_track = EXCLUDED.quality_track,
                                        fetched_at    = NOW()
                                    """,
                                    item_id, quality_track,
                                )
                                all_item_ids.add(item_id)
                                qt_registered += 1

        logger.info(
            "landing fill: appearance crawl complete — %d quality-track item(s) registered",
            qt_registered,
        )
        _landing_status.update(
            detail=f"{qt_registered} catalyst item(s) registered from appearance sets"
        )

        step += 1
        _landing_status.update(
            step=step,
            phase_label="Fetching item records from Blizzard API",
            detail="",
        )

        # For gap-filling: skip items already fetched recently
        if not flush:
            stale_cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_DAYS)
            async with pool.acquire() as conn:
                existing_rows = await conn.fetch(
                    """
                    SELECT blizzard_item_id, MAX(fetched_at) AS latest_at
                      FROM landing.blizzard_items
                     GROUP BY blizzard_item_id
                    """
                )
            existing_items = {
                r["blizzard_item_id"]: r["latest_at"] for r in existing_rows
            }
            items_to_fetch = sorted(
                item_id for item_id in all_item_ids
                if item_id not in existing_items
                or existing_items[item_id] < stale_cutoff
            )
            fresh_count = len(all_item_ids) - len(items_to_fetch)
            logger.info(
                "landing catch-up: %d items total, %d fresh (skipping), %d to fetch",
                len(all_item_ids), fresh_count, len(items_to_fetch),
            )
        else:
            items_to_fetch = sorted(all_item_ids)
            fresh_count = 0

        items_stored  = 0
        items_skipped = 0
        for item_id in items_to_fetch:
            try:
                item_data = await blizzard_client.get_item(item_id)
            except Exception as item_exc:
                logger.warning("landing fill: skipping item %d — %s", item_id, item_exc)
                items_skipped += 1
                continue

            if not item_data:
                items_skipped += 1
                continue

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO landing.blizzard_items (blizzard_item_id, payload)
                    VALUES ($1, $2::jsonb)
                    """,
                    item_id, _json.dumps(item_data),
                )
            items_stored += 1

            if items_stored % 50 == 0:
                _landing_status.update(
                    detail=f"{items_stored}/{len(items_to_fetch)} items fetched"
                )

        skip_note = f" ({items_skipped} skipped)" if items_skipped else ""
        fresh_note = f", {fresh_count} already fresh" if fresh_count else ""
        detail = (
            f"{instances_stored} instances, {encounters_stored} encounters, "
            f"{items_stored} items stored{skip_note}{fresh_note}. "
            "Run Enrich & Classify next."
        )
        _landing_status.update({
            "running": False,
            "phase_label": "Complete",
            "step": _landing_status["total_steps"],
            "detail": detail,
            "finished_at": datetime.now(timezone.utc),
            "error": None,
        })
        logger.info("landing fill complete: %s", detail)

    except Exception as exc:
        logger.exception("landing fill failed")
        _landing_status.update({
            "running": False,
            "phase_label": "Error",
            "detail": str(exc),
            "finished_at": datetime.now(timezone.utc),
            "error": str(exc),
        })


@router.post("/landing-flush-fill")
async def landing_flush_fill(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """Truncate landing tables then refill from Blizzard API (raw data only, GL only).

    Background task — poll GET /landing-status for progress.
    Steps: truncate → instances/encounters → item sets → item records.
    Run Section C → Enrich & Classify afterward.
    """
    import asyncio
    global _landing_status

    if _landing_status["running"]:
        return {"ok": False, "error": "Landing fill already running — check status."}

    pool = _pool(request)
    blizzard_client = await _get_blizzard_client(request)

    _landing_status = {
        "running": True,
        "phase_label": "Starting…",
        "step": 0,
        "total_steps": 5,
        "detail": "",
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "error": None,
    }
    asyncio.create_task(_run_landing_fill(pool, blizzard_client, flush=True))
    return {"ok": True, "started": True}


@router.post("/landing-catch-up")
async def landing_catch_up(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """Incremental landing table fill — pulls new/missing data without truncating (GL only).

    Background task — poll GET /landing-status for progress.
    Steps: instances/encounters → item sets → item records.
    Run Section C → Enrich & Classify afterward.
    """
    import asyncio
    global _landing_status

    if _landing_status["running"]:
        return {"ok": False, "error": "Landing fill already running — check status."}

    pool = _pool(request)
    blizzard_client = await _get_blizzard_client(request)

    _landing_status = {
        "running": True,
        "phase_label": "Starting…",
        "step": 0,
        "total_steps": 3,
        "detail": "",
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "error": None,
    }
    asyncio.create_task(_run_landing_fill(pool, blizzard_client, flush=False))
    return {"ok": True, "started": True}


@router.get("/enrich-classify-status")
async def enrich_classify_status():
    """Poll the enrich-and-classify job status."""
    s = _enrich_classify_status.copy()
    s["started_at"] = s["started_at"].isoformat() if s["started_at"] else None
    s["finished_at"] = s["finished_at"].isoformat() if s["finished_at"] else None
    return {"ok": True, **s}


@router.post("/enrich-and-classify")
async def enrich_and_classify(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """New pipeline: rebuild enrichment schema from landing tables (GL only).

    Step 1 — sp_rebuild_all(): reads from landing.blizzard_items + journal tables.
              Populates enrichment.items, item_sources, item_recipes, item_seasons.
              Runs classification (item_category, flag_junk_sources).
    Step 2 — rebuild_bis_from_landing(): re-parses landing.bis_scrape_raw HTML →
              enrichment.bis_entries (all scraped BIS recommendations).
    Step 3 — rebuild_trinket_ratings_from_landing(): re-parses Wowhead landing HTML →
              enrichment.trinket_ratings (S/A/B/C/D tier lists).
    Step 4 — Fetch icon URLs: Blizzard media API for items in enrichment.items
              where icon_url IS NULL.

    Background task — poll GET /enrich-classify-status for progress.
    """
    import asyncio
    global _enrich_classify_status

    if _enrich_classify_status["running"]:
        return {"ok": False, "error": "Enrich & classify already running — check status."}

    pool = _pool(request)
    blizzard_client = await _get_blizzard_client(request)

    _enrich_classify_status = {
        "running": True,
        "phase_label": "Starting…",
        "step": 0,
        "total_steps": 5,
        "detail": "",
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "error": None,
    }

    async def _run():
        global _enrich_classify_status

        try:
            # ── Step 1: rebuild enrichment schema (items, sources, categories) ──
            _enrich_classify_status.update(
                step=1,
                phase_label="Rebuilding enrichment schema from landing tables",
                detail="",
            )
            async with pool.acquire() as conn:
                await conn.execute("CALL enrichment.sp_rebuild_all()")
                item_counts = await conn.fetchrow("""
                    SELECT
                        (SELECT count(*) FROM enrichment.items)           AS items,
                        (SELECT count(*) FROM enrichment.item_sources)    AS sources,
                        (SELECT count(*) FROM enrichment.items
                          WHERE icon_url IS NULL)                         AS missing_icons
                """)
            logger.info(
                "enrich-and-classify: sp_rebuild_all complete — %d items, %d sources",
                item_counts["items"], item_counts["sources"],
            )

            # ── Step 2: rebuild BIS entries from landing HTML ──────────────────
            _enrich_classify_status.update(
                step=2,
                phase_label="Rebuilding BIS entries from landing.bis_scrape_raw",
                detail="",
            )
            from sv_common.guild_sync.bis_sync import (
                rebuild_bis_from_landing as _rebuild_bis,
                rebuild_trinket_ratings_from_landing as _rebuild_trinkets,
            )
            bis_result = await _rebuild_bis(pool)
            logger.info(
                "enrich-and-classify: BIS rebuild complete — %d entries",
                bis_result.get("bis_entries_inserted", 0),
            )

            # ── Step 3: rebuild trinket ratings from landing HTML ──────────────
            _enrich_classify_status.update(
                step=3,
                phase_label="Rebuilding trinket ratings from landing.bis_scrape_raw",
                detail="",
            )
            trinket_result = await _rebuild_trinkets(pool)
            logger.info(
                "enrich-and-classify: trinket rebuild complete — %d ratings",
                trinket_result.get("trinket_ratings_inserted", 0),
            )

            # ── Step 4: fetch icon URLs for items missing them ─────────────────
            missing = item_counts["missing_icons"] or 0
            _enrich_classify_status.update(
                step=4,
                phase_label=f"Fetching icon URLs for {missing} items from Blizzard media API",
                detail="",
            )
            icons_filled  = 0
            icons_skipped = 0
            if missing > 0:
                # Only fetch items not already in the landing icon cache
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT ei.blizzard_item_id
                          FROM enrichment.items ei
                         WHERE ei.icon_url IS NULL
                           AND NOT EXISTS (
                                 SELECT 1 FROM landing.blizzard_item_icons lii
                                  WHERE lii.blizzard_item_id = ei.blizzard_item_id
                               )
                        """
                    )
                item_ids = [r["blizzard_item_id"] for r in rows]
                missing = len(item_ids)
                for item_id in item_ids:
                    icon_url = await blizzard_client.get_item_media(item_id)
                    if icon_url:
                        async with pool.acquire() as conn:
                            # Write to landing cache (survives rebuilds)
                            await conn.execute(
                                """
                                INSERT INTO landing.blizzard_item_icons
                                    (blizzard_item_id, icon_url, fetched_at)
                                VALUES ($1, $2, NOW())
                                ON CONFLICT (blizzard_item_id) DO UPDATE
                                    SET icon_url = EXCLUDED.icon_url,
                                        fetched_at = EXCLUDED.fetched_at
                                """,
                                item_id, icon_url,
                            )
                            # Also update current enrichment row for this run
                            await conn.execute(
                                "UPDATE enrichment.items SET icon_url = $1 WHERE blizzard_item_id = $2",
                                icon_url, item_id,
                            )
                        icons_filled += 1
                    else:
                        icons_skipped += 1

                    if (icons_filled + icons_skipped) % 50 == 0:
                        _enrich_classify_status.update(
                            detail=f"{icons_filled + icons_skipped}/{missing} processed"
                        )

            detail = (
                f"{item_counts['items']} items, {item_counts['sources']} sources, "
                f"{bis_result.get('bis_entries_inserted', 0)} BIS entries, "
                f"{trinket_result.get('trinket_ratings_inserted', 0)} trinket ratings. "
                f"Icons: {icons_filled} fetched"
                + (f", {icons_skipped} unavailable" if icons_skipped else "")
                + "."
            )
            _enrich_classify_status.update({
                "running": False,
                "phase_label": "Complete",
                "step": 4,
                "detail": detail,
                "finished_at": datetime.now(timezone.utc),
                "error": None,
            })
            logger.info("enrich-and-classify complete: %s", detail)

        except Exception as exc:
            logger.exception("enrich-and-classify failed")
            _enrich_classify_status.update({
                "running": False,
                "phase_label": "Error",
                "detail": str(exc),
                "finished_at": datetime.now(timezone.utc),
                "error": str(exc),
            })

    asyncio.create_task(_run())
    return {"ok": True, "started": True}


@router.post("/test-blood-dk")
async def sync_test_blood_dk(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """Test BIS fetch for Blood Death Knight (GL only).

    Looks up the Blood DK spec_id from the database, then runs a full
    single-spec BIS sync. Use this to verify scraping is working before
    running Sync All.
    """
    pool = _pool(request)
    async with pool.acquire() as conn:
        spec = await conn.fetchrow(
            """SELECT s.id FROM ref.specializations s
               JOIN ref.classes c ON c.id = s.class_id
               WHERE c.name = 'Death Knight' AND s.name = 'Blood'
               LIMIT 1"""
        )
    if not spec:
        return {"ok": False, "error": "Blood Death Knight spec not found in database"}

    from sv_common.guild_sync.bis_sync import sync_spec as _sync_spec
    result = await _sync_spec(pool, spec["id"])
    return {"ok": True, "spec": "Blood Death Knight", "spec_id": spec["id"], **result}


@router.post("/resync-errors")
async def resync_errors(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """Re-sync only failed/errored BIS scrape targets (GL only).

    Runs synchronously spec by spec, returning when complete.
    """
    import asyncio
    pool = _pool(request)
    async with pool.acquire() as conn:
        failed_specs = await conn.fetch(
            """SELECT DISTINCT spec_id FROM config.bis_scrape_targets
               WHERE status IN ('failed', 'partial')
               ORDER BY spec_id"""
        )
    if not failed_specs:
        return {"ok": True, "message": "No failed targets found.", "specs_resynced": 0}

    from sv_common.guild_sync.bis_sync import sync_spec as _sync_spec
    results = []
    for row in failed_specs:
        r = await _sync_spec(pool, row["spec_id"])
        results.append(r)
        await asyncio.sleep(0.2)

    total_items = sum(r.get("items_found", 0) for r in results)
    return {
        "ok": True,
        "specs_resynced": len(results),
        "total_items": total_items,
    }


@router.get("/trinket-ratings-status")
async def trinket_ratings_status(request: Request):
    """Return trinket rating counts per spec × source combination (Officer+ read-only).

    Used by the Admin → Gear Plan → Trinket Ratings section to show scrape coverage.
    """
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                sp.name          AS spec_name,
                c.name           AS class_name,
                bls.name         AS source_name,
                bls.origin       AS source_origin,
                COUNT(ttr.id)    AS rating_count,
                NULL::timestamptz AS last_updated
              FROM ref.specializations sp
              JOIN ref.classes c ON c.id = sp.class_id
              CROSS JOIN ref.bis_list_sources bls
              LEFT JOIN enrichment.trinket_ratings ttr
                     ON ttr.spec_id = sp.id AND ttr.source_id = bls.id
             WHERE bls.is_active = TRUE
               AND bls.origin != 'icy_veins'
             GROUP BY sp.id, sp.name, c.name, bls.id, bls.name, bls.origin
             ORDER BY c.name, sp.name, bls.sort_order
            """
        )

    return JSONResponse({
        "ok": True,
        "data": [
            {
                "spec_name": r["spec_name"],
                "class_name": r["class_name"],
                "source_name": r["source_name"],
                "source_origin": r["source_origin"],
                "rating_count": r["rating_count"],
                "last_updated": r["last_updated"].isoformat() if r["last_updated"] else None,
            }
            for r in rows
        ],
    })
