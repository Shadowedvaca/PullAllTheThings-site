"""
Unit tests for Phase 6.3 — maybe_notify_discord() routing helper.

Tests:
1. Posts on first occurrence when dest_discord=True, first_only=True
2. Suppresses repeat when first_only=True and is_first_occurrence=False
3. Posts repeat when first_only=False and is_first_occurrence=False
4. Suppresses when dest_discord=False
5. No-op when bot=None
6. No-op when audit_channel_id=None
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(rule: dict):
    """Build a mock asyncpg pool that returns `rule` from get_routing_rule."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {
            "id": 1,
            "issue_type": None,
            "min_severity": rule.get("min_severity", "warning"),
            "dest_audit_log": rule.get("dest_audit_log", True),
            "dest_discord": rule.get("dest_discord", True),
            "first_only": rule.get("first_only", True),
            "enabled": True,
            "notes": None,
            "updated_at": None,
        }
    ])
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_bot(channel=None):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel or MagicMock())
    return bot


# ---------------------------------------------------------------------------
# 1. Posts on first occurrence
# ---------------------------------------------------------------------------


class TestMaybeNotifyFirstOccurrence:
    @pytest.mark.asyncio
    async def test_posts_on_first_occurrence(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        pool = _make_pool({"dest_discord": True, "first_only": True})
        bot = _make_bot()

        with patch("sv_common.guild_sync.reporter.send_error", new_callable=AsyncMock) as mock_send:
            await er_mod.maybe_notify_discord(
                pool, bot, 12345, "bnet_token_expired", "warning",
                "Token expired", is_first_occurrence=True,
            )
            mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Suppresses repeat when first_only=True
# ---------------------------------------------------------------------------


class TestMaybeNotifySuppressRepeat:
    @pytest.mark.asyncio
    async def test_suppresses_repeat_when_first_only_true(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        pool = _make_pool({"dest_discord": True, "first_only": True})
        bot = _make_bot()

        with patch("sv_common.guild_sync.reporter.send_error", new_callable=AsyncMock) as mock_send:
            await er_mod.maybe_notify_discord(
                pool, bot, 12345, "bnet_token_expired", "warning",
                "Token expired", is_first_occurrence=False,
            )
            mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Posts repeat when first_only=False
# ---------------------------------------------------------------------------


class TestMaybeNotifyRepeatWhenFirstOnlyFalse:
    @pytest.mark.asyncio
    async def test_posts_repeat_when_first_only_false(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        pool = _make_pool({"dest_discord": True, "first_only": False})
        bot = _make_bot()

        with patch("sv_common.guild_sync.reporter.send_error", new_callable=AsyncMock) as mock_send:
            await er_mod.maybe_notify_discord(
                pool, bot, 12345, "bnet_token_expired", "warning",
                "Token expired", is_first_occurrence=False,
            )
            mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Suppresses when dest_discord=False
# ---------------------------------------------------------------------------


class TestMaybeNotifySuppressDestDiscordFalse:
    @pytest.mark.asyncio
    async def test_suppresses_when_dest_discord_false(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        pool = _make_pool({"dest_discord": False, "first_only": True})
        bot = _make_bot()

        with patch("sv_common.guild_sync.reporter.send_error", new_callable=AsyncMock) as mock_send:
            await er_mod.maybe_notify_discord(
                pool, bot, 12345, "bnet_token_expired", "warning",
                "Token expired", is_first_occurrence=True,
            )
            mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 5. No-op when bot=None
# ---------------------------------------------------------------------------


class TestMaybeNotifyNoopBotNone:
    @pytest.mark.asyncio
    async def test_noop_when_bot_none(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        pool = _make_pool({"dest_discord": True, "first_only": True})

        with patch("sv_common.guild_sync.reporter.send_error", new_callable=AsyncMock) as mock_send:
            # Should not raise
            await er_mod.maybe_notify_discord(
                pool, None, 12345, "bnet_token_expired", "warning",
                "Token expired", is_first_occurrence=True,
            )
            mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 6. No-op when audit_channel_id=None
# ---------------------------------------------------------------------------


class TestMaybeNotifyNoopChannelIdNone:
    @pytest.mark.asyncio
    async def test_noop_when_channel_id_none(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        pool = _make_pool({"dest_discord": True, "first_only": True})
        bot = _make_bot()

        with patch("sv_common.guild_sync.reporter.send_error", new_callable=AsyncMock) as mock_send:
            await er_mod.maybe_notify_discord(
                pool, bot, None, "bnet_token_expired", "warning",
                "Token expired", is_first_occurrence=True,
            )
            mock_send.assert_not_called()
