"""Contest agent service — monitors live campaigns and posts Discord updates.

Runs every 5 minutes while any campaign is live. Detects milestones (lead
changes, participation thresholds, time warnings) and posts personality-driven
messages to the campaign's Discord channel.
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import (
    Campaign,
    CampaignEntry,
    CampaignResult,
    ContestAgentLog,
    DiscordConfig,
    Vote,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chattiness configuration
# ---------------------------------------------------------------------------

CHATTINESS_TRIGGERS: dict[str, set[str]] = {
    "quiet": {"campaign_launch", "campaign_closed"},
    "normal": {
        "campaign_launch",
        "first_vote",
        "milestone_50",
        "final_stretch",
        "last_call",
        "all_voted",
        "campaign_closed",
    },
    "hype": {
        "campaign_launch",
        "first_vote",
        "lead_change",
        "milestone_25",
        "milestone_50",
        "milestone_75",
        "final_stretch",
        "last_call",
        "all_voted",
        "campaign_closed",
    },
}

# Priority order — most exciting first
MILESTONE_PRIORITY = [
    "all_voted",
    "campaign_closed",
    "lead_change",
    "last_call",
    "final_stretch",
    "milestone_75",
    "milestone_50",
    "milestone_25",
    "first_vote",
    "campaign_launch",
]

PATT_GOLD = 0xD4A84B

# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, list[str]] = {
    "campaign_launch": [
        (
            "🎉 **{title}** is NOW OPEN for voting!\n\n"
            "{description}\n\n"
            "🗳️ Cast your vote: {vote_url}\n"
            "📅 Voting closes: {close_date}"
        ),
        (
            "Hear ye, hear ye! 📜\n\n"
            "**{title}** has begun! Your top 3 picks determine the winner.\n\n"
            "Vote now: {vote_url}\n"
            "You have until {close_date} — don't sleep on it!"
        ),
    ],
    "first_vote": [
        (
            "And we're off! The first vote has been cast in **{title}**. "
            "The race is on! 🏁\n\nHaven't voted yet? {vote_url}"
        ),
        "Someone couldn't wait! First vote is in for **{title}**. Who's next? 🗳️",
    ],
    "lead_change": [
        (
            "🔥 **{new_leader}** just took the lead from **{old_leader}**! "
            "The score is {new_score} to {old_score}.\n\n"
            "Think you can change the standings? {vote_url}"
        ),
        (
            "Plot twist! 😱 **{new_leader}** surges ahead of **{old_leader}**!\n\n"
            "Current standings:\n"
            "🥇 {new_leader} — {new_score} pts\n"
            "🥈 {old_leader} — {old_score} pts\n\n"
            "Make your voice heard: {vote_url}"
        ),
        (
            "{old_leader}'s in the rear view mirror now! 🪞 "
            "**{new_leader}** takes the top spot with {new_score} points!\n\n"
            "Still time to vote: {vote_url}"
        ),
    ],
    "milestone_25": [
        (
            "A quarter of the guild has spoken! {voted_count} of {total_count} votes are in.\n\n"
            "Current leader: **{leader_name}** with {leader_score} points.\n\n"
            "Join them: {vote_url}"
        ),
    ],
    "milestone_50": [
        (
            "We're at the halfway mark! 🎯 {voted_count} of {total_count} members have voted.\n\n"
            "**{leader_name}** leads with {leader_score} points.\n\n"
            "Don't let your vote go to waste: {vote_url}"
        ),
    ],
    "milestone_75": [
        (
            "Three quarters in! 📊 {voted_count} of {total_count} votes cast.\n\n"
            "{remaining_count} members still haven't voted — you know who you are 👀\n\n"
            "Current standings:\n"
            "🥇 {first_name} — {first_score}\n"
            "🥈 {second_name} — {second_score}\n"
            "🥉 {third_name} — {third_score}"
        ),
    ],
    "final_stretch": [
        (
            "⏰ **24 hours left** to vote in **{title}**!\n\n"
            "{remaining_count} members still need to cast their votes.\n\n"
            "Current leader: **{leader_name}** ({leader_score} pts) — but it's not over yet!\n\n"
            "Last chance: {vote_url}"
        ),
    ],
    "last_call": [
        (
            "🚨 **LAST CALL!** Voting for **{title}** closes in ONE HOUR!\n\n"
            "If you haven't voted, now's the time: {vote_url}\n\n"
            "Current standings:\n"
            "🥇 {first_name} — {first_score}\n"
            "🥈 {second_name} — {second_score}\n"
            "🥉 {third_name} — {third_score}"
        ),
    ],
    "all_voted": [
        (
            "Every eligible member has voted! 🎊 That's {total_count} out of {total_count} "
            "— a clean sweep!\n\n"
            "No need to wait — **the results are in!**\n\n"
            "🏆 **{title}** Winner: **{first_name}**!\n\n"
            "🥇 **{first_name}** — {first_score} points\n"
            "🥈 {second_name} — {second_score} points\n"
            "🥉 {third_name} — {third_score} points\n\n"
            "Full results: {results_url}"
        ),
    ],
    "campaign_closed": [
        (
            "🏆 **{title}** — THE RESULTS ARE IN!\n\n"
            "{total_voters} members cast their votes. Here's how it shook out:\n\n"
            "🥇 **{first_name}** — {first_score} points\n"
            "🥈 {second_name} — {second_score} points\n"
            "🥉 {third_name} — {third_score} points\n\n"
            "Congratulations to **{first_name}**! 🎉\n\n"
            "See the full breakdown: {results_url}"
        ),
    ],
}

# Fallback strings for standings when entries are missing
_UNKNOWN = "Unknown"
_NO_SCORE = "0"


# ---------------------------------------------------------------------------
# Pure functions (unit-testable, no DB / Discord)
# ---------------------------------------------------------------------------


def get_allowed_events(chattiness: str) -> set[str]:
    """Return the set of event types active for a given chattiness level."""
    return CHATTINESS_TRIGGERS.get(chattiness, CHATTINESS_TRIGGERS["normal"])


def detect_milestone(
    campaign_status: str,
    stats: dict,
    time_remaining_hours: float,
    logged_events: set[str],
    chattiness: str,
    current_leader_id: Optional[int] = None,
    previous_leader_id: Optional[int] = None,
) -> Optional[str]:
    """Determine the highest-priority new milestone to post.

    Args:
        campaign_status: "live" or "closed"
        stats: {total_eligible, total_voted, percent_voted, all_voted}
        time_remaining_hours: hours until/since close (0 if already closed)
        logged_events: event_type strings already posted for this campaign
        chattiness: "quiet" | "normal" | "hype"
        current_leader_id: entry ID currently leading (None if no votes)
        previous_leader_id: entry ID that was leading at last check (for lead_change)

    Returns:
        event_type string to post, or None
    """
    allowed = get_allowed_events(chattiness)
    detected: set[str] = set()

    if campaign_status == "closed":
        if stats.get("all_voted") and "all_voted" not in logged_events:
            detected.add("all_voted")
        elif "campaign_closed" not in logged_events and "all_voted" not in logged_events:
            detected.add("campaign_closed")
    elif campaign_status == "live":
        # Launch: fire once, before any other votes-based events
        if "campaign_launch" not in logged_events:
            detected.add("campaign_launch")

        total_voted = stats.get("total_voted", 0)
        pct = stats.get("percent_voted", 0)

        if total_voted >= 1 and "first_vote" not in logged_events:
            detected.add("first_vote")

        if pct >= 25 and "milestone_25" not in logged_events:
            detected.add("milestone_25")
        if pct >= 50 and "milestone_50" not in logged_events:
            detected.add("milestone_50")
        if pct >= 75 and "milestone_75" not in logged_events:
            detected.add("milestone_75")

        if stats.get("all_voted") and "all_voted" not in logged_events:
            detected.add("all_voted")

        if time_remaining_hours <= 24 and "final_stretch" not in logged_events:
            detected.add("final_stretch")
        if time_remaining_hours <= 1 and "last_call" not in logged_events:
            detected.add("last_call")

        # Lead change: fires whenever the leader changes (can repeat)
        if (
            current_leader_id is not None
            and previous_leader_id is not None
            and current_leader_id != previous_leader_id
        ):
            detected.add("lead_change")

    # Filter by chattiness
    applicable = detected & allowed

    # Return highest-priority applicable event
    for event in MILESTONE_PRIORITY:
        if event in applicable:
            return event
    return None


def generate_message(event_type: str, data: dict) -> str:
    """Pick a random template for the event type and fill in data."""
    pool = TEMPLATES.get(event_type, [])
    if not pool:
        return f"[{event_type}] {data}"
    template = random.choice(pool)
    try:
        return template.format(**data)
    except KeyError:
        return template


# ---------------------------------------------------------------------------
# Standings helpers
# ---------------------------------------------------------------------------


def _standings_data(standings: list[dict]) -> dict:
    """Extract top-3 standings fields for template filling."""
    def _entry(idx: int) -> tuple[str, str]:
        if idx < len(standings):
            e = standings[idx]
            return e.get("name", _UNKNOWN), str(e.get("weighted_score", 0))
        return _UNKNOWN, _NO_SCORE

    first_name, first_score = _entry(0)
    second_name, second_score = _entry(1)
    third_name, third_score = _entry(2)
    return {
        "first_name": first_name,
        "first_score": first_score,
        "second_name": second_name,
        "second_score": second_score,
        "third_name": third_name,
        "third_score": third_score,
        "leader_name": first_name,
        "leader_score": first_score,
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _get_logged_events(db: AsyncSession, campaign_id: int) -> set[str]:
    """Return the set of event_types already logged for this campaign."""
    result = await db.execute(
        select(ContestAgentLog.event_type).where(
            ContestAgentLog.campaign_id == campaign_id
        )
    )
    return {row[0] for row in result.all()}


async def _get_previous_leader_id(
    db: AsyncSession, campaign_id: int
) -> Optional[int]:
    """Return the entry_id of the leader from the last lead_change log entry.

    We store the leader entry_id in the message as 'leader_id:{id}' suffix.
    Returns None if no lead_change has been posted yet.
    """
    result = await db.execute(
        select(ContestAgentLog)
        .where(
            ContestAgentLog.campaign_id == campaign_id,
            ContestAgentLog.event_type == "lead_change",
        )
        .order_by(ContestAgentLog.posted_at.desc())
        .limit(1)
    )
    log_entry = result.scalar_one_or_none()
    if log_entry is None:
        return None
    # Extract leader_id from message suffix "leader_id:{id}"
    for part in log_entry.message.split():
        if part.startswith("leader_id:"):
            try:
                return int(part.split(":")[1])
            except (IndexError, ValueError):
                pass
    return None


async def _get_live_standings(
    db: AsyncSession, campaign_id: int
) -> list[dict]:
    """Return current live standings sorted by weighted score desc.

    Computes on-the-fly from the votes table (not from campaign_results,
    which is only populated on close).
    """
    from patt.services.vote_service import compute_scores, rank_results

    votes_result = await db.execute(
        select(Vote).where(Vote.campaign_id == campaign_id)
    )
    vote_dicts = [
        {"entry_id": v.entry_id, "rank": v.rank}
        for v in votes_result.scalars().all()
    ]
    scores = compute_scores(vote_dicts)

    # Load entry names
    entries_result = await db.execute(
        select(CampaignEntry).where(CampaignEntry.campaign_id == campaign_id)
    )
    entries = {e.id: e.name for e in entries_result.scalars().all()}

    ranked = rank_results(scores)
    return [
        {
            "entry_id": entry_id,
            "name": entries.get(entry_id, _UNKNOWN),
            "weighted_score": entry_scores["weighted_score"],
            "first": entry_scores["first"],
        }
        for entry_id, entry_scores in ranked
        if entry_scores["weighted_score"] > 0
    ]


async def _get_closed_standings(
    db: AsyncSession, campaign_id: int
) -> list[dict]:
    """Return standings from campaign_results (populated on close)."""
    result = await db.execute(
        select(CampaignResult)
        .join(CampaignEntry, CampaignResult.entry_id == CampaignEntry.id)
        .where(CampaignResult.campaign_id == campaign_id)
        .order_by(CampaignResult.final_rank)
    )
    rows = result.scalars().all()
    # Load entry names
    entries_result = await db.execute(
        select(CampaignEntry).where(CampaignEntry.campaign_id == campaign_id)
    )
    entries = {e.id: e.name for e in entries_result.scalars().all()}
    return [
        {
            "entry_id": r.entry_id,
            "name": entries.get(r.entry_id, _UNKNOWN),
            "weighted_score": r.weighted_score,
        }
        for r in rows
    ]


async def _get_total_voters(db: AsyncSession, campaign_id: int) -> int:
    """Return distinct voter count for this campaign."""
    from sqlalchemy import func as sqlfunc
    result = await db.execute(
        select(sqlfunc.count(sqlfunc.distinct(Vote.player_id))).where(
            Vote.campaign_id == campaign_id
        )
    )
    return result.scalar_one()


async def _get_vote_stats(db: AsyncSession, campaign_id: int) -> dict:
    """Return {total_eligible, total_voted, percent_voted, all_voted}."""
    from patt.services.vote_service import get_vote_stats
    return await get_vote_stats(db, campaign_id)


async def _get_default_channel_id(db: AsyncSession) -> Optional[str]:
    """Return default announcement channel from discord_config."""
    result = await db.execute(select(DiscordConfig).limit(1))
    config = result.scalar_one_or_none()
    return config.default_announcement_channel_id if config else None


async def _log_event(
    db: AsyncSession,
    campaign_id: int,
    event_type: str,
    message: str,
    discord_message_id: Optional[str] = None,
) -> None:
    """Write an event to contest_agent_log."""
    log = ContestAgentLog(
        campaign_id=campaign_id,
        event_type=event_type,
        message=message,
        discord_message_id=discord_message_id,
    )
    db.add(log)
    await db.flush()


# ---------------------------------------------------------------------------
# Core update logic
# ---------------------------------------------------------------------------


async def _process_campaign(
    db: AsyncSession,
    campaign: Campaign,
    bot,
    base_url: str,
    default_channel_id: Optional[str],
) -> None:
    """Check a single campaign for milestones and post if needed."""
    import discord as _discord

    campaign_id = campaign.id
    channel_id = campaign.discord_channel_id or default_channel_id
    if not channel_id:
        logger.debug("Campaign %d has no channel configured, skipping", campaign_id)
        return

    logged_events = await _get_logged_events(db, campaign_id)
    vote_url = f"{base_url}/vote/{campaign_id}"
    results_url = f"{base_url}/vote/{campaign_id}"

    if campaign.status == "live":
        stats = await _get_vote_stats(db, campaign_id)
        standings = await _get_live_standings(db, campaign_id)
        now = datetime.now(timezone.utc)
        end_at = campaign.start_at + timedelta(hours=campaign.duration_hours)
        time_remaining_hours = max(0.0, (end_at - now).total_seconds() / 3600)

        current_leader_id = standings[0]["entry_id"] if standings else None
        previous_leader_id = await _get_previous_leader_id(db, campaign_id)

        event_type = detect_milestone(
            campaign_status="live",
            stats=stats,
            time_remaining_hours=time_remaining_hours,
            logged_events=logged_events,
            chattiness=campaign.agent_chattiness,
            current_leader_id=current_leader_id,
            previous_leader_id=previous_leader_id,
        )

        if event_type is None:
            return

        close_date = end_at.strftime("%b %d at %I:%M %p UTC")
        std = _standings_data(standings)

        # Build data dict for template
        data: dict = {
            "title": campaign.title,
            "description": campaign.description or "",
            "vote_url": vote_url,
            "results_url": results_url,
            "close_date": close_date,
            "voted_count": stats["total_voted"],
            "total_count": stats["total_eligible"],
            "remaining_count": max(0, stats["total_eligible"] - stats["total_voted"]),
            **std,
        }

        if event_type == "lead_change" and standings:
            old_entry_name = _UNKNOWN
            old_score = _NO_SCORE
            if previous_leader_id:
                for s in standings:
                    if s["entry_id"] == previous_leader_id:
                        old_entry_name = s["name"]
                        old_score = str(s["weighted_score"])
                        break
            data.update(
                {
                    "new_leader": std["leader_name"],
                    "new_score": std["leader_score"],
                    "old_leader": old_entry_name,
                    "old_score": old_score,
                }
            )

        message_text = generate_message(event_type, data)

        # For lead_change, append the new leader id so we can detect next change
        stored_message = message_text
        if event_type == "lead_change" and current_leader_id:
            stored_message = f"{message_text}\nleader_id:{current_leader_id}"

    elif campaign.status == "closed":
        stats = await _get_vote_stats(db, campaign_id)
        standings = await _get_closed_standings(db, campaign_id)
        total_voters = await _get_total_voters(db, campaign_id)

        event_type = detect_milestone(
            campaign_status="closed",
            stats=stats,
            time_remaining_hours=0,
            logged_events=logged_events,
            chattiness=campaign.agent_chattiness,
        )

        if event_type is None:
            return

        std = _standings_data(standings)
        data = {
            "title": campaign.title,
            "vote_url": vote_url,
            "results_url": results_url,
            "total_voters": total_voters,
            "total_count": stats["total_eligible"],
            **std,
        }
        message_text = generate_message(event_type, data)
        stored_message = message_text
    else:
        return

    # Post to Discord
    discord_message_id: Optional[str] = None
    if bot is not None:
        try:
            embed = _discord.Embed(
                title=f"{'🏆' if 'closed' in event_type or 'voted' in event_type else '📣'} {campaign.title}",
                description=message_text,
                color=PATT_GOLD,
            )
            embed.set_footer(text="PATT-Bot • pullallthethings.com")
            embed.timestamp = datetime.now(timezone.utc)

            from sv_common.discord.channels import post_embed_to_channel
            discord_message_id = await post_embed_to_channel(bot, channel_id, embed)
        except Exception as exc:
            logger.error("Discord post failed for campaign %d: %s", campaign_id, exc)

    # Log the event
    await _log_event(db, campaign_id, event_type, stored_message, discord_message_id)
    logger.info(
        "Contest agent: posted %s for campaign %d (msg_id=%s)",
        event_type,
        campaign_id,
        discord_message_id,
    )


async def check_campaign_updates(
    db: AsyncSession,
    bot,
    base_url: str = "https://pullallthethings.com",
) -> None:
    """Check all agent-enabled campaigns for milestone events and post updates.

    Processes:
    - Live campaigns: check for all milestone types
    - Closed campaigns: post results if not yet announced
    """
    default_channel_id = await _get_default_channel_id(db)

    # Live campaigns
    live_result = await db.execute(
        select(Campaign).where(
            Campaign.status == "live",
            Campaign.agent_enabled.is_(True),
        )
    )
    for campaign in live_result.scalars().all():
        try:
            await _process_campaign(db, campaign, bot, base_url, default_channel_id)
        except Exception as exc:
            logger.error(
                "Contest agent error processing live campaign %d: %s",
                campaign.id,
                exc,
            )

    # Closed campaigns that haven't had a results announcement yet
    closed_result = await db.execute(
        select(Campaign).where(
            Campaign.status == "closed",
            Campaign.agent_enabled.is_(True),
        )
    )
    for campaign in closed_result.scalars().all():
        try:
            logged = await _get_logged_events(db, campaign.id)
            if "campaign_closed" not in logged and "all_voted" not in logged:
                await _process_campaign(
                    db, campaign, bot, base_url, default_channel_id
                )
        except Exception as exc:
            logger.error(
                "Contest agent error processing closed campaign %d: %s",
                campaign.id,
                exc,
            )

    await db.commit()


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


async def run_contest_agent(session_factory, base_url: str = "https://pullallthethings.com") -> None:
    """Background asyncio task: checks for campaign updates every 5 minutes."""
    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            from sv_common.discord.bot import get_bot
            bot = get_bot()

            async with session_factory() as session:
                await check_campaign_updates(session, bot, base_url)
        except asyncio.CancelledError:
            logger.info("Contest agent cancelled")
            break
        except Exception as exc:
            logger.error("Contest agent error: %s", exc)
