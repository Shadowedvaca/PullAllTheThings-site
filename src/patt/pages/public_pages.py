"""Public page routes: landing page."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from patt.deps import get_db, get_page_member
from patt.services import campaign_service
from patt.templating import templates
from sv_common.db.models import GuildMember

logger = logging.getLogger(__name__)

router = APIRouter(tags=["public-pages"])


def _rank_level(member: GuildMember | None) -> int:
    if member is None:
        return 0
    return member.rank.level if member.rank else 0


@router.get("/", response_class=HTMLResponse)
async def landing_page(
    request: Request,
    db=Depends(get_db),
    current_member: GuildMember | None = Depends(get_page_member),
):
    viewer_level = _rank_level(current_member)

    # Load live campaigns visible to this viewer
    all_campaigns = await campaign_service.list_campaigns(db)
    live_campaigns = [
        c for c in all_campaigns
        if c.status == "live"
        and (c.min_rank_to_view is None or viewer_level >= c.min_rank_to_view)
    ]
    # Also show recently closed
    closed_campaigns = [
        c for c in all_campaigns
        if c.status == "closed"
        and (c.min_rank_to_view is None or viewer_level >= c.min_rank_to_view)
    ][:3]

    ctx = {
        "request": request,
        "current_member": current_member,
        "active_campaigns": live_campaigns,
        "live_campaigns": live_campaigns,
        "closed_campaigns": closed_campaigns,
    }
    return templates.TemplateResponse("public/index.html", ctx)
