# Phase 2.5D: Test Suite

## Overview

Complete test suite for the Guild Identity & Integrity System.
Uses pytest with pytest-asyncio for async tests.
All database tests use a test schema that gets created/destroyed per test session.

## Dependencies

```
# Add to requirements.txt or pyproject.toml
pytest>=7.0
pytest-asyncio>=0.21
pytest-mock>=3.10
httpx>=0.25  # Already in main deps
asyncpg>=0.28  # Already in main deps
```

## Test Configuration

```python
# tests/conftest.py

import asyncio
import os
import pytest
import pytest_asyncio
import asyncpg

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://patt_test:testpass@localhost/patt_test"
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """Create a test database pool and initialize schema."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL)
    
    # Read and execute the schema SQL
    schema_path = os.path.join(
        os.path.dirname(__file__), '..', 'sv_common', 'guild_sync', 'schema.sql'
    )
    
    async with pool.acquire() as conn:
        # Drop and recreate test schema
        await conn.execute("DROP SCHEMA IF EXISTS guild_identity CASCADE")
        with open(schema_path, 'r') as f:
            await conn.execute(f.read())
    
    yield pool
    
    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS guild_identity CASCADE")
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_pool):
    """Truncate all tables between tests."""
    async with db_pool.acquire() as conn:
        await conn.execute("""
            TRUNCATE guild_identity.persons,
                     guild_identity.wow_characters,
                     guild_identity.discord_members,
                     guild_identity.identity_links,
                     guild_identity.audit_issues,
                     guild_identity.sync_log
            CASCADE
        """)
    yield
```

## Task 1: Schema Tests

```python
# tests/test_schema.py

import pytest
import pytest_asyncio


@pytest.mark.asyncio
async def test_schema_exists(db_pool):
    """Verify the guild_identity schema was created."""
    async with db_pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'guild_identity'"
        )
        assert result == "guild_identity"


@pytest.mark.asyncio
async def test_all_tables_exist(db_pool):
    """Verify all expected tables exist."""
    expected_tables = [
        "persons", "wow_characters", "discord_members",
        "identity_links", "audit_issues", "sync_log"
    ]
    async with db_pool.acquire() as conn:
        for table in expected_tables:
            result = await conn.fetchval(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema = 'guild_identity' AND table_name = $1""",
                table
            )
            assert result == table, f"Table {table} not found"


@pytest.mark.asyncio
async def test_character_unique_constraint(db_pool):
    """Characters must be unique by name + realm."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug) VALUES ('Trogmoon', 'senjin')"""
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """INSERT INTO guild_identity.wow_characters
                   (character_name, realm_slug) VALUES ('Trogmoon', 'senjin')"""
            )


@pytest.mark.asyncio
async def test_character_different_realm_allowed(db_pool):
    """Same character name on different realms should be allowed."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug) VALUES ('Trogmoon', 'senjin')"""
        )
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug) VALUES ('Trogmoon', 'area-52')"""
        )
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.wow_characters WHERE character_name = 'Trogmoon'"
        )
        assert count == 2


@pytest.mark.asyncio
async def test_discord_member_unique_id(db_pool):
    """Discord IDs must be unique."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username) VALUES ('123456', 'testuser')"""
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """INSERT INTO guild_identity.discord_members
                   (discord_id, username) VALUES ('123456', 'otheruser')"""
            )


@pytest.mark.asyncio
async def test_identity_link_one_char_one_person(db_pool):
    """A character can only be linked to one person."""
    async with db_pool.acquire() as conn:
        p1 = await conn.fetchval(
            "INSERT INTO guild_identity.persons (display_name) VALUES ('Person1') RETURNING id"
        )
        p2 = await conn.fetchval(
            "INSERT INTO guild_identity.persons (display_name) VALUES ('Person2') RETURNING id"
        )
        wc = await conn.fetchval(
            """INSERT INTO guild_identity.wow_characters (character_name, realm_slug)
               VALUES ('TestChar', 'senjin') RETURNING id"""
        )
        await conn.execute(
            """INSERT INTO guild_identity.identity_links
               (person_id, wow_character_id, link_source, confidence)
               VALUES ($1, $2, 'test', 'high')""",
            p1, wc
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """INSERT INTO guild_identity.identity_links
                   (person_id, wow_character_id, link_source, confidence)
                   VALUES ($1, $2, 'test', 'high')""",
                p2, wc
            )


@pytest.mark.asyncio
async def test_audit_issue_dedup(db_pool):
    """Same issue_hash can't have two unresolved entries."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.audit_issues
               (issue_type, severity, summary, issue_hash)
               VALUES ('test', 'info', 'Test issue', 'hash123')"""
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """INSERT INTO guild_identity.audit_issues
                   (issue_type, severity, summary, issue_hash)
                   VALUES ('test', 'info', 'Test issue 2', 'hash123')"""
            )


@pytest.mark.asyncio
async def test_audit_issue_resolved_allows_new(db_pool):
    """Resolved issues allow a new issue with same hash."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.audit_issues
               (issue_type, severity, summary, issue_hash, resolved_at)
               VALUES ('test', 'info', 'Old issue', 'hash123', NOW())"""
        )
        # This should succeed because the previous one is resolved
        await conn.execute(
            """INSERT INTO guild_identity.audit_issues
               (issue_type, severity, summary, issue_hash)
               VALUES ('test', 'info', 'New issue', 'hash123')"""
        )
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.audit_issues WHERE issue_hash = 'hash123'"
        )
        assert count == 2
```

## Task 2: Blizzard Client Tests

```python
# tests/test_blizzard_client.py

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from sv_common.guild_sync.blizzard_client import (
    BlizzardClient, GuildMemberData, CharacterProfileData,
    CLASS_ID_MAP, RANK_NAME_MAP,
)


@pytest_asyncio.fixture
async def client():
    """Create a BlizzardClient with mocked HTTP."""
    c = BlizzardClient(
        client_id="test_id",
        client_secret="test_secret",
    )
    c._http_client = AsyncMock()
    c._access_token = "mock_token"
    c._token_expires_at = 9999999999  # Far future
    yield c


@pytest.mark.asyncio
async def test_token_refresh(client):
    """Test OAuth token refresh flow."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new_token",
        "expires_in": 86400,
    }
    mock_response.raise_for_status = MagicMock()
    
    client._http_client.post = AsyncMock(return_value=mock_response)
    client._token_expires_at = 0  # Force refresh
    
    await client._ensure_token()
    assert client._access_token == "new_token"


@pytest.mark.asyncio
async def test_parse_guild_roster(client):
    """Test guild roster API response parsing."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "members": [
            {
                "character": {
                    "name": "Trogmoon",
                    "realm": {"slug": "senjin", "name": "Sen'jin"},
                    "level": 80,
                    "playable_class": {"id": 11},  # Druid
                },
                "rank": 0,
            },
            {
                "character": {
                    "name": "Shodoom",
                    "realm": {"slug": "bleeding-hollow", "name": "Bleeding Hollow"},
                    "level": 80,
                    "playable_class": {"id": 7},  # Shaman
                },
                "rank": 1,
            },
        ]
    }
    mock_response.raise_for_status = MagicMock()
    client._http_client.get = AsyncMock(return_value=mock_response)
    
    roster = await client.get_guild_roster()
    
    assert len(roster) == 2
    assert roster[0].character_name == "Trogmoon"
    assert roster[0].character_class == "Druid"
    assert roster[0].guild_rank == 0
    assert roster[1].character_name == "Shodoom"
    assert roster[1].character_class == "Shaman"
    assert roster[1].guild_rank == 1


@pytest.mark.asyncio
async def test_parse_character_profile(client):
    """Test character profile response parsing with spec and ilvl."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "Trogmoon",
        "realm": {"slug": "senjin", "name": "Sen'jin"},
        "character_class": {"name": "Druid"},
        "active_spec": {"name": "Balance"},
        "level": 80,
        "equipped_item_level": 623,
        "last_login_timestamp": 1740000000000,
        "race": {"name": "Tauren"},
        "gender": {"name": "Male"},
    }
    mock_response.raise_for_status = MagicMock()
    client._http_client.get = AsyncMock(return_value=mock_response)
    
    profile = await client.get_character_profile("senjin", "Trogmoon")
    
    assert profile is not None
    assert profile.character_name == "Trogmoon"
    assert profile.active_spec == "Balance"
    assert profile.item_level == 623
    assert profile.character_class == "Druid"


@pytest.mark.asyncio
async def test_special_characters_in_name(client):
    """Test that special characters (like Zatañña) are URL-encoded properly."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "Zatañña",
        "realm": {"slug": "sargeras", "name": "Sargeras"},
        "character_class": {"name": "Mage"},
        "active_spec": {"name": "Arcane"},
        "level": 80,
    }
    mock_response.raise_for_status = MagicMock()
    client._http_client.get = AsyncMock(return_value=mock_response)
    
    profile = await client.get_character_profile("sargeras", "Zatañña")
    
    assert profile is not None
    assert profile.character_name == "Zatañña"
    # Verify the URL was properly encoded
    call_args = client._http_client.get.call_args
    url = call_args[0][0]
    assert "zata" in url.lower()  # Name was lowercased


@pytest.mark.asyncio
async def test_character_not_found(client):
    """Test 404 handling for deleted/transferred characters."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    client._http_client.get = AsyncMock(return_value=mock_response)
    
    profile = await client.get_character_profile("senjin", "DeletedChar")
    assert profile is None


@pytest.mark.asyncio
async def test_class_id_mapping():
    """Verify all WoW classes are mapped."""
    expected_classes = [
        "Warrior", "Paladin", "Hunter", "Rogue", "Priest",
        "Death Knight", "Shaman", "Mage", "Warlock", "Monk",
        "Druid", "Demon Hunter", "Evoker",
    ]
    for cls in expected_classes:
        assert cls in CLASS_ID_MAP.values(), f"Missing class: {cls}"


@pytest.mark.asyncio
async def test_rank_name_mapping():
    """Verify PATT rank names are mapped."""
    assert RANK_NAME_MAP[0] == "Guild Leader"
    assert RANK_NAME_MAP[1] == "Officer"
    assert RANK_NAME_MAP[2] == "Veteran"
    assert RANK_NAME_MAP[3] == "Member"
    assert RANK_NAME_MAP[4] == "Initiate"
```

## Task 3: Database Sync Tests

```python
# tests/test_db_sync.py

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from sv_common.guild_sync.blizzard_client import CharacterProfileData
from sv_common.guild_sync.db_sync import sync_blizzard_roster, sync_addon_data


@pytest.mark.asyncio
async def test_new_character_creates_row(db_pool):
    """New character from Blizzard API creates a DB row."""
    chars = [
        CharacterProfileData(
            character_name="Trogmoon",
            realm_slug="senjin",
            realm_name="Sen'jin",
            character_class="Druid",
            active_spec="Balance",
            level=80,
            item_level=623,
            guild_rank=0,
            guild_rank_name="Guild Leader",
        )
    ]
    
    stats = await sync_blizzard_roster(db_pool, chars)
    
    assert stats["new"] == 1
    assert stats["found"] == 1
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM guild_identity.wow_characters WHERE character_name = 'Trogmoon'"
        )
        assert row is not None
        assert row['character_class'] == "Druid"
        assert row['active_spec'] == "Balance"
        assert row['guild_rank'] == 0
        assert row['role_category'] == "Ranged"


@pytest.mark.asyncio
async def test_existing_character_updates(db_pool):
    """Existing character gets updated fields on re-sync."""
    # First sync
    chars_v1 = [
        CharacterProfileData(
            character_name="Trogmoon", realm_slug="senjin", realm_name="Sen'jin",
            character_class="Druid", active_spec="Balance", level=79, item_level=600,
            guild_rank=0, guild_rank_name="Guild Leader",
        )
    ]
    await sync_blizzard_roster(db_pool, chars_v1)
    
    # Second sync with updated data
    chars_v2 = [
        CharacterProfileData(
            character_name="Trogmoon", realm_slug="senjin", realm_name="Sen'jin",
            character_class="Druid", active_spec="Balance", level=80, item_level=623,
            guild_rank=0, guild_rank_name="Guild Leader",
        )
    ]
    stats = await sync_blizzard_roster(db_pool, chars_v2)
    
    assert stats["updated"] == 1
    assert stats["new"] == 0
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM guild_identity.wow_characters WHERE character_name = 'Trogmoon'"
        )
        assert row['level'] == 80
        assert row['item_level'] == 623


@pytest.mark.asyncio
async def test_character_removal_detection(db_pool):
    """Characters missing from roster get marked as removed."""
    # Sync with two characters
    chars = [
        CharacterProfileData(
            character_name="Trogmoon", realm_slug="senjin", realm_name="Sen'jin",
            character_class="Druid", active_spec="Balance", level=80, guild_rank=0,
            guild_rank_name="Guild Leader",
        ),
        CharacterProfileData(
            character_name="LeavingGuy", realm_slug="senjin", realm_name="Sen'jin",
            character_class="Warrior", active_spec="Arms", level=80, guild_rank=3,
            guild_rank_name="Member",
        ),
    ]
    await sync_blizzard_roster(db_pool, chars)
    
    # Re-sync without LeavingGuy
    chars_after = [chars[0]]
    stats = await sync_blizzard_roster(db_pool, chars_after)
    
    assert stats["removed"] == 1
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT removed_at FROM guild_identity.wow_characters WHERE character_name = 'LeavingGuy'"
        )
        assert row['removed_at'] is not None


@pytest.mark.asyncio
async def test_character_returns_to_guild(db_pool):
    """Removed character reappearing clears removed_at."""
    # Initial sync
    char = CharacterProfileData(
        character_name="ReturnGuy", realm_slug="senjin", realm_name="Sen'jin",
        character_class="Mage", active_spec="Fire", level=80, guild_rank=3,
        guild_rank_name="Member",
    )
    await sync_blizzard_roster(db_pool, [char])
    
    # Remove
    await sync_blizzard_roster(db_pool, [])
    
    # Return
    stats = await sync_blizzard_roster(db_pool, [char])
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT removed_at FROM guild_identity.wow_characters WHERE character_name = 'ReturnGuy'"
        )
        assert row['removed_at'] is None


@pytest.mark.asyncio
async def test_addon_data_updates_notes(db_pool):
    """Addon upload updates guild_note and officer_note fields."""
    # Create character first (via Blizzard sync)
    char = CharacterProfileData(
        character_name="Trogmoon", realm_slug="senjin", realm_name="Sen'jin",
        character_class="Druid", active_spec="Balance", level=80, guild_rank=0,
        guild_rank_name="Guild Leader",
    )
    await sync_blizzard_roster(db_pool, [char])
    
    # Upload addon data
    addon_data = [
        {
            "name": "Trogmoon",
            "realm": "Sen'jin",
            "guild_note": "GM / Mike",
            "officer_note": "Discord: Trog",
        }
    ]
    stats = await sync_addon_data(db_pool, addon_data)
    
    assert stats["updated"] == 1
    
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT guild_note, officer_note FROM guild_identity.wow_characters WHERE character_name = 'Trogmoon'"
        )
        assert row['guild_note'] == "GM / Mike"
        assert row['officer_note'] == "Discord: Trog"
```

## Task 4: Identity Engine Tests

```python
# tests/test_identity_engine.py

import pytest
import pytest_asyncio

from sv_common.guild_sync.identity_engine import (
    normalize_name, extract_discord_hints_from_note,
    fuzzy_match_score, run_matching,
)
from sv_common.guild_sync.migration import get_role_category


class TestNormalizeName:
    def test_basic(self):
        assert normalize_name("Trogmoon") == "trogmoon"
    
    def test_accents(self):
        assert normalize_name("Zatañña") == "zatanna"
    
    def test_empty(self):
        assert normalize_name("") == ""
        assert normalize_name(None) == ""
    
    def test_whitespace(self):
        assert normalize_name("  Trog  ") == "trog"


class TestExtractDiscordHints:
    def test_discord_colon(self):
        assert "trog" in [h.lower() for h in extract_discord_hints_from_note("Discord: Trog")]
    
    def test_dc_colon(self):
        assert "trog" in [h.lower() for h in extract_discord_hints_from_note("DC: Trog")]
    
    def test_at_mention(self):
        assert "trog" in [h.lower() for h in extract_discord_hints_from_note("@Trog")]
    
    def test_alt_of(self):
        hints = extract_discord_hints_from_note("alt of Trogmoon")
        assert any("trogmoon" in h.lower() for h in hints)
    
    def test_main_equals(self):
        hints = extract_discord_hints_from_note("Main: Trogmoon")
        assert any("trogmoon" in h.lower() for h in hints)
    
    def test_empty_note(self):
        assert extract_discord_hints_from_note("") == []
        assert extract_discord_hints_from_note(None) == []
    
    def test_no_hint(self):
        assert extract_discord_hints_from_note("Just a regular note") == []


class TestFuzzyMatchScore:
    def test_exact_match(self):
        assert fuzzy_match_score("Shodoom", "shodoom") == 1.0
    
    def test_contained_name(self):
        score = fuzzy_match_score("Trog", "Trogmoon")
        assert 0.4 < score < 0.7  # Partial match
    
    def test_completely_different(self):
        score = fuzzy_match_score("Trogmoon", "Skatefarm")
        assert score < 0.4
    
    def test_empty(self):
        assert fuzzy_match_score("", "test") == 0.0


class TestRoleCategoryDetection:
    def test_frost_dk_is_melee(self):
        assert get_role_category("Death Knight", "Frost", "") == "Melee"
    
    def test_frost_mage_is_ranged(self):
        assert get_role_category("Mage", "Frost", "") == "Ranged"
    
    def test_balance_druid(self):
        assert get_role_category("Druid", "Balance", "") == "Ranged"
    
    def test_feral_druid(self):
        assert get_role_category("Druid", "Feral", "") == "Melee"
    
    def test_guardian_druid(self):
        assert get_role_category("Druid", "Guardian", "") == "Tank"
    
    def test_resto_druid(self):
        assert get_role_category("Druid", "Restoration", "") == "Healer"
    
    def test_explicit_role_overrides(self):
        assert get_role_category("Unknown", "Unknown", "Tank") == "Tank"


@pytest.mark.asyncio
async def test_exact_name_matching(db_pool):
    """Characters with names matching Discord usernames get auto-linked."""
    async with db_pool.acquire() as conn:
        # Create unlinked character
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, character_class)
               VALUES ('Shodoom', 'bleeding-hollow', 'Shaman')"""
        )
        # Create unlinked Discord member
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username, is_present)
               VALUES ('999', 'shodoom', TRUE)"""
        )
    
    stats = await run_matching(db_pool)
    
    assert stats["exact"] == 1
    
    async with db_pool.acquire() as conn:
        char = await conn.fetchrow(
            "SELECT person_id FROM guild_identity.wow_characters WHERE character_name = 'Shodoom'"
        )
        discord = await conn.fetchrow(
            "SELECT person_id FROM guild_identity.discord_members WHERE username = 'shodoom'"
        )
        assert char['person_id'] is not None
        assert char['person_id'] == discord['person_id']


@pytest.mark.asyncio
async def test_guild_note_matching(db_pool):
    """Characters with Discord hints in guild notes get linked."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, guild_note)
               VALUES ('SomeAlt', 'senjin', 'Discord: Rocket')"""
        )
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username, is_present)
               VALUES ('888', 'rocket', TRUE)"""
        )
    
    stats = await run_matching(db_pool)
    
    assert stats["guild_note"] == 1


@pytest.mark.asyncio
async def test_no_double_linking(db_pool):
    """Already-linked characters don't get re-linked."""
    async with db_pool.acquire() as conn:
        pid = await conn.fetchval(
            "INSERT INTO guild_identity.persons (display_name) VALUES ('Trog') RETURNING id"
        )
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, person_id)
               VALUES ('Trogmoon', 'senjin', $1)""",
            pid
        )
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username, person_id, is_present)
               VALUES ('777', 'trog', $1, TRUE)""",
            pid
        )
    
    stats = await run_matching(db_pool)
    
    # Nothing should be matched because everything is already linked
    assert stats["exact"] == 0
    assert stats["guild_note"] == 0
```

## Task 5: Integrity Checker Tests

```python
# tests/test_integrity_checker.py

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from sv_common.guild_sync.integrity_checker import run_integrity_check


@pytest.mark.asyncio
async def test_detect_orphan_wow(db_pool):
    """Unlinked WoW characters create orphan_wow issues."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, guild_rank_name, character_class)
               VALUES ('OrphanChar', 'senjin', 'Member', 'Warrior')"""
        )
    
    stats = await run_integrity_check(db_pool)
    assert stats["orphan_wow"] == 1
    
    async with db_pool.acquire() as conn:
        issue = await conn.fetchrow(
            "SELECT * FROM guild_identity.audit_issues WHERE issue_type = 'orphan_wow'"
        )
        assert issue is not None
        assert "OrphanChar" in issue['summary']


@pytest.mark.asyncio
async def test_detect_orphan_discord(db_pool):
    """Discord members with guild roles but no WoW link create issues."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username, highest_guild_role, is_present)
               VALUES ('555', 'mystery_user', 'Member', TRUE)"""
        )
    
    stats = await run_integrity_check(db_pool)
    assert stats["orphan_discord"] == 1


@pytest.mark.asyncio
async def test_detect_role_mismatch(db_pool):
    """Mismatched in-game rank vs Discord role creates an issue."""
    async with db_pool.acquire() as conn:
        pid = await conn.fetchval(
            "INSERT INTO guild_identity.persons (display_name) VALUES ('TestPerson') RETURNING id"
        )
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, guild_rank_name, person_id, guild_rank)
               VALUES ('TestChar', 'senjin', 'Officer', $1, 1)""",
            pid
        )
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username, highest_guild_role, person_id, is_present)
               VALUES ('444', 'testperson', 'Member', $1, TRUE)""",
            pid
        )
    
    stats = await run_integrity_check(db_pool)
    assert stats["role_mismatch"] == 1


@pytest.mark.asyncio
async def test_no_duplicate_issues(db_pool):
    """Running integrity check twice doesn't create duplicate issues."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, guild_rank_name, character_class)
               VALUES ('OrphanChar', 'senjin', 'Member', 'Warrior')"""
        )
    
    stats1 = await run_integrity_check(db_pool)
    stats2 = await run_integrity_check(db_pool)
    
    assert stats1["orphan_wow"] == 1
    assert stats2["orphan_wow"] == 0  # Already tracked, no new issue
    
    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.audit_issues WHERE issue_type = 'orphan_wow'"
        )
        assert count == 1


@pytest.mark.asyncio
async def test_auto_resolve_orphan(db_pool):
    """Orphan issues auto-resolve when the character gets linked."""
    async with db_pool.acquire() as conn:
        wc_id = await conn.fetchval(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, guild_rank_name, character_class)
               VALUES ('WasOrphan', 'senjin', 'Member', 'Mage') RETURNING id"""
        )
    
    # Create the orphan issue
    await run_integrity_check(db_pool)
    
    # Now link the character to a person
    async with db_pool.acquire() as conn:
        pid = await conn.fetchval(
            "INSERT INTO guild_identity.persons (display_name) VALUES ('Found') RETURNING id"
        )
        await conn.execute(
            "UPDATE guild_identity.wow_characters SET person_id = $1 WHERE id = $2",
            pid, wc_id
        )
    
    # Re-run integrity check
    await run_integrity_check(db_pool)
    
    async with db_pool.acquire() as conn:
        issue = await conn.fetchrow(
            """SELECT resolved_at, resolved_by FROM guild_identity.audit_issues
               WHERE issue_type = 'orphan_wow' AND wow_character_id = $1""",
            wc_id
        )
        assert issue['resolved_at'] is not None
        assert issue['resolved_by'] == 'auto'


@pytest.mark.asyncio
async def test_stale_character_detection(db_pool):
    """Characters not logged in for 30+ days are flagged."""
    stale_ts = int((datetime.now(timezone.utc) - timedelta(days=45)).timestamp() * 1000)
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.wow_characters
               (character_name, realm_slug, guild_rank_name, character_class,
                last_login_timestamp)
               VALUES ('InactiveGuy', 'senjin', 'Member', 'Hunter', $1)""",
            stale_ts
        )
    
    stats = await run_integrity_check(db_pool)
    assert stats["stale"] == 1
```

## Task 6: Lua Parser Tests

```python
# tests/test_lua_parser.py

import pytest
import tempfile
import os

from companion_app.patt_sync_watcher import LuaParser


class TestLuaParser:
    def test_simple_table(self):
        result, _ = LuaParser._parse_value('{ name = "Trog", level = 80 }', 0)
        assert result["name"] == "Trog"
        assert result["level"] == 80
    
    def test_nested_table(self):
        result, _ = LuaParser._parse_value(
            '{ lastExport = { memberCount = 45 } }', 0
        )
        assert result["lastExport"]["memberCount"] == 45
    
    def test_array_table(self):
        result, _ = LuaParser._parse_value('{ "one", "two", "three" }', 0)
        assert isinstance(result, list)
        assert result == ["one", "two", "three"]
    
    def test_boolean_values(self):
        result, _ = LuaParser._parse_value('{ isOnline = true, isMobile = false }', 0)
        assert result["isOnline"] is True
        assert result["isMobile"] is False
    
    def test_nil_value(self):
        result, _ = LuaParser._parse_value('{ data = nil }', 0)
        assert result["data"] is None
    
    def test_string_escaping(self):
        result, _ = LuaParser._parse_value('{ name = "Sen\'jin" }', 0)
        assert result["name"] == "Sen'jin"
    
    def test_negative_numbers(self):
        result, _ = LuaParser._parse_value('{ offset = -5 }', 0)
        assert result["offset"] == -5
    
    def test_realistic_saved_variables(self):
        lua_content = '''PATTSyncDB = {
            lastExportTime = 1740153600,
            totalExports = 3,
            lastExport = {
                exportTime = 1740153600,
                addonVersion = "1.0.0",
                guildName = "Pull All The Things",
                memberCount = 2,
                characters = {
                    {
                        name = "Trogmoon",
                        realm = "Sen'jin",
                        class = "Druid",
                        level = 80,
                        rank = 0,
                        rankName = "Guild Leader",
                        note = "GM / Mike",
                        officerNote = "Discord: Trog",
                        isOnline = true,
                    },
                    {
                        name = "Shodoom",
                        realm = "Bleeding Hollow",
                        class = "Shaman",
                        level = 80,
                        rank = 1,
                        rankName = "Officer",
                        note = "",
                        officerNote = "",
                        isOnline = false,
                    },
                },
            },
        }'''
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False) as f:
            f.write(lua_content)
            f.flush()
            
            result = LuaParser.parse_file(f.name)
        
        os.unlink(f.name)
        
        assert result["totalExports"] == 3
        assert result["lastExport"]["guildName"] == "Pull All The Things"
        
        chars = result["lastExport"]["characters"]
        assert len(chars) == 2
        assert chars[0]["name"] == "Trogmoon"
        assert chars[0]["note"] == "GM / Mike"
        assert chars[1]["name"] == "Shodoom"
        assert chars[1]["isOnline"] is False
    
    def test_malformed_input(self):
        with pytest.raises(ValueError):
            LuaParser.parse_file("/nonexistent/file.lua")
```

## Task 7: Discord Sync Tests

```python
# tests/test_discord_sync.py

import pytest
from unittest.mock import MagicMock, AsyncMock

from sv_common.guild_sync.discord_sync import (
    get_highest_guild_role, get_all_guild_roles,
)


def make_mock_member(role_names: list[str], username="testuser", nick=None, bot=False):
    """Create a mock Discord member with specified roles."""
    member = MagicMock()
    member.bot = bot
    member.name = username
    member.nick = nick
    member.display_name = nick or username
    member.id = 12345
    member.joined_at = None
    
    roles = []
    for rn in role_names:
        role = MagicMock()
        role.name = rn
        roles.append(role)
    
    # Add @everyone role (always present)
    everyone = MagicMock()
    everyone.name = "@everyone"
    roles.insert(0, everyone)
    
    member.roles = roles
    return member


class TestGetHighestGuildRole:
    def test_gm_is_highest(self):
        member = make_mock_member(["Member", "GM", "Officer"])
        assert get_highest_guild_role(member) == "GM"
    
    def test_officer(self):
        member = make_mock_member(["Member", "Officer"])
        assert get_highest_guild_role(member) == "Officer"
    
    def test_veteran(self):
        member = make_mock_member(["Member", "Veteran"])
        assert get_highest_guild_role(member) == "Veteran"
    
    def test_member_only(self):
        member = make_mock_member(["Member"])
        assert get_highest_guild_role(member) == "Member"
    
    def test_initiate(self):
        member = make_mock_member(["Initiate"])
        assert get_highest_guild_role(member) == "Initiate"
    
    def test_no_guild_role(self):
        member = make_mock_member(["Booster", "Nitro"])
        assert get_highest_guild_role(member) is None
    
    def test_case_insensitive(self):
        member = make_mock_member(["officer"])
        assert get_highest_guild_role(member) == "Officer"


class TestGetAllGuildRoles:
    def test_multiple_roles(self):
        member = make_mock_member(["Member", "Officer", "Veteran"])
        roles = get_all_guild_roles(member)
        assert "Officer" in roles
        assert "Veteran" in roles
        assert "Member" in roles
    
    def test_no_guild_roles(self):
        member = make_mock_member(["Booster"])
        assert get_all_guild_roles(member) == []
```

## Running Tests

```bash
# Set up test database
createdb patt_test
export TEST_DATABASE_URL="postgresql://localhost/patt_test"

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_identity_engine.py -v

# Run with coverage
pytest tests/ --cov=sv_common.guild_sync --cov-report=html
```
