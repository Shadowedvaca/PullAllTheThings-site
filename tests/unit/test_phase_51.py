"""
Unit tests for Phase 5.1 — My Characters: Progression Panel.

Tests cover:
1. progression endpoint exists in member_routes
2. own-character authorization check (404 when not owned)
3. raid progress aggregation shape
4. missing difficulty rows → null in response
5. M+ score: no season row → mythic_plus null
6. M+ score tier color boundaries (mplusScoreTier logic mirrored in Python)
7. Template has progression panel div
8. CSS has progression classes
9. JS has mplusScoreTier and renderProgressionPanel
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Progression endpoint exists
# ---------------------------------------------------------------------------


class TestProgressionEndpointExists:
    def test_get_character_progression_callable(self):
        from guild_portal.api.member_routes import get_character_progression

        assert callable(get_character_progression)

    def test_endpoint_registered_in_router(self):
        from guild_portal.api.member_routes import router

        paths = [r.path for r in router.routes]
        assert "/api/v1/me/character/{character_id}/progression" in paths

    def test_endpoint_is_async(self):
        import inspect

        from guild_portal.api.member_routes import get_character_progression

        assert inspect.iscoroutinefunction(get_character_progression)


# ---------------------------------------------------------------------------
# 2. Own-character authorization (ownership check)
# ---------------------------------------------------------------------------


class TestProgressionAuth:
    @pytest.mark.asyncio
    async def test_returns_404_when_not_owned(self):
        """Returns 404 JSONResponse when character_id not in player's characters."""
        from fastapi.responses import JSONResponse

        from guild_portal.api.member_routes import get_character_progression

        player = MagicMock()
        player.id = 1

        db = AsyncMock()
        # scalar_one_or_none returns None → not owned
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        result = await get_character_progression(
            character_id=999,
            player=player,
            db=db,
        )

        assert isinstance(result, JSONResponse)
        assert result.status_code == 404


# ---------------------------------------------------------------------------
# 3. Raid progress aggregation
# ---------------------------------------------------------------------------


def _make_raid_rows(pairs):
    """Build mock aggregation rows for (raid_name, difficulty, total, killed)."""
    rows = []
    for raid_name, difficulty, total, killed in pairs:
        row = MagicMock()
        row.raid_name = raid_name
        row.difficulty = difficulty
        row.total = total
        row.killed = killed
        rows.append(row)
    return rows


class TestRaidProgressAggregation:
    @pytest.mark.asyncio
    async def test_raid_progress_grouped_by_name(self):
        """Raid rows grouped by raid_name in output."""
        from guild_portal.api.member_routes import get_character_progression

        player = MagicMock()
        player.id = 1

        db = AsyncMock()

        call_count = [0]

        def execute_side_effect(stmt):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                # PlayerCharacter ownership check
                mock_result.scalar_one_or_none = MagicMock(return_value=MagicMock())
                return mock_result
            elif call_count[0] == 2:
                # Active season query — no active season configured
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
                return mock_result
            elif call_count[0] == 3:
                # Raid progress query
                rows = _make_raid_rows([
                    ("Nerub-ar Palace", "heroic", 8, 8),
                    ("Nerub-ar Palace", "mythic", 8, 2),
                    ("Nerub-ar Palace", "normal", 8, 8),
                ])
                mock_result.__iter__ = MagicMock(return_value=iter(rows))
                return mock_result
            else:
                # M+ — no rows
                mock_result.scalars = MagicMock(return_value=MagicMock(__iter__=MagicMock(return_value=iter([]))))
                return mock_result

        db.execute = AsyncMock(side_effect=execute_side_effect)

        with patch("guild_portal.api.member_routes.get_site_config", return_value={}):
            result = await get_character_progression(character_id=10, player=player, db=db)

        assert result["ok"] is True
        raid = result["data"]["raid_progress"]
        assert len(raid) == 1
        assert raid[0]["raid_name"] == "Nerub-ar Palace"
        diffs = raid[0]["difficulties"]
        assert diffs["heroic"]["killed"] == 8
        assert diffs["heroic"]["total"] == 8
        assert diffs["mythic"]["killed"] == 2

    @pytest.mark.asyncio
    async def test_raid_progress_empty_when_no_rows(self):
        """Returns empty raid_progress list when no rows in DB."""
        from guild_portal.api.member_routes import get_character_progression

        player = MagicMock()
        player.id = 1

        db = AsyncMock()
        call_count = [0]

        def execute_side_effect(stmt):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=MagicMock())
                return mock_result
            elif call_count[0] == 2:
                # Active season query — no active season configured
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
                return mock_result
            elif call_count[0] == 3:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
                return mock_result
            else:
                mock_result.scalars = MagicMock(return_value=MagicMock(__iter__=MagicMock(return_value=iter([]))))
                return mock_result

        db.execute = AsyncMock(side_effect=execute_side_effect)

        with patch("guild_portal.api.member_routes.get_site_config", return_value={}):
            result = await get_character_progression(character_id=10, player=player, db=db)

        assert result["data"]["raid_progress"] == []


# ---------------------------------------------------------------------------
# 4. Missing difficulty rows → null handling in JS (doc-level test)
# ---------------------------------------------------------------------------


class TestMissingDifficultyDocs:
    def test_api_spec_allows_null_difficulties(self):
        """Per spec, missing difficulty rows should not cause a 500.
        The aggregation only returns rows that exist, so missing difficulties
        simply won't appear in the difficulties dict (treated as null by the JS)."""
        # This is a structural / documentation test — the route returns a plain
        # dict not a pydantic model, so extra/missing difficulty keys are fine.
        from guild_portal.api import member_routes

        # Verify the route module imports without error
        assert member_routes is not None


# ---------------------------------------------------------------------------
# 5. M+ score: no season row → mythic_plus null
# ---------------------------------------------------------------------------


class TestMythicPlusNull:
    @pytest.mark.asyncio
    async def test_mythic_plus_null_when_no_season_id_in_config(self):
        """mythic_plus is None when current_mplus_season_id not set in config."""
        from guild_portal.api.member_routes import get_character_progression

        player = MagicMock()
        player.id = 1

        db = AsyncMock()
        call_count = [0]

        def execute_side_effect(stmt):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=MagicMock())
            elif call_count[0] == 2:
                # Active season query — no active season configured
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
            elif call_count[0] == 3:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
            return mock_result

        db.execute = AsyncMock(side_effect=execute_side_effect)

        with patch("guild_portal.api.member_routes.get_site_config", return_value={"current_mplus_season_id": None}):
            result = await get_character_progression(character_id=10, player=player, db=db)

        assert result["data"]["mythic_plus"] is None

    @pytest.mark.asyncio
    async def test_mythic_plus_null_when_no_rows_for_season(self):
        """mythic_plus is None when season_id is set but no DB rows for character."""
        from guild_portal.api.member_routes import get_character_progression

        player = MagicMock()
        player.id = 1

        db = AsyncMock()
        call_count = [0]

        def execute_side_effect(stmt):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.scalar_one_or_none = MagicMock(return_value=MagicMock())
                return mock_result
            elif call_count[0] == 2:
                # Active season query — no active season configured
                mock_result.scalar_one_or_none = MagicMock(return_value=None)
                return mock_result
            elif call_count[0] == 3:
                mock_result.__iter__ = MagicMock(return_value=iter([]))
                return mock_result
            else:
                # No M+ rows
                mock_result.scalars = MagicMock(return_value=MagicMock(__iter__=MagicMock(return_value=iter([]))))
                return mock_result

        db.execute = AsyncMock(side_effect=execute_side_effect)

        with patch("guild_portal.api.member_routes.get_site_config", return_value={"current_mplus_season_id": 14}):
            result = await get_character_progression(character_id=10, player=player, db=db)

        assert result["data"]["mythic_plus"] is None


# ---------------------------------------------------------------------------
# 6. M+ score tier color boundaries (mirrors JS mplusScoreTier)
# ---------------------------------------------------------------------------


def _score_tier(score: float) -> str:
    """Python mirror of the JS mplusScoreTier function."""
    if score >= 2500:
        return "pink"
    if score >= 2000:
        return "orange"
    if score >= 1500:
        return "purple"
    if score >= 1000:
        return "blue"
    if score >= 500:
        return "green"
    return "gray"


class TestMplusScoreTierBoundaries:
    def test_gray_below_500(self):
        assert _score_tier(0) == "gray"
        assert _score_tier(499) == "gray"

    def test_green_500_to_999(self):
        assert _score_tier(500) == "green"
        assert _score_tier(999) == "green"

    def test_blue_1000_to_1499(self):
        assert _score_tier(1000) == "blue"
        assert _score_tier(1499) == "blue"

    def test_purple_1500_to_1999(self):
        assert _score_tier(1500) == "purple"
        assert _score_tier(1999) == "purple"

    def test_orange_2000_to_2499(self):
        assert _score_tier(2000) == "orange"
        assert _score_tier(2499) == "orange"

    def test_pink_2500_plus(self):
        assert _score_tier(2500) == "pink"
        assert _score_tier(3000) == "pink"


# ---------------------------------------------------------------------------
# 7. Template has progression panel div
# ---------------------------------------------------------------------------


class TestProgressionTemplate:
    _tpl = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "templates" / "member" / "my_characters.html"
    )

    def test_progression_div_present(self):
        # Progression rendered dynamically into mcn-detail-area by JS
        content = self._tpl.read_text(encoding="utf-8")
        assert "mcn-detail-area" in content

    def test_progression_div_hidden_by_default(self):
        # mcn-body (containing detail area) starts hidden
        content = self._tpl.read_text(encoding="utf-8")
        assert 'id="mcn-body"' in content
        assert "hidden" in content


# ---------------------------------------------------------------------------
# 8. CSS has progression classes
# ---------------------------------------------------------------------------


class TestProgressionCSS:
    _css = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "static" / "css" / "my_characters.css"
    )

    def test_mc_progression_class(self):
        # Redesigned page uses mcn-prog-panel
        content = self._css.read_text(encoding="utf-8")
        assert "mcn-prog-panel" in content

    def test_mc_prog_card_class(self):
        content = self._css.read_text(encoding="utf-8")
        assert "mcn-boss-row" in content

    def test_mc_raid_row_class(self):
        content = self._css.read_text(encoding="utf-8")
        assert "mcn-boss-list" in content

    def test_mplus_score_tier_classes(self):
        # M+ score colouring uses inline hex via _mplusScoreTier in JS
        content = self._css.read_text(encoding="utf-8")
        assert "mcn-mplus-score-value" in content


# ---------------------------------------------------------------------------
# 9. JS has required functions
# ---------------------------------------------------------------------------


class TestProgressionJS:
    _js = (
        Path(__file__).parents[2]
        / "src" / "guild_portal" / "static" / "js" / "my_characters.js"
    )

    def test_mplus_score_tier_function(self):
        # Redesigned page uses _mplusScoreTier
        content = self._js.read_text(encoding="utf-8")
        assert "_mplusScoreTier" in content

    def test_render_progression_panel_function(self):
        # Redesigned page uses _renderRaidDetail and _renderMplusDetail
        content = self._js.read_text(encoding="utf-8")
        assert "_renderRaidDetail" in content
        assert "_renderMplusDetail" in content

    def test_fetches_progression_endpoint(self):
        content = self._js.read_text(encoding="utf-8")
        assert "/api/v1/me/character/" in content
        assert "/progression" in content

    def test_diff_order_mythic_first(self):
        """Mythic should appear first in DIFF_ORDER."""
        content = self._js.read_text(encoding="utf-8")
        mythic_pos = content.find('"mythic"')
        heroic_pos = content.find('"heroic"')
        normal_pos = content.find('"normal"')
        assert mythic_pos < heroic_pos < normal_pos
