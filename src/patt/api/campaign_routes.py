"""Campaign API routes.

Admin routes (Officer+):
    POST   /api/v1/admin/campaigns
    PATCH  /api/v1/admin/campaigns/{id}
    POST   /api/v1/admin/campaigns/{id}/entries
    DELETE /api/v1/admin/campaigns/{id}/entries/{eid}
    PATCH  /api/v1/admin/campaign-entries/{eid}
    POST   /api/v1/admin/campaigns/{id}/activate
    POST   /api/v1/admin/campaigns/{id}/close
    GET    /api/v1/admin/campaigns/{id}/stats

Vote routes (authenticated, rank-gated):
    POST /api/v1/campaigns/{id}/vote
    GET  /api/v1/campaigns/{id}/my-vote

Public routes (rank-gated for visibility):
    GET  /api/v1/campaigns
    GET  /api/v1/campaigns/{id}
    GET  /api/v1/campaigns/{id}/results
    GET  /api/v1/campaigns/{id}/results/live
"""

import logging
from datetime import datetime

import jwt as _jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from patt.config import get_settings
from patt.deps import get_current_member, get_db, require_rank
from patt.services import campaign_service, vote_service
from sv_common.db.models import Campaign, GuildMember

_optional_bearer = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CampaignCreate(BaseModel):
    title: str
    description: str | None = None
    type: str = "ranked_choice"
    picks_per_voter: int = 3
    min_rank_to_vote: int
    min_rank_to_view: int | None = None
    start_at: datetime
    duration_hours: int
    discord_channel_id: str | None = None
    early_close_if_all_voted: bool = True


class CampaignUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    picks_per_voter: int | None = None
    min_rank_to_vote: int | None = None
    min_rank_to_view: int | None = None
    start_at: datetime | None = None
    duration_hours: int | None = None
    discord_channel_id: str | None = None
    early_close_if_all_voted: bool | None = None


class EntryCreate(BaseModel):
    name: str
    description: str | None = None
    image_url: str | None = None
    associated_member_id: int | None = None
    sort_order: int = 0


class EntryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    sort_order: int | None = None


class VoteSubmit(BaseModel):
    picks: list[dict]  # [{"entry_id": int, "rank": int}, ...]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _campaign_dict(campaign: Campaign) -> dict:
    return {
        "id": campaign.id,
        "title": campaign.title,
        "description": campaign.description,
        "type": campaign.type,
        "picks_per_voter": campaign.picks_per_voter,
        "min_rank_to_vote": campaign.min_rank_to_vote,
        "min_rank_to_view": campaign.min_rank_to_view,
        "start_at": campaign.start_at.isoformat(),
        "duration_hours": campaign.duration_hours,
        "status": campaign.status,
        "early_close_if_all_voted": campaign.early_close_if_all_voted,
        "discord_channel_id": campaign.discord_channel_id,
        "created_by": campaign.created_by,
        "entries": [
            {
                "id": e.id,
                "name": e.name,
                "description": e.description,
                "image_url": e.image_url,
                "sort_order": e.sort_order,
                "associated_member_id": e.associated_member_id,
            }
            for e in (campaign.entries or [])
        ],
    }


# ---------------------------------------------------------------------------
# Admin campaign routes (Officer+)
# ---------------------------------------------------------------------------

admin_campaign_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-campaigns"],
    dependencies=[Depends(require_rank(4))],
)


@admin_campaign_router.post("/campaigns")
async def admin_create_campaign(
    body: CampaignCreate,
    admin: GuildMember = Depends(require_rank(4)),
    db: AsyncSession = Depends(get_db),
):
    try:
        campaign = await campaign_service.create_campaign(
            db,
            title=body.title,
            description=body.description,
            type=body.type,
            picks_per_voter=body.picks_per_voter,
            min_rank_to_vote=body.min_rank_to_vote,
            min_rank_to_view=body.min_rank_to_view,
            start_at=body.start_at,
            duration_hours=body.duration_hours,
            discord_channel_id=body.discord_channel_id,
            early_close_if_all_voted=body.early_close_if_all_voted,
            created_by=admin.id,
        )
        return {"ok": True, "data": _campaign_dict(campaign)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@admin_campaign_router.patch("/campaigns/{campaign_id}")
async def admin_update_campaign(
    campaign_id: int,
    body: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        updates = body.model_dump(exclude_none=True)
        campaign = await campaign_service.update_campaign(db, campaign_id, **updates)
        return {"ok": True, "data": _campaign_dict(campaign)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@admin_campaign_router.post("/campaigns/{campaign_id}/entries")
async def admin_add_entry(
    campaign_id: int,
    body: EntryCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        entry = await campaign_service.add_entry(
            db,
            campaign_id,
            name=body.name,
            description=body.description,
            image_url=body.image_url,
            associated_member_id=body.associated_member_id,
            sort_order=body.sort_order,
        )
        return {
            "ok": True,
            "data": {
                "id": entry.id,
                "campaign_id": entry.campaign_id,
                "name": entry.name,
                "description": entry.description,
                "image_url": entry.image_url,
                "sort_order": entry.sort_order,
            },
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@admin_campaign_router.delete("/campaigns/{campaign_id}/entries/{entry_id}")
async def admin_remove_entry(
    campaign_id: int,
    entry_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        deleted = await campaign_service.remove_entry(db, campaign_id, entry_id)
        if not deleted:
            return {"ok": False, "error": f"Entry {entry_id} not found in campaign {campaign_id}"}
        return {"ok": True, "data": {"deleted": True}}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@admin_campaign_router.patch("/campaign-entries/{entry_id}")
async def admin_update_entry(
    entry_id: int,
    body: EntryUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        updates = body.model_dump(exclude_none=True)
        entry = await campaign_service.update_entry(db, entry_id, **updates)
        return {
            "ok": True,
            "data": {
                "id": entry.id,
                "name": entry.name,
                "image_url": entry.image_url,
                "sort_order": entry.sort_order,
            },
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@admin_campaign_router.post("/campaigns/{campaign_id}/activate")
async def admin_activate_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        campaign = await campaign_service.activate_campaign(db, campaign_id)
        return {"ok": True, "data": _campaign_dict(campaign)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@admin_campaign_router.post("/campaigns/{campaign_id}/close")
async def admin_close_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        campaign = await campaign_service.close_campaign(db, campaign_id)
        return {"ok": True, "data": _campaign_dict(campaign)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@admin_campaign_router.get("/campaigns/{campaign_id}/stats")
async def admin_campaign_stats(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        stats = await vote_service.get_vote_stats(db, campaign_id)
        return {"ok": True, "data": stats}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Vote routes (authenticated members)
# ---------------------------------------------------------------------------

vote_router = APIRouter(
    prefix="/api/v1/campaigns",
    tags=["votes"],
)


@vote_router.post("/{campaign_id}/vote")
async def cast_vote(
    campaign_id: int,
    body: VoteSubmit,
    member: GuildMember = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    try:
        votes = await vote_service.cast_vote(
            db,
            campaign_id=campaign_id,
            member_id=member.id,
            picks=body.picks,
        )
        return {
            "ok": True,
            "data": {
                "campaign_id": campaign_id,
                "member_id": member.id,
                "votes": [
                    {"entry_id": v.entry_id, "rank": v.rank} for v in votes
                ],
            },
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@vote_router.get("/{campaign_id}/my-vote")
async def get_my_vote(
    campaign_id: int,
    member: GuildMember = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    votes = await vote_service.get_member_vote(db, campaign_id, member.id)
    if votes is None:
        raise HTTPException(status_code=404, detail="You have not voted in this campaign")
    return {
        "ok": True,
        "data": {
            "campaign_id": campaign_id,
            "votes": [{"entry_id": v.entry_id, "rank": v.rank} for v in votes],
        },
    }


# ---------------------------------------------------------------------------
# Public campaign routes (rank-gated for visibility)
# ---------------------------------------------------------------------------

public_campaign_router = APIRouter(
    prefix="/api/v1/campaigns",
    tags=["campaigns"],
)


def _viewer_rank_level(member: GuildMember | None) -> int:
    """Return the rank level of the viewer, or 0 for anonymous."""
    if member is None:
        return 0
    return member.rank.level if member.rank else 0


async def _get_optional_member(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db),
) -> GuildMember | None:
    """Try to extract the current member from JWT; return None if unauthenticated."""
    if credentials is None:
        return None
    try:
        settings = get_settings()
        payload = _jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        member_id = payload.get("member_id")
        if member_id is None:
            return None
        result = await db.execute(
            select(GuildMember).where(GuildMember.id == member_id)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None


@public_campaign_router.get("")
async def list_campaigns(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    member: GuildMember | None = Depends(_get_optional_member),
):
    """List campaigns visible to the current viewer."""
    viewer_level = _viewer_rank_level(member)
    campaigns = await campaign_service.list_campaigns(db, status=status)
    visible = [
        c for c in campaigns
        if c.min_rank_to_view is None or viewer_level >= c.min_rank_to_view
    ]
    return {"ok": True, "data": [_campaign_dict(c) for c in visible]}


@public_campaign_router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    member: GuildMember | None = Depends(_get_optional_member),
):
    """Get campaign detail. Respects min_rank_to_view."""
    campaign = await campaign_service.get_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    viewer_level = _viewer_rank_level(member)
    if campaign.min_rank_to_view and viewer_level < campaign.min_rank_to_view:
        raise HTTPException(status_code=403, detail="Insufficient rank to view this campaign")
    return {"ok": True, "data": _campaign_dict(campaign)}


@public_campaign_router.get("/{campaign_id}/results")
async def get_campaign_results(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    member: GuildMember | None = Depends(_get_optional_member),
):
    """Get final results.

    Visible if:
    - campaign is closed, OR member has already voted
    - Respects min_rank_to_view
    """
    campaign = await campaign_service.get_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    viewer_level = _viewer_rank_level(member)
    if campaign.min_rank_to_view and viewer_level < campaign.min_rank_to_view:
        raise HTTPException(status_code=403, detail="Insufficient rank to view this campaign")

    # Results visible if: campaign closed OR member has voted
    if campaign.status != "closed":
        if member is None:
            raise HTTPException(status_code=403, detail="Results not yet available")
        has_voted = await vote_service.has_member_voted(db, campaign_id, member.id)
        if not has_voted:
            raise HTTPException(status_code=403, detail="Vote to see live standings")

    results = await vote_service.get_results(db, campaign_id)
    return {"ok": True, "data": results}


@public_campaign_router.get("/{campaign_id}/results/live")
async def get_live_standings(
    campaign_id: int,
    member: GuildMember = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """Get live standings — only visible to members who have already voted."""
    campaign = await campaign_service.get_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    viewer_level = _viewer_rank_level(member)
    if campaign.min_rank_to_view and viewer_level < campaign.min_rank_to_view:
        raise HTTPException(status_code=403, detail="Insufficient rank to view this campaign")

    has_voted = await vote_service.has_member_voted(db, campaign_id, member.id)
    if not has_voted:
        raise HTTPException(status_code=403, detail="You must vote before seeing live standings")

    results = await vote_service.get_results(db, campaign_id)
    return {"ok": True, "data": results}
