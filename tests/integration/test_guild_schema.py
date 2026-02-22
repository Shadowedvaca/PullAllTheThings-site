"""
Integration tests: guild_identity schema validation.

Verifies that the schema, tables, and constraints exist as expected.
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
            "persons", "wow_characters", "discord_members",
            "identity_links", "audit_issues", "sync_log",
        ]
        async with guild_sync_pool.acquire() as conn:
            for table in expected_tables:
                result = await conn.fetchval(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'guild_identity' AND table_name = $1",
                    table,
                )
                assert result == table, f"Table '{table}' not found in guild_identity schema"


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


class TestDiscordMemberConstraints:
    async def test_discord_id_must_be_unique(self, guild_db):
        async with guild_db.acquire() as conn:
            await conn.execute(
                "INSERT INTO guild_identity.discord_members "
                "(discord_id, username) VALUES ('123456', 'userA')"
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.discord_members "
                    "(discord_id, username) VALUES ('123456', 'userB')"
                )


class TestIdentityLinkConstraints:
    async def test_character_can_only_be_linked_once(self, guild_db):
        async with guild_db.acquire() as conn:
            p1 = await conn.fetchval(
                "INSERT INTO guild_identity.persons (display_name) "
                "VALUES ('PersonOne') RETURNING id"
            )
            p2 = await conn.fetchval(
                "INSERT INTO guild_identity.persons (display_name) "
                "VALUES ('PersonTwo') RETURNING id"
            )
            wc = await conn.fetchval(
                "INSERT INTO guild_identity.wow_characters "
                "(character_name, realm_slug) VALUES ('TestChar', 'senjin') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO guild_identity.identity_links "
                "(person_id, wow_character_id, link_source, confidence) "
                "VALUES ($1, $2, 'test', 'high')",
                p1, wc,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.identity_links "
                    "(person_id, wow_character_id, link_source, confidence) "
                    "VALUES ($1, $2, 'test', 'high')",
                    p2, wc,
                )

    async def test_discord_member_can_only_be_linked_once(self, guild_db):
        async with guild_db.acquire() as conn:
            p1 = await conn.fetchval(
                "INSERT INTO guild_identity.persons (display_name) "
                "VALUES ('PersonOne') RETURNING id"
            )
            p2 = await conn.fetchval(
                "INSERT INTO guild_identity.persons (display_name) "
                "VALUES ('PersonTwo') RETURNING id"
            )
            dm = await conn.fetchval(
                "INSERT INTO guild_identity.discord_members "
                "(discord_id, username) VALUES ('999', 'testuser') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO guild_identity.identity_links "
                "(person_id, discord_member_id, link_source, confidence) "
                "VALUES ($1, $2, 'test', 'high')",
                p1, dm,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.identity_links "
                    "(person_id, discord_member_id, link_source, confidence) "
                    "VALUES ($1, $2, 'test', 'high')",
                    p2, dm,
                )

    async def test_link_requires_at_least_one_target(self, guild_db):
        """identity_links CHECK constraint: at least one of wow/discord must be set."""
        async with guild_db.acquire() as conn:
            p = await conn.fetchval(
                "INSERT INTO guild_identity.persons (display_name) "
                "VALUES ('Nobody') RETURNING id"
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO guild_identity.identity_links "
                    "(person_id, link_source, confidence) "
                    "VALUES ($1, 'test', 'high')",
                    p,
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
