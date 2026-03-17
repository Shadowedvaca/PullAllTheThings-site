"""
Internal storage layer for sv_common.errors.

All SQL lives here. Not part of the public API — import from sv_common.errors instead.
"""

import hashlib
import json
import logging

import asyncpg

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _make_hash(issue_type: str, identifier: str | None) -> str:
    """Deterministic dedup key. Same type+identifier always produces the same hash."""
    raw = f"{issue_type}:{identifier or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _upsert(
    pool: asyncpg.Pool,
    issue_type: str,
    severity: str,
    summary: str,
    source_module: str,
    details: dict | None,
    identifier: str | None,
) -> dict:
    """
    Insert a new error record or increment an existing open one.

    Returns:
        {
            "id": int,
            "is_first_occurrence": bool,
            "occurrence_count": int,
        }
    """
    issue_hash = _make_hash(issue_type, identifier)
    details_json = json.dumps(details) if details else None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO common.error_log
                (issue_type, severity, source_module, identifier,
                 summary, details, issue_hash)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            ON CONFLICT (issue_hash) WHERE resolved_at IS NULL
            DO UPDATE SET
                occurrence_count = common.error_log.occurrence_count + 1,
                last_occurred_at = NOW(),
                summary          = EXCLUDED.summary,
                details          = EXCLUDED.details,
                severity         = EXCLUDED.severity
            RETURNING id, occurrence_count
            """,
            issue_type, severity, source_module, identifier,
            summary, details_json, issue_hash,
        )

    return {
        "id": row["id"],
        "is_first_occurrence": row["occurrence_count"] == 1,
        "occurrence_count": row["occurrence_count"],
    }


async def _resolve(
    pool: asyncpg.Pool,
    issue_type: str,
    identifier: str | None,
    resolved_by: str,
) -> int:
    """
    Soft-delete all open records matching issue_type + identifier.
    Returns the count of records resolved.
    """
    issue_hash = _make_hash(issue_type, identifier)

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE common.error_log
               SET resolved_at = NOW(),
                   resolved_by = $1
             WHERE issue_hash = $2
               AND resolved_at IS NULL
            """,
            resolved_by, issue_hash,
        )

    # asyncpg returns "UPDATE N" as a string
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def _query_unresolved(
    pool: asyncpg.Pool,
    severity: str | None,
    issue_type: str | None,
    source_module: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    """Return open error records, most recently seen first."""
    conditions = ["resolved_at IS NULL"]
    params: list = []

    if severity:
        min_order = SEVERITY_ORDER.get(severity, 0)
        matching = [s for s, o in SEVERITY_ORDER.items() if o >= min_order]
        conditions.append(f"severity = ANY(${len(params) + 1}::text[])")
        params.append(matching)

    if issue_type:
        conditions.append(f"issue_type = ${len(params) + 1}")
        params.append(issue_type)

    if source_module:
        conditions.append(f"source_module = ${len(params) + 1}")
        params.append(source_module)

    where = " AND ".join(conditions)
    params.extend([limit, offset])

    sql = f"""
        SELECT id, issue_type, severity, source_module, identifier,
               summary, details, occurrence_count,
               first_occurred_at, last_occurred_at
          FROM common.error_log
         WHERE {where}
         ORDER BY last_occurred_at DESC
         LIMIT ${len(params) - 1}
        OFFSET ${len(params)}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    return [dict(r) for r in rows]
