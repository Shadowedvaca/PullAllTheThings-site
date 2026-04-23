"""Unit tests for _fetch_missing_item_icons in equipment_sync.py."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from sv_common.guild_sync.equipment_sync import _fetch_missing_item_icons


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(existing_ids: list[int]) -> MagicMock:
    """Return a mock pool whose blizzard_item_icons table contains existing_ids."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[{"blizzard_item_id": iid} for iid in existing_ids]
    )
    conn.execute = AsyncMock()

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncContextManager(conn))
    return pool, conn


class _AsyncContextManager:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        pass


def _make_client(icon_map: dict[int, str | None]) -> MagicMock:
    """Return a mock BlizzardClient whose get_item_media returns from icon_map."""
    client = MagicMock()
    client.get_item_media = AsyncMock(side_effect=lambda iid: icon_map.get(iid))
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFetchMissingItemIcons:
    @pytest.mark.asyncio
    async def test_no_item_ids_is_noop(self):
        pool, conn = _make_pool([])
        client = _make_client({})

        await _fetch_missing_item_icons(pool, client, [], "Trogmoon")

        conn.fetch.assert_not_called()
        client.get_item_media.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_already_cached_skips_fetch(self):
        pool, conn = _make_pool([111, 222])
        client = _make_client({})

        await _fetch_missing_item_icons(pool, client, [111, 222], "Trogmoon")

        client.get_item_media.assert_not_called()
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_items_are_fetched_and_inserted(self):
        pool, conn = _make_pool([111])  # 111 already cached, 222 is missing
        client = _make_client({222: "https://wow.zamimg.com/icons/medium/inv_sword_01.jpg"})

        # Need a second conn for the INSERT — reuse same mock
        await _fetch_missing_item_icons(pool, client, [111, 222], "Trogmoon")

        client.get_item_media.assert_called_once_with(222)
        conn.execute.assert_called_once()
        sql, item_id, icon_url = conn.execute.call_args.args
        assert item_id == 222
        assert "inv_sword_01" in icon_url

    @pytest.mark.asyncio
    async def test_none_icon_url_is_not_inserted(self):
        pool, conn = _make_pool([])
        client = _make_client({333: None})

        await _fetch_missing_item_icons(pool, client, [333], "Trogmoon")

        client.get_item_media.assert_called_once_with(333)
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_exception_is_swallowed(self):
        pool, conn = _make_pool([])
        client = MagicMock()
        client.get_item_media = AsyncMock(side_effect=Exception("network error"))

        # Should not raise
        await _fetch_missing_item_icons(pool, client, [444], "Trogmoon")

        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_missing_items_fetched_individually(self):
        pool, conn = _make_pool([])
        client = _make_client(
            {
                10: "https://wow.zamimg.com/icons/medium/a.jpg",
                20: "https://wow.zamimg.com/icons/medium/b.jpg",
            }
        )

        await _fetch_missing_item_icons(pool, client, [10, 20], "Trogmoon")

        assert client.get_item_media.call_count == 2
        assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_partial_failure_still_inserts_successful_items(self):
        pool, conn = _make_pool([])

        async def _media(iid: int):
            if iid == 10:
                raise Exception("timeout")
            return "https://wow.zamimg.com/icons/medium/b.jpg"

        client = MagicMock()
        client.get_item_media = AsyncMock(side_effect=_media)

        await _fetch_missing_item_icons(pool, client, [10, 20], "Trogmoon")

        # item 10 failed but item 20 should still be inserted
        assert conn.execute.call_count == 1
        _, item_id, _ = conn.execute.call_args.args
        assert item_id == 20
