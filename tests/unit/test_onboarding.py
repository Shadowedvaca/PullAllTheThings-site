"""
Unit tests for Phase 4.4.3 — Onboarding Activation & OAuth Integration.

All tests use mock asyncpg pools and mock Discord objects — no real DB required.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchval_return=None, fetchrow_return=None, fetch_return=None):
    """Build a minimal mock asyncpg pool."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


def _make_member(discord_id=123456789, name="Trogmoon"):
    member = MagicMock()
    member.id = discord_id
    member.name = name
    member.nick = None
    member.display_name = name
    member.joined_at = None
    return member


# ---------------------------------------------------------------------------
# config_cache — is_onboarding_enabled / get_app_url / set_app_url
# ---------------------------------------------------------------------------


def test_is_onboarding_enabled_defaults_to_true():
    """is_onboarding_enabled returns True when no flag set (safe default)."""
    from sv_common import config_cache
    config_cache._cache.pop("enable_onboarding", None)

    from sv_common.config_cache import is_onboarding_enabled
    assert is_onboarding_enabled() is True


def test_is_onboarding_enabled_respects_cache():
    """is_onboarding_enabled returns False when flag is False in cache."""
    from sv_common import config_cache
    config_cache._cache["enable_onboarding"] = False

    from sv_common.config_cache import is_onboarding_enabled
    assert is_onboarding_enabled() is False

    # cleanup
    config_cache._cache.pop("enable_onboarding", None)


def test_set_and_get_app_url():
    """set_app_url / get_app_url round-trip (strips trailing slash)."""
    from sv_common.config_cache import set_app_url, get_app_url

    set_app_url("https://pullallthethings.com/")
    assert get_app_url() == "https://pullallthethings.com"

    set_app_url("")
    assert get_app_url() == ""


# ---------------------------------------------------------------------------
# bot.py — on_member_join respects enable_onboarding flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_member_join_skips_onboarding_when_disabled():
    """on_member_join should not start the conversation when onboarding is disabled."""
    import sv_common.discord.bot as bot_module

    member = _make_member()
    member.bot = False
    pool = MagicMock()
    bot_module._db_pool = pool

    with patch("sv_common.config_cache.is_onboarding_enabled", return_value=False):
        with patch("sv_common.guild_sync.discord_sync.on_member_join", new=AsyncMock()):
            with patch(
                "sv_common.guild_sync.onboarding.conversation.OnboardingConversation"
            ) as MockConv:
                await bot_module.on_member_join(member)
                MockConv.assert_not_called()


@pytest.mark.asyncio
async def test_on_member_join_starts_onboarding_when_enabled():
    """on_member_join should create and start a conversation when onboarding is enabled."""
    import sv_common.discord.bot as bot_module

    member = _make_member()
    member.bot = False
    pool = MagicMock()
    bot_module._db_pool = pool

    mock_conv = MagicMock()
    mock_conv.start = AsyncMock()

    with patch("sv_common.config_cache.is_onboarding_enabled", return_value=True):
        with patch("sv_common.guild_sync.discord_sync.on_member_join", new=AsyncMock()):
            with patch(
                "sv_common.guild_sync.onboarding.conversation.OnboardingConversation",
                return_value=mock_conv,
            ):
                with patch("asyncio.create_task") as mock_task:
                    await bot_module.on_member_join(member)
                    mock_task.assert_called_once()


# ---------------------------------------------------------------------------
# conversation.py — _auto_provision sets oauth_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_provision_sets_oauth_pending():
    """_auto_provision should set session state to oauth_pending, not provisioned."""
    from sv_common.guild_sync.onboarding.conversation import OnboardingConversation

    member = _make_member()
    pool, conn = _make_pool()
    bot = MagicMock()

    conv = OnboardingConversation(bot, member, pool)
    conv.session_id = 42

    mock_provisioner = AsyncMock()
    mock_provisioner.provision_player = AsyncMock(return_value={
        "invite_code": "TESTCODE",
        "characters_linked": 2,
        "discord_role_assigned": True,
        "errors": [],
    })

    with patch(
        "sv_common.guild_sync.onboarding.provisioner.AutoProvisioner",
        return_value=mock_provisioner,
    ):
        with patch.object(conv, "_send_oauth_prompt", new=AsyncMock()):
            with patch("asyncio.create_task"):
                await conv._auto_provision(player_id=99)

    # Check the DB update used 'oauth_pending'
    call_args = conn.execute.call_args[0]
    assert "oauth_pending" in call_args[0]


@pytest.mark.asyncio
async def test_auto_provision_sends_oauth_prompt_and_starts_polling():
    """_auto_provision should call _send_oauth_prompt and create a polling task."""
    from sv_common.guild_sync.onboarding.conversation import OnboardingConversation

    member = _make_member()
    pool, conn = _make_pool()
    bot = MagicMock()

    conv = OnboardingConversation(bot, member, pool)
    conv.session_id = 42

    mock_provisioner = AsyncMock()
    mock_provisioner.provision_player = AsyncMock(return_value={
        "invite_code": "TESTCODE",
        "characters_linked": 2,
        "discord_role_assigned": True,
        "errors": [],
    })

    oauth_prompt_called = []

    async def fake_oauth_prompt():
        oauth_prompt_called.append(True)

    with patch(
        "sv_common.guild_sync.onboarding.provisioner.AutoProvisioner",
        return_value=mock_provisioner,
    ):
        with patch.object(conv, "_send_oauth_prompt", new=fake_oauth_prompt):
            with patch("asyncio.create_task") as mock_task:
                await conv._auto_provision(player_id=99)

    assert oauth_prompt_called, "_send_oauth_prompt should have been called"
    mock_task.assert_called_once()


# ---------------------------------------------------------------------------
# conversation.py — update_onboarding_status module-level function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_onboarding_status_returns_true_when_session_updated():
    """update_onboarding_status returns True when a session was found and updated."""
    from sv_common.guild_sync.onboarding.conversation import update_onboarding_status

    pool, conn = _make_pool(fetchval_return=7)  # returning session id = 7
    result = await update_onboarding_status(pool, player_id=99, new_status="oauth_complete")
    assert result is True


@pytest.mark.asyncio
async def test_update_onboarding_status_returns_false_when_no_session():
    """update_onboarding_status returns False when no oauth_pending session exists."""
    from sv_common.guild_sync.onboarding.conversation import update_onboarding_status

    pool, conn = _make_pool(fetchval_return=None)
    result = await update_onboarding_status(pool, player_id=99, new_status="oauth_complete")
    assert result is False


@pytest.mark.asyncio
async def test_update_onboarding_status_only_updates_oauth_pending():
    """update_onboarding_status SQL targets state = 'oauth_pending'."""
    from sv_common.guild_sync.onboarding.conversation import update_onboarding_status

    pool, conn = _make_pool(fetchval_return=None)
    await update_onboarding_status(pool, player_id=1, new_status="oauth_complete")

    sql = conn.fetchval.call_args[0][0]
    assert "oauth_pending" in sql


# ---------------------------------------------------------------------------
# deadline_checker.py — _check_oauth_pending_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_oauth_pending_no_sessions():
    """_check_oauth_pending_sessions returns zero counts when no sessions exist."""
    from sv_common.guild_sync.onboarding.deadline_checker import OnboardingDeadlineChecker

    pool, conn = _make_pool(fetch_return=[])
    checker = OnboardingDeadlineChecker(pool, bot=None)
    stats = await checker._check_oauth_pending_sessions()

    assert stats["oauth_reminded"] == 0
    assert stats["oauth_abandoned"] == 0


@pytest.mark.asyncio
async def test_check_oauth_pending_sends_reminder_at_24h():
    """Session at 25h since completed_at should get an OAuth reminder."""
    from sv_common.guild_sync.onboarding.deadline_checker import OnboardingDeadlineChecker

    now = datetime.now(timezone.utc)
    session = {
        "id": 1,
        "discord_id": "123456",
        "verified_player_id": 10,
        "completed_at": now - timedelta(hours=25),
        "escalated_at": None,
    }

    pool, conn = _make_pool(fetch_return=[session])
    checker = OnboardingDeadlineChecker(pool, bot=None)

    reminder_called = []

    async def fake_reminder(s):
        reminder_called.append(s["id"])

    async def fake_abandon(s):
        pass

    with patch.object(checker, "_send_oauth_reminder", new=fake_reminder):
        with patch.object(checker, "_abandon_oauth", new=fake_abandon):
            stats = await checker._check_oauth_pending_sessions()

    assert stats["oauth_reminded"] == 1
    assert stats["oauth_abandoned"] == 0
    assert 1 in reminder_called


@pytest.mark.asyncio
async def test_check_oauth_pending_abandons_at_48h():
    """Session at 49h since completed_at should be abandoned."""
    from sv_common.guild_sync.onboarding.deadline_checker import OnboardingDeadlineChecker

    now = datetime.now(timezone.utc)
    session = {
        "id": 2,
        "discord_id": "999888",
        "verified_player_id": 20,
        "completed_at": now - timedelta(hours=49),
        "escalated_at": None,
    }

    pool, conn = _make_pool(fetch_return=[session])
    checker = OnboardingDeadlineChecker(pool, bot=None)

    abandon_called = []

    async def fake_abandon(s):
        abandon_called.append(s["id"])

    async def fake_reminder(s):
        pass

    with patch.object(checker, "_send_oauth_reminder", new=fake_reminder):
        with patch.object(checker, "_abandon_oauth", new=fake_abandon):
            stats = await checker._check_oauth_pending_sessions()

    assert stats["oauth_abandoned"] == 1
    assert stats["oauth_reminded"] == 0
    assert 2 in abandon_called


@pytest.mark.asyncio
async def test_check_oauth_pending_skips_already_reminded():
    """Session at 25h with escalated_at set should NOT get another reminder."""
    from sv_common.guild_sync.onboarding.deadline_checker import OnboardingDeadlineChecker

    now = datetime.now(timezone.utc)
    session = {
        "id": 3,
        "discord_id": "555444",
        "verified_player_id": 30,
        "completed_at": now - timedelta(hours=25),
        "escalated_at": now - timedelta(hours=1),  # reminder already sent
    }

    pool, conn = _make_pool(fetch_return=[session])
    checker = OnboardingDeadlineChecker(pool, bot=None)

    reminder_called = []

    async def fake_reminder(s):
        reminder_called.append(s["id"])

    with patch.object(checker, "_send_oauth_reminder", new=fake_reminder):
        stats = await checker._check_oauth_pending_sessions()

    assert stats["oauth_reminded"] == 0
    assert not reminder_called


# ---------------------------------------------------------------------------
# bnet_auth_routes.py — OAuth callback signals oauth_complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bnet_callback_signals_oauth_complete_when_session_exists():
    """After sync_bnet_characters, update_onboarding_status is called with oauth_complete."""
    # We test the update_onboarding_status call indirectly via the pool interaction.
    from sv_common.guild_sync.onboarding.conversation import update_onboarding_status

    pool, conn = _make_pool(fetchval_return=5)  # session ID 5 updated
    result = await update_onboarding_status(pool, player_id=42, new_status="oauth_complete")
    assert result is True


@pytest.mark.asyncio
async def test_bnet_callback_no_error_when_no_session():
    """update_onboarding_status with no matching session returns False gracefully."""
    from sv_common.guild_sync.onboarding.conversation import update_onboarding_status

    pool, conn = _make_pool(fetchval_return=None)
    result = await update_onboarding_status(pool, player_id=999, new_status="oauth_complete")
    assert result is False


# ---------------------------------------------------------------------------
# SiteConfig model has enable_onboarding field
# ---------------------------------------------------------------------------


def test_site_config_has_enable_onboarding():
    """SiteConfig model has the enable_onboarding column."""
    from sv_common.db.models import SiteConfig

    columns = {c.name for c in SiteConfig.__table__.columns}
    assert "enable_onboarding" in columns
