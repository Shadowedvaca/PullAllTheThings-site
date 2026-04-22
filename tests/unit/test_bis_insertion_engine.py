"""Unit tests for the BIS insertion engine (Phase 1.5-2).

Tests insert_bis_items() and BisInsertionContext in isolation using a
mock asyncpg pool — no real DB required.

Coverage:
- Empty items list returns zeros immediately
- Normal slot inserts with guide_order assignment
- Ring/trinket paired slots get sequential guide_orders per slot key
- main_hand resolved to main_hand_2h (two_hand) or main_hand_1h (one_hand)
- main_hand item missing from enrichment.items (resolver returns None) → skipped
- FK check: item not in enrichment.items → skipped
- Duplicate INSERT exception → counted as skipped
- bis_note stamped when note param provided
- guide_order_start offset shifts all guide_orders
- Return dict contains "inserted" and "skipped" keys
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from sv_common.guild_sync.bis_sync import BisInsertionContext, insert_bis_items
from sv_common.guild_sync.simc_parser import SimcSlot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(fetchval_side_effect=None, execute_side_effect=None):
    """Build a minimal mock asyncpg pool.

    fetchval_side_effect: list of return values consumed in order (per call),
                          or a single value returned every call.
    """
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    conn.execute = AsyncMock(side_effect=execute_side_effect)
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


def _ctx(pool, spec_id=1, source_id=2, hero_talent_id=None, content_type="overall"):
    return BisInsertionContext(
        pool=pool,
        spec_id=spec_id,
        source_id=source_id,
        hero_talent_id=hero_talent_id,
        content_type=content_type,
    )


def _slot(slot: str, item_id: int) -> SimcSlot:
    return SimcSlot(slot=slot, blizzard_item_id=item_id)


# ---------------------------------------------------------------------------
# Empty items
# ---------------------------------------------------------------------------


class TestInsertBisItemsEmpty:
    @pytest.mark.asyncio
    async def test_empty_list_returns_zeros_without_db(self):
        pool, conn = _make_pool()
        result = await insert_bis_items(_ctx(pool), [])
        assert result == {"inserted": 0, "skipped": 0}
        conn.fetchval.assert_not_called()
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Basic insertion
# ---------------------------------------------------------------------------


class TestInsertBisItemsBasic:
    @pytest.mark.asyncio
    async def test_single_slot_inserted(self):
        # fetchval returns 1 (item exists) for the FK check
        pool, conn = _make_pool(fetchval_side_effect=[1])
        result = await insert_bis_items(_ctx(pool), [_slot("head", 100)])
        assert result == {"inserted": 1, "skipped": 0}

    @pytest.mark.asyncio
    async def test_insert_uses_correct_params(self):
        pool, conn = _make_pool(fetchval_side_effect=[1])
        ctx = _ctx(pool, spec_id=5, source_id=3, hero_talent_id=7, content_type="raid")
        await insert_bis_items(ctx, [_slot("head", 100)])

        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        sql, s_id, sp_id, ht_id, slot, item_id, guide_order, note = args
        assert s_id == 3
        assert sp_id == 5
        assert ht_id == 7
        assert slot == "head"
        assert item_id == 100
        assert guide_order == 1
        assert note is None

    @pytest.mark.asyncio
    async def test_two_items_different_slots_get_guide_order_1(self):
        pool, conn = _make_pool(fetchval_side_effect=[1, 1])
        result = await insert_bis_items(_ctx(pool), [_slot("head", 100), _slot("neck", 200)])
        assert result == {"inserted": 2, "skipped": 0}
        calls = conn.execute.call_args_list
        # head → guide_order 1, neck → guide_order 1
        assert calls[0][0][6] == 1  # head guide_order
        assert calls[1][0][6] == 1  # neck guide_order


# ---------------------------------------------------------------------------
# Repeated slots (ring/trinket pairing)
# ---------------------------------------------------------------------------


class TestInsertBisItemsRepeatedSlots:
    @pytest.mark.asyncio
    async def test_ring_gets_sequential_guide_orders(self):
        """Three rings: ring_1 x2 (orders 1,2) and ring_2 x1 (order 1)."""
        pool, conn = _make_pool(fetchval_side_effect=[1, 1, 1])
        slots = [_slot("ring_1", 10), _slot("ring_1", 11), _slot("ring_2", 20)]
        result = await insert_bis_items(_ctx(pool), slots)
        assert result == {"inserted": 3, "skipped": 0}
        orders = [conn.execute.call_args_list[i][0][6] for i in range(3)]
        assert orders == [1, 2, 1]

    @pytest.mark.asyncio
    async def test_trinket_gets_sequential_guide_orders(self):
        pool, conn = _make_pool(fetchval_side_effect=[1, 1])
        slots = [_slot("trinket_1", 30), _slot("trinket_1", 31)]
        result = await insert_bis_items(_ctx(pool), slots)
        assert result["inserted"] == 2
        assert conn.execute.call_args_list[0][0][6] == 1
        assert conn.execute.call_args_list[1][0][6] == 2


# ---------------------------------------------------------------------------
# Weapon slot resolution
# ---------------------------------------------------------------------------


class TestInsertBisItemsWeapons:
    @pytest.mark.asyncio
    async def test_main_hand_two_hand_resolves_to_main_hand_2h(self):
        # First fetchval = slot_type from enrichment.items, second = FK existence check
        pool, conn = _make_pool(fetchval_side_effect=["two_hand", 1])
        result = await insert_bis_items(_ctx(pool), [_slot("main_hand", 500)])
        assert result == {"inserted": 1, "skipped": 0}
        args = conn.execute.call_args[0]
        assert args[4] == "main_hand_2h"

    @pytest.mark.asyncio
    async def test_main_hand_one_hand_resolves_to_main_hand_1h(self):
        pool, conn = _make_pool(fetchval_side_effect=["one_hand", 1])
        result = await insert_bis_items(_ctx(pool), [_slot("main_hand", 501)])
        assert result == {"inserted": 1, "skipped": 0}
        args = conn.execute.call_args[0]
        assert args[4] == "main_hand_1h"

    @pytest.mark.asyncio
    async def test_main_hand_ranged_resolves_to_main_hand_2h(self):
        pool, conn = _make_pool(fetchval_side_effect=["ranged", 1])
        result = await insert_bis_items(_ctx(pool), [_slot("main_hand", 502)])
        assert result == {"inserted": 1, "skipped": 0}
        args = conn.execute.call_args[0]
        assert args[4] == "main_hand_2h"

    @pytest.mark.asyncio
    async def test_main_hand_missing_from_enrichment_items_skipped(self):
        # _resolve_weapon_slot returns None when slot_type is None (item not found)
        pool, conn = _make_pool(fetchval_side_effect=[None])
        result = await insert_bis_items(_ctx(pool), [_slot("main_hand", 999)])
        assert result == {"inserted": 0, "skipped": 1}
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_weapons_get_guide_orders_1_and_2(self):
        # weapon_counter increments for each main_hand: first=1, second=2
        pool, conn = _make_pool(fetchval_side_effect=["two_hand", 1, "one_hand", 1])
        slots = [_slot("main_hand", 500), _slot("main_hand", 501)]
        result = await insert_bis_items(_ctx(pool), slots)
        assert result == {"inserted": 2, "skipped": 0}
        assert conn.execute.call_args_list[0][0][6] == 1
        assert conn.execute.call_args_list[1][0][6] == 2


# ---------------------------------------------------------------------------
# FK validation
# ---------------------------------------------------------------------------


class TestInsertBisItemsFkCheck:
    @pytest.mark.asyncio
    async def test_item_not_in_enrichment_items_skipped(self):
        # fetchval returns None → item not found
        pool, conn = _make_pool(fetchval_side_effect=[None])
        result = await insert_bis_items(_ctx(pool), [_slot("head", 999)])
        assert result == {"inserted": 0, "skipped": 1}
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_existing_and_missing_items(self):
        # first item exists, second does not
        pool, conn = _make_pool(fetchval_side_effect=[1, None])
        slots = [_slot("head", 100), _slot("neck", 999)]
        result = await insert_bis_items(_ctx(pool), slots)
        assert result == {"inserted": 1, "skipped": 1}


# ---------------------------------------------------------------------------
# Duplicate INSERT exception
# ---------------------------------------------------------------------------


class TestInsertBisItemsDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate_exception_counted_as_skipped(self):
        pool, conn = _make_pool(fetchval_side_effect=[1])
        conn.execute = AsyncMock(side_effect=Exception("duplicate key"))
        result = await insert_bis_items(_ctx(pool), [_slot("head", 100)])
        assert result == {"inserted": 0, "skipped": 1}


# ---------------------------------------------------------------------------
# bis_note stamping
# ---------------------------------------------------------------------------


class TestInsertBisItemsNote:
    @pytest.mark.asyncio
    async def test_note_is_passed_to_insert(self):
        pool, conn = _make_pool(fetchval_side_effect=[1])
        await insert_bis_items(_ctx(pool), [_slot("head", 100)], note="San'layn build")
        args = conn.execute.call_args[0]
        assert args[7] == "San'layn build"

    @pytest.mark.asyncio
    async def test_no_note_passes_none(self):
        pool, conn = _make_pool(fetchval_side_effect=[1])
        await insert_bis_items(_ctx(pool), [_slot("head", 100)])
        args = conn.execute.call_args[0]
        assert args[7] is None

    @pytest.mark.asyncio
    async def test_note_applied_to_all_items(self):
        pool, conn = _make_pool(fetchval_side_effect=[1, 1])
        await insert_bis_items(_ctx(pool), [_slot("head", 100), _slot("neck", 200)], note="M+ variant")
        for c in conn.execute.call_args_list:
            assert c[0][7] == "M+ variant"


# ---------------------------------------------------------------------------
# guide_order_start offset
# ---------------------------------------------------------------------------


class TestInsertBisItemsGuideOrderStart:
    @pytest.mark.asyncio
    async def test_guide_order_start_2_offsets_regular_slots(self):
        pool, conn = _make_pool(fetchval_side_effect=[1, 1])
        slots = [_slot("head", 100), _slot("head", 101)]
        await insert_bis_items(_ctx(pool), slots, guide_order_start=2)
        orders = [conn.execute.call_args_list[i][0][6] for i in range(2)]
        assert orders == [2, 3]

    @pytest.mark.asyncio
    async def test_guide_order_start_1_is_default(self):
        pool, conn = _make_pool(fetchval_side_effect=[1, 1])
        slots = [_slot("head", 100), _slot("head", 101)]
        await insert_bis_items(_ctx(pool), slots, guide_order_start=1)
        orders = [conn.execute.call_args_list[i][0][6] for i in range(2)]
        assert orders == [1, 2]

    @pytest.mark.asyncio
    async def test_guide_order_start_3_offsets_weapons(self):
        pool, conn = _make_pool(fetchval_side_effect=["two_hand", 1])
        await insert_bis_items(_ctx(pool), [_slot("main_hand", 500)], guide_order_start=3)
        args = conn.execute.call_args[0]
        assert args[6] == 3  # weapon_counter starts at 2, increments to 3


# ---------------------------------------------------------------------------
# Return value shape
# ---------------------------------------------------------------------------


class TestInsertBisItemsReturnShape:
    @pytest.mark.asyncio
    async def test_return_has_inserted_and_skipped_keys(self):
        pool, conn = _make_pool(fetchval_side_effect=[1])
        result = await insert_bis_items(_ctx(pool), [_slot("head", 100)])
        assert "inserted" in result
        assert "skipped" in result

    @pytest.mark.asyncio
    async def test_all_skipped_returns_zero_inserted(self):
        pool, conn = _make_pool(fetchval_side_effect=[None, None])
        slots = [_slot("head", 999), _slot("neck", 998)]
        result = await insert_bis_items(_ctx(pool), slots)
        assert result["inserted"] == 0
        assert result["skipped"] == 2
