"""Unit tests for run_drift_scan() in drift_scanner.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sv_common.guild_sync.drift_scanner import run_drift_scan, DRIFT_RULE_TYPES


# ---------------------------------------------------------------------------
# DRIFT_RULE_TYPES constant
# ---------------------------------------------------------------------------

class TestDriftRuleTypes:
    def test_contains_expected_types(self):
        assert "duplicate_discord" in DRIFT_RULE_TYPES
        assert "stale_discord_link" in DRIFT_RULE_TYPES

    def test_retired_types_removed(self):
        assert "note_mismatch" not in DRIFT_RULE_TYPES
        assert "link_contradicts_note" not in DRIFT_RULE_TYPES

    def test_is_frozenset(self):
        assert isinstance(DRIFT_RULE_TYPES, frozenset)

    def test_has_two_types(self):
        assert len(DRIFT_RULE_TYPES) == 2


# ---------------------------------------------------------------------------
# run_drift_scan() result shape
# ---------------------------------------------------------------------------

def _make_pool(conn):
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


class TestRunDriftScan:
    @pytest.mark.asyncio
    async def test_returns_dict(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_result_has_expected_keys(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert "duplicate_discord" in result
        assert "total_new" in result
        assert "auto_mitigated" in result

    @pytest.mark.asyncio
    async def test_retired_keys_not_in_result(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert "note_mismatch" not in result
        assert "link_contradicts_note" not in result

    @pytest.mark.asyncio
    async def test_total_new_equals_discord_detections(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=3)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert result["total_new"] == 3
        assert result["duplicate_discord"]["detected"] == 3

    @pytest.mark.asyncio
    async def test_all_zero_when_no_drift(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert result["total_new"] == 0
        assert result["auto_mitigated"] == 0

    @pytest.mark.asyncio
    async def test_detect_function_called(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        detect_dd = AsyncMock(return_value=0)
        mitigate = AsyncMock(return_value={"resolved": 0})
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", detect_dd),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", mitigate),
        ):
            await run_drift_scan(pool)
        detect_dd.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_mitigations_always_run(self):
        """run_auto_mitigations should be called regardless of detection counts."""
        conn = AsyncMock()
        pool = _make_pool(conn)
        mitigate = AsyncMock(return_value={"resolved": 0})
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", mitigate),
        ):
            await run_drift_scan(pool)
        mitigate.assert_called_once_with(pool)
