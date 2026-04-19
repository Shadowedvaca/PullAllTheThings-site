"""Unit tests for the tier token processor in item_source_sync.py.

Covers tooltip parsing helpers and process_tier_tokens() orchestration.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sv_common.guild_sync.item_source_sync import (
    _armor_type_from_class_ids,
    _armor_type_from_tooltip,
    _is_tier_token,
    _parse_token_class_ids,
    _parse_token_slot,
    process_tier_tokens,
)


# ---------------------------------------------------------------------------
# Minimal pool mock
# ---------------------------------------------------------------------------


def _make_pool(fetch_side_effect=None, fetchrow_returns=None, execute_return="UPDATE 0"):
    """Build a minimal asyncpg pool mock.

    fetch_side_effect: list of return values for successive conn.fetch calls.
                       Single call = token candidates (tier piece backfill removed in Phase B).
                       Defaults to returning [] for all calls.
    fetchrow_returns: list of side-effect values for conn.fetchrow calls
    execute_return: string returned by conn.execute (mimics asyncpg 'UPDATE N' etc.)
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_return)
    conn.fetch = AsyncMock(side_effect=fetch_side_effect or [[]])
    if fetchrow_returns is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_returns)
    else:
        conn.fetchrow = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


# ---------------------------------------------------------------------------
# _is_tier_token
# ---------------------------------------------------------------------------


class TestIsTierToken:
    def test_synthesize_soulbound_set(self):
        html = '<div>Use: Synthesize a soulbound set chest item appropriate for your class.</div>'
        assert _is_tier_token(html) is True

    def test_trade_for_class_set_armor(self):
        html = '<div>Use: Trade this for powerful class set armor.</div>'
        assert _is_tier_token(html) is True

    def test_regular_drop_item(self):
        html = '<div><span>Ky\'veza\'s Ring of Dread</span><div class="wowhead-tooltip-item-ilvl">658</div></div>'
        assert _is_tier_token(html) is False

    def test_empty_tooltip(self):
        assert _is_tier_token("") is False

    def test_none_tooltip(self):
        assert _is_tier_token(None) is False  # type: ignore[arg-type]

    def test_item_set_link_not_a_token(self):
        # A tier *piece* has /item-set= in its tooltip but is NOT a token
        html = '<a href="/item-set=1689/dawnbreakers-radiance">Dawnbreaker\'s Radiance</a>'
        assert _is_tier_token(html) is False


# ---------------------------------------------------------------------------
# _parse_token_slot
# ---------------------------------------------------------------------------


class TestParseTokenSlot:
    def test_chest_slot(self):
        html = 'Use: Synthesize a soulbound set chest item appropriate for your class.'
        assert _parse_token_slot(html) == "chest"

    def test_head_slot(self):
        html = 'Use: Synthesize a soulbound set head item appropriate for your class.'
        assert _parse_token_slot(html) == "head"

    def test_hand_normalises_to_hands(self):
        html = 'Use: Synthesize a soulbound set hand item appropriate for your class.'
        assert _parse_token_slot(html) == "hands"

    def test_shoulder_slot(self):
        html = 'Use: Synthesize a soulbound set shoulder item appropriate for your class.'
        assert _parse_token_slot(html) == "shoulder"

    def test_legs_slot(self):
        html = 'Use: Synthesize a soulbound set legs item appropriate for your class.'
        assert _parse_token_slot(html) == "legs"

    def test_no_use_text_returns_any(self):
        html = '<div>Generic tradeable item.</div>'
        assert _parse_token_slot(html) == "any"

    def test_case_insensitive(self):
        html = 'USE: SYNTHESIZE A SOULBOUND SET CHEST ITEM appropriate for your class.'
        assert _parse_token_slot(html) == "chest"


# ---------------------------------------------------------------------------
# _parse_token_class_ids
# ---------------------------------------------------------------------------


class TestParseTokenClassIds:
    _CLOTH_HTML = """
    <div class="wowhead-tooltip-item-classes">
      Classes: <a href="/class=5/priest">Priest</a>,
               <a href="/class=8/mage">Mage</a>,
               <a href="/class=9/warlock">Warlock</a>
    </div>
    """

    _PLATE_HTML = """
    <div class="wowhead-tooltip-item-classes">
      Classes: <a href="/class=1/warrior">Warrior</a>,
               <a href="/class=2/paladin">Paladin</a>,
               <a href="/class=6/death-knight">Death Knight</a>
    </div>
    """

    def test_cloth_classes(self):
        ids = _parse_token_class_ids(self._CLOTH_HTML)
        assert set(ids) == {5, 8, 9}

    def test_plate_classes(self):
        ids = _parse_token_class_ids(self._PLATE_HTML)
        assert set(ids) == {1, 2, 6}

    def test_no_classes_div_returns_empty(self):
        # Chiming Void Curio — no class restriction
        html = '<div>Use: Synthesize a soulbound set chest item appropriate for your class.</div>'
        assert _parse_token_class_ids(html) == []

    def test_empty_string(self):
        assert _parse_token_class_ids("") == []


# ---------------------------------------------------------------------------
# _armor_type_from_tooltip
# ---------------------------------------------------------------------------


class TestArmorTypeFromTooltip:
    def test_plate_armor(self):
        html = '<span>Plate</span><span>Armor</span>'
        assert _armor_type_from_tooltip(html) == "plate"

    def test_leather_armor(self):
        html = 'some stuff >Leather< more stuff'
        assert _armor_type_from_tooltip(html) == "leather"

    def test_cloth_armor(self):
        html = '>Cloth< Armor</span>'
        assert _armor_type_from_tooltip(html) == "cloth"

    def test_mail_armor(self):
        html = '>Mail< Armor</span>'
        assert _armor_type_from_tooltip(html) == "mail"

    def test_case_insensitive(self):
        html = '>LEATHER< Armor</span>'
        assert _armor_type_from_tooltip(html) == "leather"

    def test_no_armor_type_returns_none(self):
        html = '<div>Some random item text without armor type</div>'
        assert _armor_type_from_tooltip(html) is None

    def test_empty_returns_none(self):
        assert _armor_type_from_tooltip("") is None

    def test_none_returns_none(self):
        assert _armor_type_from_tooltip(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _armor_type_from_class_ids
# ---------------------------------------------------------------------------


class TestArmorTypeFromClassIds:
    def test_cloth_classes(self):
        # Priest=5, Mage=8, Warlock=9
        assert _armor_type_from_class_ids([5, 8, 9]) == "cloth"

    def test_plate_classes(self):
        # Warrior=1, Paladin=2, Death Knight=6
        assert _armor_type_from_class_ids([1, 2, 6]) == "plate"

    def test_leather_classes(self):
        # Rogue=4, Druid=11, Monk=10, DH=12
        assert _armor_type_from_class_ids([4, 10, 11, 12]) == "leather"

    def test_mail_classes(self):
        # Hunter=3, Shaman=7, Evoker=13
        assert _armor_type_from_class_ids([3, 7, 13]) == "mail"

    def test_empty_returns_any(self):
        # No class restriction — universal token
        assert _armor_type_from_class_ids([]) == "any"

    def test_mixed_armor_types_returns_any(self):
        # Cloth + plate = can't determine single armor type
        assert _armor_type_from_class_ids([5, 1]) == "any"

    def test_unknown_class_id_ignored(self):
        # Class 99 is not in the map; remaining classes are cloth
        result = _armor_type_from_class_ids([5, 8, 99])
        # Only 5 and 8 map; both are cloth — result should be cloth
        assert result == "cloth"


# ---------------------------------------------------------------------------
# process_tier_tokens — integration smoke tests with mocked pool
# ---------------------------------------------------------------------------


class TestProcessTierTokens:
    """Smoke tests for process_tier_tokens() orchestration.

    These mock the DB and flag_junk_sources to isolate the logic.
    """

    _TOKEN_HTML = (
        'Use: Synthesize a soulbound set chest item appropriate for your class. '
        '<div class="wowhead-tooltip-item-classes">'
        '<a href="/class=1/warrior">Warrior</a>'
        '</div>'
    )

    _NON_TOKEN_HTML = (
        '<a href="/item-set=1689/dawnbreakers-radiance">Dawnbreaker\'s Radiance</a>'
    )

    @pytest.mark.asyncio
    async def test_processes_tier_token(self):
        """A token item gets upserted into tier_token_attrs."""
        token_row = {
            "blizzard_item_id": 12345,
            "name": "Alnforged Riftbloom",
            "wowhead_tooltip_html": self._TOKEN_HTML,
        }
        pool, conn = _make_pool(
            fetch_side_effect=[[token_row]],
            fetchrow_returns=[None],  # no existing tier_token_attrs row
            execute_return="UPDATE 0",
        )

        with patch(
            "sv_common.guild_sync.item_source_sync.flag_junk_sources",
            new=AsyncMock(return_value={
                "flagged_world_boss": 0,
                "flagged_tier_piece": 1,
                "total_flagged": 1,
            }),
        ):
            result = await process_tier_tokens(pool)

        assert result["tokens_found"] == 1
        assert result["tokens_processed"] == 1
        assert result["tokens_skipped_override"] == 0
        assert result["junk_flagged"] == 1

    @pytest.mark.asyncio
    async def test_skips_manual_override(self):
        """Tokens with is_manual_override=TRUE are not reprocessed."""
        token_row = {
            "blizzard_item_id": 12345,
            "name": "Alnforged Riftbloom",
            "wowhead_tooltip_html": self._TOKEN_HTML,
        }
        override_row = {"is_manual_override": True}
        pool, conn = _make_pool(
            fetch_side_effect=[[token_row]],
            fetchrow_returns=[override_row],
            execute_return="UPDATE 1",
        )

        with patch(
            "sv_common.guild_sync.item_source_sync.flag_junk_sources",
            new=AsyncMock(return_value={
                "flagged_world_boss": 0,
                "flagged_tier_piece": 0,
                "total_flagged": 0,
            }),
        ):
            result = await process_tier_tokens(pool)

        assert result["tokens_found"] == 1
        assert result["tokens_processed"] == 0
        assert result["tokens_skipped_override"] == 1

    @pytest.mark.asyncio
    async def test_ignores_non_token_items(self):
        """Items with slot_type='other' but no tier token text are ignored."""
        non_token_row = {
            "blizzard_item_id": 99999,
            "name": "Some Other Item",
            "wowhead_tooltip_html": self._NON_TOKEN_HTML,
        }
        pool, conn = _make_pool(
            fetch_side_effect=[[non_token_row]],
            fetchrow_returns=[None],
            execute_return="UPDATE 0",
        )

        with patch(
            "sv_common.guild_sync.item_source_sync.flag_junk_sources",
            new=AsyncMock(return_value={
                "flagged_world_boss": 0,
                "flagged_tier_piece": 0,
                "total_flagged": 0,
            }),
        ):
            result = await process_tier_tokens(pool)

        assert result["tokens_found"] == 0
        assert result["tokens_processed"] == 0

    @pytest.mark.asyncio
    async def test_empty_wow_items_returns_zero_counts(self):
        """When no enrichment.items with slot_type='other' and tooltip exist, returns all zeros."""
        pool, conn = _make_pool(fetch_side_effect=[[]], execute_return="UPDATE 0")

        with patch(
            "sv_common.guild_sync.item_source_sync.flag_junk_sources",
            new=AsyncMock(return_value={
                "flagged_world_boss": 0,
                "flagged_tier_piece": 0,
                "total_flagged": 0,
            }),
        ):
            result = await process_tier_tokens(pool)

        assert result["tokens_found"] == 0
        assert result["tokens_processed"] == 0
        assert result["junk_flagged"] == 0

    @pytest.mark.asyncio
    async def test_candidates_query_uses_enrichment_and_landing(self):
        """Candidates fetch must read from enrichment.items and landing.wowhead_tooltips."""
        pool, conn = _make_pool(fetch_side_effect=[[]], execute_return="UPDATE 0")

        with patch(
            "sv_common.guild_sync.item_source_sync.flag_junk_sources",
            new=AsyncMock(return_value={
                "flagged_world_boss": 0,
                "flagged_tier_piece": 0,
                "total_flagged": 0,
            }),
        ):
            await process_tier_tokens(pool)

        fetch_sql = conn.fetch.call_args_list[0].args[0]
        assert "enrichment.items" in fetch_sql
        assert "landing.wowhead_tooltips" in fetch_sql
        assert "guild_identity.wow_items" not in fetch_sql  # Phase E: wow_items fully retired

    @pytest.mark.asyncio
    async def test_slot_and_armor_type_parsed_correctly(self):
        """Parsed slot and armor type are logged correctly (smoke test via conn.execute)."""
        # Plate chest token
        plate_chest_html = (
            'Use: Synthesize a soulbound set chest item appropriate for your class. '
            '<div class="wowhead-tooltip-item-classes">'
            '<a href="/class=1/warrior">Warrior</a>'
            '<a href="/class=2/paladin">Paladin</a>'
            '<a href="/class=6/death-knight">Death Knight</a>'
            '</div>'
        )
        token_row = {
            "blizzard_item_id": 11111,
            "name": "Alnforged Riftbloom",
            "wowhead_tooltip_html": plate_chest_html,
        }
        pool, conn = _make_pool(
            fetch_side_effect=[[token_row]],
            fetchrow_returns=[None],
            execute_return="UPDATE 0",
        )

        with patch(
            "sv_common.guild_sync.item_source_sync.flag_junk_sources",
            new=AsyncMock(return_value={
                "flagged_world_boss": 0,
                "flagged_tier_piece": 1,
                "total_flagged": 1,
            }),
        ):
            await process_tier_tokens(pool)

        # The upsert execute call should have been made with slot='chest', armor='plate'
        upsert_calls = [
            call for call in conn.execute.call_args_list
            if "tier_token_attrs" in str(call)
        ]
        assert len(upsert_calls) == 1
        call_args = upsert_calls[0].args
        # Args: (sql, blizzard_item_id, target_slot, armor_type, class_ids, now)
        assert call_args[1] == 11111     # blizzard_item_id (now the PK)
        assert call_args[2] == "chest"   # target_slot
        assert call_args[3] == "plate"   # armor_type
        assert set(call_args[4]) == {1, 2, 6}  # eligible_class_ids
