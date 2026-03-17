"""
Routing config cache for sv_common error events.

Loads common.error_routing from DB, caches in-process with a 5-minute TTL.
Call invalidate_cache() from the admin PATCH endpoint to flush immediately.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_CACHE_TTL = timedelta(minutes=5)

_cache: list[dict] | None = None
_cache_loaded_at: datetime | None = None

_SAFE_DEFAULT = {
    "dest_audit_log": True,
    "dest_discord": False,
    "first_only": True,
}


async def _load_rules(pool: asyncpg.Pool) -> list[dict]:
    """Load all enabled routing rules from the DB."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, issue_type, min_severity, dest_audit_log, dest_discord,
                   first_only, enabled, notes, updated_at
              FROM common.error_routing
             WHERE enabled = TRUE
             ORDER BY issue_type NULLS LAST, min_severity
            """
        )
    return [dict(r) for r in rows]


async def _get_rules(pool: asyncpg.Pool) -> list[dict]:
    """Return cached rules, refreshing if stale or absent."""
    global _cache, _cache_loaded_at
    now = datetime.now(timezone.utc)
    if _cache is None or _cache_loaded_at is None or (now - _cache_loaded_at) > _CACHE_TTL:
        _cache = await _load_rules(pool)
        _cache_loaded_at = now
    return _cache


def invalidate_cache() -> None:
    """Force next call to get_routing_rule to reload from DB."""
    global _cache, _cache_loaded_at
    _cache = None
    _cache_loaded_at = None


async def get_routing_rule(
    pool: asyncpg.Pool,
    issue_type: str,
    severity: str,
) -> dict:
    """
    Return the resolved routing rule for a given issue_type + severity.
    Always returns a dict — falls back to safe defaults if no rule matches.

    Resolution order:
    1. Exact issue_type match, highest min_severity that still <= event severity
    2. Wildcard (issue_type IS NULL), highest min_severity that still <= event severity
    3. Safe default (dest_audit_log=True, dest_discord=False, first_only=True)

    Return shape:
    {
        "dest_audit_log": bool,
        "dest_discord":   bool,
        "first_only":     bool,
    }
    """
    rules = await _get_rules(pool)
    event_sev_order = _SEVERITY_ORDER.get(severity, 0)

    # Separate exact vs wildcard candidates that qualify for this severity
    exact_candidates = []
    wildcard_candidates = []
    for rule in rules:
        rule_sev_order = _SEVERITY_ORDER.get(rule["min_severity"], 0)
        if rule_sev_order > event_sev_order:
            continue  # rule requires higher severity than this event
        if rule["issue_type"] == issue_type:
            exact_candidates.append(rule)
        elif rule["issue_type"] is None:
            wildcard_candidates.append(rule)

    # Pick best: exact first, then wildcard; within each group prefer highest min_severity
    best = None
    for candidates in (exact_candidates, wildcard_candidates):
        if candidates:
            best = max(candidates, key=lambda r: _SEVERITY_ORDER.get(r["min_severity"], 0))
            break

    if best is None:
        return dict(_SAFE_DEFAULT)

    return {
        "dest_audit_log": best["dest_audit_log"],
        "dest_discord": best["dest_discord"],
        "first_only": best["first_only"],
    }


async def maybe_notify_discord(
    pool: asyncpg.Pool,
    bot: "discord.Client | None",
    audit_channel_id: "int | None",
    issue_type: str,
    severity: str,
    summary: str,
    is_first_occurrence: bool,
) -> None:
    """
    Post to the audit Discord channel if the routing rule says to.

    Call this immediately after report_error() returns, passing its is_first_occurrence.
    Does nothing if:
    - routing rule says dest_discord=False
    - routing rule says first_only=True AND is_first_occurrence=False
    - bot is None or audit_channel_id is None
    """
    if bot is None or audit_channel_id is None:
        return

    rule = await get_routing_rule(pool, issue_type, severity)
    if not rule["dest_discord"]:
        return
    if rule["first_only"] and not is_first_occurrence:
        return

    from sv_common.guild_sync.reporter import send_error
    channel = bot.get_channel(audit_channel_id)
    if channel is None:
        return

    await send_error(channel, _format_title(issue_type, severity), summary)


def _format_title(issue_type: str, severity: str) -> str:
    """Convert issue_type to a readable title for the Discord embed."""
    from sv_common.guild_sync.reporter import ISSUE_TYPE_NAMES
    label = ISSUE_TYPE_NAMES.get(issue_type, issue_type.replace("_", " ").title())
    prefix = {"critical": "CRITICAL", "warning": "Warning", "info": "Notice"}.get(severity, severity.title())
    return f"{prefix}: {label}"
