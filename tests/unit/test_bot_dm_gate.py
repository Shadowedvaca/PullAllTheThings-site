"""
Unit tests for the bot DM gate (is_bot_dm_enabled) and onboarding DM toggle.

All tests use mock asyncpg pools — no real database required.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# is_bot_dm_enabled()
# ---------------------------------------------------------------------------


def _make_pool(fetchval_return):
    """Build a minimal mock asyncpg pool that returns a fixed value from fetchval."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.mark.asyncio
async def test_is_bot_dm_enabled_returns_false_when_flag_is_false():
    """is_bot_dm_enabled returns False when the DB flag is false."""
    from sv_common.discord.dm import is_bot_dm_enabled

    pool, _ = _make_pool(False)
    result = await is_bot_dm_enabled(pool)
    assert result is False


@pytest.mark.asyncio
async def test_is_bot_dm_enabled_returns_true_when_flag_is_true():
    """is_bot_dm_enabled returns True when the DB flag is true."""
    from sv_common.discord.dm import is_bot_dm_enabled

    pool, _ = _make_pool(True)
    result = await is_bot_dm_enabled(pool)
    assert result is True


@pytest.mark.asyncio
async def test_is_bot_dm_enabled_returns_false_when_no_config_row():
    """is_bot_dm_enabled returns False when discord_config has no rows (None returned)."""
    from sv_common.discord.dm import is_bot_dm_enabled

    pool, _ = _make_pool(None)
    result = await is_bot_dm_enabled(pool)
    assert result is False


# ---------------------------------------------------------------------------
# conversation.py — DM gate in start()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_start_creates_session_but_skips_dm_when_disabled():
    """
    When bot_dm_enabled=False, start() should create an onboarding session
    in awaiting_dm state but NOT call _send_welcome().
    """
    from sv_common.guild_sync.onboarding.conversation import OnboardingConversation

    # Mock discord member
    member = MagicMock()
    member.id = 123456789
    member.name = "Trogmoon"
    member.nick = None
    member.display_name = "Trogmoon"
    member.joined_at = None

    # Build pool: no existing session, discord_users lookup returns dm_id=1,
    # onboarding_sessions insert returns session_id=42
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # no existing session
    conn.fetchval = AsyncMock(side_effect=[1, 42])  # discord_users.id=1, session_id=42

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    bot = MagicMock()
    conv = OnboardingConversation(bot, member, pool)

    with patch("sv_common.discord.dm.is_bot_dm_enabled", new=AsyncMock(return_value=False)):
        await conv.start()

    # Session was created
    assert conv.session_id == 42
    # _send_welcome was NOT called (bot.wait_for never called)
    bot.wait_for.assert_not_called()


@pytest.mark.asyncio
async def test_conversation_start_calls_send_welcome_when_dm_enabled():
    """
    When bot_dm_enabled=True, start() should call _send_welcome().
    _send_welcome is patched so we just verify it was called.
    """
    from sv_common.guild_sync.onboarding.conversation import OnboardingConversation

    member = MagicMock()
    member.id = 987654321
    member.name = "Rocketman"
    member.nick = None
    member.display_name = "Rocketman"
    member.joined_at = None

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)  # no existing session
    conn.fetchval = AsyncMock(side_effect=[2, 99])

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    bot = MagicMock()
    conv = OnboardingConversation(bot, member, pool)

    with patch("sv_common.discord.dm.is_onboarding_dm_enabled", new=AsyncMock(return_value=True)):
        with patch.object(conv, "_send_welcome", new=AsyncMock()) as mock_welcome:
            await conv.start()

    mock_welcome.assert_called_once()


@pytest.mark.asyncio
async def test_conversation_start_skips_if_existing_active_session():
    """
    start() should bail early and NOT create a new session if an active one exists.
    """
    from sv_common.guild_sync.onboarding.conversation import OnboardingConversation

    member = MagicMock()
    member.id = 111
    member.name = "Existing"

    existing = {"id": 7, "state": "pending_verification"}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=existing)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    bot = MagicMock()
    conv = OnboardingConversation(bot, member, pool)

    # Even with DM enabled, existing session should bail early
    with patch("sv_common.discord.dm.is_bot_dm_enabled", new=AsyncMock(return_value=True)):
        await conv.start()

    assert conv.session_id == 7
    # No insert should have happened (fetchval not called after fetchrow returned existing)
    conn.fetchval.assert_not_called()


# ---------------------------------------------------------------------------
# provisioner.py — DM gate in _send_invite_dm()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provisioner_skips_invite_dm_when_dm_disabled():
    """
    _send_invite_dm should log and return without sending when DM is disabled.
    """
    from sv_common.guild_sync.onboarding.provisioner import AutoProvisioner

    pool = MagicMock()
    bot = AsyncMock()
    provisioner = AutoProvisioner(pool, bot)

    with patch("sv_common.discord.dm.is_bot_dm_enabled", new=AsyncMock(return_value=False)):
        await provisioner._send_invite_dm("123456", "TESTCODE")

    # Bot should NOT have been asked to fetch a user
    bot.fetch_user.assert_not_called()


@pytest.mark.asyncio
async def test_provisioner_sends_invite_dm_when_dm_enabled():
    """
    _send_invite_dm sends the DM when bot_dm_enabled is True.
    """
    from sv_common.guild_sync.onboarding.provisioner import AutoProvisioner

    pool = MagicMock()
    bot = AsyncMock()
    user = AsyncMock()
    dm_channel = AsyncMock()
    user.create_dm = AsyncMock(return_value=dm_channel)
    bot.fetch_user = AsyncMock(return_value=user)

    provisioner = AutoProvisioner(pool, bot)

    with patch("sv_common.discord.dm.is_bot_dm_enabled", new=AsyncMock(return_value=True)):
        await provisioner._send_invite_dm("123456", "TESTCODE")

    bot.fetch_user.assert_called_once_with(123456)
    dm_channel.send.assert_called_once()


# ---------------------------------------------------------------------------
# deadline_checker.py — _resume_awaiting_dm_sessions()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_awaiting_dm_skips_when_dm_disabled():
    """
    _resume_awaiting_dm_sessions returns 0 immediately when DMs are disabled.
    """
    from sv_common.guild_sync.onboarding.deadline_checker import OnboardingDeadlineChecker

    pool = MagicMock()
    checker = OnboardingDeadlineChecker(pool)

    with patch("sv_common.discord.dm.is_bot_dm_enabled", new=AsyncMock(return_value=False)):
        result = await checker._resume_awaiting_dm_sessions()

    assert result == 0


@pytest.mark.asyncio
async def test_resume_awaiting_dm_skips_when_no_sessions():
    """
    _resume_awaiting_dm_sessions returns 0 when DMs are enabled but no awaiting sessions.
    """
    from sv_common.guild_sync.onboarding.deadline_checker import OnboardingDeadlineChecker

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    checker = OnboardingDeadlineChecker(pool, bot=None)

    with patch("sv_common.discord.dm.is_bot_dm_enabled", new=AsyncMock(return_value=True)):
        result = await checker._resume_awaiting_dm_sessions()

    assert result == 0


# ---------------------------------------------------------------------------
# Smoke: DiscordConfig has bot_dm_enabled field
# ---------------------------------------------------------------------------


def test_discord_config_has_bot_dm_enabled():
    """DiscordConfig model has the bot_dm_enabled column."""
    from sv_common.db.models import DiscordConfig

    columns = {c.name for c in DiscordConfig.__table__.columns}
    assert "bot_dm_enabled" in columns
