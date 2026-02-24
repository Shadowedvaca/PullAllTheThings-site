"""
Integration tests: guild_identity schema validation (Phase 2.7).

Verifies that the Phase 2.7 schema, tables, and constraints exist as expected.
These tests require a running PostgreSQL instance with the guild_identity
schema created (via test_engine fixture which calls Base.metadata.create_all).
"""

import pytest
import asyncpg


class TestSchemaExists:
    async def test_schema_exists(self, guild_sync_pool):
        async with guild_sync_pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'guild_identity'"
            )
        assert result == "guild_identity"

    async def test_all_expected_tables_exist(self, guild_sync_pool):
        expected_tables = [
            "players", "wow_characters", "discord_users",
            "player_characters", "audit_issues", "sync_log",
            "classes", "specializations", "roles",
        ]
        async with guild_sync_pool.acquire() as conn:
            for table in expected_tables:
                result = await conn.fetchval(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'guild_identity' AND table_name = $1",
                    table,
                )
                assert result == table, f"Table '{table}' not found in guild_identity schema"

    async def test_old_tables_removed(self, guild_sync_pool):
        """Verify legacy tables no longer exist after Phase 2.7 migration."""
        removed_tables = ["persons", "discord_members", "identity_links"]
        async with guild_sync_pool.acquire() as conn:
            for table in removed_tables:
                result = await conn.fetchval(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'guild_identity' AND table_name = $1",
                    table,
                )
                assert result is None, f"Old table '{table}' still exists — migration may not have run"


class TestCharacterConstraints:
    async def test_character_name_realm_unique(self, guild_db):
        async with guild_db.acquire() as conn:
            await conn.execute(
                "INSERT INTO guild_identity.wow_characters "
                "(character_name, realm_slug) VALUES ('Trogmoon', 'senjin')"
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.wow_characters "
                    "(character_name, realm_slug) VALUES ('Trogmoon', 'senjin')"
                )

    async def test_same_name_different_realm_allowed(self, guild_db):
        async with guild_db.acquire() as conn:
            await conn.execute(
                "INSERT INTO guild_identity.wow_characters "
                "(character_name, realm_slug) VALUES ('Trogmoon', 'senjin')"
            )
            await conn.execute(
                "INSERT INTO guild_identity.wow_characters "
                "(character_name, realm_slug) VALUES ('Trogmoon', 'area-52')"
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon'"
            )
        assert count == 2


class TestDiscordUserConstraints:
    async def test_discord_id_must_be_unique(self, guild_db):
        async with guild_db.acquire() as conn:
            await conn.execute(
                "INSERT INTO guild_identity.discord_users "
                "(discord_id, username) VALUES ('123456', 'userA')"
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.discord_users "
                    "(discord_id, username) VALUES ('123456', 'userB')"
                )


class TestPlayerCharactersConstraints:
    async def test_character_can_only_belong_to_one_player(self, guild_db):
        """A wow_character can only be linked to one player (UNIQUE on character_id)."""
        async with guild_db.acquire() as conn:
            p1 = await conn.fetchval(
                "INSERT INTO guild_identity.players (display_name) "
                "VALUES ('PlayerOne') RETURNING id"
            )
            p2 = await conn.fetchval(
                "INSERT INTO guild_identity.players (display_name) "
                "VALUES ('PlayerTwo') RETURNING id"
            )
            wc = await conn.fetchval(
                "INSERT INTO guild_identity.wow_characters "
                "(character_name, realm_slug) VALUES ('TestChar', 'senjin') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO guild_identity.player_characters "
                "(player_id, character_id) VALUES ($1, $2)",
                p1, wc,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.player_characters "
                    "(player_id, character_id) VALUES ($1, $2)",
                    p2, wc,
                )

    async def test_player_can_have_multiple_characters(self, guild_db):
        """A player can own many characters."""
        async with guild_db.acquire() as conn:
            player_id = await conn.fetchval(
                "INSERT INTO guild_identity.players (display_name) "
                "VALUES ('MultiCharPlayer') RETURNING id"
            )
            wc1 = await conn.fetchval(
                "INSERT INTO guild_identity.wow_characters "
                "(character_name, realm_slug) VALUES ('MainChar', 'senjin') RETURNING id"
            )
            wc2 = await conn.fetchval(
                "INSERT INTO guild_identity.wow_characters "
                "(character_name, realm_slug) VALUES ('AltChar', 'senjin') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO guild_identity.player_characters (player_id, character_id) "
                "VALUES ($1, $2), ($1, $3)",
                player_id, wc1, wc2,
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.player_characters WHERE player_id = $1",
                player_id,
            )
        assert count == 2

    async def test_discord_user_id_unique_on_player(self, guild_db):
        """Two players cannot share the same discord_user_id."""
        async with guild_db.acquire() as conn:
            du = await conn.fetchval(
                "INSERT INTO guild_identity.discord_users "
                "(discord_id, username) VALUES ('777', 'discorduser') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO guild_identity.players "
                "(display_name, discord_user_id) VALUES ('Player1', $1)",
                du,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.players "
                    "(display_name, discord_user_id) VALUES ('Player2', $1)",
                    du,
                )


class TestAuditIssueConstraints:
    async def test_resolved_issue_allows_new_with_same_hash(self, guild_db):
        """After resolving an issue, a new one with the same hash can be created."""
        async with guild_db.acquire() as conn:
            # Resolved issue
            await conn.execute(
                "INSERT INTO guild_identity.audit_issues "
                "(issue_type, severity, summary, issue_hash, resolved_at) "
                "VALUES ('test', 'info', 'Old issue', 'hash999', NOW())"
            )
            # New unresolved issue with same hash — should succeed
            await conn.execute(
                "INSERT INTO guild_identity.audit_issues "
                "(issue_type, severity, summary, issue_hash) "
                "VALUES ('test', 'info', 'New issue', 'hash999')"
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.audit_issues "
                "WHERE issue_hash = 'hash999'"
            )
        assert count == 2

    async def test_upsert_issue_dedup_at_app_level(self, guild_db):
        """_upsert_issue returns False on second call, doesn't create duplicate."""
        from sv_common.guild_sync.integrity_checker import _upsert_issue, make_issue_hash

        issue_hash = make_issue_hash("test_type", 99999)

        async with guild_db.acquire() as conn:
            created_first = await _upsert_issue(
                conn,
                issue_type="test_type",
                severity="info",
                summary="First occurrence",
                details={},
                issue_hash=issue_hash,
            )
            created_second = await _upsert_issue(
                conn,
                issue_type="test_type",
                severity="info",
                summary="Second occurrence",
                details={},
                issue_hash=issue_hash,
            )

        assert created_first is True
        assert created_second is False

        async with guild_db.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.audit_issues "
                "WHERE issue_hash = $1 AND resolved_at IS NULL",
                issue_hash,
            )
        assert count == 1
