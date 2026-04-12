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
"""

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
              FROM guild_identity.bis_list_sources
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
            f"UPDATE guild_identity.bis_list_sources SET {set_clauses} WHERE id = $1",
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
                   e.item_id, e.priority, e.notes,
                   wi.blizzard_item_id, wi.name AS item_name, wi.icon_url
              FROM guild_identity.bis_list_entries e
              JOIN guild_identity.wow_items wi ON wi.id = e.item_id
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
        # Ensure item exists in wow_items
        await conn.execute(
            """
            INSERT INTO guild_identity.wow_items (blizzard_item_id, name, slot_type)
            VALUES ($1, '', $2)
            ON CONFLICT (blizzard_item_id) DO NOTHING
            """,
            body.blizzard_item_id, body.slot,
        )
        item_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
            body.blizzard_item_id,
        )
        if item_row is None:
            raise HTTPException(status_code=500, detail="Failed to create item")

        item_id = item_row["id"]
        row = await conn.fetchrow(
            """
            INSERT INTO guild_identity.bis_list_entries
                (source_id, spec_id, hero_talent_id, slot, item_id, priority, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (source_id, spec_id, hero_talent_id, slot, item_id)
            DO UPDATE SET priority = EXCLUDED.priority, notes = EXCLUDED.notes
            RETURNING id
            """,
            body.source_id, body.spec_id, body.hero_talent_id,
            body.slot, item_id, body.priority, body.notes,
        )
    return {"ok": True, "id": row["id"]}


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: int, request: Request):
    pool = _pool(request)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM guild_identity.bis_list_entries WHERE id = $1",
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
              FROM guild_identity.bis_scrape_targets t
              JOIN guild_identity.bis_list_sources s ON s.id = t.source_id
              JOIN guild_identity.specializations sp ON sp.id = t.spec_id
              JOIN guild_identity.classes c ON c.id = sp.class_id
              LEFT JOIN guild_identity.hero_talents ht ON ht.id = t.hero_talent_id
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
            f"UPDATE guild_identity.bis_scrape_targets SET {set_clauses} WHERE id = $1",
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
              FROM guild_identity.bis_scrape_log l
              JOIN guild_identity.bis_scrape_targets t ON t.id = l.target_id
              JOIN guild_identity.bis_list_sources s ON s.id = t.source_id
              JOIN guild_identity.specializations sp ON sp.id = t.spec_id
              JOIN guild_identity.classes c ON c.id = sp.class_id
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
    """Import a SimC BIS profile as bis_list_entries for a spec (GL only)."""
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


@router.post("/sync-crafted-items")
async def sync_crafted_items(
    request: Request,
    player: Player = Depends(require_rank(5)),
):
    """Fire-and-forget discovery of craftable gear items via the Blizzard Recipe API.

    For each recipe in the active expansion with no item_recipe_links entry,
    calls GET /data/wow/recipe/{id} + GET /data/wow/item/{id} to identify the
    crafted item, stubs it into wow_items, and creates the link.  Non-equippable
    items (consumables, enchant scrolls, etc.) are skipped automatically.

    Returns immediately — the sync runs in the background (~1–3 min for a full
    expansion).  Run Enrich Items (Step 2) afterwards to populate Wowhead
    tooltips for newly stubbed items.
    """
    import asyncio
    from sv_common.guild_sync.item_recipe_link_sync import discover_and_link_crafted_items

    pool   = _pool(request)
    client = await _get_blizzard_client(request)

    async def _run():
        try:
            stats = await discover_and_link_crafted_items(pool, client)
            logger.info("Crafted item discovery complete: %s", stats)
        except Exception as exc:
            logger.error("Crafted item discovery background task failed: %s", exc, exc_info=True)

    asyncio.create_task(_run())
    return {
        "ok": True,
        "message": (
            "Crafted item discovery started in background. "
            "This takes 1–3 minutes — watch server logs for progress. "
            "Run Enrich Items when done."
        ),
    }


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
                      JOIN guild_identity.bis_list_entries ble ON ble.item_id = wi.id
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
            "SELECT id FROM guild_identity.bis_list_sources WHERE name = 'Wowhead Overall' LIMIT 1"
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
