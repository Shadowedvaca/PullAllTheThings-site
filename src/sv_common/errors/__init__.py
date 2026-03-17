"""
sv_common.errors — portable error catalogue.

Provides three functions:

    report_error(pool, issue_type, severity, summary, source_module, ...)
        → {"id": int, "is_first_occurrence": bool, "occurrence_count": int}

    resolve_issue(pool, issue_type, identifier, resolved_by)
        → int  (count of records resolved)

    get_unresolved(pool, ...)
        → list[dict]

No Discord. No routing. No application dependencies.
The consuming application decides what to do with is_first_occurrence.
"""

import asyncpg

from ._store import _upsert, _resolve, _query_unresolved


async def report_error(
    pool: asyncpg.Pool,
    issue_type: str,
    severity: str,
    summary: str,
    source_module: str,
    details: dict | None = None,
    identifier: str | None = None,
) -> dict:
    """
    Record an error. Creates a new record or increments an existing open one.

    Args:
        pool:          asyncpg connection pool
        issue_type:    machine-readable type string, e.g. "bnet_token_expired"
        severity:      "critical" | "warning" | "info"
        summary:       one-line human-readable description
        source_module: which module is reporting, e.g. "bnet_character_sync"
        details:       optional dict of additional context (stored as JSONB)
        identifier:    optional scope for deduplication, e.g. str(player_id) or battletag
                       errors with the same issue_type+identifier share one open record

    Returns:
        {
            "id": int,
            "is_first_occurrence": bool,   # True when this is a new or re-opened record
            "occurrence_count": int,       # 1 on first occurrence
        }
    """
    return await _upsert(pool, issue_type, severity, summary, source_module, details, identifier)


async def resolve_issue(
    pool: asyncpg.Pool,
    issue_type: str,
    identifier: str | None = None,
    resolved_by: str = "system",
) -> int:
    """
    Mark all open records for this issue_type+identifier as resolved.

    Call this when the error condition clears (e.g. BNet token refresh succeeded).
    If the error recurs later, report_error() will create a fresh first_occurrence record.

    Returns the number of records resolved (usually 0 or 1).
    """
    return await _resolve(pool, issue_type, identifier, resolved_by)


async def get_unresolved(
    pool: asyncpg.Pool,
    severity: str | None = None,
    issue_type: str | None = None,
    source_module: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    Return open (unresolved) error records, most recently seen first.

    Args:
        severity:      if set, return only records at this severity or higher
                       ("info" → all; "warning" → warning + critical; "critical" → critical only)
        issue_type:    exact match filter
        source_module: exact match filter
        limit/offset:  pagination
    """
    return await _query_unresolved(pool, severity, issue_type, source_module, limit, offset)
