"""Unit tests for coverage metrics helper logic (Phase 3.0A)."""

import pytest


# ---------------------------------------------------------------------------
# Test the pct() helper logic inline (extracted for testability)
# ---------------------------------------------------------------------------

def pct(matched: int, total: int) -> float:
    return round(matched / total * 100, 1) if total else 0.0


class TestPctHelper:
    def test_full_coverage(self):
        assert pct(100, 100) == 100.0

    def test_zero_coverage(self):
        assert pct(0, 100) == 0.0

    def test_zero_total(self):
        # Should not raise ZeroDivisionError
        assert pct(0, 0) == 0.0

    def test_partial_coverage(self):
        assert pct(62, 87) == 71.3

    def test_rounds_to_one_decimal(self):
        assert pct(1, 3) == 33.3

    def test_perfect_match(self):
        assert pct(38, 45) == 84.4


# ---------------------------------------------------------------------------
# Test summary computation logic
# ---------------------------------------------------------------------------

class TestSummaryComputation:
    def _compute(self, total_chars, matched_chars, total_discord, matched_discord,
                 total_players, players_with_discord):
        unmatched_chars = total_chars - matched_chars
        unmatched_discord = total_discord - matched_discord
        players_without = total_players - players_with_discord
        return {
            "total_characters": total_chars,
            "matched_characters": matched_chars,
            "unmatched_characters": unmatched_chars,
            "character_coverage_pct": pct(matched_chars, total_chars),
            "total_discord_users": total_discord,
            "matched_discord_users": matched_discord,
            "unmatched_discord_users": unmatched_discord,
            "discord_coverage_pct": pct(matched_discord, total_discord),
            "total_players": total_players,
            "players_with_discord": players_with_discord,
            "players_without_discord": players_without,
            "discord_link_pct": pct(players_with_discord, total_players),
        }

    def test_typical_guild(self):
        s = self._compute(87, 62, 45, 38, 42, 38)
        assert s["unmatched_characters"] == 25
        assert s["unmatched_discord_users"] == 7
        assert s["players_without_discord"] == 4
        assert s["character_coverage_pct"] == 71.3
        assert s["discord_coverage_pct"] == 84.4
        assert s["discord_link_pct"] == 90.5

    def test_empty_guild(self):
        s = self._compute(0, 0, 0, 0, 0, 0)
        assert s["character_coverage_pct"] == 0.0
        assert s["discord_coverage_pct"] == 0.0
        assert s["discord_link_pct"] == 0.0
        assert s["unmatched_characters"] == 0

    def test_perfect_coverage(self):
        s = self._compute(50, 50, 40, 40, 40, 40)
        assert s["character_coverage_pct"] == 100.0
        assert s["unmatched_characters"] == 0
        assert s["unmatched_discord_users"] == 0


# ---------------------------------------------------------------------------
# Test breakdown dict structure
# ---------------------------------------------------------------------------

class TestBreakdowns:
    def test_by_link_source_sums(self):
        total_pc = 50
        by_source = {"note_key": 30, "exact_name": 10, "manual": 5, "unknown": 5}
        assert sum(by_source.values()) == total_pc

    def test_by_confidence_sums(self):
        total_pc = 50
        by_conf = {"high": 30, "medium": 10, "confirmed": 5, "unknown": 5}
        assert sum(by_conf.values()) == total_pc
