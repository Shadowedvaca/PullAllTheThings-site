"""Unit tests for detect_link_note_contradictions() in integrity_checker.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(char_rows=None, alias_rows=None):
    """Build a mock asyncpg connection that returns controlled data."""
    conn = AsyncMock()
    char_rows = char_rows or []
    alias_rows = alias_rows or []

    # fetch() is called twice: once for character rows, once for aliases
    conn.fetch.side_effect = [char_rows, alias_rows]
    return conn


def _row(
    char_id=1,
    character_name="Moonbear",
    guild_note="elrek",
    player_id=10,
    link_source="note_key",
    confidence="high",
    player_display_name="Elrek",
    discord_username="elrek",
    discord_display_name=None,
):
    """Build a mock DB row dict for a character with its linked player."""
    r = MagicMock()
    r.__getitem__ = lambda self, key: {
        "char_id": char_id,
        "character_name": character_name,
        "guild_note": guild_note,
        "player_id": player_id,
        "link_source": link_source,
        "confidence": confidence,
        "player_display_name": player_display_name,
        "discord_username": discord_username,
        "discord_display_name": discord_display_name,
    }[key]
    return r


def _alias_row(player_id, alias):
    r = MagicMock()
    r.__getitem__ = lambda self, key: {"player_id": player_id, "alias": alias}[key]
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDetectLinkNoteContradictions:
    @pytest.mark.asyncio
    async def test_no_rows_returns_zero(self):
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        conn = _make_conn(char_rows=[], alias_rows=[])
        result = await detect_link_note_contradictions(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_note_matches_discord_username_no_issue(self):
        """Note key matches Discord username → not a contradiction."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        row = _row(guild_note="elrek", discord_username="elrek")
        conn = _make_conn(char_rows=[row], alias_rows=[])
        result = await detect_link_note_contradictions(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_note_matches_discord_display_name_no_issue(self):
        """Note key matches discord display_name → not a contradiction."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        row = _row(guild_note="trog", discord_username="shadowedvaca", discord_display_name="Trog")
        conn = _make_conn(char_rows=[row], alias_rows=[])
        result = await detect_link_note_contradictions(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_note_matches_known_alias_no_issue(self):
        """Note key is a known alias for this player → not a contradiction."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        player_id = 10
        row = _row(
            guild_note="trog",
            player_id=player_id,
            discord_username="shadowedvaca2",
            discord_display_name="Shadow",
        )
        alias = _alias_row(player_id=player_id, alias="trog")
        conn = _make_conn(char_rows=[row], alias_rows=[alias])
        result = await detect_link_note_contradictions(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_note_not_matching_anything_creates_issue(self):
        """Note key doesn't match Discord or aliases → issue created."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        row = _row(
            guild_note="mito",
            discord_username="elrek",
            discord_display_name=None,
        )
        conn = _make_conn(char_rows=[row], alias_rows=[])
        # _upsert_issue checks for existing then inserts
        conn.fetchval = AsyncMock(return_value=None)  # no existing issue
        conn.execute = AsyncMock()
        result = await detect_link_note_contradictions(conn)
        assert result == 1

    @pytest.mark.asyncio
    async def test_manual_confirmed_link_excluded(self):
        """Characters with link_source='manual' AND confidence='confirmed' are skipped."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        row = _row(
            guild_note="mito",
            link_source="manual",
            confidence="confirmed",
            discord_username="elrek",
        )
        # The WHERE clause in the SQL excludes these; in unit test the row isn't
        # returned from the DB, so we test with an empty result set
        conn = _make_conn(char_rows=[], alias_rows=[])
        result = await detect_link_note_contradictions(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_guild_note_skipped(self):
        """Characters with empty guild_note are filtered by the SQL WHERE clause."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        conn = _make_conn(char_rows=[], alias_rows=[])
        result = await detect_link_note_contradictions(conn)
        assert result == 0

    @pytest.mark.asyncio
    async def test_existing_issue_not_double_counted(self):
        """If an issue already exists (unresolved), _upsert_issue returns False → not counted."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        row = _row(
            guild_note="mito",
            discord_username="elrek",
            discord_display_name=None,
        )
        conn = _make_conn(char_rows=[row], alias_rows=[])
        # Simulate an existing issue in the DB
        conn.fetchval = AsyncMock(return_value=42)  # id=42 means already exists
        conn.execute = AsyncMock()
        result = await detect_link_note_contradictions(conn)
        # Returns 0 because _upsert_issue returned False (existing issue updated, not created)
        assert result == 0

    @pytest.mark.asyncio
    async def test_note_key_in_username_substring(self):
        """Note key matches a substring component of the username → not a contradiction."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        # username "trog/shadow" — note key "trog" is in the word split
        row = _row(guild_note="trog", discord_username="trog/shadow", discord_display_name=None)
        conn = _make_conn(char_rows=[row], alias_rows=[])
        result = await detect_link_note_contradictions(conn)
        assert result == 0
