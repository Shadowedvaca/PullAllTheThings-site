"""Unit tests for the iterative matching rule runner (Phase 3.0B)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sv_common.guild_sync.matching_rules.base import MatchingContext, RuleResult


# ---------------------------------------------------------------------------
# RuleResult — pure unit tests (no DB)
# ---------------------------------------------------------------------------

class TestRuleResult:
    def test_changed_anything_false_when_all_zero(self):
        r = RuleResult(rule_name="test")
        assert r.changed_anything is False

    def test_changed_anything_true_on_players_created(self):
        r = RuleResult(rule_name="test", players_created=1)
        assert r.changed_anything is True

    def test_changed_anything_true_on_chars_linked(self):
        r = RuleResult(rule_name="test", chars_linked=3)
        assert r.changed_anything is True

    def test_changed_anything_true_on_discord_linked(self):
        r = RuleResult(rule_name="test", discord_linked=2)
        assert r.changed_anything is True

    def test_changed_anything_false_stubs_only(self):
        # Creating stubs doesn't count as "changed" for convergence purposes
        r = RuleResult(rule_name="test", stubs_created=5)
        assert r.changed_anything is False

    def test_changed_anything_false_skipped_only(self):
        r = RuleResult(rule_name="test", skipped=10)
        assert r.changed_anything is False

    def test_details_defaults_to_empty_list(self):
        r = RuleResult(rule_name="test")
        assert r.details == []


# ---------------------------------------------------------------------------
# Rule runner convergence logic
# ---------------------------------------------------------------------------

def _make_no_change_rule(name="noop", order=10):
    """A mock rule that always returns no changes."""
    rule = MagicMock()
    rule.name = name
    rule.order = order
    rule.run = AsyncMock(return_value=RuleResult(rule_name=name))
    return rule


def _make_one_change_rule(name="change", order=10):
    """A mock rule that returns a change on first call, then nothing."""
    results = [
        RuleResult(rule_name=name, chars_linked=1),  # first call
        RuleResult(rule_name=name),                  # subsequent calls
    ]
    call_count = {"n": 0}

    async def run(conn, context):
        idx = min(call_count["n"], len(results) - 1)
        call_count["n"] += 1
        return results[idx]

    rule = MagicMock()
    rule.name = name
    rule.order = order
    rule.run = run
    return rule


def _empty_context():
    return MatchingContext(
        unlinked_chars=[],
        all_discord=[],
        discord_player_cache={},
        note_groups={},
        no_note_chars=[],
    )


class TestRunnerConvergence:
    """Test the iterative loop logic via a patched runner."""

    @pytest.fixture
    def mock_pool(self):
        pool = MagicMock()
        conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return pool

    @pytest.mark.asyncio
    async def test_immediate_convergence_no_changes(self, mock_pool):
        """Single pass with no changes → converged after 1 pass."""
        noop = _make_no_change_rule()
        ctx = _empty_context()

        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[noop]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            from sv_common.guild_sync.matching_rules.runner import run_matching_rules
            result = await run_matching_rules(mock_pool)

        assert result["passes"] == 1
        assert result["converged"] is True
        assert result["totals"]["players_created"] == 0
        assert result["totals"]["chars_linked"] == 0

    @pytest.mark.asyncio
    async def test_two_passes_when_first_has_changes(self, mock_pool):
        """Rule produces change in pass 1 → pass 2 runs and finds nothing new."""
        changer = _make_one_change_rule()
        ctx = _empty_context()

        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[changer]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            from sv_common.guild_sync.matching_rules.runner import run_matching_rules
            result = await run_matching_rules(mock_pool)

        assert result["passes"] == 2
        assert result["converged"] is True
        assert result["totals"]["chars_linked"] == 1  # only the first pass found anything

    @pytest.mark.asyncio
    async def test_max_passes_respected(self, mock_pool):
        """Rule that always changes something stops at max_passes."""
        always_change = MagicMock()
        always_change.name = "always"
        always_change.order = 10
        always_change.run = AsyncMock(
            return_value=RuleResult(rule_name="always", chars_linked=1)
        )
        ctx = _empty_context()

        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[always_change]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            from sv_common.guild_sync.matching_rules.runner import run_matching_rules
            result = await run_matching_rules(mock_pool, max_passes=3)

        assert result["passes"] == 3
        assert result["converged"] is False

    @pytest.mark.asyncio
    async def test_results_list_has_entry_per_rule_per_pass(self, mock_pool):
        """results list has one entry per rule per pass."""
        rule_a = _make_one_change_rule("rule_a", order=10)
        rule_b = _make_no_change_rule("rule_b", order=20)
        ctx = _empty_context()

        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[rule_a, rule_b]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            from sv_common.guild_sync.matching_rules.runner import run_matching_rules
            result = await run_matching_rules(mock_pool)

        # rule_a changes in pass 1, so pass 2 runs → 2 rules × 2 passes = 4 entries
        assert len(result["results"]) == 4
        assert result["results"][0]["rule"] == "rule_a"
        assert result["results"][0]["pass"] == 1
        assert result["results"][1]["rule"] == "rule_b"
        assert result["results"][1]["pass"] == 1
        assert result["results"][2]["rule"] == "rule_a"
        assert result["results"][2]["pass"] == 2

    @pytest.mark.asyncio
    async def test_backward_compat_flat_keys_present(self, mock_pool):
        """Return dict includes flat backward-compat keys alongside structured format."""
        noop = _make_no_change_rule()
        ctx = _empty_context()

        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[noop]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            from sv_common.guild_sync.matching_rules.runner import run_matching_rules
            result = await run_matching_rules(mock_pool)

        # Old flat keys must be present
        assert "players_created" in result
        assert "chars_linked" in result
        assert "discord_linked" in result
        assert "no_discord_match" in result
        assert "skipped" in result
        # New structured keys must also be present
        assert "passes" in result
        assert "converged" in result
        assert "results" in result
        assert "totals" in result
