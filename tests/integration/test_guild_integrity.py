"""
Integration tests for integrity_checker.run_integrity_check().

Tests cover: orphan detection (WoW + Discord), role mismatch detection,
stale character detection, deduplication, and auto-resolution.
"""

from datetime import datetime, timezone, timedelta

import pytest

from sv_common.guild_sync.integrity_checker import run_integrity_check


async def _insert_person(conn, display_name: str) -> int:
    return await conn.fetchval(
        "INSERT INTO guild_identity.persons (display_name) VALUES ($1) RETURNING id",
        display_name,
    )


async def _insert_char(conn, name: str, realm: str = "senjin",
                       rank_name: str = "Member", wow_class: str = "Warrior",
                       person_id: int = None, guild_rank: int = 3,
                       last_login_ts: int = None) -> int:
    return await conn.fetchval(
        """INSERT INTO guild_identity.wow_characters
           (character_name, realm_slug, guild_rank_name, character_class,
            person_id, guild_rank, last_login_timestamp)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
        name, realm, rank_name, wow_class, person_id, guild_rank, last_login_ts,
    )


async def _insert_discord(conn, discord_id: str, username: str,
                          person_id: int = None, is_present: bool = True,
                          highest_role: str = None) -> int:
    return await conn.fetchval(
        """INSERT INTO guild_identity.discord_members
           (discord_id, username, person_id, is_present, highest_guild_role)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        discord_id, username, person_id, is_present, highest_role,
    )


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
            pid = await _insert_person(conn, "Trog")
            await _insert_char(conn, "Trogmoon", person_id=pid)

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


class TestOrphanDiscordDetection:
    async def test_unlinked_discord_with_guild_role_creates_issue(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_discord(conn, "555", "mystery_user",
                                  is_present=True, highest_role="Member")

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 1

    async def test_linked_discord_no_orphan_issue(self, guild_db):
        async with guild_db.acquire() as conn:
            pid = await _insert_person(conn, "Trog")
            await _insert_discord(conn, "666", "trog",
                                  person_id=pid, is_present=True,
                                  highest_role="GM")

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 0

    async def test_discord_without_guild_role_not_flagged(self, guild_db):
        """Discord member without any guild role (e.g., guest) is not an orphan."""
        async with guild_db.acquire() as conn:
            await _insert_discord(conn, "777", "guest", is_present=True,
                                  highest_role=None)

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 0

    async def test_absent_discord_member_not_flagged(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_discord(conn, "888", "leftserver",
                                  is_present=False, highest_role="Member")

        stats = await run_integrity_check(guild_db)

        assert stats["orphan_discord"] == 0


class TestRoleMismatch:
    async def test_ingame_officer_discord_member_creates_mismatch(self, guild_db):
        async with guild_db.acquire() as conn:
            pid = await _insert_person(conn, "TestPerson")
            await _insert_char(conn, "TestChar", rank_name="Officer",
                               guild_rank=1, person_id=pid)
            await _insert_discord(conn, "444", "testperson",
                                  person_id=pid, is_present=True,
                                  highest_role="Member")  # Wrong role

        stats = await run_integrity_check(guild_db)

        assert stats["role_mismatch"] == 1

    async def test_matching_roles_no_mismatch(self, guild_db):
        async with guild_db.acquire() as conn:
            pid = await _insert_person(conn, "Trog")
            await _insert_char(conn, "Trogmoon", rank_name="Guild Leader",
                               guild_rank=0, person_id=pid)
            # GM maps to "Guild Leader" in INGAME_TO_DISCORD_ROLE
            await _insert_discord(conn, "111", "trog",
                                  person_id=pid, is_present=True,
                                  highest_role="GM")

        stats = await run_integrity_check(guild_db)

        assert stats["role_mismatch"] == 0


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
            await _insert_char(conn, "Char2", wow_class="Mage")

        first_run = await run_integrity_check(guild_db)
        second_run = await run_integrity_check(guild_db)

        assert first_run["orphan_wow"] == 2
        assert second_run["orphan_wow"] == 0


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

        # Link the character to a person
        async with guild_db.acquire() as conn:
            pid = await _insert_person(conn, "Found")
            await conn.execute(
                "UPDATE guild_identity.wow_characters SET person_id = $1 WHERE id = $2",
                pid, wc_id,
            )

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
            dm_id = await _insert_discord(conn, "555", "nowlinked",
                                          is_present=True, highest_role="Member")

        await run_integrity_check(guild_db)

        # Link the discord member
        async with guild_db.acquire() as conn:
            pid = await _insert_person(conn, "Linked")
            await conn.execute(
                "UPDATE guild_identity.discord_members SET person_id = $1 WHERE id = $2",
                pid, dm_id,
            )

        await run_integrity_check(guild_db)

        async with guild_db.acquire() as conn:
            issue = await conn.fetchrow(
                "SELECT resolved_at, resolved_by FROM guild_identity.audit_issues "
                "WHERE issue_type = 'orphan_discord' AND discord_member_id = $1",
                dm_id,
            )
        assert issue["resolved_at"] is not None
        assert issue["resolved_by"] == "auto"


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
        # Should mention days
        assert "day" in issue["summary"].lower()
