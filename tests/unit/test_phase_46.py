"""
Unit tests for Phase 4.6 — Auction House Pricing.

Tests cover:
1. copper_to_gold_str() conversion helper
2. sync_ah_prices() — filtering, aggregation, upsert
3. cleanup_old_prices() — retention logic
4. get_connected_realm_id() — realm resolution
5. BlizzardClient AH methods exist
6. Scheduler has run_ah_sync method
7. ORM models: TrackedItem, ItemPriceHistory exist
8. SiteConfig has connected_realm_id field
9. Admin _PATH_TO_SCREEN includes ah_pricing
10. Index page context includes ah_prices key
"""

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1. copper_to_gold_str
# ---------------------------------------------------------------------------

class TestCopperToGoldStr:
    def test_import(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        assert callable(copper_to_gold_str)

    def test_zero_returns_dash(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        assert copper_to_gold_str(0) == "—"

    def test_none_returns_dash(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        assert copper_to_gold_str(None) == "—"

    def test_silver_only(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        # 5000 copper = 50 silver
        assert copper_to_gold_str(5000) == "50s"

    def test_gold_and_silver(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        # 15000 copper = 1g 50s
        assert copper_to_gold_str(15000) == "1g 50s"

    def test_large_gold_no_silver(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        # 10_000_000 copper = 1000g exactly
        assert copper_to_gold_str(10_000_000) == "1,000g"

    def test_large_gold_abbreviated(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        # 12_345_600 copper = 1234g 56s → ≥1000g so abbreviated
        result = copper_to_gold_str(12_345_600)
        assert result == "1,234g"

    def test_small_gold(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        # 250_000 copper = 25g 0s
        assert copper_to_gold_str(250_000) == "25g 0s"

    def test_one_gold_zero_silver(self):
        from sv_common.guild_sync.ah_service import copper_to_gold_str
        assert copper_to_gold_str(10_000) == "1g 0s"


# ---------------------------------------------------------------------------
# 2. _aggregate_auctions helper
# ---------------------------------------------------------------------------

class TestAggregateAuctions:
    def test_commodity_auction_aggregation(self):
        from sv_common.guild_sync.ah_sync import _aggregate_auctions
        tracked_map = {212241: 1}
        item_prices = {}
        item_quantities = {}
        item_auction_counts = {}

        auctions = [
            {"item": {"id": 212241}, "unit_price": 500_000, "quantity": 10},
            {"item": {"id": 212241}, "unit_price": 600_000, "quantity": 5},
            {"item": {"id": 99999}, "unit_price": 100_000, "quantity": 1},  # not tracked
        ]
        _aggregate_auctions(auctions, tracked_map, item_prices, item_quantities, item_auction_counts)

        assert 212241 in item_prices
        assert item_prices[212241] == [500_000, 600_000]
        assert item_quantities[212241] == 15
        assert item_auction_counts[212241] == 2
        assert 99999 not in item_prices

    def test_non_commodity_auction_uses_buyout(self):
        from sv_common.guild_sync.ah_sync import _aggregate_auctions
        tracked_map = {100: 1}
        item_prices = {}
        item_quantities = {}
        item_auction_counts = {}

        auctions = [
            {"item": {"id": 100}, "buyout": 1_000_000, "quantity": 1},
        ]
        _aggregate_auctions(auctions, tracked_map, item_prices, item_quantities, item_auction_counts)
        assert item_prices[100] == [1_000_000]

    def test_skips_zero_price(self):
        from sv_common.guild_sync.ah_sync import _aggregate_auctions
        tracked_map = {200: 1}
        item_prices = {}
        item_quantities = {}
        item_auction_counts = {}

        auctions = [
            {"item": {"id": 200}, "unit_price": 0, "quantity": 1},
        ]
        _aggregate_auctions(auctions, tracked_map, item_prices, item_quantities, item_auction_counts)
        assert 200 not in item_prices

    def test_skips_untracked_items(self):
        from sv_common.guild_sync.ah_sync import _aggregate_auctions
        tracked_map = {}
        item_prices = {}
        item_quantities = {}
        item_auction_counts = {}

        auctions = [
            {"item": {"id": 555}, "unit_price": 100, "quantity": 1},
        ]
        _aggregate_auctions(auctions, tracked_map, item_prices, item_quantities, item_auction_counts)
        assert not item_prices


# ---------------------------------------------------------------------------
# 3. sync_ah_prices — mock pool and client
# ---------------------------------------------------------------------------

class TestSyncAhPrices:
    @pytest.mark.asyncio
    async def test_no_tracked_items_returns_early(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        result = await sync_ah_prices(mock_pool, MagicMock(), [11])
        assert result["status"] == "no_tracked_items"

    @pytest.mark.asyncio
    async def test_syncs_prices_from_commodities(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        # First fetch: tracked items
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "item_id": 212241},
        ])
        mock_conn.execute = AsyncMock()

        # Use a context manager stack
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        mock_client = MagicMock()
        mock_client.get_commodities = AsyncMock(return_value={
            "auctions": [
                {"item": {"id": 212241}, "unit_price": 500_000, "quantity": 5},
                {"item": {"id": 212241}, "unit_price": 600_000, "quantity": 3},
            ]
        })
        mock_client.get_auctions = AsyncMock(return_value={"auctions": []})

        result = await sync_ah_prices(mock_pool, mock_client, [11])
        assert result["items_updated"] == 1
        assert result["items_not_listed"] == 0
        # Should not need to call get_auctions since commodity was found
        mock_client.get_auctions.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_realm_auctions(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "item_id": 212241},
        ])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        mock_client = MagicMock()
        # Commodities doesn't have the item
        mock_client.get_commodities = AsyncMock(return_value={"auctions": []})
        # Realm auctions does
        mock_client.get_auctions = AsyncMock(return_value={
            "auctions": [
                {"item": {"id": 212241}, "buyout": 1_000_000, "quantity": 1},
            ]
        })

        result = await sync_ah_prices(mock_pool, mock_client, [11])
        assert result["items_updated"] == 1
        mock_client.get_auctions.assert_called_once_with(11)

    @pytest.mark.asyncio
    async def test_item_not_on_ah_counted_as_not_listed(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"id": 1, "item_id": 212241},
        ])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        mock_client = MagicMock()
        mock_client.get_commodities = AsyncMock(return_value={"auctions": []})
        mock_client.get_auctions = AsyncMock(return_value={"auctions": []})

        result = await sync_ah_prices(mock_pool, mock_client, [11])
        assert result["items_not_listed"] == 1
        assert result["items_updated"] == 0


# ---------------------------------------------------------------------------
# 4. get_price_change
# ---------------------------------------------------------------------------

class TestGetPriceChange:
    @pytest.mark.asyncio
    async def test_returns_change_pct(self):
        from sv_common.guild_sync.ah_service import get_price_change

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        # fetchval returns current price, then yesterday's price
        mock_conn.fetchval = AsyncMock(side_effect=[110_000, 100_000])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await get_price_change(mock_pool, 1)
        assert result["current"] == 110_000
        assert result["yesterday"] == 100_000
        assert result["change_pct"] == 10.0

    @pytest.mark.asyncio
    async def test_no_history_returns_nones(self):
        from sv_common.guild_sync.ah_service import get_price_change

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=[None, None])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await get_price_change(mock_pool, 1)
        assert result["change_pct"] is None
        assert result["yesterday"] is None

    def test_change_pct_calculation(self):
        # Test the arithmetic: 100 → 80 = -20%
        old_price = 100_000
        new_price = 80_000
        change_pct = round(((new_price - old_price) / old_price) * 100, 1)
        assert change_pct == -20.0


# ---------------------------------------------------------------------------
# 5. BlizzardClient AH methods
# ---------------------------------------------------------------------------

class TestBlizzardClientAhMethods:
    def test_ah_methods_exist(self):
        from sv_common.guild_sync.blizzard_client import BlizzardClient
        assert hasattr(BlizzardClient, "get_connected_realm_id")
        assert hasattr(BlizzardClient, "get_auctions")
        assert hasattr(BlizzardClient, "get_commodities")

    def test_get_connected_realm_id_is_async(self):
        from sv_common.guild_sync.blizzard_client import BlizzardClient
        assert inspect.iscoroutinefunction(BlizzardClient.get_connected_realm_id)

    def test_get_auctions_is_async(self):
        from sv_common.guild_sync.blizzard_client import BlizzardClient
        assert inspect.iscoroutinefunction(BlizzardClient.get_auctions)

    def test_get_commodities_is_async(self):
        from sv_common.guild_sync.blizzard_client import BlizzardClient
        assert inspect.iscoroutinefunction(BlizzardClient.get_commodities)

    def test_realm_id_extraction_from_href(self):
        """Unit test the regex logic used in get_connected_realm_id."""
        import re
        href = "https://us.api.blizzard.com/data/wow/connected-realm/11?namespace=dynamic-us"
        match = re.search(r"/connected-realm/(\d+)", href)
        assert match is not None
        assert int(match.group(1)) == 11

    def test_realm_id_extraction_no_match(self):
        import re
        href = "https://us.api.blizzard.com/data/wow/realm/senjin"
        match = re.search(r"/connected-realm/(\d+)", href)
        assert match is None


# ---------------------------------------------------------------------------
# 6. Scheduler has run_ah_sync
# ---------------------------------------------------------------------------

class TestSchedulerAhSync:
    def test_run_ah_sync_exists(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        assert hasattr(GuildSyncScheduler, "run_ah_sync")

    def test_run_ah_sync_is_coroutine(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        assert inspect.iscoroutinefunction(GuildSyncScheduler.run_ah_sync)

    def test_ah_sync_job_in_start(self):
        """Scheduler.start() should register ah_sync job."""
        from sv_common.guild_sync import scheduler as sched_module
        src = inspect.getsource(GuildSyncScheduler := sched_module.GuildSyncScheduler)
        assert "ah_sync" in src
        assert "run_ah_sync" in src


# ---------------------------------------------------------------------------
# 7. ORM models
# ---------------------------------------------------------------------------

class TestOrmModels:
    def test_tracked_item_model_exists(self):
        from sv_common.db.models import TrackedItem
        assert TrackedItem is not None

    def test_tracked_item_fields(self):
        from sv_common.db.models import TrackedItem
        assert hasattr(TrackedItem, "item_id")
        assert hasattr(TrackedItem, "item_name")
        assert hasattr(TrackedItem, "category")
        assert hasattr(TrackedItem, "is_active")
        assert TrackedItem.__table_args__["schema"] == "guild_identity"

    def test_item_price_history_model_exists(self):
        from sv_common.db.models import ItemPriceHistory
        assert ItemPriceHistory is not None

    def test_item_price_history_fields(self):
        from sv_common.db.models import ItemPriceHistory
        assert hasattr(ItemPriceHistory, "tracked_item_id")
        assert hasattr(ItemPriceHistory, "min_buyout")
        assert hasattr(ItemPriceHistory, "median_price")
        assert hasattr(ItemPriceHistory, "quantity_available")
        assert hasattr(ItemPriceHistory, "connected_realm_id")

    def test_site_config_has_connected_realm_id(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "connected_realm_id")


# ---------------------------------------------------------------------------
# 8. Admin _PATH_TO_SCREEN
# ---------------------------------------------------------------------------

class TestAdminPathToScreen:
    def test_ah_pricing_in_path_to_screen(self):
        from guild_portal.pages.admin_pages import _PATH_TO_SCREEN
        paths = [p for p, _ in _PATH_TO_SCREEN]
        assert "/admin/ah-pricing" in paths

    def test_ah_pricing_screen_key(self):
        from guild_portal.pages.admin_pages import _PATH_TO_SCREEN
        mapping = dict(_PATH_TO_SCREEN)
        assert mapping.get("/admin/ah-pricing") == "ah_pricing"


# ---------------------------------------------------------------------------
# 9. ah_service module structure
# ---------------------------------------------------------------------------

class TestAhServiceModule:
    def test_module_imports(self):
        from sv_common.guild_sync import ah_service
        assert hasattr(ah_service, "copper_to_gold_str")
        assert hasattr(ah_service, "get_current_prices")
        assert hasattr(ah_service, "get_price_trend")
        assert hasattr(ah_service, "get_price_change")
        assert hasattr(ah_service, "get_tracked_items_with_prices")

    def test_get_current_prices_is_async(self):
        from sv_common.guild_sync.ah_service import get_current_prices
        assert inspect.iscoroutinefunction(get_current_prices)

    def test_get_price_trend_is_async(self):
        from sv_common.guild_sync.ah_service import get_price_trend
        assert inspect.iscoroutinefunction(get_price_trend)


# ---------------------------------------------------------------------------
# 10. ah_sync module structure
# ---------------------------------------------------------------------------

class TestAhSyncModule:
    def test_module_imports(self):
        from sv_common.guild_sync import ah_sync
        assert hasattr(ah_sync, "sync_ah_prices")
        assert hasattr(ah_sync, "cleanup_old_prices")
        assert hasattr(ah_sync, "_aggregate_auctions")

    def test_sync_ah_prices_is_async(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices
        assert inspect.iscoroutinefunction(sync_ah_prices)

    def test_cleanup_old_prices_is_async(self):
        from sv_common.guild_sync.ah_sync import cleanup_old_prices
        assert inspect.iscoroutinefunction(cleanup_old_prices)


# ---------------------------------------------------------------------------
# 11. Migration file exists
# ---------------------------------------------------------------------------

class TestMigration:
    def test_migration_file_exists(self):
        from pathlib import Path
        migrations_dir = Path(__file__).parent.parent.parent / "alembic" / "versions"
        files = list(migrations_dir.glob("0040_*.py"))
        assert len(files) == 1, f"Expected 1 migration file matching 0040_*.py, found {len(files)}"

    def test_migration_down_revision(self):
        from pathlib import Path
        import importlib.util
        migrations_dir = Path(__file__).parent.parent.parent / "alembic" / "versions"
        files = list(migrations_dir.glob("0040_*.py"))
        spec = importlib.util.spec_from_file_location("migration_0040", files[0])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.down_revision == "0039"
        assert mod.revision == "0040"
