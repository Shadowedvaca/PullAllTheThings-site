"""Backward-compatibility tests for run_matching() (Phase 3.0B).

Verifies that the public API of run_matching() is unchanged and that the
return dict still includes all the flat keys that callers relied on before
the rule runner was introduced.
"""

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sv_common.guild_sync.identity_engine import run_matching
from sv_common.guild_sync.matching_rules.base import MatchingContext, RuleResult


# ---------------------------------------------------------------------------
# Signature tests (no DB needed)
# ---------------------------------------------------------------------------

class TestRunMatchingSignature:
    def test_is_coroutine_function(self):
        assert inspect.iscoroutinefunction(run_matching)

    def test_accepts_pool_positional(self):
        sig = inspect.signature(run_matching)
        params = list(sig.parameters)
        assert "pool" in params

    def test_accepts_min_rank_level_kwarg(self):
        sig = inspect.signature(run_matching)
        assert "min_rank_level" in sig.parameters

    def test_min_rank_level_defaults_to_none(self):
        sig = inspect.signature(run_matching)
        assert sig.parameters["min_rank_level"].default is None


# ---------------------------------------------------------------------------
# Return dict shape tests (mocked runner)
# ---------------------------------------------------------------------------

def _make_noop_rule():
    rule = MagicMock()
    rule.name = "noop"
    rule.order = 10
    rule.run = AsyncMock(return_value=RuleResult(rule_name="noop"))
    return rule


class TestRunMatchingReturnShape:
    @pytest.fixture
    def mock_pool(self):
        pool = MagicMock()
        conn = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return pool

    @pytest.mark.asyncio
    async def test_returns_dict(self, mock_pool):
        ctx = MatchingContext([], [], {}, {}, [])
        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[_make_noop_rule()]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            result = await run_matching(mock_pool)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_old_flat_keys_present(self, mock_pool):
        """All keys the old run_matching() returned must still be present."""
        ctx = MatchingContext([], [], {}, {}, [])
        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[_make_noop_rule()]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            result = await run_matching(mock_pool)

        for key in ("players_created", "chars_linked", "discord_linked",
                    "no_discord_match", "skipped"):
            assert key in result, f"Missing backward-compat key: {key}"

    @pytest.mark.asyncio
    async def test_new_structured_keys_present(self, mock_pool):
        """New keys from Phase 3.0B must also be present."""
        ctx = MatchingContext([], [], {}, {}, [])
        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[_make_noop_rule()]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", AsyncMock(return_value=ctx)),
        ):
            result = await run_matching(mock_pool)

        assert "passes" in result
        assert "converged" in result
        assert "results" in result
        assert "totals" in result

    @pytest.mark.asyncio
    async def test_min_rank_level_forwarded(self, mock_pool):
        """min_rank_level passed to run_matching() is forwarded to the runner."""
        ctx = MatchingContext([], [], {}, {}, [], min_rank_level=4)
        build_context_mock = AsyncMock(return_value=ctx)

        with (
            patch("sv_common.guild_sync.matching_rules.runner.get_registered_rules", return_value=[_make_noop_rule()]),
            patch("sv_common.guild_sync.matching_rules.runner.build_context", build_context_mock),
        ):
            await run_matching(mock_pool, min_rank_level=4)

        build_context_mock.assert_called_once_with(mock_pool, 4)


# ---------------------------------------------------------------------------
# Registry tests (no DB needed)
# ---------------------------------------------------------------------------

class TestRuleRegistry:
    def test_get_registered_rules_returns_list(self):
        from sv_common.guild_sync.matching_rules import get_registered_rules
        rules = get_registered_rules()
        assert isinstance(rules, list)
        assert len(rules) >= 2

    def test_rules_sorted_by_order(self):
        from sv_common.guild_sync.matching_rules import get_registered_rules
        rules = get_registered_rules()
        orders = [r.order for r in rules]
        assert orders == sorted(orders)

    def test_note_group_before_name_match(self):
        from sv_common.guild_sync.matching_rules import get_registered_rules
        rules = get_registered_rules()
        names = [r.name for r in rules]
        assert names.index("note_group") < names.index("name_match")

    def test_all_rules_have_required_attributes(self):
        from sv_common.guild_sync.matching_rules import get_registered_rules
        for rule in get_registered_rules():
            assert hasattr(rule, "name")
            assert hasattr(rule, "description")
            assert hasattr(rule, "link_source")
            assert hasattr(rule, "order")
            assert hasattr(rule, "run")
            assert callable(rule.run)
