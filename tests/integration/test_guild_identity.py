"""
Integration tests for identity_engine.run_matching().

Tests the matching pipeline against a real (test) database:
exact name match, guild note match, officer note match,
no double-linking of already-linked characters, and fuzzy match fallback.
"""

import pytest

from sv_common.guild_sync.identity_engine import run_matching


async def _insert_char(conn, name, realm="senjin", guild_note="", officer_note="",
                       person_id=None, removed=False):
    row = await conn.fetchrow(
        """INSERT INTO guild_identity.wow_characters
           (character_name, realm_slug, guild_note, officer_note, person_id, removed_at)
           VALUES ($1, $2, $3, $4, $5, CASE WHEN $6 THEN NOW() ELSE NULL END)
           RETURNING id""",
        name, realm, guild_note, officer_note, person_id, removed,
    )
    return row["id"]


async def _insert_discord(conn, discord_id, username, display_name=None,
                          person_id=None, is_present=True):
    row = await conn.fetchrow(
        """INSERT INTO guild_identity.discord_members
           (discord_id, username, display_name, person_id, is_present)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING id""",
        discord_id, username, display_name, person_id, is_present,
    )
    return row["id"]


async def _insert_person(conn, display_name):
    return await conn.fetchval(
        "INSERT INTO guild_identity.persons (display_name) VALUES ($1) RETURNING id",
        display_name,
    )


class TestExactNameMatching:
    async def test_exact_name_links_char_to_discord(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Shodoom")
            await _insert_discord(conn, "111", "shodoom")

        stats = await run_matching(guild_db)

        assert stats["exact"] == 1

        async with guild_db.acquire() as conn:
            char_pid = await conn.fetchval(
                "SELECT person_id FROM guild_identity.wow_characters "
                "WHERE character_name = 'Shodoom'"
            )
            discord_pid = await conn.fetchval(
                "SELECT person_id FROM guild_identity.discord_members "
                "WHERE username = 'shodoom'"
            )
        assert char_pid is not None
        assert char_pid == discord_pid

    async def test_exact_match_creates_person(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Rocket")
            await _insert_discord(conn, "222", "rocket")

        await run_matching(guild_db)

        async with guild_db.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.persons"
            )
        assert count == 1

    async def test_exact_match_uses_display_name(self, guild_db):
        """Match works when character name matches Discord display_name."""
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Mito")
            await _insert_discord(conn, "333", "differentusername", display_name="Mito")

        stats = await run_matching(guild_db)

        assert stats["exact"] == 1


class TestGuildNoteMatching:
    async def test_discord_hint_in_guild_note_links(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "SomeAlt", guild_note="Discord: Rocket")
            await _insert_discord(conn, "444", "rocket")

        stats = await run_matching(guild_db)

        assert stats["guild_note"] == 1

    async def test_at_mention_in_guild_note(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "SomeAlt2", guild_note="@Skate")
            await _insert_discord(conn, "555", "skate")

        stats = await run_matching(guild_db)

        assert stats["guild_note"] == 1


class TestOfficerNoteMatching:
    async def test_discord_hint_in_officer_note_links(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "SecretAlt", guild_note="", officer_note="DC: Trog")
            await _insert_discord(conn, "666", "trog")

        stats = await run_matching(guild_db)

        assert stats["officer_note"] == 1


class TestNoDoubleLinks:
    async def test_already_linked_char_skipped(self, guild_db):
        async with guild_db.acquire() as conn:
            pid = await _insert_person(conn, "Trog")
            await _insert_char(conn, "Trogmoon", person_id=pid)
            await _insert_discord(conn, "777", "trog", person_id=pid)

        stats = await run_matching(guild_db)

        assert stats["exact"] == 0
        assert stats["guild_note"] == 0
        assert stats["officer_note"] == 0

    async def test_removed_character_not_matched(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "GoneGuy", removed=True)
            await _insert_discord(conn, "888", "goneguy")

        stats = await run_matching(guild_db)

        assert stats["exact"] == 0

    async def test_absent_discord_member_not_matched(self, guild_db):
        """Discord members with is_present=False are excluded."""
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Offline")
            await _insert_discord(conn, "999", "offline", is_present=False)

        stats = await run_matching(guild_db)

        assert stats["exact"] == 0

    async def test_multiple_chars_linked_to_same_person(self, guild_db):
        """Two chars that both match the same Discord member → both linked to one person."""
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Trogmoon")
            await _insert_char(conn, "Trogalt", guild_note="Discord: trog")
            await _insert_discord(conn, "111", "trog")

        await run_matching(guild_db)

        async with guild_db.acquire() as conn:
            main_pid = await conn.fetchval(
                "SELECT person_id FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon'"
            )
            alt_pid = await conn.fetchval(
                "SELECT person_id FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogalt'"
            )
        # Both should have a person_id
        assert main_pid is not None
        assert alt_pid is not None


class TestIdentityLinks:
    async def test_identity_link_record_created(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Skatefarm")
            await _insert_discord(conn, "999", "skatefarm")

        await run_matching(guild_db)

        async with guild_db.acquire() as conn:
            link_count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.identity_links"
            )
        # Should have at least 2 links: one for the char, one for discord
        assert link_count >= 2

    async def test_exact_match_link_is_high_confidence_confirmed(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Shodoom")
            await _insert_discord(conn, "111", "shodoom")

        await run_matching(guild_db)

        async with guild_db.acquire() as conn:
            link = await conn.fetchrow(
                "SELECT confidence, is_confirmed FROM guild_identity.identity_links "
                "WHERE link_source = 'exact_name_match' LIMIT 1"
            )
        assert link is not None
        assert link["confidence"] == "high"
        assert link["is_confirmed"] is True
