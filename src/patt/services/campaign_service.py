"""Campaign lifecycle management service."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sv_common.db.models import Campaign, CampaignEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_campaign(
    db: AsyncSession,
    *,
    title: str,
    description: str | None = None,
    type: str = "ranked_choice",
    picks_per_voter: int = 3,
    min_rank_to_vote: int,
    min_rank_to_view: int | None = None,
    start_at: datetime,
    duration_hours: int,
    discord_channel_id: str | None = None,
    created_by: int | None = None,
    early_close_if_all_voted: bool = True,
    agent_enabled: bool = True,
    agent_chattiness: str = "normal",
) -> Campaign:
    """Create a new campaign in draft status."""
    campaign = Campaign(
        title=title,
        description=description,
        type=type,
        picks_per_voter=picks_per_voter,
        min_rank_to_vote=min_rank_to_vote,
        min_rank_to_view=min_rank_to_view,
        start_at=start_at,
        duration_hours=duration_hours,
        discord_channel_id=discord_channel_id,
        created_by_player_id=created_by,
        early_close_if_all_voted=early_close_if_all_voted,
        agent_enabled=agent_enabled,
        agent_chattiness=agent_chattiness,
        status="draft",
    )
    db.add(campaign)
    await db.flush()
    return campaign


async def get_campaign(db: AsyncSession, campaign_id: int) -> Campaign | None:
    """Fetch a campaign by ID, eagerly loading entries."""
    result = await db.execute(
        select(Campaign)
        .options(selectinload(Campaign.entries))
        .where(Campaign.id == campaign_id)
    )
    return result.scalar_one_or_none()


async def list_campaigns(
    db: AsyncSession, status: str | None = None
) -> list[Campaign]:
    """List campaigns, optionally filtered by status."""
    q = select(Campaign).options(selectinload(Campaign.entries))
    if status is not None:
        q = q.where(Campaign.status == status)
    result = await db.execute(q)
    return list(result.scalars().all())


async def update_campaign(
    db: AsyncSession, campaign_id: int, **kwargs
) -> Campaign:
    """Update campaign settings. Only allowed while campaign is draft."""
    campaign = await get_campaign(db, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    if campaign.status != "draft":
        raise ValueError("Campaign can only be edited while in draft status")
    for key, value in kwargs.items():
        setattr(campaign, key, value)
    await db.flush()
    return campaign


# ---------------------------------------------------------------------------
# Entry management
# ---------------------------------------------------------------------------


async def add_entry(
    db: AsyncSession,
    campaign_id: int,
    *,
    name: str,
    description: str | None = None,
    image_url: str | None = None,
    associated_member_id: int | None = None,
    sort_order: int = 0,
) -> CampaignEntry:
    """Add an entry to a campaign. Only allowed while campaign is draft."""
    campaign = await get_campaign(db, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    if campaign.status != "draft":
        raise ValueError("Entries can only be added while campaign is in draft status")
    entry = CampaignEntry(
        campaign_id=campaign_id,
        name=name,
        description=description,
        image_url=image_url,
        player_id=associated_member_id,  # renamed column; caller still uses old kwarg
        sort_order=sort_order,
    )
    db.add(entry)
    await db.flush()
    return entry


async def remove_entry(
    db: AsyncSession, campaign_id: int, entry_id: int
) -> bool:
    """Remove an entry from a campaign. Only allowed while campaign is draft."""
    campaign = await get_campaign(db, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    if campaign.status != "draft":
        raise ValueError("Entries can only be removed while campaign is in draft status")
    result = await db.execute(
        select(CampaignEntry).where(
            CampaignEntry.id == entry_id,
            CampaignEntry.campaign_id == campaign_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return False
    await db.delete(entry)
    await db.flush()
    return True


async def update_entry(
    db: AsyncSession, entry_id: int, **kwargs
) -> CampaignEntry:
    """Update a campaign entry. Only allowed while parent campaign is draft."""
    result = await db.execute(
        select(CampaignEntry).where(CampaignEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ValueError(f"Entry {entry_id} not found")
    campaign = await get_campaign(db, entry.campaign_id)
    if campaign is None or campaign.status != "draft":
        raise ValueError("Entries can only be edited while campaign is in draft status")
    for key, value in kwargs.items():
        setattr(entry, key, value)
    await db.flush()
    return entry


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


async def activate_campaign(db: AsyncSession, campaign_id: int) -> Campaign:
    """Transition campaign from draft to live."""
    campaign = await get_campaign(db, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    if campaign.status != "draft":
        raise ValueError(f"Campaign is already {campaign.status}, cannot activate")
    now = datetime.now(timezone.utc)
    if campaign.start_at <= now:
        campaign.start_at = now
    campaign.status = "live"
    await db.flush()
    return campaign


async def close_campaign(db: AsyncSession, campaign_id: int) -> Campaign:
    """Close a live campaign and calculate final results."""
    from patt.services.vote_service import calculate_results

    campaign = await get_campaign(db, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    if campaign.status != "live":
        raise ValueError(f"Campaign is {campaign.status}, cannot close")
    campaign.status = "closed"
    await db.flush()
    await calculate_results(db, campaign_id)
    return campaign


async def delete_campaign(db: AsyncSession, campaign_id: int) -> bool:
    """Delete a campaign. Only draft campaigns may be deleted."""
    campaign = await get_campaign(db, campaign_id)
    if campaign is None:
        return False
    if campaign.status == "live":
        raise ValueError("Cannot delete a live campaign — close it first")
    await db.delete(campaign)
    await db.flush()
    return True


async def get_campaign_status(db: AsyncSession, campaign_id: int) -> dict:
    """Return a status summary dict for the campaign."""
    from sqlalchemy import func
    from sv_common.db.models import Vote

    campaign = await get_campaign(db, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")

    now = datetime.now(timezone.utc)
    time_remaining = None
    if campaign.status == "live":
        end_at = campaign.start_at + timedelta(hours=campaign.duration_hours)
        time_remaining = max(0.0, (end_at - now).total_seconds())

    votes_q = await db.execute(
        select(func.count(func.distinct(Vote.player_id))).where(
            Vote.campaign_id == campaign_id
        )
    )
    votes_cast = votes_q.scalar_one()

    return {
        "id": campaign.id,
        "title": campaign.title,
        "status": campaign.status,
        "time_remaining_seconds": time_remaining,
        "votes_cast": votes_cast,
        "start_at": campaign.start_at.isoformat(),
        "duration_hours": campaign.duration_hours,
    }


# ---------------------------------------------------------------------------
# Background task: campaign status checker
# ---------------------------------------------------------------------------


async def check_campaign_statuses(session_factory) -> None:
    """Background asyncio task: runs every 60s to transition campaign statuses."""
    while True:
        await asyncio.sleep(60)
        try:
            async with session_factory() as session:
                now = datetime.now(timezone.utc)

                draft_result = await session.execute(
                    select(Campaign).where(
                        Campaign.status == "draft",
                        Campaign.start_at <= now,
                    )
                )
                for campaign in draft_result.scalars().all():
                    campaign.status = "live"
                    logger.info("Background: auto-activated campaign %d", campaign.id)

                live_result = await session.execute(
                    select(Campaign).where(Campaign.status == "live")
                )
                for campaign in live_result.scalars().all():
                    end_at = campaign.start_at + timedelta(hours=campaign.duration_hours)
                    if now >= end_at:
                        campaign.status = "closed"
                        await session.flush()
                        from patt.services.vote_service import calculate_results
                        await calculate_results(session, campaign.id)
                        logger.info(
                            "Background: auto-closed expired campaign %d", campaign.id
                        )
                    elif campaign.early_close_if_all_voted:
                        from patt.services.vote_service import check_early_close
                        await check_early_close(session, campaign.id)

                await session.commit()
        except asyncio.CancelledError:
            logger.info("Campaign status checker cancelled")
            break
        except Exception as exc:
            logger.error("Campaign status checker error: %s", exc)
