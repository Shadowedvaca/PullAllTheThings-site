"""Member-facing gear plan API routes.

All routes require a logged-in player (Bearer token or session cookie).
Character ownership is verified per request — you can only access plans
for characters linked to your player record.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from guild_portal.deps import get_current_player, get_db
from guild_portal.services import gear_plan_service as svc
from guild_portal.services.item_service import get_or_fetch_item
from sv_common.db.models import Player, PlayerCharacter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/me/gear-plan", tags=["gear-plan"])
items_router = APIRouter(prefix="/api/v1/items", tags=["items"])


async def _get_pool(request: Request):
    return request.app.state.guild_sync_pool


async def _verify_ownership(
    player: Player,
    character_id: int,
    db: AsyncSession,
) -> bool:
    """Return True if the character is linked to the player."""
    result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
        )
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# GET /api/v1/me/gear-plan/{character_id}
# ---------------------------------------------------------------------------


@router.get("/{character_id}")
async def get_gear_plan(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return the gear plan for a character, creating one if it doesn't exist."""
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    # Ensure plan exists
    await svc.get_or_create_plan(pool, current_player.id, character_id)

    detail = await svc.get_plan_detail(pool, current_player.id, character_id)
    if not detail:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    return JSONResponse({"ok": True, "data": detail})


# ---------------------------------------------------------------------------
# POST /api/v1/me/gear-plan/{character_id}
# ---------------------------------------------------------------------------


@router.post("/{character_id}")
async def create_gear_plan(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Create (or retrieve) a gear plan for a character.

    Body (all optional):
        spec_id, hero_talent_id, bis_source_id
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    spec_id: Optional[int] = body.get("spec_id")
    hero_talent_id: Optional[int] = body.get("hero_talent_id")
    bis_source_id: Optional[int] = body.get("bis_source_id")

    plan = await svc.get_or_create_plan(
        pool, current_player.id, character_id,
        spec_id=spec_id,
        hero_talent_id=hero_talent_id,
        bis_source_id=bis_source_id,
    )
    return JSONResponse({"ok": True, "data": {"plan": plan}})


# ---------------------------------------------------------------------------
# PATCH /api/v1/me/gear-plan/{character_id}/config
# ---------------------------------------------------------------------------


@router.patch("/{character_id}/config")
async def update_plan_config(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Update spec / hero talent / BIS source on the plan.

    Body (all optional):
        spec_id, hero_talent_id, bis_source_id
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    spec_id: Optional[int] = body.get("spec_id")
    hero_talent_id: Optional[int] = body.get("hero_talent_id")  # can be None to clear
    bis_source_id: Optional[int] = body.get("bis_source_id")

    ok = await svc.update_plan_config(
        pool, current_player.id, character_id,
        spec_id=spec_id,
        hero_talent_id=hero_talent_id,
        bis_source_id=bis_source_id,
    )
    if not ok:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# PUT /api/v1/me/gear-plan/{character_id}/slot/{slot}
# ---------------------------------------------------------------------------


@router.put("/{character_id}/slot/{slot}")
async def update_slot(
    character_id: int,
    slot: str,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Set the desired item for a slot.

    Body:
        blizzard_item_id (int or null to clear)
        item_name        (optional string)
        is_locked        (optional bool)
    """
    if slot not in svc.WOW_SLOTS:
        return JSONResponse({"ok": False, "error": f"Unknown slot: {slot}"}, status_code=400)

    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    blizzard_item_id: Optional[int] = body.get("blizzard_item_id")
    item_name: Optional[str] = body.get("item_name")
    is_locked: Optional[bool] = body.get("is_locked")

    ok = await svc.update_slot(
        pool, current_player.id, character_id, slot,
        blizzard_item_id=blizzard_item_id,
        item_name=item_name,
        is_locked=is_locked,
    )
    if not ok:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/v1/me/gear-plan/{character_id}/populate
# ---------------------------------------------------------------------------


@router.post("/{character_id}/populate")
async def populate_from_bis(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Re-populate unlocked slots from a BIS source.

    Body (all optional):
        source_id      — override plan's bis_source_id
        hero_talent_id — override plan's hero_talent_id
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    source_id: Optional[int] = body.get("source_id")
    hero_talent_id: Optional[int] = body.get("hero_talent_id")

    populated = await svc.populate_from_bis(
        pool, current_player.id, character_id,
        source_id=source_id,
        hero_talent_id=hero_talent_id,
    )
    return JSONResponse({"ok": True, "data": {"populated": populated}})


# ---------------------------------------------------------------------------
# DELETE /api/v1/me/gear-plan/{character_id}
# ---------------------------------------------------------------------------


@router.delete("/{character_id}")
async def delete_gear_plan(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Delete the gear plan for a character."""
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    ok = await svc.delete_plan(pool, current_player.id, character_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# GET /api/v1/me/gear-plan/{character_id}/available-items
# ---------------------------------------------------------------------------


@router.get("/{character_id}/trinket-ratings")
async def get_trinket_ratings(
    character_id: int,
    slot: str,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return trinket tier ratings for a character's plan spec.

    Query params:
        slot — must be 'trinket_1' or 'trinket_2'

    Returns tiers (S→F) with is_equipped, is_bis, is_available_this_season per item.
    """
    if slot not in ("trinket_1", "trinket_2"):
        return JSONResponse({"ok": False, "error": "slot must be 'trinket_1' or 'trinket_2'"}, status_code=400)

    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    data = await svc.get_trinket_ratings(pool, current_player.id, character_id, slot)
    if data is None:
        return JSONResponse({"ok": False, "error": "Invalid slot"}, status_code=400)

    return JSONResponse({"ok": True, "data": data})


@router.get("/{character_id}/available-items")
async def get_available_items(
    character_id: int,
    slot: str,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return all class-eligible scanned items for a gear plan slot.

    Used to populate the 'Available from Content' section in the slot drawer.
    Query params:
        slot — one of the 16 WOW_SLOTS keys (e.g. 'head', 'ring_1', 'main_hand')
    """
    if slot not in svc.WOW_SLOTS:
        return JSONResponse({"ok": False, "error": f"Unknown slot: {slot}"}, status_code=400)

    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    items = await svc.get_available_items(pool, current_player.id, character_id, slot)
    return JSONResponse({"ok": True, "data": items})


# ---------------------------------------------------------------------------
# PATCH /api/v1/me/gear-plan/{character_id}/slots/{slot}/exclude
# DELETE /api/v1/me/gear-plan/{character_id}/slots/{slot}/exclude
# Phase 1E.5 — item exclusion
# ---------------------------------------------------------------------------


@router.patch("/{character_id}/slots/{slot}/exclude")
async def add_item_exclusion(
    character_id: int,
    slot: str,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Permanently exclude an item from a slot's suggestions and Fill BIS.

    Body:
        blizzard_item_id (int)
    """
    if slot not in svc.WOW_SLOTS:
        return JSONResponse({"ok": False, "error": f"Unknown slot: {slot}"}, status_code=400)

    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    blizzard_item_id: Optional[int] = body.get("blizzard_item_id")
    if not blizzard_item_id:
        return JSONResponse({"ok": False, "error": "blizzard_item_id is required"}, status_code=400)

    ok = await svc.add_exclusion(pool, current_player.id, character_id, slot, blizzard_item_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "Plan or item not found"}, status_code=404)

    return JSONResponse({"ok": True})


@router.delete("/{character_id}/slots/{slot}/exclude")
async def remove_item_exclusion(
    character_id: int,
    slot: str,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Un-exclude a previously excluded item from a slot.

    Body:
        blizzard_item_id (int)
    """
    if slot not in svc.WOW_SLOTS:
        return JSONResponse({"ok": False, "error": f"Unknown slot: {slot}"}, status_code=400)

    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    blizzard_item_id: Optional[int] = body.get("blizzard_item_id")
    if not blizzard_item_id:
        return JSONResponse({"ok": False, "error": "blizzard_item_id is required"}, status_code=400)

    ok = await svc.remove_exclusion(pool, current_player.id, character_id, slot, blizzard_item_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# PATCH /api/v1/me/gear-plan/{character_id}/source
# Phase 1E.6 — switch equipped gear source between 'blizzard' and 'simc'
# ---------------------------------------------------------------------------


@router.patch("/{character_id}/source")
async def set_equipped_source(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Switch the equipped gear source for the paperdoll display.

    Body:
        source (str) — 'blizzard' or 'simc'
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    source: str = body.get("source", "")
    if source not in ("blizzard", "simc"):
        return JSONResponse({"ok": False, "error": "source must be 'blizzard' or 'simc'"}, status_code=400)

    ok, err = await svc.set_equipped_source(pool, current_player.id, character_id, source)
    if not ok:
        if err == "no_simc":
            return JSONResponse(
                {"ok": False, "error": "No SimC profile imported yet — paste one first"},
                status_code=409,
            )
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/v1/me/gear-plan/{character_id}/import-equipped-simc
# Phase 1E.6 — store SimC profile as the equipped gear source
# ---------------------------------------------------------------------------


@router.post("/{character_id}/import-equipped-simc")
async def import_equipped_simc(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Store SimC text as the equipped gear source for the paperdoll.

    Saves simc_profile, stamps simc_imported_at, sets equipped_source='simc'.
    Does NOT touch gear_plan_slots.
    Body:
        simc_text (string)
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    simc_text: str = body.get("simc_text", "").strip()
    if not simc_text:
        return JSONResponse({"ok": False, "error": "simc_text is required"}, status_code=400)

    await svc.get_or_create_plan(pool, current_player.id, character_id)

    ok, err = await svc.store_equipped_simc(pool, current_player.id, character_id, simc_text)
    if not ok:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/v1/me/gear-plan/{character_id}/import-simc
# Phase 1E.6 — import SimC as BIS goals (slot population only)
# ---------------------------------------------------------------------------


@router.post("/{character_id}/import-simc")
async def import_simc(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Paste SimC text to set gear_plan_slots as BIS goals.

    Overwrites all non-locked slots from the SimC string.
    Does NOT change equipped_source or simc_profile.
    Body:
        simc_text (string)
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    simc_text: str = body.get("simc_text", "").strip()
    if not simc_text:
        return JSONResponse({"ok": False, "error": "simc_text is required"}, status_code=400)

    # Ensure plan exists first
    await svc.get_or_create_plan(pool, current_player.id, character_id)

    result = await svc.import_simc_goals(pool, current_player.id, character_id, simc_text)
    return JSONResponse({"ok": True, "data": result})


# ---------------------------------------------------------------------------
# POST /api/v1/me/gear-plan/{character_id}/set-goals-from-equipped
# ---------------------------------------------------------------------------


@router.post("/{character_id}/set-goals-from-equipped")
async def set_goals_from_equipped(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Populate gear_plan_slots from the character's Blizzard-synced equipment.

    Overwrites all non-locked slots with the items currently in
    guild_identity.character_equipment for this character.
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    result = await svc.set_goals_from_equipped(pool, current_player.id, character_id)
    return JSONResponse({"ok": True, "data": result})


# ---------------------------------------------------------------------------
# GET /api/v1/me/gear-plan/{character_id}/export-simc
# ---------------------------------------------------------------------------


@router.get("/{character_id}/export-simc")
async def export_simc(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return SimC profile text from gear_plan_slots."""
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    simc_text = await svc.export_simc(pool, current_player.id, character_id)
    if not simc_text:
        return JSONResponse({"ok": False, "error": "No gear plan slots found"}, status_code=404)

    return PlainTextResponse(
        simc_text,
        headers={"Content-Disposition": "attachment; filename=gear_plan.simc"},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/me/gear-plan/{character_id}/export-equipped-simc
# Phase 1E.6 — export the currently displayed equipped gear as SimC
# ---------------------------------------------------------------------------


@router.get("/{character_id}/export-equipped-simc")
async def export_equipped_simc(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return SimC profile text for the current equipped gear source.

    If equipped_source='simc', returns the stored profile.
    If equipped_source='blizzard', generates one from character_equipment.
    """
    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse({"ok": False, "error": "Character not linked to your account"}, status_code=403)

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    simc_text = await svc.export_equipped_simc(pool, current_player.id, character_id)
    if not simc_text:
        return JSONResponse({"ok": False, "error": "No equipped gear data found"}, status_code=404)

    return PlainTextResponse(
        simc_text,
        headers={"Content-Disposition": "attachment; filename=equipped_gear.simc"},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/me/gear-plan/{character_id}/sync-equipment
# ---------------------------------------------------------------------------


@router.post("/{character_id}/sync-equipment")
async def sync_character_equipment(
    character_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Sync the equipped gear for this character using the Blizzard API.

    Tries the running scheduler's client first (most efficient).
    Falls back to creating a short-lived client from env vars if the
    scheduler isn't running (e.g. audit channel not configured on dev).
    Populates / refreshes guild_identity.character_equipment.
    """
    import os

    if not await _verify_ownership(current_player, character_id, db):
        return JSONResponse(
            {"ok": False, "error": "Character not linked to your account"},
            status_code=403,
        )

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse(
            {"ok": False, "error": "Database pool unavailable"}, status_code=503
        )

    async with pool.acquire() as conn:
        char_row = await conn.fetchrow(
            """
            SELECT id, character_name, realm_slug,
                   last_login_timestamp, last_equipment_sync
              FROM guild_identity.wow_characters
             WHERE id = $1
            """,
            character_id,
        )

    if not char_row:
        return JSONResponse(
            {"ok": False, "error": "Character not found"}, status_code=404
        )

    from sv_common.guild_sync.blizzard_client import BlizzardClient
    from sv_common.guild_sync.equipment_sync import sync_equipment

    # Prefer the long-lived scheduler client; fall back to a per-request client.
    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    owned_client: Optional[BlizzardClient] = None

    if scheduler is not None and hasattr(scheduler, "blizzard_client"):
        blizzard_client = scheduler.blizzard_client
    else:
        client_id     = os.environ.get("BLIZZARD_CLIENT_ID", "")
        client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        "Blizzard API credentials not configured. "
                        "Ask your Guild Leader to add BLIZZARD_CLIENT_ID / "
                        "BLIZZARD_CLIENT_SECRET to the server's .env file."
                    ),
                },
                status_code=503,
            )
        owned_client = BlizzardClient(
            client_id=client_id,
            client_secret=client_secret,
        )
        await owned_client.initialize()
        blizzard_client = owned_client

    try:
        stats = await sync_equipment(pool, blizzard_client, [dict(char_row)])
    finally:
        if owned_client is not None:
            await owned_client.close()

    if stats.get("equipment_errors", 0) > 0:
        return JSONResponse(
            {"ok": False, "error": "Equipment sync failed — check server logs"}
        )

    return JSONResponse({"ok": True, "data": stats})


# ---------------------------------------------------------------------------
# GET /api/v1/items/search?q=
# Must be registered before /{blizzard_item_id} to avoid "search" matching as an int
# ---------------------------------------------------------------------------


@items_router.get("/search")
async def search_items(
    q: str,
    request: Request,
    current_player: Player = Depends(get_current_player),
):
    """Search cached wow_items by name (ILIKE). Returns up to 10 matches."""
    if len(q.strip()) < 2:
        return JSONResponse({"ok": True, "data": []})

    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT blizzard_item_id, name, icon_url, slot_type
              FROM enrichment.items
             WHERE name ILIKE $1
               AND name != 'Unknown Item'
             ORDER BY name
             LIMIT 10
            """,
            f"%{q.strip()}%",
        )

    return JSONResponse({"ok": True, "data": [dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# GET /api/v1/items/{blizzard_item_id}
# ---------------------------------------------------------------------------


@items_router.get("/{blizzard_item_id}")
async def get_item(
    blizzard_item_id: int,
    request: Request,
    current_player: Player = Depends(get_current_player),
):
    """Fetch (and cache) item metadata from Wowhead."""
    pool = await _get_pool(request)
    if not pool:
        return JSONResponse({"ok": False, "error": "Database pool unavailable"}, status_code=503)

    async with httpx.AsyncClient(timeout=10) as http_client:
        item = await get_or_fetch_item(pool, blizzard_item_id, http_client)

    if not item:
        return JSONResponse({"ok": False, "error": "Item not found"}, status_code=404)

    return JSONResponse({"ok": True, "data": item})
