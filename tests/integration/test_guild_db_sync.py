"""
Integration tests for guild_sync.db_sync — syncing Blizzard and addon data.

Phase 2.7: wow_characters now uses class_id/active_spec_id/guild_rank_id FKs
instead of character_class/active_spec/guild_rank text fields.

Tests cover: new character creation, existing character update, removal
detection, return-to-guild handling, and addon note updates.

Note: Tests that verify FK resolution (class_id, active_spec_id) require
the reference tables (classes, specializations) to be pre-seeded.
The _seed_reference_data fixture handles this.
"""

import pytest

from sv_common.guild_sync.blizzard_client import CharacterProfileData
from sv_common.guild_sync.db_sync import sync_blizzard_roster, sync_addon_data


def _make_char(
    name: str = "Trogmoon",
    realm: str = "senjin",
    realm_name: str = "Sen'jin",
    wow_class: str = "Druid",
    spec: str = "Balance",
    level: int = 80,
    item_level: int = 620,
    guild_rank: int = 0,
    guild_rank_name: str = "Guild Leader",
    last_login: int = None,
) -> CharacterProfileData:
    return CharacterProfileData(
        character_name=name,
        realm_slug=realm,
        realm_name=realm_name,
        character_class=wow_class,
        active_spec=spec,
        level=level,
        item_level=item_level,
        guild_rank=guild_rank,
        guild_rank_name=guild_rank_name,
        last_login_timestamp=last_login,
    )


async def _seed_reference_data(conn):
    """Seed minimum reference data (Druid/Balance, Warrior/Arms) for sync tests."""
    # Check if already seeded
    count = await conn.fetchval("SELECT COUNT(*) FROM guild_identity.classes")
    if count > 0:
        return

    # Roles
    tank_id = await conn.fetchval(
        "INSERT INTO guild_identity.roles (name) VALUES ('Tank') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )
    healer_id = await conn.fetchval(
        "INSERT INTO guild_identity.roles (name) VALUES ('Healer') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )
    melee_id = await conn.fetchval(
        "INSERT INTO guild_identity.roles (name) VALUES ('Melee DPS') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )
    ranged_id = await conn.fetchval(
        "INSERT INTO guild_identity.roles (name) VALUES ('Ranged DPS') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )

    # Classes
    druid_id = await conn.fetchval(
        "INSERT INTO guild_identity.classes (name) VALUES ('Druid') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )
    warrior_id = await conn.fetchval(
        "INSERT INTO guild_identity.classes (name) VALUES ('Warrior') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )
    mage_id = await conn.fetchval(
        "INSERT INTO guild_identity.classes (name) VALUES ('Mage') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )
    priest_id = await conn.fetchval(
        "INSERT INTO guild_identity.classes (name) VALUES ('Priest') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )
    rogue_id = await conn.fetchval(
        "INSERT INTO guild_identity.classes (name) VALUES ('Rogue') ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
    )

    # Specs
    specs = [
        (druid_id, "Balance", ranged_id),
        (druid_id, "Restoration", healer_id),
        (warrior_id, "Arms", melee_id),
        (warrior_id, "Protection", tank_id),
        (mage_id, "Fire", ranged_id),
        (mage_id, "Frost", ranged_id),
        (priest_id, "Holy", healer_id),
        (priest_id, "Shadow", ranged_id),
        (rogue_id, "Subtlety", melee_id),
    ]
    for class_id, spec_name, role_id in specs:
        await conn.execute(
            "INSERT INTO guild_identity.specializations (class_id, name, default_role_id) "
            "VALUES ($1, $2, $3) ON CONFLICT (class_id, name) DO NOTHING",
            class_id, spec_name, role_id,
        )


class TestSyncBlizzardRoster:
    async def test_new_character_creates_row(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        char = _make_char("Trogmoon", wow_class="Druid", spec="Balance")
        stats = await sync_blizzard_roster(guild_db, [char])

        assert stats["new"] == 1
        assert stats["found"] == 1
        assert stats["updated"] == 0
        assert stats["removed"] == 0

        async with guild_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon'"
            )
        assert row is not None
        assert row["removed_at"] is None
        # Phase 2.7: no text fields, use FKs
        assert "character_class" not in row.keys()
        assert "active_spec" not in row.keys()

    async def test_new_character_class_id_resolved(self, guild_db):
        """After sync, class_id should be set (not NULL) for known classes."""
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        char = _make_char("Trogmoon2", wow_class="Druid", spec="Balance")
        await sync_blizzard_roster(guild_db, [char])

        async with guild_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT class_id, active_spec_id FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon2'"
            )
        assert row["class_id"] is not None
        assert row["active_spec_id"] is not None

    async def test_existing_character_updated(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        char_v1 = _make_char("Trogmoon", level=79, item_level=600)
        await sync_blizzard_roster(guild_db, [char_v1])

        char_v2 = _make_char("Trogmoon", level=80, item_level=623)
        stats = await sync_blizzard_roster(guild_db, [char_v2])

        assert stats["updated"] == 1
        assert stats["new"] == 0

        async with guild_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT level, item_level FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon'"
            )
        assert row["level"] == 80
        assert row["item_level"] == 623

    async def test_character_removal_detection(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        chars = [
            _make_char("Trogmoon", guild_rank=0),
            _make_char("LeavingGuy", wow_class="Warrior", spec="Arms", guild_rank=3,
                       guild_rank_name="Member"),
        ]
        await sync_blizzard_roster(guild_db, chars)

        # Re-sync without LeavingGuy
        stats = await sync_blizzard_roster(guild_db, [chars[0]])

        assert stats["removed"] == 1

        async with guild_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT removed_at FROM guild_identity.wow_characters "
                "WHERE character_name = 'LeavingGuy'"
            )
        assert row["removed_at"] is not None

    async def test_character_returns_to_guild(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        char = _make_char("ReturnGuy", wow_class="Mage", spec="Fire", guild_rank=3,
                          guild_rank_name="Member")

        await sync_blizzard_roster(guild_db, [char])
        await sync_blizzard_roster(guild_db, [])

        async with guild_db.acquire() as conn:
            removed = await conn.fetchval(
                "SELECT removed_at FROM guild_identity.wow_characters "
                "WHERE character_name = 'ReturnGuy'"
            )
        assert removed is not None

        await sync_blizzard_roster(guild_db, [char])

        async with guild_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT removed_at FROM guild_identity.wow_characters "
                "WHERE character_name = 'ReturnGuy'"
            )
        assert row["removed_at"] is None

    async def test_multiple_characters_batch(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        chars = [
            _make_char("Char1", wow_class="Warrior", spec="Arms"),
            _make_char("Char2", wow_class="Priest", spec="Holy"),
            _make_char("Char3", wow_class="Rogue", spec="Subtlety"),
        ]
        stats = await sync_blizzard_roster(guild_db, chars)

        assert stats["new"] == 3
        assert stats["found"] == 3

    async def test_empty_roster_removes_all_active(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        chars = [
            _make_char("Char1"),
            _make_char("Char2", wow_class="Warrior", spec="Arms"),
        ]
        await sync_blizzard_roster(guild_db, chars)

        stats = await sync_blizzard_roster(guild_db, [])

        assert stats["removed"] == 2

    async def test_case_insensitive_name_matching(self, guild_db):
        """Characters are matched case-insensitively on re-sync."""
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        char_v1 = _make_char("Trogmoon")
        await sync_blizzard_roster(guild_db, [char_v1])

        char_v2 = _make_char("TROGMOON", level=80, item_level=630)
        stats = await sync_blizzard_roster(guild_db, [char_v2])

        assert stats["updated"] == 1
        assert stats["new"] == 0


class TestSyncAddonData:
    async def test_addon_updates_guild_and_officer_notes(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        char = _make_char("Trogmoon")
        await sync_blizzard_roster(guild_db, [char])

        addon_data = [
            {
                "name": "Trogmoon",
                "realm": "Sen'jin",
                "guild_note": "GM / Mike",
                "officer_note": "Discord: Trog",
                "rank": 0,
                "rank_name": "Guild Leader",
            }
        ]
        stats = await sync_addon_data(guild_db, addon_data)

        assert stats["updated"] == 1

        async with guild_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT guild_note, officer_note FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon'"
            )
        assert row["guild_note"] == "GM / Mike"
        assert row["officer_note"] == "Discord: Trog"

    async def test_addon_unknown_character_not_found(self, guild_db):
        addon_data = [
            {
                "name": "NoSuchChar",
                "realm": "senjin",
                "guild_note": "some note",
                "officer_note": "",
            }
        ]
        stats = await sync_addon_data(guild_db, addon_data)

        assert stats["updated"] == 0
        assert stats["not_found"] == 1

    async def test_addon_multiple_characters(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        chars = [
            _make_char("Char1"),
            _make_char("Char2", wow_class="Warrior", spec="Arms"),
        ]
        await sync_blizzard_roster(guild_db, chars)

        addon_data = [
            {"name": "Char1", "realm": "Sen'jin", "guild_note": "Note1", "officer_note": ""},
            {"name": "Char2", "realm": "Sen'jin", "guild_note": "", "officer_note": "DC: Bob"},
        ]
        stats = await sync_addon_data(guild_db, addon_data)

        assert stats["updated"] == 2
        assert stats["not_found"] == 0

    async def test_addon_updates_addon_last_sync_timestamp(self, guild_db):
        async with guild_db.acquire() as conn:
            await _seed_reference_data(conn)

        char = _make_char("Trogmoon")
        await sync_blizzard_roster(guild_db, [char])

        async with guild_db.acquire() as conn:
            before = await conn.fetchval(
                "SELECT addon_last_sync FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon'"
            )
        assert before is None

        await sync_addon_data(guild_db, [
            {"name": "Trogmoon", "realm": "Sen'jin", "guild_note": "", "officer_note": ""}
        ])

        async with guild_db.acquire() as conn:
            after = await conn.fetchval(
                "SELECT addon_last_sync FROM guild_identity.wow_characters "
                "WHERE character_name = 'Trogmoon'"
            )
        assert after is not None
