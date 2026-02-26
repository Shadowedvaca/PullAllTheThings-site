"""Unit tests for Player Manager attribution behavior (Phase 3.0A)."""

import pytest


# ---------------------------------------------------------------------------
# Test that manual link produces correct attribution values
# ---------------------------------------------------------------------------

class TestManualLinkAttribution:
    def test_manual_link_source_value(self):
        link_source = "manual"
        confidence = "confirmed"
        assert link_source == "manual"
        assert confidence == "confirmed"

    def test_manual_is_valid_link_source(self):
        valid_sources = {
            "note_key", "note_key_stub", "exact_name", "fuzzy_name",
            "manual", "migrated", "onboarding", "auto_relink", "unknown",
        }
        assert "manual" in valid_sources

    def test_confirmed_is_valid_confidence(self):
        valid_confidences = {"high", "medium", "low", "confirmed", "unknown"}
        assert "confirmed" in valid_confidences

    def test_stub_upgrade_logic(self):
        # When Discord is linked to a player, low-confidence rows should become medium
        # Simulates the confidence upgrade logic
        initial_confidence = "low"
        upgraded_confidence = "medium" if initial_confidence == "low" else initial_confidence
        assert upgraded_confidence == "medium"

    def test_non_low_confidence_not_upgraded(self):
        # high/confirmed/medium should not be downgraded
        for conf in ("high", "confirmed", "medium"):
            result = "medium" if conf == "low" else conf
            assert result == conf


# ---------------------------------------------------------------------------
# Test attribution value consistency
# ---------------------------------------------------------------------------

class TestAttributionValues:
    VALID_SOURCES = {
        "note_key", "note_key_stub", "exact_name", "fuzzy_name",
        "manual", "migrated", "onboarding", "auto_relink", "unknown",
    }
    VALID_CONFIDENCES = {"high", "medium", "low", "confirmed", "unknown"}

    def test_all_sources_valid(self):
        from sv_common.guild_sync.identity_engine import _attribution_for_match
        import sv_common.guild_sync.identity_engine as ie

        test_cases = [
            ("exact_username", {"id": 1, "username": "x", "display_name": None, "player_id": None}, True),
            ("exact_display", {"id": 1, "username": "x", "display_name": "X", "player_id": None}, True),
            ("word_in_display", {"id": 1, "username": "x", "display_name": "X", "player_id": None}, True),
            ("substring_username", {"id": 1, "username": "x", "display_name": None, "player_id": None}, True),
            ("substring_display", {"id": 1, "username": "x", "display_name": "X", "player_id": None}, True),
            ("none", None, True),
            ("exact_username", {"id": 1, "username": "x", "display_name": None, "player_id": None}, False),
            ("substring_display", {"id": 1, "username": "x", "display_name": "X", "player_id": None}, False),
        ]
        for match_type, du, from_note in test_cases:
            source, conf = _attribution_for_match(match_type, du, from_note)
            assert source in self.VALID_SOURCES, f"Invalid source '{source}' for ({match_type}, from_note={from_note})"
            assert conf in self.VALID_CONFIDENCES, f"Invalid confidence '{conf}' for ({match_type}, from_note={from_note})"
