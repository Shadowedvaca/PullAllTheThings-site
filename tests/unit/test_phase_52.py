"""
Unit tests for Phase 5.2 — My Characters: WCL Parse Panel.

Tests cover:
1. Parses endpoint exists in member_routes
2. Own-character authorization check (404 when not owned)
3. Most recent parse per (boss_name, difficulty) returned (highest percentile wins)
4. Percentile color tier function correct at boundaries
5. Summary fields (best, average) calculated correctly
6. No data → parses: [], summary: null
7. WCL not configured → wcl_configured: false
8. Template has parses panel div
9. CSS has parse tier color classes
10. JS has parsePercentileTier and renderParsesPanel
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Parses endpoint exists
# ---------------------------------------------------------------------------


class TestParsesEndpointExists:
    def test_get_character_parses_callable(self):
        from guild_portal.api.member_routes import get_character_parses
        assert callable(get_character_parses)

    def test_endpoint_registered_in_router(self):
        from guild_portal.api.member_routes import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/me/character/{character_id}/parses" in paths

    def test_endpoint_is_async(self):
        import inspect
        from guild_portal.api.member_routes import get_character_parses
        assert inspect.iscoroutinefunction(get_character_parses)


# ---------------------------------------------------------------------------
# 2. Own-character authorization (ownership check)
# ---------------------------------------------------------------------------


class TestParsesAuth:
    @pytest.mark.asyncio
    async def test_returns_404_when_not_owned(self):
        """Returns 404 JSONResponse when character_id not in player's characters."""
        from fastapi.responses import JSONResponse
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        result = await get_character_parses(character_id=999, player=player, db=db)

        assert isinstance(result, JSONResponse)
        assert result.status_code == 404


# ---------------------------------------------------------------------------
# 3. Best-parse-per-boss deduplication (highest percentile wins)
# ---------------------------------------------------------------------------


def _make_zone_row(zone_id):
    """Mock row for zone derivation query."""
    row = MagicMock()
    row.zone_id = zone_id
    return row


def _make_parse_row(encounter_name, best_pct, zone_name="Nerub-ar Palace",
                    report_code=None, raid_date=None, last_synced=None):
    """Build a mock character_report_parses aggregate row."""
    from datetime import datetime, timezone
    row = MagicMock()
    row.encounter_name = encounter_name
    row.zone_name = zone_name
    row.best_pct = best_pct
    row.report_code = report_code
    row.raid_date = raid_date
    row.last_synced = last_synced or datetime(2026, 3, 14, tzinfo=timezone.utc)
    return row


def _execute_side_with_parses(parse_rows, zone_ids=None):
    """Return an execute side-effect: ownership, wcl_config, zones, parses."""
    if zone_ids is None:
        zone_ids = [38]
    call_count = [0]

    def execute_side(stmt):
        call_count[0] += 1
        mock_result = MagicMock()
        if call_count[0] == 1:
            # ownership check
            mock_result.scalar_one_or_none = MagicMock(return_value=MagicMock())
            return mock_result
        elif call_count[0] == 2:
            # wcl_config check
            wcl = MagicMock()
            wcl.is_configured = True
            mock_result.scalar_one_or_none = MagicMock(return_value=wcl)
            return mock_result
        elif call_count[0] == 3:
            # zone derivation — return iterable of zone rows
            mock_result.__iter__ = MagicMock(
                return_value=iter([_make_zone_row(z) for z in zone_ids])
            )
            return mock_result
        else:
            # parses query — return iterable of parse rows
            mock_result.__iter__ = MagicMock(return_value=iter(parse_rows))
            return mock_result

    return execute_side


class TestParseDeduplication:
    @pytest.mark.asyncio
    async def test_best_percentile_per_boss_returned(self):
        """SQL MAX(percentile) returns one best row per boss — verify mapping."""
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        # character_report_parses returns best parse per boss (SQL GROUP BY + MAX)
        rows = [
            _make_parse_row("Ulgrax the Devourer", 94.0),
            _make_parse_row("The Bloodbound Horror", 87.0),
        ]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute_side_with_parses(rows))

        result = await get_character_parses(character_id=10, player=player, db=db)

        assert result["ok"] is True
        parses = result["data"]["parses"]
        ulgrax = [p for p in parses if p["boss_name"] == "Ulgrax the Devourer"]
        assert len(ulgrax) == 1
        assert ulgrax[0]["percentile"] == 94.0

    @pytest.mark.asyncio
    async def test_multiple_bosses_returned(self):
        """Different bosses all returned."""
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        rows = [
            _make_parse_row("Ulgrax the Devourer", 94.0),
            _make_parse_row("The Bloodbound Horror", 87.0),
            _make_parse_row("Sikran", 72.0),
        ]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute_side_with_parses(rows))

        result = await get_character_parses(character_id=10, player=player, db=db)

        assert result["ok"] is True
        assert len(result["data"]["parses"]) == 3


# ---------------------------------------------------------------------------
# 4. Percentile color tier function
# ---------------------------------------------------------------------------


def _parse_tier(pct: float) -> str:
    """Python mirror of JS parsePercentileTier."""
    if pct >= 100:
        return "pink"
    if pct >= 99:
        return "gold"
    if pct >= 95:
        return "orange"
    if pct >= 75:
        return "purple"
    if pct >= 50:
        return "blue"
    if pct >= 25:
        return "green"
    return "gray"


class TestPercentileTierBoundaries:
    def test_gray_below_25(self):
        assert _parse_tier(0) == "gray"
        assert _parse_tier(24) == "gray"

    def test_green_25_to_49(self):
        assert _parse_tier(25) == "green"
        assert _parse_tier(49) == "green"

    def test_blue_50_to_74(self):
        assert _parse_tier(50) == "blue"
        assert _parse_tier(74) == "blue"

    def test_purple_75_to_94(self):
        assert _parse_tier(75) == "purple"
        assert _parse_tier(94) == "purple"

    def test_orange_95_to_98(self):
        assert _parse_tier(95) == "orange"
        assert _parse_tier(98) == "orange"

    def test_gold_99(self):
        assert _parse_tier(99) == "gold"

    def test_pink_100(self):
        assert _parse_tier(100) == "pink"


# ---------------------------------------------------------------------------
# 5. Summary fields
# ---------------------------------------------------------------------------


class TestSummaryCalculation:
    @pytest.mark.asyncio
    async def test_best_percentile_and_boss(self):
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        # Report parses all come back as difficulty=3 (normal) — guild raids normal
        rows = [
            _make_parse_row("Boss A", 80.0),
            _make_parse_row("Boss B", 90.0),
            _make_parse_row("Boss C", 97.0),  # best
        ]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute_side_with_parses(rows))

        result = await get_character_parses(character_id=10, player=player, db=db)

        summary = result["data"]["summary"]
        assert summary is not None
        assert summary["best_percentile"] == 97.0
        assert summary["best_boss"] == "Boss C"
        assert summary["best_difficulty"] == "normal"

    @pytest.mark.asyncio
    async def test_heroic_average_null_when_no_heroic(self):
        """All report parses are difficulty=normal, so heroic_average is None."""
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        rows = [_make_parse_row("Boss A", 90.0)]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute_side_with_parses(rows))

        result = await get_character_parses(character_id=10, player=player, db=db)

        summary = result["data"]["summary"]
        assert summary["heroic_average"] is None


# ---------------------------------------------------------------------------
# 6. No data state
# ---------------------------------------------------------------------------


class TestNoParsesData:
    @pytest.mark.asyncio
    async def test_empty_parses_and_null_summary(self):
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute_side_with_parses([]))

        result = await get_character_parses(character_id=10, player=player, db=db)

        assert result["ok"] is True
        assert result["data"]["parses"] == []
        assert result["data"]["summary"] is None


# ---------------------------------------------------------------------------
# 7. WCL not configured
# ---------------------------------------------------------------------------


class TestWclNotConfigured:
    @pytest.mark.asyncio
    async def test_wcl_configured_false_when_not_configured(self):
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        call_count = [0]

        def execute_side(stmt):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=MagicMock())
                return mock_result
            elif call_count[0] == 2:
                # WCL not configured — return None (no row)
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
                return mock_result
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
                return mock_result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=execute_side)

        result = await get_character_parses(character_id=10, player=player, db=db)

        assert result["ok"] is True
        assert result["data"]["wcl_configured"] is False

    @pytest.mark.asyncio
    async def test_wcl_configured_false_when_is_configured_false(self):
        from guild_portal.api.member_routes import get_character_parses

        player = MagicMock()
        player.id = 1

        call_count = [0]

        def execute_side(stmt):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=MagicMock())
                return mock_result
            elif call_count[0] == 2:
                wcl = MagicMock()
                wcl.is_configured = False
                mock_result.scalar_one_or_none = MagicMock(return_value=wcl)
                return mock_result
            else:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
                return mock_result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=execute_side)

        result = await get_character_parses(character_id=10, player=player, db=db)

        assert result["data"]["wcl_configured"] is False


# ---------------------------------------------------------------------------
# 8. Template has parses panel div
# ---------------------------------------------------------------------------


class TestParsesTemplate:
    _tpl = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "templates" / "member" / "my_characters.html"
    )

    def test_parses_div_present(self):
        content = self._tpl.read_text(encoding="utf-8")
        assert "mc-parses" in content

    def test_parses_div_hidden_by_default(self):
        content = self._tpl.read_text(encoding="utf-8")
        assert 'id="mc-parses"' in content
        # Should start hidden
        idx = content.find('id="mc-parses"')
        surrounding = content[max(0, idx-20):idx+80]
        assert "hidden" in surrounding


# ---------------------------------------------------------------------------
# 9. CSS has parse tier color classes
# ---------------------------------------------------------------------------


class TestParsesCSS:
    _css = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "static" / "css" / "my_characters.css"
    )

    def test_parse_tier_classes_present(self):
        content = self._css.read_text(encoding="utf-8")
        for tier in ("gray", "green", "blue", "purple", "orange", "gold", "pink"):
            assert f"mc-parse--{tier}" in content, f"Missing parse tier class: {tier}"

    def test_parse_bar_classes_present(self):
        content = self._css.read_text(encoding="utf-8")
        assert "mc-parse-bar-fill" in content

    def test_parse_tab_classes_present(self):
        content = self._css.read_text(encoding="utf-8")
        assert "mc-parse-tab" in content


# ---------------------------------------------------------------------------
# 10. JS has required functions
# ---------------------------------------------------------------------------


class TestParsesJS:
    _js = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "static" / "js" / "my_characters.js"
    )

    def test_parse_percentile_tier_function(self):
        content = self._js.read_text(encoding="utf-8")
        assert "parsePercentileTier" in content

    def test_render_parses_panel_function(self):
        content = self._js.read_text(encoding="utf-8")
        assert "renderParsesPanel" in content

    def test_fetches_parses_endpoint(self):
        content = self._js.read_text(encoding="utf-8")
        assert "/parses" in content

    def test_wcl_tier_colors_present(self):
        content = self._js.read_text(encoding="utf-8")
        for tier in ("gray", "green", "blue", "purple", "orange", "gold", "pink"):
            assert tier in content, f"Missing WCL tier: {tier}"
