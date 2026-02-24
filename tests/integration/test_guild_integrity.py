"""
Integration tests for integrity_checker.run_integrity_check().

Tests cover: orphan detection (WoW + Discord), role mismatch detection,
stale character detection, deduplication, and auto-resolution.

Uses Phase 2.7+ schema: players, discord_users, player_characters, wow_characters.
"""

from datetime import datetime, timezone, timedelta

import pytest

from sv_common.guild_sync.integrity_checker import run_integrity_check


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def _insert_player(conn, display_name: str, discord_user_id: int = None) -> int:
    """Insert a player row, optionally linked to a discord_user."""
    return await conn.fetchval(
        """INSERT INTO guild_identity.players (display_name, discord_user_id)
           VALUES ($1, $2) RETURNING id""",
        display_name, discord_user_id,
    )


async def _insert_discord_user(
    conn,
    discord_id: str,
    username: str,
    is_present: bool = True,
    highest_role: str = None,
) -> int:
    """Insert a discord_users row."""
    return await conn.fetchval(
        """INSERT INTO guild_identity.discord_users
           (discord_id, username, is_present, highest_guild_role)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        discord_id, username, is_present, highest_role,
    )


async def _insert_char(
    conn,
    name: str,
    realm: str = "senjin",
    guild_rank_id: int = None,
    last_login_ts: int = None,
) -> int:
    """Insert a wow_characters row."""
    return await conn.fetchval(
        """INSERT INTO guild_identity.wow_characters
           (character_name, realm_slug, guild_rank_id, last_login_timestamp)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        name, realm, guild_rank_id, last_login_ts,
    )


async def _link_char_to_player(conn, player_id: int, char_id: int):
    """Create a player_characters entry linking a character to a player."""
    await conn.execute(
        """INSERT INTO guild_identity.player_characters (player_id, character_id)
           VALUES ($1, $2) ON CONFLICT DO NOTHING""",
        player_id, char_id,
    )


async def _get_rank_id(conn, rank_name: str) -> int:
    """Look up a guild_rank id by name (reference data seeded by migration)."""
    return await conn.fetchval(
        "SELECT id FROM common.guild_ranks WHERE name = $1",
        rank_name,
    )


# ---------------------------------------------------------------------------
# Tests: Orphaned WoW Characters
# ---------------------------------------------------------------------------

class TestOrphanWowDetection:
    async def test_unlinked_char_creates_orphan_wow_issue(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "OrphanChar")

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_wow"] == 1

        async with guild_db.acquire() as conn:
            issue = await conn.fetchrow(
                "SELECT * FROM guild_identity.audit_issues "
                "WHERE issue_type = 'orphan_wow'"
            )
        assert issue is not None
        assert "OrphanChar" in issue["summary"]

    async def test_linked_char_no_orphan_issue(self, guild_db):
        async with guild_db.acquire() as conn:
            player_id = await _insert_player(conn, "Trog")
            char_id = await _insert_char(conn, "Trogmoon")
            await _link_char_to_player(conn, player_id, char_id)

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_wow"] == 0

    async def test_removed_char_not_orphan(self, guild_db):
        async with guild_db.acquire() as conn:
            wc_id = await _insert_char(conn, "GoneChar")
            await conn.execute(
                "UPDATE guild_identity.wow_characters SET removed_at = NOW() WHERE id = $1",
                wc_id,
            )

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_wow"] == 0


# ---------------------------------------------------------------------------
# Tests: Orphaned Discord Users
# ---------------------------------------------------------------------------

class TestOrphanDiscordDetection:
    async def test_unlinked_discord_with_guild_role_creates_issue(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_discord_user(conn, "555", "mystery_user",
                                       is_present=True, highest_role="Member")

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 1

    async def test_linked_discord_no_orphan_issue(self, guild_db):
        async with guild_db.acquire() as conn:
            du_id = await _insert_discord_user(conn, "666", "trog",
                                               is_present=True, highest_role="GM")
            await _insert_player(conn, "Trog", discord_user_id=du_id)

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 0

    async def test_discord_without_guild_role_not_flagged(self, guild_db):
        """Discord user without any guild role (e.g., guest) is not an orphan."""
        async with guild_db.acquire() as conn:
            await _insert_discord_user(conn, "777", "guest",
                                       is_present=True, highest_role=None)

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 0

    async def test_absent_discord_user_not_flagged(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_discord_user(conn, "888", "leftserver",
                                       is_present=False, highest_role="Member")

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 0


# ---------------------------------------------------------------------------
# Tests: Role Mismatches
# ---------------------------------------------------------------------------

class TestRoleMismatch:
    async def test_ingame_officer_discord_member_creates_mismatch(self, guild_db):
        async with guild_db.acquire() as conn:
            officer_rank_id = await _get_rank_id(conn, "Officer")
            du_id = await _insert_discord_user(conn, "444", "testperson",
                                               is_present=True,
                                               highest_role="Member")  # Wrong role
            player_id = await _insert_player(conn, "TestPerson", discord_user_id=du_id)
            char_id = await _insert_char(conn, "TestChar", guild_rank_id=officer_rank_id)
            await _link_char_to_player(conn, player_id, char_id)

        stats = await run_integrity_check(guild_db)

        assert stats["role_mismatch"] == 1

    async def test_matching_roles_no_mismatch(self, guild_db):
        async with guild_db.acquire() as conn:
            gl_rank_id = await _get_rank_id(conn, "Guild Leader")
            du_id = await _insert_discord_user(conn, "111", "trog",
                                               is_present=True,
                                               highest_role="GM")
            player_id = await _insert_player(conn, "Trog", discord_user_id=du_id)
            char_id = await _insert_char(conn, "Trogmoon", guild_rank_id=gl_rank_id)
            await _link_char_to_player(conn, player_id, char_id)

        stats = await run_integrity_check(guild_db)

        assert stats["role_mismatch"] == 0


# ---------------------------------------------------------------------------
# Tests: Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    async def test_second_run_creates_no_new_issues(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "OrphanChar")

        first_run = await run_integrity_check(guild_db)
        second_run = await run_integrity_check(guild_db)

        assert first_run["orphan_wow"] == 1
        assert second_run["orphan_wow"] == 0  # Already tracked

        async with guild_db.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.audit_issues "
                "WHERE issue_type = 'orphan_wow' AND resolved_at IS NULL"
            )
        assert count == 1

    async def test_multiple_orphans_deduplicated_separately(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Char1")
            await _insert_char(conn, "Char2")

        first_run = await run_integrity_check(guild_db)
        second_run = await run_integrity_check(guild_db)

        assert first_run["orphan_wow"] == 2
        assert second_run["orphan_wow"] == 0


# ---------------------------------------------------------------------------
# Tests: Auto-Resolution
# ---------------------------------------------------------------------------

class TestAutoResolution:
    async def test_orphan_wow_auto_resolves_when_linked(self, guild_db):
        async with guild_db.acquire() as conn:
            wc_id = await _insert_char(conn, "WasOrphan")

        await run_integrity_check(guild_db)

        async with guild_db.acquire() as conn:
            issue_before = await conn.fetchrow(
                "SELECT resolved_at FROM guild_identity.audit_issues "
                "WHERE issue_type = 'orphan_wow' AND wow_character_id = $1",
                wc_id,
            )
        assert issue_before["resolved_at"] is None

        # Link the character to a player
        async with guild_db.acquire() as conn:
            player_id = await _insert_player(conn, "Found")
            await _link_char_to_player(conn, player_id, wc_id)

        await run_integrity_check(guild_db)

        async with guild_db.acquire() as conn:
            issue_after = await conn.fetchrow(
                "SELECT resolved_at, resolved_by FROM guild_identity.audit_issues "
                "WHERE issue_type = 'orphan_wow' AND wow_character_id = $1",
                wc_id,
            )
        assert issue_after["resolved_at"] is not None
        assert issue_after["resolved_by"] == "auto"

    async def test_orphan_discord_auto_resolves_when_linked(self, guild_db):
        async with guild_db.acquire() as conn:
            du_id = await _insert_discord_user(conn, "555", "nowlinked",
                                               is_present=True, highest_role="Member")

        await run_integrity_check(guild_db)

        # Create a player linked to this discord user
        async with guild_db.acquire() as conn:
            await _insert_player(conn, "Linked", discord_user_id=du_id)

        await run_integrity_check(guild_db)

        async with guild_db.acquire() as conn:
            issue = await conn.fetchrow(
                "SELECT resolved_at, resolved_by FROM guild_identity.audit_issues "
                "WHERE issue_type = 'orphan_discord' AND discord_member_id = $1",
                du_id,
            )
        assert issue["resolved_at"] is not None
        assert issue["resolved_by"] == "auto"


# ---------------------------------------------------------------------------
# Tests: Stale Character Detection
# ---------------------------------------------------------------------------

class TestStaleCharacterDetection:
    async def test_character_stale_after_30_days(self, guild_db):
        stale_ts = int(
            (datetime.now(timezone.utc) - timedelta(days=45)).timestamp() * 1000
        )
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "InactiveGuy", last_login_ts=stale_ts)

        stats = await run_integrity_check(guild_db)

        assert stats["stale"] == 1

    async def test_recently_active_character_not_stale(self, guild_db):
        recent_ts = int(
            (datetime.now(timezone.utc) - timedelta(days=5)).timestamp() * 1000
        )
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "ActiveGuy", last_login_ts=recent_ts)

        stats = await run_integrity_check(guild_db)

        assert stats["stale"] == 0

    async def test_no_login_timestamp_not_stale(self, guild_db):
        """Characters with no last_login_timestamp are not flagged as stale."""
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "NewChar", last_login_ts=None)

        stats = await run_integrity_check(guild_db)

        assert stats["stale"] == 0

    async def test_stale_character_summary_includes_days(self, guild_db):
        stale_ts = int(
            (datetime.now(timezone.utc) - timedelta(days=60)).timestamp() * 1000
        )
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Dusty", last_login_ts=stale_ts)

        await run_integrity_check(guild_db)

        async with guild_db.acquire() as conn:
            issue = await conn.fetchrow(
                "SELECT summary FROM guild_identity.audit_issues "
                "WHERE issue_type = 'stale_character'"
            )
        assert issue is not None
        assert "Dusty" in issue["summary"]
        assert "day" in issue["summary"].lower()
