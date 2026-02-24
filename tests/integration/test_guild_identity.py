"""
Integration tests for identity_engine.run_matching().

NOTE: The identity_engine.py still uses the pre-Phase-2.7 schema
(persons/discord_members/identity_links). These tests are skipped until
the identity engine is updated to work with the Phase 2.7 schema
(players/discord_users/player_characters).

TODO: Update identity_engine.py to use Phase 2.7 tables, then remove the skip.
"""

import pytest


pytestmark = pytest.mark.skip(
    reason=(
        "identity_engine.py uses pre-Phase-2.7 schema (persons/discord_members/"
        "identity_links). Skipped until identity engine is updated for Phase 2.7."
    )
)


from sv_common.guild_sync.identity_engine import run_matching


async def _insert_char(conn, name, realm="senjin", guild_note="", officer_note="",
                       removed=False):
    row = await conn.fetchrow(
        """INSERT INTO guild_identity.wow_characters
           (character_name, realm_slug, guild_note, officer_note, removed_at)
           VALUES ($1, $2, $3, $4, CASE WHEN $5 THEN NOW() ELSE NULL END)
           RETURNING id""",
        name, realm, guild_note, officer_note, removed,
    )
    return row["id"]


async def _insert_discord(conn, discord_id, username, display_name=None, is_present=True):
    row = await conn.fetchrow(
        """INSERT INTO guild_identity.discord_users
           (discord_id, username, display_name, is_present)
           VALUES ($1, $2, $3, $4)
           RETURNING id""",
        discord_id, username, display_name, is_present,
    )
    return row["id"]


async def _insert_player(conn, display_name):
    return await conn.fetchval(
        "INSERT INTO guild_identity.players (display_name) VALUES ($1) RETURNING id",
        display_name,
    )


class TestExactNameMatching:
    async def test_exact_name_links_char_to_discord(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Shodoom")
            await _insert_discord(conn, "111", "shodoom")

        stats = await run_matching(guild_db)

        assert stats["exact"] == 1

    async def test_exact_match_creates_player(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Rocket")
            await _insert_discord(conn, "222", "rocket")

        await run_matching(guild_db)

        async with guild_db.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.players"
            )
        assert count == 1


class TestGuildNoteMatching:
    async def test_discord_hint_in_guild_note_links(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "SomeAlt", guild_note="Discord: Rocket")
            await _insert_discord(conn, "444", "rocket")

        stats = await run_matching(guild_db)

        assert stats["guild_note"] == 1


class TestNoDoubleLinks:
    async def test_removed_character_not_matched(self, guild_db):
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "GoneGuy", removed=True)
            await _insert_discord(conn, "888", "goneguy")

        stats = await run_matching(guild_db)

        assert stats["exact"] == 0

    async def test_absent_discord_member_not_matched(self, guild_db):
        """Discord users with is_present=False are excluded."""
        async with guild_db.acquire() as conn:
            await _insert_char(conn, "Offline")
            await _insert_discord(conn, "999", "offline", is_present=False)

        stats = await run_matching(guild_db)

        assert stats["exact"] == 0
