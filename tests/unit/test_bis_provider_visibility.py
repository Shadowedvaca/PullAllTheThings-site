"""Regression coverage for admin-only BIS provider visibility."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from guild_portal.services.gear_plan_service import (
    _resolve_member_bis_source,
    get_or_create_plan,
)
from guild_portal.services.guide_links_service import get_enabled_sites, invalidate_cache
from sv_common.bis_provider_policy import (
    HIDDEN_BIS_SOURCE_ORIGINS,
    HIDDEN_GUIDE_SITE_NAMES,
    is_hidden_bis_source,
    is_hidden_guide_site,
)
from sv_common.guild_sync.bis_sync import _snapshot_bis_entries
from sv_common.guild_sync.scheduler import GuildSyncScheduler


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_hidden_provider_policy_names_all_three_sources():
    assert HIDDEN_BIS_SOURCE_ORIGINS == ("archon", "icy_veins", "ugg")
    assert HIDDEN_GUIDE_SITE_NAMES == (
        "Archon",
        "Archon.gg",
        "Icy Veins",
        "U.GG",
        "u.gg",
    )
    assert is_hidden_bis_source("archon")
    assert is_hidden_bis_source("icy_veins")
    assert is_hidden_bis_source("ugg")
    assert not is_hidden_bis_source("wowhead")
    assert is_hidden_guide_site("Icy Veins")
    assert is_hidden_guide_site("u.gg")
    assert is_hidden_guide_site("Archon.gg")
    assert not is_hidden_guide_site("Wowhead")


@pytest.mark.asyncio
async def test_member_source_resolver_falls_back_from_admin_only_source():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,
            {
                "id": 10,
                "name": "Wowhead Overall",
                "short_label": "Wowhead",
                "content_type": "overall",
                "origin": "wowhead",
                "is_default": True,
                "sort_order": 1,
            },
        ]
    )

    source = await _resolve_member_bis_source(conn, 30)

    assert source["origin"] == "wowhead"
    assert conn.fetchrow.await_count == 2
    for call in conn.fetchrow.await_args_list:
        assert list(HIDDEN_BIS_SOURCE_ORIGINS) in call.args
        assert "COALESCE(origin, '')" in call.args[0]


@pytest.mark.asyncio
async def test_member_source_resolver_rejects_admin_only_source_without_fallback():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)

    source = await _resolve_member_bis_source(
        conn, 30, allow_fallback=False
    )

    assert source is None
    conn.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_plan_moves_from_hidden_source_to_visible_fallback():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            {
                "id": 99,
                "player_id": 1,
                "character_id": 2,
                "spec_id": 3,
                "hero_talent_id": None,
                "bis_source_id": 30,
                "simc_profile": None,
                "is_active": True,
                "simc_imported_at": None,
                "equipped_source": "blizzard",
            },
            None,
            {
                "id": 10,
                "name": "Wowhead Overall",
                "short_label": "Wowhead",
                "content_type": "overall",
                "origin": "wowhead",
                "is_default": True,
                "sort_order": 1,
            },
        ]
    )
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _Acquire(conn)

    plan = await get_or_create_plan(pool, 1, 2)

    assert plan["bis_source_id"] == 10
    assert plan["_member_source_fallback_applied"] is True
    conn.execute.assert_awaited_once()
    assert "UPDATE guild_identity.gear_plans" in conn.execute.await_args.args[0]
    assert conn.execute.await_args.args[1:] == (10, 99)


@pytest.mark.asyncio
async def test_member_guide_query_excludes_hidden_sites():
    invalidate_cache()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    assert await get_enabled_sites(db) == []

    statement = db.execute.await_args.args[0]
    sql = str(statement)
    assert "common.guide_sites.name NOT IN" in sql


@pytest.mark.asyncio
async def test_filtered_snapshot_excludes_admin_only_origins_in_sql():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    assert await _snapshot_bis_entries(conn, HIDDEN_BIS_SOURCE_ORIGINS) == {}

    sql, origins = conn.fetch.await_args.args
    assert "JOIN ref.bis_list_sources" in sql
    assert "COALESCE(bls.origin, '')" in sql
    assert origins == list(HIDDEN_BIS_SOURCE_ORIGINS)


@pytest.mark.asyncio
async def test_legacy_archon_schedule_performs_no_database_or_network_work():
    scheduler = GuildSyncScheduler.__new__(GuildSyncScheduler)
    scheduler.db_pool = MagicMock()

    await scheduler.run_archon_sync()

    scheduler.db_pool.assert_not_called()
