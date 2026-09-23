"""Provider access-challenge detection and circuit-skip persistence tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sv_common.guild_sync.bis_sync import (
    _provider_access_block_reason,
    record_provider_circuit_skip,
    sync_source,
)


def _response(status: int, body: str, server: str = "cloudflare") -> httpx.Response:
    request = httpx.Request("GET", "https://provider.example/gear")
    return httpx.Response(
        status,
        text=body,
        headers={"server": server},
        request=request,
    )


def test_detects_cloudflare_403_challenge():
    response = _response(403, "<html><title>Just a moment...</title></html>")

    reason = _provider_access_block_reason(response)

    assert reason == (
        "provider access blocked: provider.example returned "
        "HTTP 403 Cloudflare challenge"
    )


def test_detects_human_verification_returned_with_http_200():
    response = _response(200, "<html><title>Human Verification</title></html>")

    reason = _provider_access_block_reason(response)

    assert reason == (
        "provider access blocked: provider.example returned "
        "HTTP 200 Cloudflare challenge"
    )


def test_normal_provider_content_is_not_blocked():
    response = _response(200, "<html><title>Gear guide</title></html>", server="nginx")

    assert _provider_access_block_reason(response) is None


@pytest.mark.asyncio
async def test_circuit_skip_preserves_cached_items_and_last_success():
    conn = AsyncMock()
    pool = MagicMock()
    acquire = AsyncMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire)

    await record_provider_circuit_skip(
        pool,
        target_id=42,
        technique="json_embed",
        items_found=16,
        error="provider access blocked: provider.example returned HTTP 403 Cloudflare challenge",
    )

    assert conn.execute.await_count == 2
    insert_sql, target_id, technique, items_found, error, _created_at = (
        conn.execute.await_args_list[0].args
    )
    update_sql, updated_target_id = conn.execute.await_args_list[1].args
    assert "INSERT INTO log.bis_scrape_log" in insert_sql
    assert target_id == 42
    assert technique == "json_embed"
    assert items_found == 16
    assert error.startswith("provider circuit open: provider access blocked")
    assert "SET status = 'failed'" in update_sql
    assert "last_fetched" not in update_sql
    assert "items_found" not in update_sql
    assert updated_target_id == 42


@pytest.mark.asyncio
async def test_source_sync_stops_requesting_after_blocked_canary():
    targets = [
        {
            "id": 1,
            "source_id": 13,
            "spec_id": 1,
            "hero_talent_id": None,
            "content_type": "raid",
            "url": "https://www.archon.gg/one",
            "preferred_technique": "json_embed_archon",
            "items_found": 16,
            "origin": "archon",
        },
        {
            "id": 2,
            "source_id": 13,
            "spec_id": 2,
            "hero_talent_id": None,
            "content_type": "raid",
            "url": "https://www.archon.gg/two",
            "preferred_technique": "json_embed_archon",
            "items_found": 16,
            "origin": "archon",
        },
    ]
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=targets)
    pool = MagicMock()
    acquire = AsyncMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire)

    blocked = {
        "items_found": 0,
        "status": "failed",
        "error": "provider access blocked: www.archon.gg returned HTTP 200 Cloudflare challenge",
        "provider_access_blocked": True,
    }
    with (
        patch(
            "sv_common.guild_sync.bis_sync.sync_target",
            new_callable=AsyncMock,
            return_value=blocked,
        ) as sync_target,
        patch(
            "sv_common.guild_sync.bis_sync.record_provider_circuit_skip",
            new_callable=AsyncMock,
        ) as record_skip,
        patch(
            "sv_common.guild_sync.bis_sync.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        stats = await sync_source(pool, source_id=13)

    sync_target.assert_awaited_once()
    assert sync_target.await_args.args[1] == 1
    record_skip.assert_awaited_once()
    assert record_skip.await_args.args[1] == 2
    assert stats == {"targets_run": 1, "items_found": 0, "errors": 1, "skipped": 1}
