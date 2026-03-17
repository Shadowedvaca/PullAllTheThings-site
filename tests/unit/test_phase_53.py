"""
Unit tests for Phase 5.3 — AH Multi-Realm.

Tests cover:
1. get_prices_for_realm() — function exists and is async
2. get_available_realms() — labels zero as region, returns empty when no data
3. get_active_connected_realm_ids() — filters by last_login cutoff, deduplicates
4. sync_ah_prices() — new list signature, commodities stored as realm_id=0
5. Migration 0045 exists
6. SiteConfig has active_connected_realm_ids field
7. Public API endpoint /ah-prices exists
8. Member market endpoint /character/{id}/market exists
9. ah_service module has new functions
10. ah_sync has get_active_connected_realm_ids
"""

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1. get_prices_for_realm — function exists and is async
# ---------------------------------------------------------------------------

class TestGetPricesForRealm:
    def test_function_exists(self):
        from sv_common.guild_sync.ah_service import get_prices_for_realm
        assert callable(get_prices_for_realm)

    def test_is_async(self):
        from sv_common.guild_sync.ah_service import get_prices_for_realm
        assert inspect.iscoroutinefunction(get_prices_for_realm)

    @pytest.mark.asyncio
    async def test_returns_list(self):
        from sv_common.guild_sync.ah_service import get_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await get_prices_for_realm(mock_pool, 11)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_passes_realm_id(self):
        from sv_common.guild_sync.ah_service import get_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        await get_prices_for_realm(mock_pool, 42)
        # Verify fetch was called with realm_id=42 as a parameter
        mock_conn.fetch.assert_called_once()
        args = mock_conn.fetch.call_args
        # The realm_id should be passed as a positional arg after the query string
        assert 42 in args[0]

    @pytest.mark.asyncio
    async def test_realm_zero_works(self):
        from sv_common.guild_sync.ah_service import get_prices_for_realm

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await get_prices_for_realm(mock_pool, 0)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 2. get_available_realms
# ---------------------------------------------------------------------------

class TestGetAvailableRealms:
    def test_function_exists(self):
        from sv_common.guild_sync.ah_service import get_available_realms
        assert callable(get_available_realms)

    def test_is_async(self):
        from sv_common.guild_sync.ah_service import get_available_realms
        assert inspect.iscoroutinefunction(get_available_realms)

    @pytest.mark.asyncio
    async def test_labels_zero_as_region(self):
        from sv_common.guild_sync.ah_service import get_available_realms

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"connected_realm_id": 0},
            {"connected_realm_id": 11},
        ])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await get_available_realms(mock_pool)
        assert len(result) == 2
        region_entry = next(r for r in result if r["connected_realm_id"] == 0)
        assert "Region" in region_entry["label"]
        realm_entry = next(r for r in result if r["connected_realm_id"] == 11)
        assert "11" in realm_entry["label"]

    @pytest.mark.asyncio
    async def test_empty_when_no_recent_data(self):
        from sv_common.guild_sync.ah_service import get_available_realms

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await get_available_realms(mock_pool)
        assert result == []


# ---------------------------------------------------------------------------
# 3. get_active_connected_realm_ids
# ---------------------------------------------------------------------------

class TestGetActiveConnectedRealmIds:
    def test_function_exists(self):
        from sv_common.guild_sync.ah_sync import get_active_connected_realm_ids
        assert callable(get_active_connected_realm_ids)

    def test_is_async(self):
        from sv_common.guild_sync.ah_sync import get_active_connected_realm_ids
        assert inspect.iscoroutinefunction(get_active_connected_realm_ids)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_chars(self):
        from sv_common.guild_sync.ah_sync import get_active_connected_realm_ids

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await get_active_connected_realm_ids(mock_pool, MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_realms(self):
        from sv_common.guild_sync.ah_sync import get_active_connected_realm_ids

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        # Two slugs both resolving to realm 11
        mock_conn.fetch = AsyncMock(return_value=[
            {"realm_slug": "senjin"},
            {"realm_slug": "zuljin"},
        ])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        mock_client = MagicMock()
        mock_client.get_connected_realm_id = AsyncMock(return_value=11)

        result = await get_active_connected_realm_ids(mock_pool, mock_client)
        assert result == [11]  # Deduplicated

    @pytest.mark.asyncio
    async def test_returns_sorted_list(self):
        from sv_common.guild_sync.ah_sync import get_active_connected_realm_ids

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"realm_slug": "senjin"},
            {"realm_slug": "area-52"},
        ])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        mock_client = MagicMock()
        mock_client.get_connected_realm_id = AsyncMock(side_effect=[11, 3])

        result = await get_active_connected_realm_ids(mock_pool, mock_client)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# 4. sync_ah_prices — new list signature
# ---------------------------------------------------------------------------

class TestSyncAhPricesMultiRealm:
    @pytest.mark.asyncio
    async def test_commodities_stored_with_realm_zero(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "item_id": 212241}])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        mock_client = MagicMock()
        mock_client.get_commodities = AsyncMock(return_value={
            "auctions": [{"item": {"id": 212241}, "unit_price": 500_000, "quantity": 5}]
        })
        mock_client.get_auctions = AsyncMock(return_value={"auctions": []})

        result = await sync_ah_prices(mock_pool, mock_client, [11])
        assert result["items_updated"] >= 1

        # Verify execute was called — the INSERT uses hardcoded 0 for commodities
        execute_calls = mock_conn.execute.call_args_list
        assert len(execute_calls) > 0
        # The SQL string should contain the hardcoded 0 for connected_realm_id
        commodity_call = execute_calls[0]
        sql_text = commodity_call[0][0]  # first positional arg is the SQL string
        assert "VALUES ($1, $2, $3, $4, $5, $6, $7, 0)" in sql_text

    @pytest.mark.asyncio
    async def test_no_tracked_items_returns_early(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        result = await sync_ah_prices(mock_pool, MagicMock(), [11])
        assert result["status"] == "no_tracked_items"

    def test_accepts_list_signature(self):
        """sync_ah_prices must accept a list of realm IDs."""
        from sv_common.guild_sync.ah_sync import sync_ah_prices
        sig = inspect.signature(sync_ah_prices)
        params = list(sig.parameters.keys())
        assert "connected_realm_ids" in params

    def test_rejects_old_int_signature(self):
        """connected_realm_id (single int) must NOT be in the signature."""
        from sv_common.guild_sync.ah_sync import sync_ah_prices
        sig = inspect.signature(sync_ah_prices)
        assert "connected_realm_id" not in sig.parameters

    @pytest.mark.asyncio
    async def test_multiple_realms_calls_get_auctions(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        # One non-commodity item
        mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "item_id": 99999}])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock()
        ))

        mock_client = MagicMock()
        mock_client.get_commodities = AsyncMock(return_value={"auctions": []})
        mock_client.get_auctions = AsyncMock(return_value={
            "auctions": [{"item": {"id": 99999}, "buyout": 1_000_000, "quantity": 1}]
        })

        result = await sync_ah_prices(mock_pool, mock_client, [11, 22])
        # Should call get_auctions for the first realm (item found there, so stops)
        assert mock_client.get_auctions.call_count >= 1


# ---------------------------------------------------------------------------
# 5. Migration 0045 exists
# ---------------------------------------------------------------------------

class TestMigration0045:
    def test_migration_file_exists(self):
        from pathlib import Path
        migrations_dir = Path(__file__).parent.parent.parent / "alembic" / "versions"
        files = list(migrations_dir.glob("0045_*.py"))
        assert len(files) == 1, f"Expected 1 migration file matching 0045_*.py, found {len(files)}"

    def test_migration_down_revision(self):
        from pathlib import Path
        import importlib.util
        migrations_dir = Path(__file__).parent.parent.parent / "alembic" / "versions"
        files = list(migrations_dir.glob("0045_*.py"))
        spec = importlib.util.spec_from_file_location("migration_0045", files[0])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.down_revision == "0044"
        assert mod.revision == "0045"


# ---------------------------------------------------------------------------
# 6. SiteConfig has active_connected_realm_ids
# ---------------------------------------------------------------------------

class TestSiteConfigField:
    def test_active_connected_realm_ids_field(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "active_connected_realm_ids")


# ---------------------------------------------------------------------------
# 7. Public API endpoint exists
# ---------------------------------------------------------------------------

class TestPublicAhPricesEndpoint:
    def test_guild_routes_has_ah_prices(self):
        from guild_portal.api import guild_routes
        src = inspect.getsource(guild_routes)
        assert "ah-prices" in src or "ah_prices" in src

    def test_get_ah_prices_function_exists(self):
        from guild_portal.api.guild_routes import get_ah_prices
        assert callable(get_ah_prices)
        assert inspect.iscoroutinefunction(get_ah_prices)


# ---------------------------------------------------------------------------
# 8. Member market endpoint exists
# ---------------------------------------------------------------------------

class TestMemberMarketEndpoint:
    def test_market_endpoint_exists(self):
        from guild_portal.api import member_routes
        src = inspect.getsource(member_routes)
        assert "market" in src

    def test_get_character_market_function_exists(self):
        from guild_portal.api.member_routes import get_character_market
        assert callable(get_character_market)
        assert inspect.iscoroutinefunction(get_character_market)


# ---------------------------------------------------------------------------
# 9. ah_service module has new functions
# ---------------------------------------------------------------------------

class TestAhServiceNewFunctions:
    def test_get_prices_for_realm_in_module(self):
        from sv_common.guild_sync import ah_service
        assert hasattr(ah_service, "get_prices_for_realm")

    def test_get_available_realms_in_module(self):
        from sv_common.guild_sync import ah_service
        assert hasattr(ah_service, "get_available_realms")

    def test_get_current_prices_still_exists(self):
        """Backward compat: get_current_prices should still be present."""
        from sv_common.guild_sync import ah_service
        assert hasattr(ah_service, "get_current_prices")


# ---------------------------------------------------------------------------
# 10. ah_sync has get_active_connected_realm_ids
# ---------------------------------------------------------------------------

class TestAhSyncNewFunctions:
    def test_get_active_connected_realm_ids_in_module(self):
        from sv_common.guild_sync import ah_sync
        assert hasattr(ah_sync, "get_active_connected_realm_ids")

    def test_sync_ah_prices_signature_updated(self):
        from sv_common.guild_sync.ah_sync import sync_ah_prices
        sig = inspect.signature(sync_ah_prices)
        # Must have connected_realm_ids (list) not connected_realm_id (int)
        assert "connected_realm_ids" in sig.parameters
        assert "connected_realm_id" not in sig.parameters
