# Phase 6.1 — Error Catalogue (sv_common Core)

> **Branch:** `phase-6-error-handling` (create fresh from main)
> **Migration:** 0042
> **Depends on:** nothing — this is the foundation all later phases build on
> **Produces:** `sv_common.errors` — a portable, Discord-free, app-free error store

---

## Goal

Build the portable core of the error handling system. A thin module in `sv_common` that
any project can import to record, query, and resolve errors. No Discord. No routing config.
No knowledge of how the consuming app wants to surface errors.

When this phase is done: any code anywhere in the project can call `report_error(pool, ...)`
and the error is durably catalogued. Nothing else happens — Phase 6.2 and 6.3 wire up
the notifications.

---

## Why sv_common Only

`sv_common` is a shared library used by `guild_portal` and intended for other projects.
Those other projects may have no Discord bot, no web admin panel, and no concept of
"routing config." The catalogue must work in isolation. Notification decisions belong
to the consuming application, not the library.

The only external dependency for `sv_common/errors/` is `asyncpg`. Nothing else.

---

## Prerequisites

- Platform is feature-complete through Phase 4.8 (migration 0044)
- Working dev environment: `.venv` active, `pytest tests/unit/ -v` passes (664 pass, 69 skip)
- Familiarity with existing `sv_common/guild_sync/integrity_checker.py` — same upsert
  pattern, same hash approach, but generalised and moved to `sv_common/errors/`

---

## How the Existing System Works (Read First)

**`guild_identity.audit_issues`** — the existing error/issue table. Has `issue_type`,
`severity`, `summary`, `details` (JSONB), `issue_hash` (dedup key), `notified_at`,
`resolved_at`. Written by `integrity_checker.py` for identity data quality issues.

**`integrity_checker.make_issue_hash(issue_type, *identifiers)`** — builds a SHA-256
hash from `"type:id1:id2"`. Same approach used in the new module.

**`integrity_checker._upsert_issue(conn, ...)`** — inserts if new, updates summary/details
if existing. Returns `True` if new. No `occurrence_count` tracking.

Phase 6.1 builds a generalised version of this pattern into `sv_common/errors/` with
occurrence tracking and a cleaner public API.

`guild_identity.audit_issues` is **NOT** touched in this phase. It continues working
exactly as before for the integrity checker.

---

## Database Migration: 0042

### New Table: `common.error_log`

```sql
CREATE TABLE common.error_log (
    id                SERIAL PRIMARY KEY,
    issue_type        VARCHAR(80)  NOT NULL,
    severity          VARCHAR(10)  NOT NULL DEFAULT 'warning',
    -- 'critical' | 'warning' | 'info'
    source_module     VARCHAR(80),
    -- which module reported this: 'bnet_character_sync', 'scheduler', etc.
    identifier        VARCHAR(255),
    -- optional scope for deduplication: str(player_id), battletag, etc.
    summary           TEXT         NOT NULL,
    details           JSONB,
    issue_hash        VARCHAR(64)  NOT NULL,
    -- sha256("issue_type:identifier") — partial unique index enforces one open record per hash
    occurrence_count  INTEGER      NOT NULL DEFAULT 1,
    first_occurred_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at       TIMESTAMPTZ,
    resolved_by       VARCHAR(80)
);

-- One open (unresolved) record per hash at a time.
-- When resolved_at IS NOT NULL the record is "closed" and a new INSERT can create a
-- fresh first_occurred_at record if the error recurs.
CREATE UNIQUE INDEX uq_error_log_hash_active
    ON common.error_log (issue_hash)
    WHERE resolved_at IS NULL;

CREATE INDEX idx_error_log_type     ON common.error_log (issue_type);
CREATE INDEX idx_error_log_severity ON common.error_log (severity);
CREATE INDEX idx_error_log_active   ON common.error_log (resolved_at)
    WHERE resolved_at IS NULL;
```

**Key design notes:**

- `occurrence_count` starts at 1 and is incremented on each repeated report of the same
  open error. The consuming app can use this to decide whether to re-notify.
- `first_occurred_at` / `last_occurred_at` — let the weekly digest show "first seen N days
  ago, occurred M times."
- `resolved_at IS NULL` is the "open" state. A resolved error that recurs gets a brand
  new row with `occurrence_count = 1` and a new `first_occurred_at` — it is treated as a
  fresh first occurrence.
- No `notified_at` columns — notification tracking is the consuming app's responsibility,
  not the catalogue's.

### No `common.error_routing` in This Phase

`common.error_routing` (routing config) is a `guild_portal` concern. It is created in
Phase 6.2 by `guild_portal`, not by `sv_common`. This keeps the `sv_common` migration
clean and portable.

### Alembic Migration File

Create `alembic/versions/0042_error_log.py`. Follow the pattern of the most recent
migration in `alembic/versions/`. Use `op.execute(...)` for the raw SQL above.

---

## Package Structure

```
src/sv_common/errors/
├── __init__.py      — public API only: report_error, resolve_issue, get_unresolved
└── _store.py        — private: _make_hash, _upsert, _resolve, _query
```

Two files. No other files in this package in Phase 6.1.

---

## Implementation

### `sv_common/errors/_store.py`

```python
"""
Internal storage layer for sv_common.errors.

All SQL lives here. Not part of the public API — import from sv_common.errors instead.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone

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
```

### `sv_common/errors/__init__.py`

```python
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
```

---

## ORM Model (Optional)

`common.error_log` is queried via raw asyncpg in `sv_common/errors/_store.py`. If the
rest of the codebase needs an ORM model for SQLAlchemy (e.g., for admin routes that use
`get_db()`), add it to `sv_common/db/models.py`:

```python
class ErrorLog(Base):
    __tablename__ = "error_log"
    __table_args__ = {"schema": "common"}

    id                : int (PK)
    issue_type        : str
    severity          : str
    source_module     : Optional[str]
    identifier        : Optional[str]
    summary           : str
    details           : Optional[dict]   # JSONB
    issue_hash        : str
    occurrence_count  : int
    first_occurred_at : datetime
    last_occurred_at  : datetime
    resolved_at       : Optional[datetime]
    resolved_by       : Optional[str]
```

Add to `sv_common/db/models.py` following the existing pattern. Import it in
`sv_common/db/__init__.py` if applicable.

---

## Tests

File: `tests/unit/test_errors.py`

All tests use mock asyncpg pools — no live DB required.

### Test cases

**`test_make_hash_deterministic`**
Same inputs always produce the same hash. Different inputs produce different hashes.
`_make_hash("bnet_token_expired", "123")` == `_make_hash("bnet_token_expired", "123")`.
`_make_hash("bnet_token_expired", "123")` != `_make_hash("bnet_token_expired", "456")`.

**`test_make_hash_no_identifier`**
`_make_hash("some_type", None)` produces a consistent hash (hashes `"some_type:"`).

**`test_report_error_returns_first_occurrence_on_new_record`**
Mock `conn.fetchrow` returns `{"id": 1, "occurrence_count": 1}`.
`report_error(...)` returns `{"id": 1, "is_first_occurrence": True, "occurrence_count": 1}`.

**`test_report_error_returns_false_on_recurrence`**
Mock `conn.fetchrow` returns `{"id": 1, "occurrence_count": 3}`.
`report_error(...)` returns `{"id": 1, "is_first_occurrence": False, "occurrence_count": 3}`.

**`test_resolve_issue_returns_count`**
Mock `conn.execute` returns `"UPDATE 1"`.
`resolve_issue(pool, "bnet_token_expired", "Shadowedvaca#1947")` returns `1`.

**`test_resolve_issue_no_match_returns_zero`**
Mock `conn.execute` returns `"UPDATE 0"`.
`resolve_issue(...)` returns `0`.

**`test_get_unresolved_no_filters`**
Mock `conn.fetch` returns two rows. `get_unresolved(pool)` returns two dicts.

**`test_get_unresolved_severity_filter`**
When `severity="warning"`, the SQL params include `["warning", "critical"]` (not "info").

**`test_get_unresolved_empty`**
Mock returns `[]`. `get_unresolved(pool)` returns `[]` without error.

**`test_report_error_swallows_db_exception_gracefully`** *(optional, recommended)*
If the DB call raises an exception, `report_error` logs it but does not propagate —
a broken error catalogue should never crash the subsystem that's reporting an error.

---

## Deliverables Checklist

- [ ] Migration `0042_error_log.py` — `common.error_log` table + indexes
- [ ] `src/sv_common/errors/__init__.py` — `report_error`, `resolve_issue`, `get_unresolved`
- [ ] `src/sv_common/errors/_store.py` — `_make_hash`, `_upsert`, `_resolve`, `_query_unresolved`
- [ ] `src/sv_common/db/models.py` — `ErrorLog` ORM model added
- [ ] `tests/unit/test_errors.py` — all test cases above pass
- [ ] `pytest tests/unit/ -v` — existing 664 tests still pass + new error tests

---

## What This Phase Does NOT Do

- No Discord notifications (Phase 6.3)
- No routing config table (Phase 6.2)
- No admin UI (Phase 6.2)
- No changes to `guild_identity.audit_issues` or `integrity_checker.py`
- No changes to any existing callsite — `report_error` exists but nothing calls it yet
- No `common.error_routing` table (Phase 6.2, owned by guild_portal)
