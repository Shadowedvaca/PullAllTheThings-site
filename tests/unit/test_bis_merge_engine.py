"""Unit tests for merge_bis_sections() — BIS Note & Guide Folding Phase 3.

Tests the merge engine in isolation using mock asyncpg pools.
insert_bis_items() is patched so tests focus on the secondary-pass merge logic.

Coverage:
- Empty secondary list → returns primary result unchanged
- Secondary item not in primary → inserted with secondary_note at next guide_order
- Secondary item already present → stamped with match_note, counted as skipped
- Secondary item already present, match_note=None → UPDATE not called
- Mixed: some secondary items match, some are new
- primary_note is forwarded to insert_bis_items()
- secondary_note=None → INSERT with NULL note
- Paired slot (ring) — LIKE-based presence check covers ring_1 and ring_2
- Weapon item in secondary (main_hand) → resolved then checked
- FK check failure in secondary → skipped
- Weapon resolution failure in secondary → skipped
- Duplicate INSERT exception in secondary → counted as skipped
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from sv_common.guild_sync.bis_sync import BisInsertionContext, merge_bis_sections
from sv_common.guild_sync.simc_parser import SimcSlot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool(fetchval_se=None, fetchrow_se=None, execute_se=None):
    """Build a mock asyncpg pool supporting fetchval, fetchrow, and execute."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_se)
    conn.fetchrow = AsyncMock(side_effect=fetchrow_se)
    conn.execute = AsyncMock(side_effect=execute_se)
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


def _override(
    *,
    primary_note=None,
    match_note=None,
    secondary_note=None,
    secondary_section_key="area_2",
):
    return {
        "primary_note": primary_note,
        "match_note": match_note,
        "secondary_note": secondary_note,
        "secondary_section_key": secondary_section_key,
    }


_PATCH = "sv_common.guild_sync.bis_sync.insert_bis_items"


# ---------------------------------------------------------------------------
# Empty secondary list
# ---------------------------------------------------------------------------


class TestMergeBisSectionsEmptySecondary:
    @pytest.mark.asyncio
    async def test_empty_secondary_returns_primary_result(self):
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 3, "skipped": 1}
            pool, conn = _make_pool()
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [],
                _override(),
            )
        assert result == {"inserted": 3, "skipped": 1}
        conn.fetchval.assert_not_called()
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_primary_and_secondary_returns_zeros(self):
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 0, "skipped": 0}
            pool, conn = _make_pool()
            result = await merge_bis_sections(_ctx(pool), [], [], _override())
        assert result == {"inserted": 0, "skipped": 0}

    @pytest.mark.asyncio
    async def test_primary_note_forwarded_to_insert_bis_items(self):
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, _ = _make_pool()
            ctx = _ctx(pool)
            await merge_bis_sections(
                ctx,
                [_slot("head", 100)],
                [],
                _override(primary_note="Deathbringer build"),
            )
        _, kwargs = mock_insert.call_args
        assert kwargs.get("note") == "Deathbringer build" or mock_insert.call_args[0][2] == "Deathbringer build"


# ---------------------------------------------------------------------------
# Secondary item not present → insert with secondary_note
# ---------------------------------------------------------------------------


class TestMergeBisSectionsNewSecondaryItem:
    @pytest.mark.asyncio
    async def test_secondary_item_inserted_with_note(self):
        """New neck item in secondary → INSERT with secondary_note."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            # fetchval calls in order: FK check (1=exists), max guide_order (2)
            # fetchrow: presence check (None = not present)
            pool, conn = _make_pool(
                fetchval_se=[1, 2],
                fetchrow_se=[None],
            )
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("neck", 200)],
                _override(secondary_note="San'layn build"),
            )
        assert result["inserted"] == 2   # 1 primary + 1 secondary
        assert result["skipped"] == 0

        # Verify INSERT was called with secondary_note and guide_order = max+1 = 3
        insert_sql, *args = conn.execute.call_args[0]
        assert args[5] == 3          # guide_order = max_order(2) + 1
        assert args[6] == "San'layn build"

    @pytest.mark.asyncio
    async def test_secondary_item_inserted_when_max_order_zero(self):
        """max guide_order = 0 → new item gets guide_order 1."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(fetchval_se=[1, 0], fetchrow_se=[None])
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("neck", 200)],
                _override(),
            )
        assert result["inserted"] == 2
        _sql, *args = conn.execute.call_args[0]
        assert args[5] == 1   # guide_order = max_order(0) + 1

    @pytest.mark.asyncio
    async def test_secondary_note_none_inserts_null_note(self):
        """secondary_note=None → INSERT with NULL bis_note."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(fetchval_se=[1, 0], fetchrow_se=[None])
            await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("neck", 200)],
                _override(secondary_note=None),
            )
        _sql, *args = conn.execute.call_args[0]
        assert args[6] is None  # bis_note is NULL


# ---------------------------------------------------------------------------
# Secondary item already present → match_note
# ---------------------------------------------------------------------------


class TestMergeBisSectionsMatchingItem:
    @pytest.mark.asyncio
    async def test_matching_item_stamped_with_match_note(self):
        """Secondary head=100 matches primary → UPDATE bis_note with match_note."""
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: "head"  # existing["slot"]

        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(
                fetchval_se=[1],           # FK check passes
                fetchrow_se=[existing_row],  # item IS present
            )
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("head", 100)],
                _override(match_note="Both builds"),
            )
        assert result["inserted"] == 1   # only primary
        assert result["skipped"] == 1    # secondary counted as skipped (matched)
        # UPDATE should have been called with match_note
        conn.execute.assert_called_once()
        update_args = conn.execute.call_args[0]
        assert update_args[0].strip().startswith("UPDATE")
        assert update_args[1] == "Both builds"

    @pytest.mark.asyncio
    async def test_matching_item_no_match_note_no_update(self):
        """match_note=None → UPDATE not called even when item matches."""
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: "head"

        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(
                fetchval_se=[1],
                fetchrow_se=[existing_row],
            )
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("head", 100)],
                _override(match_note=None),
            )
        assert result["skipped"] == 1
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_matching_item_counted_as_skipped(self):
        """Matched items increment skipped, not inserted."""
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: "neck"

        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 2, "skipped": 0}
            pool, _ = _make_pool(fetchval_se=[1], fetchrow_se=[existing_row])
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100), _slot("neck", 200)],
                [_slot("neck", 200)],
                _override(),
            )
        assert result == {"inserted": 2, "skipped": 1}


# ---------------------------------------------------------------------------
# Mixed: some match, some new
# ---------------------------------------------------------------------------


class TestMergeBisSectionsMixed:
    @pytest.mark.asyncio
    async def test_one_match_one_new(self):
        """head matches, shoulder is new → 1 insert + 1 skipped from secondary."""
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: "head"

        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 2, "skipped": 0}
            # Per-item call sequences:
            #   head: fetchval FK(1), fetchrow(existing) → skip
            #   shoulder: fetchval FK(1), fetchrow(None), fetchval max_order(1), execute INSERT
            pool, conn = _make_pool(
                fetchval_se=[1, 1, 1],
                fetchrow_se=[existing_row, None],
            )
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100), _slot("shoulder", 101)],
                [_slot("head", 100), _slot("shoulder", 200)],
                _override(secondary_note="variant"),
            )
        assert result["inserted"] == 3   # 2 primary + 1 secondary (shoulder)
        assert result["skipped"] == 1    # head matched


# ---------------------------------------------------------------------------
# Paired slot (ring/trinket) — LIKE presence check
# ---------------------------------------------------------------------------


class TestMergeBisSectionsPairedSlots:
    @pytest.mark.asyncio
    async def test_ring_item_in_ring2_counts_as_present(self):
        """ring_2 item in secondary — presence check should use 'ring%' LIKE."""
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: "ring_1"  # item found in ring_1

        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(
                fetchval_se=[1],
                fetchrow_se=[existing_row],
            )
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("ring_1", 50)],
                [_slot("ring_2", 50)],   # same item, different paired slot key
                _override(),
            )
        assert result["skipped"] == 1   # counted as matched
        # Verify LIKE was used (5th arg should be "ring%")
        _, *fetchrow_args = conn.fetchrow.call_args[0]
        assert "ring%" in fetchrow_args

    @pytest.mark.asyncio
    async def test_trinket_paired_slot_presence_check(self):
        """trinket_2 item uses 'trinket%' LIKE to match trinket_1."""
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: "trinket_1"

        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(fetchval_se=[1], fetchrow_se=[existing_row])
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("trinket_1", 60)],
                [_slot("trinket_2", 60)],
                _override(),
            )
        assert result["skipped"] == 1
        _, *fetchrow_args = conn.fetchrow.call_args[0]
        assert "trinket%" in fetchrow_args


# ---------------------------------------------------------------------------
# Weapon resolution in secondary
# ---------------------------------------------------------------------------


class TestMergeBisSectionsWeaponInSecondary:
    @pytest.mark.asyncio
    async def test_main_hand_in_secondary_resolved_and_inserted(self):
        """main_hand in secondary: resolved to main_hand_2h, then FK + presence + insert."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            # fetchval: weapon slot_type "two_hand", FK check 1, max guide_order 0
            pool, conn = _make_pool(
                fetchval_se=["two_hand", 1, 0],
                fetchrow_se=[None],   # not present
            )
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("main_hand", 500)],
                _override(),
            )
        assert result["inserted"] == 2
        # Inserted slot should be main_hand_2h
        _sql, *insert_args = conn.execute.call_args[0]
        assert insert_args[3] == "main_hand_2h"

    @pytest.mark.asyncio
    async def test_main_hand_resolution_failure_skipped(self):
        """weapon slot_type=None → resolution fails → secondary item skipped."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(fetchval_se=[None])  # slot_type = None
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("main_hand", 999)],
                _override(),
            )
        assert result["skipped"] == 1
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# FK check failure in secondary
# ---------------------------------------------------------------------------


class TestMergeBisSectionsFkCheck:
    @pytest.mark.asyncio
    async def test_secondary_item_not_in_enrichment_skipped(self):
        """FK check returns None → secondary item skipped without DB insert."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(fetchval_se=[None])  # item not in enrichment.items
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("neck", 999)],
                _override(),
            )
        assert result["inserted"] == 1
        assert result["skipped"] == 1
        conn.fetchrow.assert_not_called()
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Duplicate INSERT exception in secondary
# ---------------------------------------------------------------------------


class TestMergeBisSectionsDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate_insert_counted_as_skipped(self):
        """INSERT raises exception (e.g. unique violation) → counted as skipped."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 1, "skipped": 0}
            pool, conn = _make_pool(
                fetchval_se=[1, 0],   # FK OK, max_order=0
                fetchrow_se=[None],   # not present
            )
            conn.execute = AsyncMock(side_effect=Exception("unique violation"))
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("neck", 200)],
                _override(),
            )
        assert result["inserted"] == 1   # only primary
        assert result["skipped"] == 1    # secondary threw exception


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------


class TestMergeBisSectionsReturnShape:
    @pytest.mark.asyncio
    async def test_return_has_inserted_and_skipped_keys(self):
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 0, "skipped": 0}
            pool, _ = _make_pool()
            result = await merge_bis_sections(_ctx(pool), [], [], _override())
        assert "inserted" in result
        assert "skipped" in result

    @pytest.mark.asyncio
    async def test_totals_sum_primary_and_secondary(self):
        """inserted = primary_inserted + sec_inserted; skipped = primary_skipped + sec_skipped."""
        with patch(_PATCH, new_callable=AsyncMock) as mock_insert:
            mock_insert.return_value = {"inserted": 5, "skipped": 2}
            # Secondary: 2 new items, each FK passes (1), not present (None), max_order=0
            pool, conn = _make_pool(
                fetchval_se=[1, 0, 1, 0],
                fetchrow_se=[None, None],
            )
            result = await merge_bis_sections(
                _ctx(pool),
                [_slot("head", 100)],
                [_slot("neck", 200), _slot("shoulder", 300)],
                _override(),
            )
        assert result["inserted"] == 7   # 5 primary + 2 secondary
        assert result["skipped"] == 2    # 2 from primary, 0 from secondary
