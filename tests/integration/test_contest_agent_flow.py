"""Integration tests for contest agent flow.

These tests require TEST_DATABASE_URL and mock the Discord bot.
They verify the full DB-backed flow: milestone detection, logging, and
Discord message dispatch.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

# Skip entire module if TEST_DATABASE_URL not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set — DB integration tests skipped",
)

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sv_common.db.models import (
    Campaign, CampaignEntry, ContestAgentLog, GuildMember, GuildRank, Vote
)
from patt.services.contest_agent import check_campaign_updates


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Mocked Discord bot that captures channel messages."""
    bot = MagicMock()
    channel = AsyncMock()
    channel.send = AsyncMock(return_value=MagicMock(id=999888777))
    bot.get_channel = MagicMock(return_value=channel)
    bot.fetch_channel = AsyncMock(return_value=channel)
    return bot


@pytest.fixture
async def rank(db_session):
    """Create a test guild rank."""
    r = GuildRank(name="Member", level=2, description="Test rank")
    db_session.add(r)
    await db_session.flush()
    return r


@pytest.fixture
async def member(db_session, rank):
    """Create a test guild member."""
    m = GuildMember(
        discord_username="testuser",
        display_name="Test User",
        rank_id=rank.id,
    )
    db_session.add(m)
    await db_session.flush()
    return m


@pytest.fixture
async def live_campaign(db_session, member):
    """Create a live campaign with agent enabled."""
    now = datetime.now(timezone.utc)
    c = Campaign(
        title="Test Art Vote",
        description="Pick your fav!",
        type="ranked_choice",
        picks_per_voter=3,
        min_rank_to_vote=1,
        start_at=now - timedelta(hours=1),
        duration_hours=168,
        status="live",
        agent_enabled=True,
        agent_chattiness="hype",
        discord_channel_id="123456789012345678",
        created_by=member.id,
        early_close_if_all_voted=True,
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest.fixture
async def campaign_entries(db_session, live_campaign):
    """Create 3 entries for the live campaign."""
    entries = []
    for i, name in enumerate(["Trogmoon Art", "Skatefarm Art", "Mito Art"]):
        e = CampaignEntry(
            campaign_id=live_campaign.id,
            name=name,
            sort_order=i,
        )
        db_session.add(e)
        entries.append(e)
    await db_session.flush()
    return entries


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentPostsLaunchMessage:
    async def test_agent_posts_launch_message_when_campaign_activates(
        self, db_session, live_campaign, mock_bot
    ):
        """A live campaign with no logged events should trigger campaign_launch."""
        with patch("sv_common.discord.channels.post_embed_to_channel", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "999888777"

            await check_campaign_updates(db_session, mock_bot, base_url="https://test.example.com")

            # Verify log entry was created
            result = await db_session.execute(
                select(ContestAgentLog).where(
                    ContestAgentLog.campaign_id == live_campaign.id,
                    ContestAgentLog.event_type == "campaign_launch",
                )
            )
            log_entry = result.scalar_one_or_none()
            assert log_entry is not None
            assert "Test Art Vote" in log_entry.message

    async def test_agent_does_not_duplicate_launch_message(
        self, db_session, live_campaign, mock_bot
    ):
        """Running the agent twice should only post launch once."""
        with patch("sv_common.discord.channels.post_embed_to_channel", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "111"

            # First run
            await check_campaign_updates(db_session, mock_bot)
            # Second run
            await check_campaign_updates(db_session, mock_bot)

            result = await db_session.execute(
                select(ContestAgentLog).where(
                    ContestAgentLog.campaign_id == live_campaign.id,
                    ContestAgentLog.event_type == "campaign_launch",
                )
            )
            launch_logs = list(result.scalars().all())
            assert len(launch_logs) == 1


class TestAgentPostsLeadChange:
    async def test_agent_posts_lead_change_on_next_check(
        self, db_session, live_campaign, campaign_entries, member, mock_bot
    ):
        """After a lead change log entry with entry A, when entry B now leads, post lead_change."""
        entry_a, entry_b, entry_c = campaign_entries

        # Pre-log campaign_launch and a previous lead_change showing entry_a was leading
        log_launch = ContestAgentLog(
            campaign_id=live_campaign.id,
            event_type="campaign_launch",
            message="Campaign launched",
        )
        log_lead = ContestAgentLog(
            campaign_id=live_campaign.id,
            event_type="lead_change",
            message=f"Old lead change message\nleader_id:{entry_a.id}",
        )
        db_session.add(log_launch)
        db_session.add(log_lead)

        # Add votes so entry_b is now leading (3 first-place votes → 9 pts)
        vote1 = Vote(
            campaign_id=live_campaign.id,
            member_id=member.id,
            entry_id=entry_b.id,
            rank=1,
        )
        db_session.add(vote1)
        await db_session.flush()

        with patch("sv_common.discord.channels.post_embed_to_channel", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "222"
            await check_campaign_updates(db_session, mock_bot)

            result = await db_session.execute(
                select(ContestAgentLog).where(
                    ContestAgentLog.campaign_id == live_campaign.id,
                    ContestAgentLog.event_type == "lead_change",
                )
            )
            # Should now have 2 lead_change entries (old + new)
            all_lead_logs = list(result.scalars().all())
            assert len(all_lead_logs) == 2


class TestAgentPostsResults:
    async def test_agent_posts_results_when_campaign_closes(
        self, db_session, live_campaign, campaign_entries, mock_bot
    ):
        """A closed campaign without a results log entry should trigger campaign_closed."""
        # Pre-log launch
        db_session.add(ContestAgentLog(
            campaign_id=live_campaign.id,
            event_type="campaign_launch",
            message="launched",
        ))

        # Close the campaign
        live_campaign.status = "closed"
        await db_session.flush()

        # Add results to campaign_results table
        from sv_common.db.models import CampaignResult
        entry_a, entry_b, entry_c = campaign_entries
        db_session.add(CampaignResult(
            campaign_id=live_campaign.id,
            entry_id=entry_a.id,
            first_place_count=5,
            weighted_score=15,
            final_rank=1,
        ))
        db_session.add(CampaignResult(
            campaign_id=live_campaign.id,
            entry_id=entry_b.id,
            first_place_count=3,
            weighted_score=9,
            final_rank=2,
        ))
        db_session.add(CampaignResult(
            campaign_id=live_campaign.id,
            entry_id=entry_c.id,
            first_place_count=1,
            weighted_score=3,
            final_rank=3,
        ))
        await db_session.flush()

        with patch("sv_common.discord.channels.post_embed_to_channel", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "333"
            await check_campaign_updates(db_session, mock_bot)

            result = await db_session.execute(
                select(ContestAgentLog).where(
                    ContestAgentLog.campaign_id == live_campaign.id,
                    ContestAgentLog.event_type == "campaign_closed",
                )
            )
            log_entry = result.scalar_one_or_none()
            assert log_entry is not None
            assert "Trogmoon Art" in log_entry.message


class TestAgentRespectsChattinessSetting:
    async def test_agent_respects_chattiness_quiet(
        self, db_session, live_campaign, mock_bot
    ):
        """Quiet campaign should only post launch + results, not milestones."""
        live_campaign.agent_chattiness = "quiet"
        await db_session.flush()

        # Pre-log launch
        db_session.add(ContestAgentLog(
            campaign_id=live_campaign.id,
            event_type="campaign_launch",
            message="launched",
        ))
        await db_session.flush()

        with patch("sv_common.discord.channels.post_embed_to_channel", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "444"
            await check_campaign_updates(db_session, mock_bot)

            # After launch, no milestones should fire for quiet mode
            result = await db_session.execute(
                select(ContestAgentLog).where(
                    ContestAgentLog.campaign_id == live_campaign.id,
                )
            )
            all_logs = list(result.scalars().all())
            event_types = {log.event_type for log in all_logs}
            # Should only have campaign_launch (no first_vote, milestone_* etc.)
            assert event_types == {"campaign_launch"}


class TestAgentLogsAllPostedMessages:
    async def test_agent_logs_all_posted_messages(
        self, db_session, live_campaign, mock_bot
    ):
        """Every Discord post should have a corresponding log entry."""
        with patch("sv_common.discord.channels.post_embed_to_channel", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "555"
            await check_campaign_updates(db_session, mock_bot)

            result = await db_session.execute(
                select(ContestAgentLog).where(
                    ContestAgentLog.campaign_id == live_campaign.id,
                )
            )
            logs = list(result.scalars().all())
            # At minimum, the launch should be logged
            assert len(logs) >= 1
            # discord_message_id should be set on all log entries
            for log in logs:
                assert log.discord_message_id == "555"


class TestAgentSkipsDisabledCampaigns:
    async def test_disabled_campaign_gets_no_updates(
        self, db_session, live_campaign, mock_bot
    ):
        """Campaigns with agent_enabled=False should never post."""
        live_campaign.agent_enabled = False
        await db_session.flush()

        with patch("sv_common.discord.channels.post_embed_to_channel", new_callable=AsyncMock) as mock_post:
            await check_campaign_updates(db_session, mock_bot)
            mock_post.assert_not_called()
