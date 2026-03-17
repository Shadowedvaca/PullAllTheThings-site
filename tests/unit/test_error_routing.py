"""
Unit tests for Phase 6.2 — guild_portal routing resolution logic.

Tests:
1. Exact match beats wildcard
2. Wildcard fallback when no exact match
3. Severity filter: rule min_severity must be <= event severity
4. No match → safe default
5. Disabled rule is ignored
6. Cache invalidation causes reload
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(rules: list[dict]):
    """Build a mock asyncpg pool that returns `rules` from conn.fetch."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rules)
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_rule(
    id=1, issue_type=None, min_severity="warning",
    dest_audit_log=True, dest_discord=True, first_only=True, enabled=True,
):
    return {
        "id": id,
        "issue_type": issue_type,
        "min_severity": min_severity,
        "dest_audit_log": dest_audit_log,
        "dest_discord": dest_discord,
        "first_only": first_only,
        "enabled": enabled,
        "notes": None,
        "updated_at": None,
    }


# ---------------------------------------------------------------------------
# 1. Exact match beats wildcard
# ---------------------------------------------------------------------------


class TestExactMatchBeatsWildcard:
    @pytest.mark.asyncio
    async def test_exact_match_overrides_wildcard(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        rules = [
            _make_rule(id=1, issue_type=None,               min_severity="warning", dest_discord=True),
            _make_rule(id=2, issue_type="bnet_token_expired", min_severity="warning", dest_discord=False),
        ]
        pool = _make_pool(rules)

        result = await er_mod.get_routing_rule(pool, "bnet_token_expired", "warning")
        assert result["dest_discord"] is False


# ---------------------------------------------------------------------------
# 2. Wildcard fallback
# ---------------------------------------------------------------------------


class TestWildcardFallback:
    @pytest.mark.asyncio
    async def test_wildcard_used_when_no_exact_match(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        rules = [
            _make_rule(id=1, issue_type=None, min_severity="warning", dest_discord=True),
        ]
        pool = _make_pool(rules)

        result = await er_mod.get_routing_rule(pool, "some_new_type", "warning")
        assert result["dest_discord"] is True
        assert result["dest_audit_log"] is True


# ---------------------------------------------------------------------------
# 3. Severity filter
# ---------------------------------------------------------------------------


class TestSeverityFilter:
    @pytest.mark.asyncio
    async def test_rule_min_warning_does_not_match_info_event(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        # Rule requires min_severity=warning; event severity is info
        rules = [
            _make_rule(id=1, issue_type=None, min_severity="warning", dest_discord=True),
        ]
        pool = _make_pool(rules)

        result = await er_mod.get_routing_rule(pool, "some_type", "info")
        # Should fall through to safe default since min_severity=warning > info
        assert result["dest_discord"] is False  # safe default

    @pytest.mark.asyncio
    async def test_rule_min_warning_matches_warning_event(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        rules = [
            _make_rule(id=1, issue_type=None, min_severity="warning", dest_discord=True),
        ]
        pool = _make_pool(rules)

        result = await er_mod.get_routing_rule(pool, "some_type", "warning")
        assert result["dest_discord"] is True

    @pytest.mark.asyncio
    async def test_rule_min_warning_matches_critical_event(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        rules = [
            _make_rule(id=1, issue_type=None, min_severity="warning", dest_discord=True),
        ]
        pool = _make_pool(rules)

        result = await er_mod.get_routing_rule(pool, "some_type", "critical")
        assert result["dest_discord"] is True


# ---------------------------------------------------------------------------
# 4. No match → safe default
# ---------------------------------------------------------------------------


class TestSafeDefault:
    @pytest.mark.asyncio
    async def test_no_rules_returns_safe_default(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        pool = _make_pool([])
        result = await er_mod.get_routing_rule(pool, "unknown_type", "warning")
        assert result == {"dest_audit_log": True, "dest_discord": False, "first_only": True}

    @pytest.mark.asyncio
    async def test_no_match_returns_safe_default(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        # Only a critical rule; info event won't match
        rules = [
            _make_rule(id=1, issue_type=None, min_severity="critical", dest_discord=True),
        ]
        pool = _make_pool(rules)

        result = await er_mod.get_routing_rule(pool, "some_type", "info")
        assert result["dest_discord"] is False
        assert result["dest_audit_log"] is True


# ---------------------------------------------------------------------------
# 5. Disabled rule is ignored
# ---------------------------------------------------------------------------


class TestDisabledRuleIgnored:
    @pytest.mark.asyncio
    async def test_disabled_exact_rule_falls_back_to_wildcard(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        # Disabled rules are NOT returned by the DB query (enabled=TRUE filter)
        # So we just test that absent exact rule falls through to wildcard
        rules = [
            _make_rule(id=1, issue_type=None, min_severity="warning", dest_discord=True, dest_audit_log=True),
        ]
        pool = _make_pool(rules)

        result = await er_mod.get_routing_rule(pool, "bnet_token_expired", "warning")
        assert result["dest_discord"] is True  # got wildcard


# ---------------------------------------------------------------------------
# 6. Cache invalidation
# ---------------------------------------------------------------------------


class TestCacheInvalidation:
    @pytest.mark.asyncio
    async def test_invalidate_forces_db_reload(self):
        from guild_portal.services import error_routing as er_mod
        er_mod.invalidate_cache()

        rules = [_make_rule(id=1, issue_type=None, min_severity="warning")]
        pool = _make_pool(rules)
        conn = pool.acquire.return_value.__aenter__.return_value

        # First call loads from DB
        await er_mod.get_routing_rule(pool, "some_type", "warning")
        assert conn.fetch.call_count == 1

        # Second call uses cache — no additional DB call
        await er_mod.get_routing_rule(pool, "other_type", "warning")
        assert conn.fetch.call_count == 1

        # After invalidate, next call reloads from DB
        er_mod.invalidate_cache()
        await er_mod.get_routing_rule(pool, "some_type", "warning")
        assert conn.fetch.call_count == 2
