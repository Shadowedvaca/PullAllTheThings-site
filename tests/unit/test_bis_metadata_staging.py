"""Regression tests for staging item metadata referenced by active BIS pages."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sv_common.guild_sync.bis_sync import stage_active_bis_item_metadata
from sv_common.guild_sync.simc_parser import SimcSlot


def _pool_with_fetches(*fetch_results):
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=fetch_results)
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.mark.asyncio
async def test_stages_base_and_catalyst_result_before_rebuild():
    raw_rows = [
        {
            "content": "guide html",
            "url": "https://example.test/guide",
            "source": "ugg",
            "source_id": 1,
            "spec_id": 2,
            "content_type": "overall",
        }
    ]
    pool, conn = _pool_with_fetches(raw_rows, [])
    client = MagicMock()
    client.get_item = AsyncMock(
        side_effect=lambda item_id: {"id": item_id, "name": str(item_id)}
    )
    parsed = [
        SimcSlot(
            "hands", 200, recommendation_type="catalyst", catalyst_tier_item_id=201
        ),
        SimcSlot("main_hand_2h", 300),
    ]

    with (
        patch(
            "sv_common.guild_sync.bis_sync._load_slot_labels",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_wowhead_invtypes",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_raid_instance_names",
            AsyncMock(return_value=frozenset()),
        ),
        patch("sv_common.guild_sync.bis_sync._parse_ugg_html", return_value=parsed),
    ):
        result = await stage_active_bis_item_metadata(pool, client)

    assert result == {
        "referenced": 3,
        "missing": 3,
        "staged": 3,
        "errors": [],
    }
    assert {call.args[0] for call in client.get_item.await_args_list} == {200, 201, 300}
    assert conn.execute.await_count == 3


@pytest.mark.asyncio
async def test_does_not_refetch_items_already_present_in_landing():
    raw_rows = [
        {
            "content": "guide html",
            "url": "https://example.test/guide",
            "source": "ugg",
            "source_id": 1,
            "spec_id": 2,
            "content_type": "overall",
        }
    ]
    pool, conn = _pool_with_fetches(raw_rows, [{"blizzard_item_id": 300}])
    client = MagicMock()
    client.get_item = AsyncMock()

    with (
        patch(
            "sv_common.guild_sync.bis_sync._load_slot_labels",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_wowhead_invtypes",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_raid_instance_names",
            AsyncMock(return_value=frozenset()),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._parse_ugg_html",
            return_value=[SimcSlot("main_hand_2h", 300)],
        ),
    ):
        result = await stage_active_bis_item_metadata(pool, client)

    assert result["missing"] == 0
    assert result["staged"] == 0
    client.get_item.assert_not_awaited()
    conn.execute.assert_not_awaited()
