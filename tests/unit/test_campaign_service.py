"""Unit tests for campaign service validation logic.

Uses patch to mock get_campaign so service functions are tested in isolation.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-for-campaigns")
os.environ.setdefault("APP_ENV", "testing")

import pytest
from sv_common.db.models import Campaign, CampaignEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_campaign(**kwargs) -> Campaign:
    """Create a transient Campaign instance with sensible defaults."""
    defaults = dict(
        title="Test Campaign",
        description=None,
        type="ranked_choice",
        picks_per_voter=3,
        min_rank_to_vote=2,
        min_rank_to_view=None,
        start_at=datetime.now(timezone.utc) + timedelta(hours=1),
        duration_hours=168,
        status="draft",
        early_close_if_all_voted=True,
        discord_channel_id=None,
        created_by=None,
    )
    defaults.update(kwargs)
    return Campaign(**defaults)


def _make_db() -> AsyncMock:
    """Minimal mock AsyncSession."""
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Campaign defaults
# ---------------------------------------------------------------------------


class TestCreateCampaignDefaults:
    def test_campaign_status_defaults_draft(self):
        """Campaign starts in draft status."""
        c = _make_campaign(status="draft")
        assert c.status == "draft"

    def test_campaign_picks_per_voter_default(self):
        c = _make_campaign()
        assert c.picks_per_voter == 3

    def test_campaign_type_default(self):
        c = _make_campaign()
        assert c.type == "ranked_choice"

    def test_campaign_early_close_default(self):
        c = _make_campaign()
        assert c.early_close_if_all_voted is True

    def test_campaign_min_rank_to_view_can_be_none(self):
        c = _make_campaign(min_rank_to_view=None)
        assert c.min_rank_to_view is None

    def test_campaign_requires_min_rank_to_vote(self):
        c = _make_campaign(min_rank_to_vote=3)
        assert c.min_rank_to_vote == 3


# ---------------------------------------------------------------------------
# Status transition: draft → live
# ---------------------------------------------------------------------------


class TestCampaignStatusTransitions:
    async def test_campaign_status_transitions_draft_to_live(self):
        from patt.services.campaign_service import activate_campaign

        future = datetime.now(timezone.utc) + timedelta(hours=2)
        campaign = _make_campaign(status="draft", start_at=future)
        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            result = await activate_campaign(db, 1)

        assert result.status == "live"
        db.flush.assert_awaited_once()

    async def test_activate_already_live_raises(self):
        from patt.services.campaign_service import activate_campaign

        campaign = _make_campaign(status="live")
        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            with pytest.raises(ValueError, match="already live"):
                await activate_campaign(db, 1)

    async def test_activate_sets_start_time_if_in_past(self):
        """If start_at is in the past, it is reset to now on activation."""
        from patt.services.campaign_service import activate_campaign

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        campaign = _make_campaign(status="draft", start_at=past)
        db = _make_db()

        before_activation = datetime.now(timezone.utc)

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            result = await activate_campaign(db, 1)

        assert result.status == "live"
        assert result.start_at >= before_activation

    async def test_close_draft_raises(self):
        from patt.services.campaign_service import close_campaign

        campaign = _make_campaign(status="draft")
        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            with pytest.raises(ValueError, match="cannot close"):
                await close_campaign(db, 1)

    async def test_campaign_not_found_raises(self):
        from patt.services.campaign_service import update_campaign

        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(ValueError, match="not found"):
                await update_campaign(db, 999, title="Whatever")


# ---------------------------------------------------------------------------
# Entry mutations blocked when campaign is not draft
# ---------------------------------------------------------------------------


class TestEntryEditingBlocked:
    async def test_campaign_cannot_add_entry_when_live(self):
        from patt.services.campaign_service import add_entry

        campaign = _make_campaign(status="live")
        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            with pytest.raises(ValueError, match="draft"):
                await add_entry(db, 1, name="New Entry")

    async def test_campaign_cannot_add_entry_when_closed(self):
        from patt.services.campaign_service import add_entry

        campaign = _make_campaign(status="closed")
        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            with pytest.raises(ValueError, match="draft"):
                await add_entry(db, 1, name="New Entry")

    async def test_campaign_cannot_remove_entry_when_live(self):
        from patt.services.campaign_service import remove_entry

        campaign = _make_campaign(status="live")
        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            with pytest.raises(ValueError, match="draft"):
                await remove_entry(db, 1, entry_id=1)

    async def test_campaign_cannot_update_settings_when_live(self):
        from patt.services.campaign_service import update_campaign

        campaign = _make_campaign(status="live")
        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=campaign),
        ):
            with pytest.raises(ValueError, match="draft"):
                await update_campaign(db, 1, title="New Title")

    async def test_update_campaign_not_found_raises(self):
        from patt.services.campaign_service import update_campaign

        db = _make_db()

        with patch(
            "patt.services.campaign_service.get_campaign",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(ValueError, match="not found"):
                await update_campaign(db, 999, title="Whatever")


# ---------------------------------------------------------------------------
# Voting status validation (via cast_vote / vote_service)
# ---------------------------------------------------------------------------


class TestVotingStatusValidation:
    async def _mock_cast_vote_with_campaign_status(self, status: str, picks=None):
        """Helper: mock cast_vote so campaign is in given status."""
        from patt.services.vote_service import cast_vote

        campaign = _make_campaign(status=status)
        db = _make_db()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = campaign
        db.execute = AsyncMock(return_value=mock_result)

        return await cast_vote(db, campaign_id=1, member_id=1, picks=picks or [])

    async def test_campaign_cannot_vote_when_draft(self):
        with pytest.raises(ValueError, match="draft"):
            await self._mock_cast_vote_with_campaign_status("draft")

    async def test_campaign_cannot_vote_when_closed(self):
        with pytest.raises(ValueError, match="closed"):
            await self._mock_cast_vote_with_campaign_status("closed")
