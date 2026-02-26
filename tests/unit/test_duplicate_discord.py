"""Unit tests for detect_duplicate_discord_links() in integrity_checker.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(dupe_rows=None, stale_rows=None, discord_user=None):
    """Build a mock asyncpg connection for duplicate/stale discord detection."""
    conn = AsyncMock()
    dupe_rows = dupe_rows or []
    stale_rows = stale_rows or []

    # fetch() calls: first for dupe check, second for stale check
    # Within dupe check, fetchrow() is called for each duplicate group to get discord username
    conn.fetch.side_effect = [dupe_rows, stale_rows]
    if discord_user is not None:
        conn.fetchrow.return_value = discord_user
    else:
        conn.fetchrow.return_value = None
    # _upsert_issue calls fetchval (check existing) and execute (insert)
    conn.fetchval = AsyncMock(return_value=None)  # no existing issues by default
    conn.execute = AsyncMock()
    return conn


def _dupe_row(discord_user_id=100, cnt=2, player_ids=None):
    r = MagicMock()
    r.__getitem__ = lambda self, key: {
        "discord_user_id": discord_user_id,
        "cnt": cnt,
        "player_ids": player_ids or [1, 2],
    }[key]
    return r


def _stale_row(player_id=1, display_name="Trog", discord_user_id=100,
               username="trogmoon", discord_display=None):
    r = MagicMock()
    r.__getitem__ = lambda self, key: {
        "player_id": player_id,
        "display_name": display_name,
        "discord_user_id": discord_user_id,
        "username": username,
        "discord_display": discord_display,
    }[key]
    return r


def _discord_user_row(username="elrek", display_name=None):
    r = MagicMock()
    r.__getitem__ = lambda self, key: {
        "username": username,
        "display_name": display_name,
    }[key]
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDetectDuplicateDiscordLinks:
    @pytest.mark.asyncio
    async def test_no_rows_returns_zero(self):
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        conn = _make_conn(dupe_rows=[], stale_rows=[])
        result = await detect_duplicate_discord_links(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_duplicate_link_creates_issues_per_player(self):
        """Two players linked to same Discord → one issue per player (2 issues)."""
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        dupe = _dupe_row(discord_user_id=100, cnt=2, player_ids=[1, 2])
        du_row = _discord_user_row(username="elrek")
        conn = _make_conn(dupe_rows=[dupe], stale_rows=[], discord_user=du_row)
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        result = await detect_duplicate_discord_links(conn)
        assert result == 2  # one issue per player_id

    @pytest.mark.asyncio
    async def test_duplicate_link_severity_error(self):
        """Duplicate Discord link issues must be severity 'error'."""
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        dupe = _dupe_row(discord_user_id=100, cnt=2, player_ids=[1, 2])
        du_row = _discord_user_row(username="elrek")
        conn = _make_conn(dupe_rows=[dupe], stale_rows=[], discord_user=du_row)
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        await detect_duplicate_discord_links(conn)
        # Check INSERT was called with severity='error'
        for call_args in conn.execute.call_args_list:
            args = call_args[0]
            if args and "INSERT INTO guild_identity.audit_issues" in args[0]:
                # Second positional arg is issue_type, third is severity
                assert args[2] == "error"
                break

    @pytest.mark.asyncio
    async def test_stale_discord_link_creates_issue(self):
        """Player linked to departed Discord user → stale_discord_link issue."""
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        stale = _stale_row(player_id=1, display_name="Trog", discord_user_id=100, username="trogmoon")
        conn = _make_conn(dupe_rows=[], stale_rows=[stale])
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        result = await detect_duplicate_discord_links(conn)
        assert result == 1

    @pytest.mark.asyncio
    async def test_stale_discord_link_severity_info(self):
        """Stale Discord link issues must be severity 'info'."""
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        stale = _stale_row(player_id=1, display_name="Trog", discord_user_id=100, username="trogmoon")
        conn = _make_conn(dupe_rows=[], stale_rows=[stale])
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        await detect_duplicate_discord_links(conn)
        for call_args in conn.execute.call_args_list:
            args = call_args[0]
            if args and "INSERT INTO guild_identity.audit_issues" in args[0]:
                assert args[2] == "info"
                break

    @pytest.mark.asyncio
    async def test_duplicate_and_stale_combined(self):
        """Both duplicate and stale issues can be detected in the same call."""
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        dupe = _dupe_row(discord_user_id=100, cnt=2, player_ids=[1, 2])
        stale = _stale_row(player_id=3, discord_user_id=200)
        du_row = _discord_user_row(username="elrek")
        conn = _make_conn(dupe_rows=[dupe], stale_rows=[stale], discord_user=du_row)
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        result = await detect_duplicate_discord_links(conn)
        assert result == 3  # 2 duplicate issues + 1 stale issue

    @pytest.mark.asyncio
    async def test_existing_issues_not_double_counted(self):
        """If an issue already exists, _upsert_issue returns False → not counted."""
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        stale = _stale_row(player_id=1, display_name="Trog", discord_user_id=100)
        conn = _make_conn(dupe_rows=[], stale_rows=[stale])
        # Simulate existing issue (fetchval returns an ID)
        conn.fetchval = AsyncMock(return_value=99)
        conn.execute = AsyncMock()
        result = await detect_duplicate_discord_links(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_normal_state_no_issues(self):
        """Standard state: no duplicates, no stale links → 0 issues."""
        from sv_common.guild_sync.integrity_checker import detect_duplicate_discord_links
        conn = _make_conn(dupe_rows=[], stale_rows=[])
        result = await detect_duplicate_discord_links(conn)
        assert result == 0
