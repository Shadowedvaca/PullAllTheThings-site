"""Vote casting, validation, and results calculation service."""

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sv_common.db.models import (
    Campaign,
    CampaignEntry,
    CampaignResult,
    GuildMember,
    GuildRank,
    Vote,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure scoring logic (no DB — testable in isolation)
# ---------------------------------------------------------------------------


def compute_scores(votes: list[dict]) -> dict[int, dict]:
    """Compute ranked-choice scores from a list of vote dicts.

    Args:
        votes: list of {"entry_id": int, "rank": int}

    Returns:
        {entry_id: {"first": N, "second": N, "third": N, "weighted_score": N}}

    Scoring: 1st = 3 pts, 2nd = 2 pts, 3rd = 1 pt.
    """
    scores: dict[int, dict] = {}
    for vote in votes:
        entry_id = vote["entry_id"]
        rank = vote["rank"]
        if entry_id not in scores:
            scores[entry_id] = {
                "first": 0,
                "second": 0,
                "third": 0,
                "weighted_score": 0,
            }
        if rank == 1:
            scores[entry_id]["first"] += 1
            scores[entry_id]["weighted_score"] += 3
        elif rank == 2:
            scores[entry_id]["second"] += 1
            scores[entry_id]["weighted_score"] += 2
        elif rank == 3:
            scores[entry_id]["third"] += 1
            scores[entry_id]["weighted_score"] += 1
    return scores


def rank_results(
    entry_scores: dict[int, dict]
) -> list[tuple[int, dict]]:
    """Sort entries by weighted_score desc, then first_place_count desc.

    Returns a list of (entry_id, score_dict) in final rank order.
    """
    return sorted(
        entry_scores.items(),
        key=lambda item: (-item[1]["weighted_score"], -item[1]["first"]),
    )


# ---------------------------------------------------------------------------
# Vote operations
# ---------------------------------------------------------------------------


async def cast_vote(
    db: AsyncSession,
    campaign_id: int,
    member_id: int,
    picks: list[dict],
) -> list[Vote]:
    """Cast ranked-choice votes for a campaign.

    picks: [{"entry_id": int, "rank": int}, ...]

    Validates: campaign live, member rank, no duplicate vote, correct pick
    count, no duplicate entries or ranks, all entries belong to campaign.
    """
    # Load campaign
    campaign_result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )
    campaign = campaign_result.scalar_one_or_none()
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    if campaign.status != "live":
        raise ValueError(f"Campaign is {campaign.status}, voting not allowed")

    # Check member rank
    member_result = await db.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .where(GuildMember.id == member_id)
    )
    member = member_result.scalar_one_or_none()
    if member is None:
        raise ValueError(f"Member {member_id} not found")
    rank_level = member.rank.level if member.rank else 0
    if rank_level < campaign.min_rank_to_vote:
        raise ValueError(
            f"Member rank {rank_level} does not meet minimum required rank "
            f"{campaign.min_rank_to_vote}"
        )

    # Check if already voted
    existing_result = await db.execute(
        select(Vote).where(
            Vote.campaign_id == campaign_id,
            Vote.member_id == member_id,
        )
    )
    if existing_result.scalars().first() is not None:
        raise ValueError("Member has already voted in this campaign")

    # Validate pick count
    if len(picks) != campaign.picks_per_voter:
        raise ValueError(
            f"Expected {campaign.picks_per_voter} picks, got {len(picks)}"
        )

    # Validate no duplicate entries
    entry_ids = [p["entry_id"] for p in picks]
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError("Duplicate entries in picks")

    # Validate no duplicate ranks
    ranks = [p["rank"] for p in picks]
    if len(ranks) != len(set(ranks)):
        raise ValueError("Duplicate ranks in picks")

    # Validate all entries belong to this campaign
    valid_q = await db.execute(
        select(CampaignEntry.id).where(CampaignEntry.campaign_id == campaign_id)
    )
    valid_entry_ids = {row[0] for row in valid_q.all()}
    for eid in entry_ids:
        if eid not in valid_entry_ids:
            raise ValueError(
                f"Entry {eid} does not belong to campaign {campaign_id}"
            )

    # Cast votes
    votes = []
    for pick in picks:
        vote = Vote(
            campaign_id=campaign_id,
            member_id=member_id,
            entry_id=pick["entry_id"],
            rank=pick["rank"],
        )
        db.add(vote)
        votes.append(vote)
    await db.flush()
    return votes


async def get_member_vote(
    db: AsyncSession, campaign_id: int, member_id: int
) -> list[Vote] | None:
    """Return member's votes for this campaign, or None if not voted."""
    result = await db.execute(
        select(Vote).where(
            Vote.campaign_id == campaign_id,
            Vote.member_id == member_id,
        )
    )
    votes = list(result.scalars().all())
    return votes if votes else None


async def has_member_voted(
    db: AsyncSession, campaign_id: int, member_id: int
) -> bool:
    result = await db.execute(
        select(Vote).where(
            Vote.campaign_id == campaign_id,
            Vote.member_id == member_id,
        )
    )
    return result.scalars().first() is not None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


async def calculate_results(
    db: AsyncSession, campaign_id: int
) -> list[CampaignResult]:
    """Calculate ranked-choice results and store in campaign_results table."""
    # Clear any existing results
    await db.execute(
        delete(CampaignResult).where(CampaignResult.campaign_id == campaign_id)
    )

    # Load all votes
    votes_result = await db.execute(
        select(Vote).where(Vote.campaign_id == campaign_id)
    )
    vote_dicts = [
        {"entry_id": v.entry_id, "rank": v.rank}
        for v in votes_result.scalars().all()
    ]
    scores = compute_scores(vote_dicts)

    # Load all entries (to include zero-vote entries)
    entries_result = await db.execute(
        select(CampaignEntry).where(CampaignEntry.campaign_id == campaign_id)
    )
    entries = entries_result.scalars().all()

    # Merge zero-score entries into the scores dict
    for entry in entries:
        if entry.id not in scores:
            scores[entry.id] = {
                "first": 0,
                "second": 0,
                "third": 0,
                "weighted_score": 0,
            }

    # Sort by score
    ranked = rank_results(scores)

    results = []
    for final_rank, (entry_id, entry_scores) in enumerate(ranked, start=1):
        row = CampaignResult(
            campaign_id=campaign_id,
            entry_id=entry_id,
            first_place_count=entry_scores["first"],
            second_place_count=entry_scores["second"],
            third_place_count=entry_scores["third"],
            weighted_score=entry_scores["weighted_score"],
            final_rank=final_rank,
        )
        db.add(row)
        results.append(row)

    await db.flush()
    return results


async def get_results(db: AsyncSession, campaign_id: int) -> list[dict]:
    """Return sorted results with entry info for display."""
    results_q = await db.execute(
        select(CampaignResult)
        .options(selectinload(CampaignResult.entry))
        .where(CampaignResult.campaign_id == campaign_id)
        .order_by(CampaignResult.final_rank)
    )
    results = results_q.scalars().all()
    return [
        {
            "entry": {
                "id": r.entry.id,
                "name": r.entry.name,
                "image_url": r.entry.image_url,
                "description": r.entry.description,
            },
            "first_place_count": r.first_place_count,
            "second_place_count": r.second_place_count,
            "third_place_count": r.third_place_count,
            "weighted_score": r.weighted_score,
            "final_rank": r.final_rank,
        }
        for r in results
    ]


# ---------------------------------------------------------------------------
# Vote stats & early close
# ---------------------------------------------------------------------------


async def get_vote_stats(db: AsyncSession, campaign_id: int) -> dict:
    """Return voting statistics: eligible count, voted count, percent, all_voted."""
    campaign_result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )
    campaign = campaign_result.scalar_one_or_none()
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")

    # Count eligible members (rank level >= min_rank_to_vote)
    eligible_q = await db.execute(
        select(func.count(GuildMember.id))
        .join(GuildRank, GuildMember.rank_id == GuildRank.id)
        .where(GuildRank.level >= campaign.min_rank_to_vote)
    )
    total_eligible = eligible_q.scalar_one()

    # Count distinct members who have voted
    voted_q = await db.execute(
        select(func.count(func.distinct(Vote.member_id))).where(
            Vote.campaign_id == campaign_id
        )
    )
    total_voted = voted_q.scalar_one()

    percent_voted = (
        round(total_voted / total_eligible * 100, 1) if total_eligible > 0 else 0.0
    )
    all_voted = total_eligible > 0 and total_voted >= total_eligible

    return {
        "total_eligible": total_eligible,
        "total_voted": total_voted,
        "percent_voted": percent_voted,
        "all_voted": all_voted,
    }


async def check_early_close(db: AsyncSession, campaign_id: int) -> bool:
    """Close campaign early if all eligible members have voted.

    Returns True if the campaign was closed, False otherwise.
    """
    campaign_result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id)
    )
    campaign = campaign_result.scalar_one_or_none()
    if (
        campaign is None
        or campaign.status != "live"
        or not campaign.early_close_if_all_voted
    ):
        return False

    stats = await get_vote_stats(db, campaign_id)
    if stats["all_voted"]:
        from patt.services.campaign_service import close_campaign

        await close_campaign(db, campaign_id)
        logger.info(
            "Campaign %d closed early — all eligible members voted", campaign_id
        )
        return True
    return False
