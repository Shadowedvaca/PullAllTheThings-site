"""Member page for the seasonal specialization wheel."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.deps import get_db, get_page_member
from guild_portal.templating import templates
from sv_common.db.models import Player

router = APIRouter(tags=["spec-wheel-pages"])


@router.get("/spec-wheel", response_class=HTMLResponse)
async def spec_wheel_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/spec-wheel", status_code=302)
    return templates.TemplateResponse(
        "member/spec_wheel.html",
        {
            "request": request,
            "current_member": current_member,
            "active_campaigns": [],
        },
    )
