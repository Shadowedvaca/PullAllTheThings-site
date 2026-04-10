"""Unit tests for item_source_sync.py and source_config.py.

Covers sync orchestration, raw storage behaviour, and the lookup layer that
computes display names / track labels at read time.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sv_common.guild_sync.item_source_sync import (
    _sync_encounter,
    _sync_instance,
    flag_junk_sources,
    sync_item_sources,
)
from sv_common.guild_sync.source_config import (
    get_display_name,
    get_track_label,
    get_tracks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*fetchrow_returns):
    """Build a minimal asyncpg pool mock."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(
        side_effect=list(fetchrow_returns) if fetchrow_returns else [None]
    )
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _make_client(**methods):
    client = AsyncMock()
    for name, value in methods.items():
        setattr(client, name, AsyncMock(return_value=value))
    return client


# ---------------------------------------------------------------------------
# source_config — the lookup layer
# ---------------------------------------------------------------------------


class TestSourceConfig:
    # get_tracks
    def test_raid_has_all_four_tracks(self):
        assert get_tracks("raid") == ["V", "C", "H", "M"]

    def test_world_boss_has_no_rf_track(self):
        tracks = get_tracks("world_boss")
        assert "V" not in tracks
        assert tracks == ["C", "H", "M"]

    def test_dungeon_has_no_rf_track(self):
        tracks = get_tracks("dungeon")
        assert "V" not in tracks
        assert tracks == ["C", "H", "M"]

    def test_unknown_type_falls_back_to_chm(self):
        assert get_tracks("unknown") == ["C", "H", "M"]

    # get_display_name
    def test_raid_uses_raw_instance_name(self):
        assert get_display_name("The Voidspire", "raid") == "The Voidspire"

    def test_world_boss_overrides_to_world_boss(self):
        # Raw API name ("Midnight") is overridden regardless of its value
        assert get_display_name("Midnight", "world_boss") == "World Boss"
        assert get_display_name("The War Within", "world_boss") == "World Boss"

    def test_dungeon_uses_raw_instance_name(self):
        assert get_display_name("Cinderbrew Meadery", "dungeon") == "Cinderbrew Meadery"

    # get_track_label
    def test_raid_label_is_rf_plus(self):
        # Raid has V as minimum → RF+
        assert get_track_label("raid") == "RF+"

    def test_world_boss_label_is_n_plus(self):
        # World boss minimum is C (Normal) → N+
        assert get_track_label("world_boss") == "N+"

    def test_dungeon_label_is_zero_plus(self):
        # Dungeon minimum is C (Champion 0+) → 0+
        assert get_track_label("dungeon") == "0+"

    def test_unknown_type_falls_back_to_n_plus(self):
        # get_tracks("unknown") returns ["C","H","M"] fallback → minimum C → N+
        assert get_track_label("unknown") == "N+"


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
        assert len(result["errors"]) == 1
        assert "expansion index" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_picks_highest_expansion_id(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_expansion_index=[
                {"id": 8, "name": "Dragonflight"},
                {"id": 10, "name": "Midnight"},
            ],
            get_journal_expansion={"id": 10, "name": "Midnight", "dungeons": [], "raids": []},
        )
        result = await sync_item_sources(pool, client)
        client.get_journal_expansion.assert_awaited_once_with(10)
        assert result["expansion_name"] == "Midnight"

    @pytest.mark.asyncio
    async def test_raid_matching_expansion_name_classified_as_world_boss(self):
        """A raid whose name == expansion name must be stored as instance_type='world_boss'."""
        pool, conn = _make_pool({"id": 1})
        client = _make_client(
            get_journal_expansion_index=[{"id": 10, "name": "Midnight"}],
            get_journal_expansion={
                "id": 10,
                "name": "Midnight",
                "dungeons": [],
                "raids": [{"id": 500, "name": "Midnight"}],  # same name = world boss
            },
            get_journal_instance={
                "id": 500,
                "encounters": {"encounters": [{"id": 600, "name": "Thorm'belan"}]},
            },
            get_journal_encounter={
                "id": 600,
                "name": "Thorm'belan",
                "items": [{"id": 1, "item": {"id": 250001, "name": "World Token"}}],
            },
        )

        with patch("sv_common.guild_sync.item_source_sync._ENCOUNTER_DELAY", 0):
            await sync_item_sources(pool, client)

        src_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        assert len(src_calls) >= 1
        src_args = src_calls[0].args
        # instance_type must be 'world_boss', NOT 'raid'
        assert "world_boss" in src_args
        assert "raid" not in src_args
        assert "raid_boss" not in src_args

    @pytest.mark.asyncio
    async def test_regular_raid_classified_as_raid(self):
        pool, conn = _make_pool({"id": 1})
        client = _make_client(
            get_journal_expansion_index=[{"id": 10, "name": "Midnight"}],
            get_journal_expansion={
                "id": 10,
                "name": "Midnight",
                "dungeons": [],
                "raids": [{"id": 501, "name": "The Voidspire"}],  # different name = real raid
            },
            get_journal_instance={
                "id": 501,
                "encounters": {"encounters": [{"id": 601, "name": "Fallen-King Salhadaar"}]},
            },
            get_journal_encounter={
                "id": 601,
                "name": "Fallen-King Salhadaar",
                "items": [{"id": 1, "item": {"id": 250002, "name": "Raid Helm"}}],
            },
        )

        with patch("sv_common.guild_sync.item_source_sync._ENCOUNTER_DELAY", 0):
            await sync_item_sources(pool, client)

        src_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        assert len(src_calls) >= 1
        src_args = src_calls[0].args
        assert "raid" in src_args
        assert "world_boss" not in src_args
        assert "raid_boss" not in src_args


# ---------------------------------------------------------------------------
# _sync_instance
# ---------------------------------------------------------------------------


class TestSyncInstance:
    @pytest.mark.asyncio
    async def test_returns_error_when_instance_not_found(self):
        pool, _ = _make_pool()
        client = _make_client(get_journal_instance=None)
        count, items, errors = await _sync_instance(pool, client, 1271, "Priory", "dungeon")
        assert count == 0 and items == 0 and len(errors) == 1

    @pytest.mark.asyncio
    async def test_handles_empty_encounter_list(self):
        pool, _ = _make_pool()
        client = _make_client(
            get_journal_instance={"encounters": {"encounters": []}}
        )
        count, items, errors = await _sync_instance(pool, client, 1271, "Priory", "dungeon")
        assert count == 0 and items == 0 and errors == []

    @pytest.mark.asyncio
    async def test_counts_encounters(self):
        pool, _ = _make_pool({"id": 1})
        client = _make_client(
            get_journal_instance={
                "encounters": {"encounters": [
                    {"id": 2600, "name": "Boss A"},
                    {"id": 2601, "name": "Boss B"},
                ]},
            },
            get_journal_encounter={"items": []},
        )
        with patch("sv_common.guild_sync.item_source_sync._ENCOUNTER_DELAY", 0):
            count, _, errors = await _sync_instance(pool, client, 1271, "Priory", "dungeon")
        assert count == 2 and errors == []


# ---------------------------------------------------------------------------
# _sync_encounter — raw storage shape
# ---------------------------------------------------------------------------


class TestSyncEncounter:
    @pytest.mark.asyncio
    async def test_returns_error_when_encounter_not_found(self):
        pool, _ = _make_pool()
        client = _make_client(get_journal_encounter=None)
        count, errors = await _sync_encounter(
            pool, client, 2639, "Captain Dailcry", 1271, "Priory", "dungeon"
        )
        assert count == 0 and len(errors) == 1 and "2639" in errors[0]

    @pytest.mark.asyncio
    async def test_handles_empty_items(self):
        pool, _ = _make_pool()
        client = _make_client(get_journal_encounter={"items": []})
        count, errors = await _sync_encounter(
            pool, client, 2639, "Boss", 1271, "Priory", "dungeon"
        )
        assert count == 0 and errors == []

    @pytest.mark.asyncio
    async def test_skips_items_without_blizzard_id(self):
        pool, _ = _make_pool()
        client = _make_client(get_journal_encounter={
            "items": [
                {"item": {}},          # no id
                {"no_item_key": True}, # no item key
            ]
        })
        count, errors = await _sync_encounter(
            pool, client, 2639, "Boss", 1271, "Priory", "dungeon"
        )
        assert count == 0 and errors == []

    @pytest.mark.asyncio
    async def test_upsert_stores_correct_raw_fields(self):
        """Upsert must store encounter_name, instance_name, instance_type — no quality_tracks."""
        pool, conn = _make_pool({"id": 42})
        client = _make_client(get_journal_encounter={
            "items": [{"item": {"id": 211438, "name": "Warband Satchel"}}],
        })

        count, errors = await _sync_encounter(
            pool, client,
            2639, "Captain Dailcry",
            1271, "Priory of the Sacred Flame",
            "dungeon",
        )

        assert count == 1 and errors == []

        src_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        assert len(src_calls) == 1
        sql, *args = src_calls[0].args

        # Correct raw values stored
        assert 42 in args               # item_id
        assert "dungeon" in args        # instance_type
        assert "Captain Dailcry" in args        # encounter_name
        assert "Priory of the Sacred Flame" in args  # instance_name
        assert 2639 in args             # blizzard_encounter_id
        assert 1271 in args             # blizzard_instance_id

        # quality_tracks must NOT be stored
        assert not any(isinstance(a, list) and set(a) <= {"V", "C", "H", "M"} for a in args)

        # Confirm new column names in SQL (not old names)
        assert "instance_type" in sql
        assert "encounter_name" in sql
        assert "instance_name" in sql
        assert "quality_tracks" not in sql
        assert "source_type" not in sql
        assert "source_name" not in sql
        assert "source_instance" not in sql

    @pytest.mark.asyncio
    async def test_world_boss_stored_as_world_boss_type(self):
        pool, conn = _make_pool({"id": 7})
        client = _make_client(get_journal_encounter={
            "items": [{"item": {"id": 250050, "name": "Boss Loot"}}],
        })

        await _sync_encounter(
            pool, client,
            600, "Thorm'belan",
            500, "Midnight",   # raw API instance name
            "world_boss",
        )

        src_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        _, *args = src_calls[0].args
        assert "world_boss" in args
        assert "raid_boss" not in args
        assert "raid" not in args
        # Raw instance name preserved
        assert "Midnight" in args

    @pytest.mark.asyncio
    async def test_skips_item_when_wow_items_row_missing(self):
        pool, conn = _make_pool(None)
        client = _make_client(get_journal_encounter={
            "items": [{"item": {"id": 12345, "name": "Item"}}],
        })
        count, errors = await _sync_encounter(
            pool, client, 2639, "Boss", 1, "Instance", "raid"
        )
        assert count == 0 and errors == []
        src_calls = [c for c in conn.execute.call_args_list if "item_sources" in str(c)]
        assert len(src_calls) == 0


# ---------------------------------------------------------------------------
# flag_junk_sources
# ---------------------------------------------------------------------------


class TestFlagJunkSources:
    def _make_flag_pool(self, wb_count: int = 0, tp_count: int = 0):
        """Pool mock where execute returns row counts for UPDATE calls."""
        conn = AsyncMock()
        # Default (no flag_tier_pieces): 2 execute calls
        #   1. Clear all flags
        #   2. World boss UPDATE → wb_count rows
        # With flag_tier_pieces=True: 3 execute calls (adds tier piece UPDATE)
        conn.execute = AsyncMock(side_effect=[
            "UPDATE 0",                    # clear all
            f"UPDATE {wb_count}",          # world boss
            f"UPDATE {tp_count}",          # tier piece (only if flag_tier_pieces=True)
        ])
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        return pool, conn

    @pytest.mark.asyncio
    async def test_returns_correct_world_boss_count(self):
        pool, _ = self._make_flag_pool(wb_count=3)
        result = await flag_junk_sources(pool)
        assert result["flagged_world_boss"] == 3
        assert result["flagged_tier_piece"] == 0
        assert result["total_flagged"] == 3

    @pytest.mark.asyncio
    async def test_returns_correct_counts_with_tier_pieces(self):
        pool, _ = self._make_flag_pool(wb_count=3, tp_count=12)
        result = await flag_junk_sources(pool, flag_tier_pieces=True)
        assert result["flagged_world_boss"] == 3
        assert result["flagged_tier_piece"] == 12
        assert result["total_flagged"] == 15

    @pytest.mark.asyncio
    async def test_clears_flags_before_re_flagging(self):
        """First execute call must unconditionally clear all junk flags."""
        pool, conn = self._make_flag_pool()
        await flag_junk_sources(pool)
        first_call_sql = conn.execute.call_args_list[0].args[0]
        assert "is_suspected_junk = FALSE" in first_call_sql

    @pytest.mark.asyncio
    async def test_world_boss_criteria_in_sql(self):
        """World boss UPDATE must filter on instance_type, null IDs, AND null encounter_name."""
        pool, conn = self._make_flag_pool()
        await flag_junk_sources(pool)
        wb_sql = conn.execute.call_args_list[1].args[0]
        assert "world_boss" in wb_sql
        assert "blizzard_encounter_id IS NULL" in wb_sql
        assert "blizzard_instance_id IS NULL" in wb_sql
        assert "encounter_name" in wb_sql

    @pytest.mark.asyncio
    async def test_tier_piece_not_flagged_by_default(self):
        """Without flag_tier_pieces=True, only 2 execute calls (no tier piece UPDATE)."""
        pool, conn = self._make_flag_pool()
        await flag_junk_sources(pool)
        # Only: clear + world_boss = 2 calls
        assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_tier_piece_criteria_uses_item_set_link_and_tier_slots(self):
        """Tier piece UPDATE must filter /item-set= and restrict to tier slot types."""
        pool, conn = self._make_flag_pool(wb_count=0, tp_count=0)
        await flag_junk_sources(pool, flag_tier_pieces=True)
        tp_sql = conn.execute.call_args_list[2].args[0]
        assert "wow_items" in tp_sql
        assert "/item-set=" in tp_sql
        assert "slot_type" in tp_sql

    @pytest.mark.asyncio
    async def test_returns_zero_counts_when_nothing_flagged(self):
        pool, _ = self._make_flag_pool()
        result = await flag_junk_sources(pool)
        assert result["total_flagged"] == 0
        assert result["flagged_world_boss"] == 0
        assert result["flagged_tier_piece"] == 0
