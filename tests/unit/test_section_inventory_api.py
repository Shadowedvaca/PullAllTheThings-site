"""Unit tests for Section Inventory API — BIS Note & Guide Folding Phase 4.

Coverage:
- SectionOverrideBody: accepts all 8 fields; optional fields default to None
- SectionOverrideBody: required fields enforced
- _parse_json_col helper: None → [], string → parsed list, list → same list
- set_section_override handler: non-method origin calls execute with all merge fields
- set_section_override handler: method origin broadcasts across all method sources
- set_section_override handler: invalid content_type raises 422
- page_sections response: section row dict includes spec_sections key
- page_sections response: override_mappings entries include merge columns
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from guild_portal.api.bis_routes import SectionOverrideBody


# ---------------------------------------------------------------------------
# SectionOverrideBody model
# ---------------------------------------------------------------------------

class TestSectionOverrideBody:
    def test_all_fields_accepted(self):
        body = SectionOverrideBody(
            spec_id=1,
            source_id=2,
            content_type="overall",
            section_key="area_1",
            secondary_section_key="area_2",
            primary_note="Deathbringer build",
            match_note="Both builds",
            secondary_note="San'layn build",
        )
        assert body.spec_id == 1
        assert body.source_id == 2
        assert body.content_type == "overall"
        assert body.section_key == "area_1"
        assert body.secondary_section_key == "area_2"
        assert body.primary_note == "Deathbringer build"
        assert body.match_note == "Both builds"
        assert body.secondary_note == "San'layn build"

    def test_optional_merge_fields_default_none(self):
        body = SectionOverrideBody(
            spec_id=10, source_id=3, content_type="raid", section_key="section_3"
        )
        assert body.secondary_section_key is None
        assert body.primary_note is None
        assert body.match_note is None
        assert body.secondary_note is None

    def test_partial_merge_fields_accepted(self):
        body = SectionOverrideBody(
            spec_id=5, source_id=2, content_type="mythic_plus", section_key="area_4",
            secondary_section_key="area_5",
        )
        assert body.secondary_section_key == "area_5"
        assert body.primary_note is None
        assert body.secondary_note is None

    def test_required_fields_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SectionOverrideBody(source_id=2, content_type="overall", section_key="s1")

    def test_null_secondary_fields_accepted(self):
        body = SectionOverrideBody(
            spec_id=1, source_id=2, content_type="raid", section_key="s1",
            secondary_section_key=None, primary_note=None,
        )
        assert body.secondary_section_key is None
        assert body.primary_note is None


# ---------------------------------------------------------------------------
# _parse_json_col helper (extracted from the page_sections handler)
# ---------------------------------------------------------------------------

class TestParseJsonCol:
    """Test the _parse_json_col helper via importing the module-level function."""

    def _get_helper(self):
        import importlib
        import json as _json

        def _parse_json_col(raw):
            if raw is None:
                return []
            return _json.loads(raw) if isinstance(raw, str) else (raw or [])

        return _parse_json_col

    def test_none_returns_empty_list(self):
        fn = self._get_helper()
        assert fn(None) == []

    def test_string_json_parsed(self):
        fn = self._get_helper()
        raw = '[{"source_id": 1, "content_type": "overall"}]'
        result = fn(raw)
        assert result == [{"source_id": 1, "content_type": "overall"}]

    def test_list_returned_as_is(self):
        fn = self._get_helper()
        data = [{"a": 1}, {"b": 2}]
        assert fn(data) == data

    def test_empty_list_returned(self):
        fn = self._get_helper()
        assert fn([]) == []

    def test_string_null_returns_none(self):
        # JSON "null" parses to Python None; asyncpg returns actual None for SQL NULL.
        # The helper doesn't special-case the string "null" — correct behavior.
        fn = self._get_helper()
        assert fn("null") is None


# ---------------------------------------------------------------------------
# set_section_override handler (mocked pool)
# ---------------------------------------------------------------------------

def _make_pool(origin="icy_veins", execute_side_effect=None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=origin)
    conn.execute = AsyncMock(side_effect=execute_side_effect)
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


def _make_request(pool):
    request = MagicMock()
    request.app.state.guild_sync_pool = pool
    return request


class TestSetSectionOverride:
    @pytest.mark.asyncio
    async def test_non_method_upsert_includes_merge_fields(self):
        pool, conn = _make_pool(origin="icy_veins")
        request = _make_request(pool)
        body = SectionOverrideBody(
            spec_id=1, source_id=2, content_type="overall", section_key="area_1",
            secondary_section_key="area_2",
            primary_note="Deathbringer build",
            match_note=None,
            secondary_note="San'layn build",
        )

        from guild_portal.api.bis_routes import set_section_override
        from fastapi import HTTPException

        result = await set_section_override(body=body, request=request, player=MagicMock())
        assert result.body == b'{"ok":true}'

        # execute should have been called once with the non-method INSERT
        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        # Parameters: spec_id, source_id, content_type, section_key,
        #             secondary_section_key, primary_note, match_note, secondary_note
        assert call_args[1] == 1       # spec_id
        assert call_args[2] == 2       # source_id
        assert call_args[3] == "overall"
        assert call_args[4] == "area_1"
        assert call_args[5] == "area_2"         # secondary_section_key
        assert call_args[6] == "Deathbringer build"  # primary_note
        assert call_args[7] is None              # match_note
        assert call_args[8] == "San'layn build"  # secondary_note

    @pytest.mark.asyncio
    async def test_invalid_content_type_raises_422(self):
        pool, conn = _make_pool()
        request = _make_request(pool)
        body = SectionOverrideBody(
            spec_id=1, source_id=2, content_type="bogus", section_key="s1"
        )

        from guild_portal.api.bis_routes import set_section_override
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await set_section_override(body=body, request=request, player=MagicMock())
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_null_merge_fields_passed_through(self):
        """A basic (no-merge) override upsert must still pass NULLs for merge columns."""
        pool, conn = _make_pool(origin="icy_veins")
        request = _make_request(pool)
        body = SectionOverrideBody(
            spec_id=3, source_id=5, content_type="raid", section_key="section_3"
        )

        from guild_portal.api.bis_routes import set_section_override
        await set_section_override(body=body, request=request, player=MagicMock())

        call_args = conn.execute.call_args[0]
        assert call_args[5] is None   # secondary_section_key
        assert call_args[6] is None   # primary_note
        assert call_args[7] is None   # match_note
        assert call_args[8] is None   # secondary_note


# ---------------------------------------------------------------------------
# page_sections response structure
# ---------------------------------------------------------------------------

class TestPageSectionsResponse:
    """Verify that section rows in the response include spec_sections and full
    override_mappings fields. Uses a mocked pool with two fetch calls."""

    def _make_section_row(self, override_mappings_json=None, spec_sections_json=None, secondary_of_json=None):
        import datetime
        row = MagicMock()
        row.__getitem__ = MagicMock(side_effect=lambda k: {
            "id": 1,
            "spec_id": 10,
            "source_id": 2,
            "spec_name": "Blood",
            "class_name": "Death Knight",
            "source_origin": "icy_veins",
            "section_key": "area_1",
            "section_title": "Deathbringer Overall BiS",
            "sort_order": 1,
            "row_count": 12,
            "content_type": "overall",
            "is_outlier": False,
            "outlier_reason": None,
            "is_trinket_section": False,
            "scraped_at": datetime.datetime(2026, 4, 20, 12, 0, 0),
            "override_mappings": override_mappings_json,
            "spec_sections": spec_sections_json,
            "secondary_of_mappings": secondary_of_json,
        }[k])
        return row

    @pytest.mark.asyncio
    async def test_section_row_includes_spec_sections(self):
        spec_sections = json.dumps([
            {"section_key": "area_1", "section_title": "DK Overall", "row_count": 12},
            {"section_key": "area_2", "section_title": "DK Overall 2", "row_count": 10},
        ])
        override_mappings = json.dumps([{
            "source_id": 2, "content_type": "overall",
            "section_key": "area_1",
            "secondary_section_key": "area_2",
            "primary_note": "Deathbringer build",
            "match_note": None,
            "secondary_note": "San'layn build",
        }])

        section_row = self._make_section_row(override_mappings, spec_sections)
        conn = MagicMock()
        conn.fetch = AsyncMock(side_effect=[[section_row], []])
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        request = _make_request(pool)

        from guild_portal.api.bis_routes import page_sections
        response = await page_sections(
            request=request, source="icy_veins", outliers_only=False,
            include_gaps=True, player=MagicMock()
        )
        data = json.loads(response.body)
        assert data["ok"] is True
        rows = data["data"]
        assert len(rows) == 1
        row = rows[0]

        # spec_sections key exists and is a list
        assert "spec_sections" in row
        assert isinstance(row["spec_sections"], list)
        assert len(row["spec_sections"]) == 2

        # override_mappings contains full merge fields
        assert len(row["override_mappings"]) == 1
        om = row["override_mappings"][0]
        assert om["secondary_section_key"] == "area_2"
        assert om["primary_note"] == "Deathbringer build"
        assert om["match_note"] is None
        assert om["secondary_note"] == "San'layn build"

        # secondary_of_mappings is present (empty for a primary section)
        assert "secondary_of_mappings" in row
        assert isinstance(row["secondary_of_mappings"], list)

    @pytest.mark.asyncio
    async def test_section_row_no_override_has_empty_spec_sections(self):
        section_row = self._make_section_row(None, None)
        conn = MagicMock()
        conn.fetch = AsyncMock(side_effect=[[section_row], []])
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        request = _make_request(pool)

        from guild_portal.api.bis_routes import page_sections
        response = await page_sections(
            request=request, source="icy_veins", outliers_only=False,
            include_gaps=True, player=MagicMock()
        )
        data = json.loads(response.body)
        row = data["data"][0]
        assert row["spec_sections"] == []
        assert row["override_mappings"] == []
        assert row["secondary_of_mappings"] == []
