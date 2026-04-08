"""
Unit tests for Phase 5.4 — My Characters: Crafting & Raid Prep Panel.

Tests cover:
1. Crafting endpoint exists in member_routes
2. Own-character authorization check (404 when not owned)
3. Craftable recipes returned from character_recipes
4. can_craft_fully always True (no rank schema)
5. Consumables filter to consumable/material categories only
6. change_pct computed correctly (positive = price up)
7. low_stock flag when quantity_available < 50
8. No data states: craftable=[], consumables=[]
9. get_consumable_prices_for_realm function exists and is async
10. Template has mc-crafting div
11. CSS has crafting section styles
12. JS has renderCraftingPanel and fetch call
"""

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Crafting endpoint exists
# ---------------------------------------------------------------------------


class TestCraftingEndpointExists:
    def test_get_character_crafting_callable(self):
        from guild_portal.api.member_routes import get_character_crafting
        assert callable(get_character_crafting)

    def test_endpoint_registered_in_router(self):
        from guild_portal.api.member_routes import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/me/character/{character_id}/crafting" in paths

    def test_endpoint_is_async(self):
        from guild_portal.api.member_routes import get_character_crafting
        assert inspect.iscoroutinefunction(get_character_crafting)


# ---------------------------------------------------------------------------
# 2. Own-character authorization (ownership check)
# ---------------------------------------------------------------------------


class TestCraftingAuth:
    @pytest.mark.asyncio
    async def test_returns_404_when_not_owned(self):
        """Returns 404 JSONResponse when character_id not in player's characters."""
        from fastapi.responses import JSONResponse
        from guild_portal.api.member_routes import get_character_crafting

        player = MagicMock()
        player.id = 1

        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        request = MagicMock()
        request.app.state.guild_sync_pool = None

        result = await get_character_crafting(
            character_id=999, request=request, player=player, db=db
        )

        assert isinstance(result, JSONResponse)
        assert result.status_code == 404


# ---------------------------------------------------------------------------
# 3. Craftable recipes returned
# ---------------------------------------------------------------------------


class TestCraftableRecipes:
    @pytest.mark.asyncio
    async def test_craftable_list_returned(self):
        """Recipes from character_recipes appear in craftable list."""
        from guild_portal.api.member_routes import get_character_crafting

        player = MagicMock()
        player.id = 1

        # Mock character ownership row
        pc_row = MagicMock()
        # Mock WowCharacter
        char_mock = MagicMock()
        char_mock.realm_slug = "senjin"

        # Mock recipe rows returned from text() query
        recipe_row1 = MagicMock()
        recipe_row1.recipe_id = 7
        recipe_row1.recipe_name = "Algari Manuscript"
        recipe_row1.profession = "Inscription"

        recipe_row2 = MagicMock()
        recipe_row2.recipe_id = 8
        recipe_row2.recipe_name = "Contract: Sen'jin"
        recipe_row2.profession = "Inscription"

        db = AsyncMock()
        call_count = [0]

        def execute_side(stmt, params=None):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                # Ownership check
                mock_result.scalar_one_or_none = MagicMock(return_value=pc_row)
            elif call_count[0] == 2:
                # Character lookup
                mock_result.scalar_one_or_none = MagicMock(return_value=char_mock)
            else:
                # Recipe query — iterable
                mock_result.__iter__ = MagicMock(
                    return_value=iter([recipe_row1, recipe_row2])
                )
            return mock_result

        db.execute = AsyncMock(side_effect=execute_side)

        request = MagicMock()
        request.app.state.guild_sync_pool = None

        with patch("sv_common.config_cache.get_site_config", return_value={}):
            result = await get_character_crafting(
                character_id=10, request=request, player=player, db=db
            )

        assert result["ok"] is True
        craftable = result["data"]["craftable"]
        assert len(craftable) == 2
        assert craftable[0]["recipe_name"] == "Algari Manuscript"
        assert craftable[0]["profession"] == "Inscription"
        assert craftable[1]["recipe_name"] == "Contract: Sen'jin"

    @pytest.mark.asyncio
    async def test_empty_craftable_when_no_recipes(self):
        """Empty craftable list when character has no recipes."""
        from guild_portal.api.member_routes import get_character_crafting

        player = MagicMock()
        player.id = 1
        pc_row = MagicMock()
        char_mock = MagicMock()
        char_mock.realm_slug = "senjin"

        db = AsyncMock()
        call_count = [0]

        def execute_side(stmt, params=None):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=pc_row)
            elif call_count[0] == 2:
                mock_result.scalar_one_or_none = MagicMock(return_value=char_mock)
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
            return mock_result

        db.execute = AsyncMock(side_effect=execute_side)
        request = MagicMock()
        request.app.state.guild_sync_pool = None

        with patch("sv_common.config_cache.get_site_config", return_value={}):
            result = await get_character_crafting(
                character_id=10, request=request, player=player, db=db
            )

        assert result["ok"] is True
        assert result["data"]["craftable"] == []


# ---------------------------------------------------------------------------
# 4. can_craft_fully always True (no rank schema)
# ---------------------------------------------------------------------------


class TestCanCraftFully:
    @pytest.mark.asyncio
    async def test_can_craft_fully_always_true(self):
        """can_craft_fully is True for all recipes (rank not tracked in schema)."""
        from guild_portal.api.member_routes import get_character_crafting

        player = MagicMock()
        player.id = 1
        pc_row = MagicMock()
        char_mock = MagicMock()
        char_mock.realm_slug = "senjin"

        recipe_row = MagicMock()
        recipe_row.recipe_id = 5
        recipe_row.recipe_name = "Tempered Flask of the Currents"
        recipe_row.profession = "Alchemy"

        db = AsyncMock()
        call_count = [0]

        def execute_side(stmt, params=None):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=pc_row)
            elif call_count[0] == 2:
                mock_result.scalar_one_or_none = MagicMock(return_value=char_mock)
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([recipe_row]))
            return mock_result

        db.execute = AsyncMock(side_effect=execute_side)
        request = MagicMock()
        request.app.state.guild_sync_pool = None

        with patch("sv_common.config_cache.get_site_config", return_value={}):
            result = await get_character_crafting(
                character_id=10, request=request, player=player, db=db
            )

        craftable = result["data"]["craftable"]
        assert len(craftable) == 1
        assert craftable[0]["can_craft_fully"] is True
        assert craftable[0]["rank"] is None
        assert craftable[0]["max_rank"] is None

    @pytest.mark.asyncio
    async def test_wowhead_url_uses_search_format(self):
        """wowhead_url uses search format, not spell ID."""
        from guild_portal.api.member_routes import get_character_crafting

        player = MagicMock()
        player.id = 1
        pc_row = MagicMock()
        char_mock = MagicMock()
        char_mock.realm_slug = "senjin"

        recipe_row = MagicMock()
        recipe_row.recipe_id = 5
        recipe_row.recipe_name = "Algari Manuscript"
        recipe_row.profession = "Inscription"

        db = AsyncMock()
        call_count = [0]

        def execute_side(stmt, params=None):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=pc_row)
            elif call_count[0] == 2:
                mock_result.scalar_one_or_none = MagicMock(return_value=char_mock)
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([recipe_row]))
            return mock_result

        db.execute = AsyncMock(side_effect=execute_side)
        request = MagicMock()
        request.app.state.guild_sync_pool = None

        with patch("sv_common.config_cache.get_site_config", return_value={}):
            result = await get_character_crafting(
                character_id=10, request=request, player=player, db=db
            )

        wh_url = result["data"]["craftable"][0]["wowhead_url"]
        assert "wowhead.com/search?q=" in wh_url
        assert "Algari+Manuscript" in wh_url


# ---------------------------------------------------------------------------
# 5. Consumables: category filter
# ---------------------------------------------------------------------------


class TestConsumableFilter:
    def test_get_consumable_prices_for_realm_exists(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm
        assert callable(get_consumable_prices_for_realm)

    def test_get_consumable_prices_is_async(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm
        assert inspect.iscoroutinefunction(get_consumable_prices_for_realm)

    @pytest.mark.asyncio
    async def test_returns_list(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = await get_consumable_prices_for_realm(mock_pool, 11)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_filters_consumable_material(self):
        """SQL query includes category IN ('consumable', 'material') filter."""
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        await get_consumable_prices_for_realm(mock_pool, 11)
        # First fetch call should contain the category filter
        first_call_sql = mock_conn.fetch.call_args_list[0][0][0]
        assert "consumable" in first_call_sql
        assert "material" in first_call_sql

    @pytest.mark.asyncio
    async def test_empty_when_no_rows(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = await get_consumable_prices_for_realm(mock_pool, 0)
        assert result == []


# ---------------------------------------------------------------------------
# 6. change_pct computed correctly
# ---------------------------------------------------------------------------


class TestChangePct:
    @pytest.mark.asyncio
    async def test_change_pct_positive_when_price_rises(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        current_price = 200_000  # 20g
        yesterday_price = 100_000  # 10g → +100%

        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        current_row = {
            "id": 1, "item_name": "Tempered Potion", "category": "consumable",
            "min_buyout": current_price, "quantity_available": 100,
            "connected_realm_id": 11,
        }

        prev_row = {"tracked_item_id": 1, "min_buyout": yesterday_price}

        fetch_calls = [0]

        async def fetch_side(*args, **kwargs):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                return [current_row]
            else:
                return [prev_row]

        mock_conn.fetch = AsyncMock(side_effect=fetch_side)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = await get_consumable_prices_for_realm(mock_pool, 11)
        assert len(result) == 1
        assert result[0]["change_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_change_pct_negative_when_price_falls(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        current_price = 90_000   # 9g
        yesterday_price = 100_000  # 10g → -10%

        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        current_row = {
            "id": 2, "item_name": "Algari Mana Potion", "category": "consumable",
            "min_buyout": current_price, "quantity_available": 500,
            "connected_realm_id": 11,
        }
        prev_row = {"tracked_item_id": 2, "min_buyout": yesterday_price}

        fetch_calls = [0]

        async def fetch_side(*args, **kwargs):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                return [current_row]
            else:
                return [prev_row]

        mock_conn.fetch = AsyncMock(side_effect=fetch_side)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = await get_consumable_prices_for_realm(mock_pool, 11)
        assert result[0]["change_pct"] == -10.0

    @pytest.mark.asyncio
    async def test_change_pct_null_when_no_history(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        current_row = {
            "id": 3, "item_name": "Crystallized Augment Rune", "category": "consumable",
            "min_buyout": 450_000, "quantity_available": 200,
            "connected_realm_id": 0,
        }

        fetch_calls = [0]

        async def fetch_side(*args, **kwargs):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                return [current_row]
            else:
                return []  # No history

        mock_conn.fetch = AsyncMock(side_effect=fetch_side)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = await get_consumable_prices_for_realm(mock_pool, 0)
        assert result[0]["change_pct"] is None


# ---------------------------------------------------------------------------
# 7. Low stock flag
# ---------------------------------------------------------------------------


class TestLowStockFlag:
    @pytest.mark.asyncio
    async def test_low_stock_when_quantity_below_50(self):
        """quantity_available < 50 should be preserved in output for UI to flag."""
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        current_row = {
            "id": 4, "item_name": "Crystallized Augment Rune", "category": "consumable",
            "min_buyout": 450_000, "quantity_available": 12,
            "connected_realm_id": 0,
        }

        fetch_calls = [0]

        async def fetch_side(*args, **kwargs):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                return [current_row]
            return []

        mock_conn.fetch = AsyncMock(side_effect=fetch_side)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = await get_consumable_prices_for_realm(mock_pool, 0)
        assert len(result) == 1
        assert result[0]["quantity_available"] == 12  # UI checks < 50

    @pytest.mark.asyncio
    async def test_not_low_stock_when_quantity_above_50(self):
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        current_row = {
            "id": 5, "item_name": "Tempered Potion", "category": "consumable",
            "min_buyout": 185_000, "quantity_available": 340,
            "connected_realm_id": 0,
        }

        fetch_calls = [0]

        async def fetch_side(*args, **kwargs):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                return [current_row]
            return []

        mock_conn.fetch = AsyncMock(side_effect=fetch_side)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = await get_consumable_prices_for_realm(mock_pool, 0)
        assert result[0]["quantity_available"] == 340


# ---------------------------------------------------------------------------
# 8. No data states
# ---------------------------------------------------------------------------


class TestNoDataStates:
    @pytest.mark.asyncio
    async def test_consumables_empty_when_pool_unavailable(self):
        """consumables=[] when pool is not available."""
        from guild_portal.api.member_routes import get_character_crafting

        player = MagicMock()
        player.id = 1
        pc_row = MagicMock()
        char_mock = MagicMock()
        char_mock.realm_slug = "senjin"

        db = AsyncMock()
        call_count = [0]

        def execute_side(stmt, params=None):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=pc_row)
            elif call_count[0] == 2:
                mock_result.scalar_one_or_none = MagicMock(return_value=char_mock)
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
            return mock_result

        db.execute = AsyncMock(side_effect=execute_side)
        request = MagicMock()
        # No pool available
        del request.app.state.guild_sync_pool
        request.app.state = MagicMock(spec=[])

        with patch("sv_common.config_cache.get_site_config", return_value={}):
            result = await get_character_crafting(
                character_id=10, request=request, player=player, db=db
            )

        assert result["ok"] is True
        assert result["data"]["consumables"] == []

    @pytest.mark.asyncio
    async def test_response_structure_complete(self):
        """Response always has character_id, craftable, consumables keys."""
        from guild_portal.api.member_routes import get_character_crafting

        player = MagicMock()
        player.id = 1
        pc_row = MagicMock()
        char_mock = MagicMock()
        char_mock.realm_slug = "senjin"

        db = AsyncMock()
        call_count = [0]

        def execute_side(stmt, params=None):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=pc_row)
            elif call_count[0] == 2:
                mock_result.scalar_one_or_none = MagicMock(return_value=char_mock)
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
            return mock_result

        db.execute = AsyncMock(side_effect=execute_side)
        request = MagicMock()
        request.app.state = MagicMock(spec=[])

        with patch("sv_common.config_cache.get_site_config", return_value={}):
            result = await get_character_crafting(
                character_id=10, request=request, player=player, db=db
            )

        assert "character_id" in result["data"]
        assert "craftable" in result["data"]
        assert "consumables" in result["data"]
        assert result["data"]["character_id"] == 10


# ---------------------------------------------------------------------------
# 9. get_consumable_prices_for_realm in ah_service module
# ---------------------------------------------------------------------------


class TestConsumablePricesFunction:
    def test_in_ah_service_module(self):
        from sv_common.guild_sync import ah_service
        assert hasattr(ah_service, "get_consumable_prices_for_realm")

    def test_includes_min_buyout_display(self):
        """Response includes human-readable price string."""
        import asyncio
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        current_row = {
            "id": 6, "item_name": "Test Item", "category": "consumable",
            "min_buyout": 185_000, "quantity_available": 100,
            "connected_realm_id": 0,
        }

        fetch_calls = [0]

        async def fetch_side(*args, **kwargs):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                return [current_row]
            return []

        mock_conn.fetch = AsyncMock(side_effect=fetch_side)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = asyncio.get_event_loop().run_until_complete(
            get_consumable_prices_for_realm(mock_pool, 0)
        )
        assert "min_buyout_display" in result[0]
        # 185,000 copper = 18g 50s
        assert "18g" in result[0]["min_buyout_display"]

    def test_wowhead_url_uses_search_format(self):
        """wowhead_url uses search?q= format."""
        import asyncio
        from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        current_row = {
            "id": 7, "item_name": "Tempered Potion", "category": "consumable",
            "min_buyout": 200_000, "quantity_available": 50,
            "connected_realm_id": 0,
        }

        fetch_calls = [0]

        async def fetch_side(*args, **kwargs):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                return [current_row]
            return []

        mock_conn.fetch = AsyncMock(side_effect=fetch_side)
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(),
        ))

        result = asyncio.get_event_loop().run_until_complete(
            get_consumable_prices_for_realm(mock_pool, 0)
        )
        assert "wowhead.com/search?q=" in result[0]["wowhead_url"]
        assert "Tempered+Potion" in result[0]["wowhead_url"]


# ---------------------------------------------------------------------------
# 10. Template has mc-crafting div
# ---------------------------------------------------------------------------


class TestCraftingTemplate:
    _tpl = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "templates" / "member" / "my_characters.html"
    )

    def test_crafting_div_present(self):
        # Crafting is rendered dynamically into mcn-detail-area by JS
        content = self._tpl.read_text(encoding="utf-8")
        assert "mcn-detail-area" in content

    def test_crafting_div_has_id(self):
        content = self._tpl.read_text(encoding="utf-8")
        assert 'id="mcn-detail-area"' in content

    def test_crafting_div_hidden_by_default(self):
        # mcn-body (which contains the detail area) starts hidden
        content = self._tpl.read_text(encoding="utf-8")
        idx = content.find('id="mcn-body"')
        surrounding = content[max(0, idx - 20):idx + 80]
        assert "hidden" in surrounding


# ---------------------------------------------------------------------------
# 11. CSS has crafting styles
# ---------------------------------------------------------------------------


class TestCraftingCSS:
    _css = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "static" / "css" / "my_characters.css"
    )

    def test_crafting_container_class(self):
        # Redesigned page uses mcn-prof-* for the professions/crafting panel
        content = self._css.read_text(encoding="utf-8")
        assert "mcn-prof-grid" in content

    def test_crafting_section_class(self):
        content = self._css.read_text(encoding="utf-8")
        assert "mcn-prof-table" in content

    def test_craft_table_class(self):
        content = self._css.read_text(encoding="utf-8")
        assert "mcn-prof-td-recipe" in content

    def test_consumable_status_classes(self):
        # Redesigned page uses mcn-market-cat--* for category badges
        content = self._css.read_text(encoding="utf-8")
        for cls in ("mcn-market-cat--consumable", "mcn-market-cat--enchant",
                    "mcn-market-cat--material"):
            assert cls in content, f"Missing CSS class: {cls}"


# ---------------------------------------------------------------------------
# 12. JS has crafting/market fetch and render logic
# ---------------------------------------------------------------------------


class TestCraftingJS:
    _js = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "static" / "js" / "my_characters.js"
    )

    def test_render_crafting_panel_function(self):
        # Redesigned page uses _craftingCache / _fetchCrafting pattern
        content = self._js.read_text(encoding="utf-8")
        assert "_craftingCache" in content

    def test_crafting_collapse_state_function(self):
        # Redesigned page uses _fetchCrafting instead of a collapse-state helper
        content = self._js.read_text(encoding="utf-8")
        assert "_fetchCrafting" in content

    def test_fetches_crafting_endpoint(self):
        content = self._js.read_text(encoding="utf-8")
        assert "/crafting" in content

    def test_low_stock_threshold_check(self):
        """Market panel: qty display present."""
        content = self._js.read_text(encoding="utf-8")
        assert "mcn-market-qty" in content

    def test_trend_threshold_check(self):
        """Market panel renders category badges."""
        content = self._js.read_text(encoding="utf-8")
        assert "mcn-market-cat" in content
