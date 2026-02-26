"""Unit tests for run_drift_scan() in drift_scanner.py."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sv_common.guild_sync.drift_scanner import run_drift_scan, DRIFT_RULE_TYPES


# ---------------------------------------------------------------------------
# DRIFT_RULE_TYPES constant
# ---------------------------------------------------------------------------

class TestDriftRuleTypes:
    def test_contains_expected_types(self):
        assert "note_mismatch" in DRIFT_RULE_TYPES
        assert "link_contradicts_note" in DRIFT_RULE_TYPES
        assert "duplicate_discord" in DRIFT_RULE_TYPES
        assert "stale_discord_link" in DRIFT_RULE_TYPES

    def test_is_frozenset(self):
        assert isinstance(DRIFT_RULE_TYPES, frozenset)

    def test_has_four_types(self):
        assert len(DRIFT_RULE_TYPES) == 4


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
            patch("sv_common.guild_sync.drift_scanner.detect_note_mismatch", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_link_note_contradictions", AsyncMock(return_value=0)),
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
            patch("sv_common.guild_sync.drift_scanner.detect_note_mismatch", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_link_note_contradictions", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert "note_mismatch" in result
        assert "link_contradicts_note" in result
        assert "duplicate_discord" in result
        assert "total_new" in result
        assert "auto_mitigated" in result

    @pytest.mark.asyncio
    async def test_note_mismatch_subkeys(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_note_mismatch", AsyncMock(return_value=2)),
            patch("sv_common.guild_sync.drift_scanner.detect_link_note_contradictions", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 2})),
        ):
            result = await run_drift_scan(pool)
        assert result["note_mismatch"]["detected"] == 2
        assert result["note_mismatch"]["mitigated"] == 2

    @pytest.mark.asyncio
    async def test_total_new_sums_all_detections(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_note_mismatch", AsyncMock(return_value=1)),
            patch("sv_common.guild_sync.drift_scanner.detect_link_note_contradictions", AsyncMock(return_value=2)),
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=3)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert result["total_new"] == 6

    @pytest.mark.asyncio
    async def test_all_zero_when_no_drift(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_note_mismatch", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_link_note_contradictions", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", AsyncMock(return_value={"resolved": 0})),
        ):
            result = await run_drift_scan(pool)
        assert result["total_new"] == 0
        assert result["auto_mitigated"] == 0

    @pytest.mark.asyncio
    async def test_all_three_detect_functions_called(self):
        conn = AsyncMock()
        pool = _make_pool(conn)
        detect_nm = AsyncMock(return_value=0)
        detect_lc = AsyncMock(return_value=0)
        detect_dd = AsyncMock(return_value=0)
        mitigate = AsyncMock(return_value={"resolved": 0})
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_note_mismatch", detect_nm),
            patch("sv_common.guild_sync.drift_scanner.detect_link_note_contradictions", detect_lc),
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", detect_dd),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", mitigate),
        ):
            await run_drift_scan(pool)
        detect_nm.assert_called_once()
        detect_lc.assert_called_once()
        detect_dd.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_mitigations_always_run(self):
        """run_auto_mitigations should be called regardless of detection counts."""
        conn = AsyncMock()
        pool = _make_pool(conn)
        mitigate = AsyncMock(return_value={"resolved": 0})
        with (
            patch("sv_common.guild_sync.drift_scanner.detect_note_mismatch", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_link_note_contradictions", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.detect_duplicate_discord_links", AsyncMock(return_value=0)),
            patch("sv_common.guild_sync.drift_scanner.run_auto_mitigations", mitigate),
        ):
            await run_drift_scan(pool)
        mitigate.assert_called_once_with(pool)
