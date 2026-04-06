"""Unit tests for item_source_sync.py.

Tests cover the sync orchestration logic using mock Blizzard API responses,
without hitting a real database or network.
"""

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from sv_common.guild_sync.item_source_sync import (
    _DUNGEON_TRACKS,
    _RAID_TRACKS,
    _sync_encounter,
    _sync_instance,
    sync_item_sources,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*fetchrow_returns, execute_returns=None):
    """Build a minimal asyncpg pool mock."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    if fetchrow_returns:
        conn.fetchrow = AsyncMock(side_effect=list(fetchrow_returns))
    else:
        conn.fetchrow = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _make_client(**methods):
    """Build a BlizzardClient-like mock."""
    client = AsyncMock()
    for name, value in methods.items():
        setattr(client, name, AsyncMock(return_value=value))
    return client


# ---------------------------------------------------------------------------
# sync_item_sources — top-level orchestration
# ---------------------------------------------------------------------------


class TestSyncItemSources:
    @pytest.mark.asyncio
    async def test_returns_error_when_expansion_index_empty(self):
        pool, _ = _make_pool()
        client = _make_client(get_journal_expansion_index=[])

        result = await sync_item_sources(pool, client)

        assert result["instances_synced"] == 0
        assert result["encounters_synced"] == 0
        assert result["items_upserted"] == 0
        assert len(result["errors"]) == 1
        assert "expansion index" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_returns_error_when_expansion_data_missing(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_expansion_index=[{"id": 10, "name": "Midnight"}],
            get_journal_expansion=None,
        )

        result = await sync_item_sources(pool, client)

        assert result["instances_synced"] == 0
        assert len(result["errors"]) == 1
        assert "expansion 10" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_picks_highest_expansion_id(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_expansion_index=[
                {"id": 8, "name": "Dragonflight"},
                {"id": 9, "name": "The War Within"},
                {"id": 10, "name": "Midnight"},
            ],
            get_journal_expansion={"id": 10, "name": "Midnight", "dungeons": [], "raids": []},
        )

        result = await sync_item_sources(pool, client)

        # Should have called get_journal_expansion with id 10
        client.get_journal_expansion.assert_awaited_once_with(10)
        assert result["expansion_name"] == "Midnight"

    @pytest.mark.asyncio
    async def test_uses_provided_expansion_id(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_expansion={"id": 9, "name": "The War Within", "dungeons": [], "raids": []},
        )

        result = await sync_item_sources(pool, client, expansion_id=9)

        # Should NOT call the index endpoint
        client.get_journal_expansion_index.assert_not_awaited()
        client.get_journal_expansion.assert_awaited_once_with(9)

    @pytest.mark.asyncio
    async def test_returns_error_when_no_instances(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_expansion_index=[{"id": 10, "name": "Midnight"}],
            get_journal_expansion={"id": 10, "name": "Midnight", "dungeons": [], "raids": []},
        )

        result = await sync_item_sources(pool, client)

        assert result["instances_synced"] == 0
        assert len(result["errors"]) == 1
        assert "No instances" in result["errors"][0]


# ---------------------------------------------------------------------------
# _sync_instance
# ---------------------------------------------------------------------------


class TestSyncInstance:
    @pytest.mark.asyncio
    async def test_returns_error_when_instance_not_found(self):
        pool, _ = _make_pool()
        client = _make_client(get_journal_instance=None)

        count, items, errors = await _sync_instance(pool, client, 1271, "Priory", "dungeon")

        assert count == 0
        assert items == 0
        assert len(errors) == 1
        assert "1271" in errors[0]

    @pytest.mark.asyncio
    async def test_handles_empty_encounter_list(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_instance={"id": 1271, "name": "Priory", "encounters": {"encounters": []}}
        )

        count, items, errors = await _sync_instance(pool, client, 1271, "Priory", "dungeon")

        assert count == 0
        assert items == 0
        assert errors == []

    @pytest.mark.asyncio
    async def test_counts_encounters(self):
        pool, _ = _make_pool({"id": 1})  # fetchrow returns a wow_items id
        client = _make_client(
            get_journal_instance={
                "id": 1271,
                "name": "Priory",
                "encounters": {
                    "encounters": [
                        {"id": 2600, "name": "Boss A"},
                        {"id": 2601, "name": "Boss B"},
                    ]
                },
            },
            get_journal_encounter={"id": 2600, "name": "Boss A", "items": []},
        )

        with patch("sv_common.guild_sync.item_source_sync._ENCOUNTER_DELAY", 0):
            count, items, errors = await _sync_instance(pool, client, 1271, "Priory", "dungeon")

        assert count == 2  # 2 encounters processed
        assert errors == []

    @pytest.mark.asyncio
    async def test_dungeon_gets_dungeon_tracks(self):
        """Dungeon encounters must receive C/H quality tracks."""
        pool, conn = _make_pool({"id": 1})
        client = _make_client(
            get_journal_instance={
                "id": 1,
                "name": "Test Dungeon",
                "encounters": {"encounters": [{"id": 100, "name": "Boss"}]},
            },
            get_journal_encounter={
                "id": 100,
                "name": "Boss",
                "items": [{"name": "Sword", "item": {"id": 99999}}],
            },
        )

        with patch("sv_common.guild_sync.item_source_sync._ENCOUNTER_DELAY", 0):
            await _sync_instance(pool, client, 1, "Test Dungeon", "dungeon")

        # Find the item_sources upsert call
        upsert_calls = [
            c for c in conn.execute.call_args_list
            if "item_sources" in str(c)
        ]
        assert len(upsert_calls) == 1
        # quality_tracks arg (7th positional arg to execute after the SQL string)
        quality_tracks = upsert_calls[0].args[-1]  # last positional arg
        assert quality_tracks == _DUNGEON_TRACKS

    @pytest.mark.asyncio
    async def test_raid_gets_raid_tracks(self):
        """Raid encounters must receive C/H/M quality tracks."""
        pool, conn = _make_pool({"id": 1})
        client = _make_client(
            get_journal_instance={
                "id": 2,
                "name": "Test Raid",
                "encounters": {"encounters": [{"id": 200, "name": "Raid Boss"}]},
            },
            get_journal_encounter={
                "id": 200,
                "name": "Raid Boss",
                "items": [{"name": "Epic Sword", "item": {"id": 88888}}],
            },
        )

        with patch("sv_common.guild_sync.item_source_sync._ENCOUNTER_DELAY", 0):
            await _sync_instance(pool, client, 2, "Test Raid", "raid")

        upsert_calls = [
            c for c in conn.execute.call_args_list
            if "item_sources" in str(c)
        ]
        assert len(upsert_calls) == 1
        quality_tracks = upsert_calls[0].args[-1]
        assert quality_tracks == _RAID_TRACKS


# ---------------------------------------------------------------------------
# _sync_encounter
# ---------------------------------------------------------------------------


class TestSyncEncounter:
    @pytest.mark.asyncio
    async def test_returns_error_when_encounter_not_found(self):
        pool, _ = _make_pool()
        client = _make_client(get_journal_encounter=None)

        count, errors = await _sync_encounter(
            pool, client, 2639, "Captain Dailcry",
            1271, "Priory", "dungeon", _DUNGEON_TRACKS,
        )

        assert count == 0
        assert len(errors) == 1
        assert "2639" in errors[0]

    @pytest.mark.asyncio
    async def test_handles_empty_items(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_encounter={"id": 2639, "name": "Captain Dailcry", "items": []}
        )

        count, errors = await _sync_encounter(
            pool, client, 2639, "Captain Dailcry",
            1271, "Priory", "dungeon", _DUNGEON_TRACKS,
        )

        assert count == 0
        assert errors == []

    @pytest.mark.asyncio
    async def test_skips_items_without_blizzard_id(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_encounter={
                "id": 2639,
                "name": "Boss",
                "items": [
                    {"id": 999, "name": "Bad entry", "item": {}},  # no item.id
                    {"id": 998, "name": "Also bad"},               # no item key at all
                ],
            }
        )

        count, errors = await _sync_encounter(
            pool, client, 2639, "Boss",
            1271, "Priory", "dungeon", _DUNGEON_TRACKS,
        )

        assert count == 0
        assert errors == []

    @pytest.mark.asyncio
    async def test_upserts_valid_items(self):
        """Valid items should be inserted into wow_items + item_sources."""
        wow_item_row = {"id": 42}
        pool, conn = _make_pool(wow_item_row)
        client = _make_client(
            get_journal_encounter={
                "id": 2639,
                "name": "Captain Dailcry",
                "items": [
                    {"id": 1, "name": "Warband Satchel", "item": {"id": 211438}},
                ],
            }
        )

        count, errors = await _sync_encounter(
            pool, client, 2639, "Captain Dailcry",
            1271, "Priory of the Sacred Flame", "dungeon", _DUNGEON_TRACKS,
        )

        assert count == 1
        assert errors == []

        # wow_items stub insert
        wow_insert_calls = [c for c in conn.execute.call_args_list if "wow_items" in str(c)]
        assert len(wow_insert_calls) == 1
        wow_args = wow_insert_calls[0].args
        assert 211438 in wow_args  # blizzard_item_id
        assert "Warband Satchel" in wow_args  # name

        # item_sources upsert
        src_insert_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        assert len(src_insert_calls) == 1
        src_args = src_insert_calls[0].args
        assert 42 in src_args          # wow_item_id
        assert "dungeon" in src_args   # source_type
        assert "Captain Dailcry" in src_args   # source_name
        assert "Priory of the Sacred Flame" in src_args  # source_instance
        assert 2639 in src_args        # encounter_id
        assert 1271 in src_args        # instance_id
        assert _DUNGEON_TRACKS in src_args

    @pytest.mark.asyncio
    async def test_skips_item_when_wow_items_row_missing(self):
        """If wow_items insert + lookup fails (returns None), item is skipped."""
        pool, conn = _make_pool(None)  # fetchrow returns None
        client = _make_client(
            get_journal_encounter={
                "id": 2639,
                "name": "Boss",
                "items": [{"id": 1, "name": "Item", "item": {"id": 12345}}],
            }
        )

        count, errors = await _sync_encounter(
            pool, client, 2639, "Boss", 1, "Instance", "raid", _RAID_TRACKS,
        )

        assert count == 0
        assert errors == []
        # No item_sources inserts
        src_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        assert len(src_calls) == 0

    @pytest.mark.asyncio
    async def test_uses_raid_boss_source_type_for_raids(self):
        pool, conn = _make_pool({"id": 7})
        client = _make_client(
            get_journal_encounter={
                "id": 300, "name": "Final Boss",
                "items": [{"id": 1, "name": "Legendary", "item": {"id": 99999}}],
            }
        )

        await _sync_encounter(
            pool, client, 300, "Final Boss",
            100, "Midnight Raid", "raid", _RAID_TRACKS,
        )

        src_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        src_args = src_calls[0].args
        assert "raid_boss" in src_args


# ---------------------------------------------------------------------------
# Track constant sanity checks
# ---------------------------------------------------------------------------


class TestTrackConstants:
    def test_dungeon_tracks(self):
        assert _DUNGEON_TRACKS == ["C", "H"]

    def test_raid_tracks(self):
        assert _RAID_TRACKS == ["C", "H", "M"]
