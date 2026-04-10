"""Gear Plan page routes — admin BIS dashboard + member gear plan."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.deps import get_db, get_page_member
from guild_portal.nav import load_nav_items
from guild_portal.services import campaign_service
from guild_portal.templating import templates
from sv_common.db.models import Player

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gear-plan-pages"])


async def _require_gear_plan(request: Request, db: AsyncSession):
    """Return player if they have Officer+ access (level 4), else None."""
    from guild_portal.deps import get_page_member
    from guild_portal.nav import get_min_rank_for_screen

    player = await get_page_member(request, db)
    if player is None:
        return None
    min_level = await get_min_rank_for_screen(db, "gear_plan")
    rank_level = player.guild_rank.level if player.guild_rank else 0
    if rank_level < min_level:
        return None
    return player


@router.get("/admin/gear-plan", response_class=HTMLResponse)
async def gear_plan_admin_page(request: Request):
    """Admin BIS Sync Dashboard."""
    import os
    from sv_common.config_cache import get_site_config

    from guild_portal.deps import get_db as _get_db
    async for db in _get_db():
        player = await _require_gear_plan(request, db)
        if player is None:
            return RedirectResponse("/admin/players")

        nav_items = await load_nav_items(db, player)

        # Determine if user is GL (level 5+) for write-access controls
        rank_level = player.guild_rank.level if player.guild_rank else 0
        is_gl = rank_level >= 5

        # Check whether Blizzard API credentials are configured so the template
        # can grey out the Sync Loot Tables button when they're missing.
        cfg = get_site_config() or {}
        has_blizzard = bool(
            os.environ.get("BLIZZARD_CLIENT_ID") or cfg.get("blizzard_client_id")
        ) and bool(
            os.environ.get("BLIZZARD_CLIENT_SECRET") or cfg.get("blizzard_client_secret_encrypted")
        )

        return templates.TemplateResponse(
            "admin/gear_plan.html",
            {
                "request": request,
                "current_member": player,
                "nav_items": nav_items,
                "current_screen": "gear_plan",
                "is_gl": is_gl,
                "has_blizzard": has_blizzard,
            },
        )


# ---------------------------------------------------------------------------
# GET /gear-plan  — member personal gear plan
# ---------------------------------------------------------------------------


@router.get("/my-characters", response_class=HTMLResponse)
async def my_characters_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    """Unified character sheet."""
    if current_member is None:
        return RedirectResponse(url="/login?next=/my-characters", status_code=302)

    active = await campaign_service.list_campaigns(db, status="live")
    nav_items = await load_nav_items(db, current_member)

    return templates.TemplateResponse(
        "member/my_characters.html",
        {
            "request": request,
            "current_member": current_member,
            "active_campaigns": active,
            "nav_items": nav_items,
            "current_screen": "my_characters",
        },
    )


@router.get("/gear-plan", response_class=HTMLResponse)
async def gear_plan_redirect(request: Request):
    """Gear Plan has moved — redirect to /my-characters."""
    return RedirectResponse(url="/my-characters", status_code=302)


# ---------------------------------------------------------------------------
# GET /admin/roster-needs — Roster aggregation page (Officer+)
# ---------------------------------------------------------------------------


@router.get("/admin/roster-needs", response_class=HTMLResponse)
async def roster_needs_page(request: Request):
    """Roster Needs — gear needs aggregated by boss/dungeon across the roster."""
    from guild_portal.deps import get_db as _get_db
    from guild_portal.nav import get_min_rank_for_screen

    async for db in _get_db():
        player = await get_page_member(request, db)
        if player is None:
            return RedirectResponse("/login?next=/admin/roster-needs", status_code=302)
        min_level = await get_min_rank_for_screen(db, "roster_needs")
        rank_level = player.guild_rank.level if player.guild_rank else 0
        if rank_level < min_level:
            return RedirectResponse("/admin/players", status_code=302)

        nav_items = await load_nav_items(db, player)

        return templates.TemplateResponse(
            "admin/roster_needs.html",
            {
                "request": request,
                "current_member": player,
                "nav_items": nav_items,
                "current_screen": "roster_needs",
            },
        )
