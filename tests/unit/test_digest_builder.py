"""
Unit tests for Phase 6.3 — _build_digest_embeds() helper.

Tests:
1. Single type — header + one type embed
2. Multiple types — header + one embed per type
3. Caps at 15 per type, shows "...and N more"
4. occurrence_count > 1 shows N× in line
5. identifier shown in line
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_error(
    issue_type="bnet_token_expired",
    severity="warning",
    summary="Token expired",
    identifier=None,
    occurrence_count=1,
    first_occurred_at=None,
):
    return {
        "issue_type": issue_type,
        "severity": severity,
        "summary": summary,
        "identifier": identifier,
        "occurrence_count": occurrence_count,
        "first_occurred_at": first_occurred_at or datetime(2026, 3, 10, 8, 0, 0, tzinfo=timezone.utc),
    }


# ---------------------------------------------------------------------------
# 1. Single type
# ---------------------------------------------------------------------------


class TestDigestSingleType:
    def test_single_type_returns_header_plus_one_embed(self):
        from sv_common.guild_sync.scheduler import _build_digest_embeds

        errors = [
            _make_error(issue_type="bnet_token_expired"),
            _make_error(issue_type="bnet_token_expired", identifier="sevin#1865"),
        ]
        embeds = _build_digest_embeds(errors)

        # Header + 1 type embed
        assert len(embeds) == 2
        assert "Weekly Error Digest" in embeds[0].title
        assert "2 open issues" in embeds[0].description
        assert "(2)" in embeds[1].title


# ---------------------------------------------------------------------------
# 2. Multiple types
# ---------------------------------------------------------------------------


class TestDigestMultipleTypes:
    def test_multiple_types_returns_header_plus_one_per_type(self):
        from sv_common.guild_sync.scheduler import _build_digest_embeds

        errors = [
            _make_error(issue_type="bnet_token_expired"),
            _make_error(issue_type="bnet_token_expired"),
            _make_error(issue_type="wcl_sync_failed"),
        ]
        embeds = _build_digest_embeds(errors)

        # Header + 2 type embeds
        assert len(embeds) == 3
        assert "3 open issues" in embeds[0].description
        assert "2 types" in embeds[0].description


# ---------------------------------------------------------------------------
# 3. Caps at 15 per type
# ---------------------------------------------------------------------------


class TestDigestCapAt15:
    def test_caps_at_15_and_shows_more(self):
        from sv_common.guild_sync.scheduler import _build_digest_embeds

        errors = [_make_error(issue_type="bnet_token_expired", identifier=f"user{i}") for i in range(20)]
        embeds = _build_digest_embeds(errors)

        # Should have header + 1 type embed
        assert len(embeds) == 2
        assert "...and 5 more" in embeds[1].description


# ---------------------------------------------------------------------------
# 4. occurrence_count shown
# ---------------------------------------------------------------------------


class TestDigestOccurrenceCount:
    def test_occurrence_count_shown_in_line(self):
        from sv_common.guild_sync.scheduler import _build_digest_embeds

        errors = [_make_error(occurrence_count=7)]
        embeds = _build_digest_embeds(errors)

        assert "7×" in embeds[1].description

    def test_occurrence_count_1_not_shown(self):
        from sv_common.guild_sync.scheduler import _build_digest_embeds

        errors = [_make_error(occurrence_count=1)]
        embeds = _build_digest_embeds(errors)

        assert "1×" not in embeds[1].description


# ---------------------------------------------------------------------------
# 5. Identifier shown
# ---------------------------------------------------------------------------


class TestDigestIdentifier:
    def test_identifier_shown_in_line(self):
        from sv_common.guild_sync.scheduler import _build_digest_embeds

        errors = [_make_error(identifier="sevin1979#1865")]
        embeds = _build_digest_embeds(errors)

        assert "sevin1979#1865" in embeds[1].description
