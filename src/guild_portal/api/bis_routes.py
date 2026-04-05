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
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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
    """Trigger full BIS pipeline across all active sources (GL only)."""
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import sync_all as _sync_all
    import asyncio
    asyncio.create_task(_sync_all(pool))
    return {"ok": True, "message": "Full BIS sync started in background"}


@router.post("/sync/{source_id}")
async def sync_source(
    source_id: int, request: Request, player: Player = Depends(require_rank(5))
):
    """Trigger BIS sync for one source across all specs (GL only)."""
    pool = _pool(request)
    from sv_common.guild_sync.bis_sync import sync_source as _sync_source
    import asyncio
    asyncio.create_task(_sync_source(pool, source_id))
    return {"ok": True, "message": f"Sync started for source {source_id}"}


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
