"""
Unit tests for Phase 6.3 — GuildSyncScheduler weekly error digest additions.

Tests:
1. start() registers weekly_error_digest job
2. run_weekly_error_digest: silent when no errors
3. run_weekly_error_digest: posts when errors exist
4. run_weekly_error_digest: handles missing audit channel gracefully
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scheduler():
    """Build a GuildSyncScheduler with all external deps mocked out."""
    import os
    from sv_common.guild_sync.scheduler import GuildSyncScheduler

    db_pool = MagicMock()
    bot = MagicMock()
    audit_channel_id = 99999

    env_overrides = {
        "BLIZZARD_CLIENT_ID": "test-client-id",
        "BLIZZARD_CLIENT_SECRET": "test-client-secret",
    }

    with patch("sv_common.guild_sync.scheduler.BlizzardClient") as mock_bc_cls, \
         patch("sv_common.guild_sync.scheduler.get_site_config", return_value={}), \
         patch.dict(os.environ, env_overrides):
        mock_bc_cls.return_value = MagicMock()
        scheduler = GuildSyncScheduler(db_pool, bot, audit_channel_id)

    # Replace the real APScheduler with a mock
    scheduler.scheduler = MagicMock()
    return scheduler


def _make_error(issue_type="bnet_token_expired"):
    return {
        "issue_type": issue_type,
        "severity": "warning",
        "summary": "Token expired",
        "identifier": "sevin#1865",
        "occurrence_count": 3,
        "first_occurred_at": datetime(2026, 3, 10, 8, 0, 0, tzinfo=timezone.utc),
    }


# ---------------------------------------------------------------------------
# 1. start() registers weekly_error_digest job
# ---------------------------------------------------------------------------


class TestSchedulerRegistersDigestJob:
    @pytest.mark.asyncio
    async def test_start_registers_weekly_digest_job(self):
        scheduler = _make_scheduler()

        with patch.object(scheduler.blizzard_client, "initialize", new_callable=AsyncMock):
            await scheduler.start()

        job_ids = [call_args[1].get("id") or call_args[0][1] if len(call_args[0]) > 1 else None
                   for call_args in scheduler.scheduler.add_job.call_args_list]
        # Collect all id= kwargs
        ids_from_kwargs = [
            c.kwargs.get("id") for c in scheduler.scheduler.add_job.call_args_list
            if c.kwargs.get("id")
        ]
        assert "weekly_error_digest" in ids_from_kwargs


# ---------------------------------------------------------------------------
# 2. Silent when no errors
# ---------------------------------------------------------------------------


class TestDigestSilentWhenNoErrors:
    @pytest.mark.asyncio
    async def test_silent_when_no_open_errors(self):
        scheduler = _make_scheduler()
        audit_channel = MagicMock()
        audit_channel.send = AsyncMock()
        scheduler.discord_bot.get_channel = MagicMock(return_value=audit_channel)

        with patch("sv_common.errors.get_unresolved", new_callable=AsyncMock, return_value=[]):
            await scheduler.run_weekly_error_digest()

        audit_channel.send.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Posts when errors exist
# ---------------------------------------------------------------------------


class TestDigestPostsWhenErrors:
    @pytest.mark.asyncio
    async def test_posts_when_errors_exist(self):
        scheduler = _make_scheduler()
        audit_channel = MagicMock()
        audit_channel.send = AsyncMock()
        scheduler.discord_bot.get_channel = MagicMock(return_value=audit_channel)

        errors = [_make_error()]
        with patch("sv_common.errors.get_unresolved", new_callable=AsyncMock, return_value=errors):
            await scheduler.run_weekly_error_digest()

        audit_channel.send.assert_called()


# ---------------------------------------------------------------------------
# 4. Handles missing channel
# ---------------------------------------------------------------------------


class TestDigestHandlesMissingChannel:
    @pytest.mark.asyncio
    async def test_no_exception_when_channel_is_none(self):
        scheduler = _make_scheduler()
        scheduler.discord_bot.get_channel = MagicMock(return_value=None)

        # When channel is None, it returns early before fetching errors
        await scheduler.run_weekly_error_digest()
