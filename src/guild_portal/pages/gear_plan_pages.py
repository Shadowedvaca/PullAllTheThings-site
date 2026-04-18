"""Gear Plan page routes — member gear plan."""

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


# ---------------------------------------------------------------------------
# GET /gear-plan  — member personal gear plan (now redirects to /my-characters)
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


