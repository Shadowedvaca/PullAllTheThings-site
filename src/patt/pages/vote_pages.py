"""Vote page routes."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from patt.deps import get_db, get_page_member
from patt.services import campaign_service, vote_service
from patt.templating import templates
from sv_common.db.models import GuildMember

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vote-pages"])


def _rank_level(member: GuildMember | None) -> int:
    if member is None:
        return 0
    return member.rank.level if member.rank else 0


def _campaign_end_ts(campaign) -> int | None:
    """Return Unix timestamp of campaign end, or None."""
    if campaign.status != "live":
        return None
    end_at = campaign.start_at + timedelta(hours=campaign.duration_hours)
    return int(end_at.timestamp())


async def _base_ctx(request: Request, member: GuildMember | None, db: AsyncSession) -> dict:
    """Build base context dict including active campaigns for nav."""
    active = await campaign_service.list_campaigns(db, status="live")
    viewer_level = _rank_level(member)
    visible_active = [
        c for c in active
        if c.min_rank_to_view is None or viewer_level >= c.min_rank_to_view
    ]
    return {
        "request": request,
        "current_member": member,
        "active_campaigns": visible_active,
    }


@router.get("/vote/{campaign_id}", response_class=HTMLResponse)
async def vote_page(
    request: Request,
    campaign_id: int,
    vote_error: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_member: GuildMember | None = Depends(get_page_member),
):
    campaign = await campaign_service.get_campaign(db, campaign_id)
    if campaign is None:
        return templates.TemplateResponse(
            "public/404.html",
            {**(await _base_ctx(request, current_member, db)), "message": "Campaign not found."},
            status_code=404,
        )

    viewer_level = _rank_level(current_member)

    # Visibility check
    if campaign.min_rank_to_view and viewer_level < campaign.min_rank_to_view:
        if current_member is None:
            return RedirectResponse(url=f"/login?next=/vote/{campaign_id}", status_code=302)
        # Logged in but wrong rank
        return templates.TemplateResponse(
            "public/403.html",
            {**(await _base_ctx(request, current_member, db)), "message": "You don't have permission to view this campaign."},
            status_code=403,
        )

    ctx = await _base_ctx(request, current_member, db)
    ctx["campaign"] = campaign
    ctx["end_ts"] = _campaign_end_ts(campaign)
    ctx["vote_error"] = vote_error
    ctx["results"] = []
    ctx["vote_stats"] = None
    ctx["my_picks"] = []

    # Determine page state
    if campaign.status == "draft":
        ctx["page_state"] = "upcoming"
        return templates.TemplateResponse("vote/campaign.html", ctx)

    if campaign.status == "closed":
        # Show final results (public if no view restriction)
        try:
            results = await vote_service.get_results(db, campaign_id)
            stats = await vote_service.get_vote_stats(db, campaign_id)
            ctx["results"] = results
            ctx["vote_stats"] = stats
        except Exception:
            pass
        ctx["page_state"] = "closed"
        return templates.TemplateResponse("vote/campaign.html", ctx)

    # Campaign is live
    if current_member is None:
        # Not logged in — if campaign is public (no min_rank_to_view), show public state
        ctx["page_state"] = "public"
        return templates.TemplateResponse("vote/campaign.html", ctx)

    # Logged in — check if already voted
    has_voted = await vote_service.has_member_voted(db, campaign_id, current_member.id)

    if has_voted:
        # Show their picks + live standings
        my_votes = await vote_service.get_member_vote(db, campaign_id, current_member.id)
        # Build picks list with entry names
        entry_map = {e.id: e.name for e in campaign.entries}
        my_picks = [
            {"rank": v.rank, "entry_name": entry_map.get(v.entry_id, f"Entry {v.entry_id}")}
            for v in (my_votes or [])
        ]
        results = await vote_service.get_results(db, campaign_id)
        stats = await vote_service.get_vote_stats(db, campaign_id)
        ctx["my_picks"] = my_picks
        ctx["results"] = results
        ctx["vote_stats"] = stats
        ctx["page_state"] = "voted"
        return templates.TemplateResponse("vote/campaign.html", ctx)

    # Can they vote?
    if viewer_level >= campaign.min_rank_to_vote:
        ctx["page_state"] = "can_vote"
    else:
        ctx["page_state"] = "view_only"

    return templates.TemplateResponse("vote/campaign.html", ctx)


@router.post("/vote/{campaign_id}", response_class=HTMLResponse)
async def vote_post(
    request: Request,
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_member: GuildMember | None = Depends(get_page_member),
):
    """Handle form-based vote submission."""
    if current_member is None:
        return RedirectResponse(url=f"/login?next=/vote/{campaign_id}", status_code=302)

    form_data = await request.form()

    campaign = await campaign_service.get_campaign(db, campaign_id)
    if campaign is None:
        return RedirectResponse(url="/", status_code=302)

    picks_per_voter = campaign.picks_per_voter

    # Extract picks from form: pick_entry_0, pick_rank_0, pick_entry_1, ...
    picks = []
    for i in range(picks_per_voter):
        entry_id_str = form_data.get(f"pick_entry_{i}")
        rank_str = form_data.get(f"pick_rank_{i}")
        if entry_id_str and rank_str:
            try:
                picks.append({"entry_id": int(entry_id_str), "rank": int(rank_str)})
            except (ValueError, TypeError):
                pass

    if len(picks) != picks_per_voter:
        return RedirectResponse(
            url=f"/vote/{campaign_id}?vote_error=Please+select+exactly+{picks_per_voter}+picks.",
            status_code=302,
        )

    try:
        await vote_service.cast_vote(db, campaign_id=campaign_id, member_id=current_member.id, picks=picks)
    except ValueError as e:
        import urllib.parse
        error_msg = urllib.parse.quote(str(e))
        return RedirectResponse(url=f"/vote/{campaign_id}?vote_error={error_msg}", status_code=302)

    return RedirectResponse(url=f"/vote/{campaign_id}", status_code=302)
