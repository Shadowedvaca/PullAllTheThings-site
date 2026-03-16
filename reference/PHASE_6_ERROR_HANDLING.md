# Phase 6 — Centralized Error Handling & Reporting

> **Status:** Planned — not yet started
> **Branch:** `phase-6-error-handling` (create fresh)
> **Goal:** Replace ad-hoc scattered error logging with a single-contract error catalogue that lives in `sv_common` and is portable to any project. Application-layer concerns (Discord, web UI) are handled by the consuming app, not by sv_common.

---

## Why This Exists

The BNet token expiry incident (2026-03-16) exposed the pattern: errors deep in background jobs were logged to the server log file and nowhere else. No audit log entry, no Discord notification. Officers had no way to know something was broken until a user complained.

Every subsystem currently does its own thing:
- Some write to `guild_identity.audit_issues`
- Some call `reporter.send_error()` directly
- Some just `logger.error()` and move on
- Some silently swallow errors with `continue`

Phase 6 builds a single contract: **call `report_error(...)` and the error is catalogued.** What happens next — Discord, web UI, email, nothing — is up to the consuming application.

---

## Separation of Concerns

This is the core architectural rule:

```
sv_common/errors/          ← portable catalogue, no app dependencies
    report_error()         ← write/upsert to common.error_log
    resolve_issue()        ← soft-delete on fix
    get_unresolved()       ← query API

guild_portal/              ← application layer, owns Discord + web UI
    scheduler              ← reads get_unresolved(), posts to Discord
    admin routes           ← exposes error log via HTTP API
    admin page             ← UI for viewing + resolving errors
    error_routing config   ← controls what goes to Discord vs audit log page
```

`sv_common` has **no knowledge of Discord, no knowledge of web pages, no knowledge of routing**. It just receives errors and exposes them via a query API. Any application using `sv_common` can build whatever notification layer it wants on top. An app with no Discord bot just calls `report_error()` and queries `get_unresolved()` however it likes (cron job, email, dashboard, etc.).

---

## Design Principles

1. **Single entry point** — one function to call, everywhere, for everything
2. **Pure catalogue in sv_common** — no routing, no sinks, no Discord; just store + query
3. **Application controls routing** — `guild_portal` owns the routing config table and decides what to do with catalogued errors
4. **Deduplication with recurrence tracking** — same error type+identifier = one record; occurrence count and last-seen always updated
5. **First-occurrence distinction** — a new occurrence after resolution is a fresh first-occurrence, not a continuation
6. **Self-healing** — call `resolve_issue(...)` on success; if the error recurs it starts fresh
7. **Weekly digest** — lives in `guild_portal` scheduler; calls `get_unresolved()` and posts to Discord

---

## sv_common Layer

### Package Structure

```
sv_common/
└── errors/
    ├── __init__.py     — public API: report_error(), resolve_issue(), get_unresolved()
    └── _store.py       — internal: hash generation, upsert SQL, query SQL
```

That's it. No sinks. No routing. No Discord imports.

### Public API

```python
async def report_error(
    pool: asyncpg.Pool,
    issue_type: str,        # e.g. "bnet_token_expired", "wcl_sync_failed"
    severity: str,          # "critical" | "warning" | "info"
    summary: str,           # one-line human-readable description
    source_module: str,     # e.g. "bnet_character_sync", "scheduler"
    details: dict | None = None,
    identifier: str | None = None,  # scopes dedup: e.g. str(player_id) or battletag
) -> dict:
    """
    Write or upsert an error record. Returns:
      {
        "id": int,
        "is_first_occurrence": bool,   # True if new record (or re-opened after resolution)
        "occurrence_count": int,
      }
    Callers use is_first_occurrence to decide whether to notify (e.g. post to Discord).
    """

async def resolve_issue(
    pool: asyncpg.Pool,
    issue_type: str,
    identifier: str | None = None,
    resolved_by: str = "system",
) -> int:
    """Soft-delete all open records matching issue_type + identifier. Returns count resolved."""

async def get_unresolved(
    pool: asyncpg.Pool,
    severity: str | None = None,    # filter to this severity or higher
    issue_type: str | None = None,
    source_module: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return open (unresolved) error records, newest first."""
```

### Issue Hash

```python
def _make_hash(issue_type: str, identifier: str | None) -> str:
    raw = f"{issue_type}:{identifier or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

### Upsert Behavior

```sql
INSERT INTO common.error_log (
    issue_type, severity, source_module, identifier,
    summary, details, issue_hash
)
VALUES (...)
ON CONFLICT (issue_hash) WHERE resolved_at IS NULL
DO UPDATE SET
    occurrence_count = error_log.occurrence_count + 1,
    last_occurred_at = NOW(),
    summary          = EXCLUDED.summary,
    details          = EXCLUDED.details,
    severity         = EXCLUDED.severity
RETURNING id, occurrence_count;
-- occurrence_count = 1 → is_first_occurrence = True
-- occurrence_count > 1 → is_first_occurrence = False
```

When `resolved_at IS NOT NULL`, the partial index doesn't conflict — a fresh INSERT creates a new record with `occurrence_count = 1` and a new `first_occurred_at`. This is how "resolved then re-opened" becomes a fresh first-occurrence.

---

## guild_portal Layer

### Routing Config Table

`guild_portal` owns the routing config — sv_common has no knowledge of it.

```sql
-- Migration 0042 also adds this table
CREATE TABLE common.error_routing (
    id              SERIAL PRIMARY KEY,
    issue_type      VARCHAR(80),    -- NULL = wildcard (matches all types)
    min_severity    VARCHAR(10)  NOT NULL DEFAULT 'warning',
    dest_audit_log  BOOLEAN      NOT NULL DEFAULT TRUE,
    dest_discord    BOOLEAN      NOT NULL DEFAULT TRUE,
    first_only      BOOLEAN      NOT NULL DEFAULT TRUE,
    -- first_only=TRUE: only notify on is_first_occurrence=True
    -- first_only=FALSE: notify on every occurrence (use with caution)
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    notes           TEXT,
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Default seed rules
INSERT INTO common.error_routing (issue_type, min_severity, dest_audit_log, dest_discord, first_only, notes) VALUES
    (NULL, 'critical', TRUE, TRUE,  FALSE, 'Critical: always everywhere, every time'),
    (NULL, 'warning',  TRUE, TRUE,  TRUE,  'Warning: everywhere, first occurrence only'),
    (NULL, 'info',     TRUE, FALSE, TRUE,  'Info: audit log only');
```

### How guild_portal Uses It

After calling `report_error()`, the caller receives `is_first_occurrence`. The scheduler (which has the Discord bot) is responsible for checking routing config and posting to Discord:

```python
# In scheduler or any guild_portal code that has the bot:
result = await report_error(pool, "bnet_token_expired", "warning", summary, "scheduler", identifier=battletag)
if result["is_first_occurrence"]:
    rule = await get_routing_rule(pool, "bnet_token_expired", "warning")
    if rule and rule["dest_discord"]:
        await send_error(audit_channel, "BNet Token Expired", summary)
```

For callsites in `admin_pages.py` (no bot available), they just call `report_error()` and don't touch Discord — the scheduler's nightly run will surface any unnotified errors via the weekly digest.

### Routing Cache

`guild_portal` caches routing rules in-process (TTL 5 min). Cache is invalidated immediately when a rule is updated via admin UI.

---

## Database Schema

### Migration 0042 — `common.error_log` + `common.error_routing`

> `guild_identity.audit_issues` is NOT dropped — integrity checker (orphan_wow, role_mismatch, etc.) stays there. These are data quality issues, not errors. Phase 6.4 migrates only error-type callsites.

```sql
CREATE TABLE common.error_log (
    id                  SERIAL PRIMARY KEY,
    issue_type          VARCHAR(80)   NOT NULL,
    severity            VARCHAR(10)   NOT NULL DEFAULT 'warning',
    source_module       VARCHAR(80),
    identifier          VARCHAR(255),
    summary             TEXT          NOT NULL,
    details             JSONB,
    issue_hash          VARCHAR(64)   NOT NULL,
    occurrence_count    INTEGER       NOT NULL DEFAULT 1,
    first_occurred_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_occurred_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    resolved_by         VARCHAR(80)
);

-- Partial unique index: only one open record per hash
CREATE UNIQUE INDEX uq_error_log_hash_active
    ON common.error_log (issue_hash)
    WHERE resolved_at IS NULL;

CREATE INDEX idx_error_log_type     ON common.error_log (issue_type);
CREATE INDEX idx_error_log_severity ON common.error_log (severity);
CREATE INDEX idx_error_log_active   ON common.error_log (resolved_at) WHERE resolved_at IS NULL;
```

Note: `first_notified_discord_at` / `last_notified_discord_at` are **not** on `common.error_log` — that's application state, not catalogue state. If `guild_portal` needs to track Discord notification history it can add a separate table or column in its own schema.

---

## Sub-Phase Documents

Each sub-phase is a standalone document with full context for a cold-start session.

| Phase | File | Summary |
|-------|------|---------|
| 6.1 | `PHASE_6_1_ERROR_CATALOGUE.md` | sv_common core — `common.error_log` schema + `report_error/resolve_issue/get_unresolved` |
| 6.2 | `PHASE_6_2_ADMIN_UI.md` | Routing config table, admin page, HTTP API |
| 6.3 | `PHASE_6_3_DISCORD_DIGEST.md` | Discord notification helper + Sunday weekly digest |
| 6.4 | `PHASE_6_4_MIGRATION.md` | Migrate all existing callsites; no more silent failures |

---

## Sub-Phases (Summary)

### Phase 6.1 — Schema + sv_common Core Module

**Migration:** 0042 — `common.error_log` + `common.error_routing` (with seed rules)

**Code (sv_common only):**
- `sv_common/errors/__init__.py` — `report_error()`, `resolve_issue()`, `get_unresolved()`
- `sv_common/errors/_store.py` — `_make_hash()`, SQL upsert, SQL resolve, SQL query

**Tests:** unit tests for hash generation, upsert returns correct `is_first_occurrence`, re-open after resolve starts fresh, resolve returns count

**No existing callsites changed. No Discord. No routing.**

---

### Phase 6.2 — guild_portal: Routing Config + Admin UI

**New `guild_portal` helper:** `error_routing.py` — loads `common.error_routing` from DB, caches with 5-min TTL, exposes `get_routing_rule(issue_type, severity) -> dict | None`

**API endpoints** (Officer+, `admin_routes.py`):
- `GET /api/v1/admin/errors/unresolved` — calls `get_unresolved(pool, ...)`, returns list
- `GET /api/v1/admin/errors/routing` — routing rules
- `PATCH /api/v1/admin/errors/routing/{id}` — update rule, flush cache
- `POST /api/v1/admin/errors/{id}/resolve` — manually resolve

**Admin page:** `/admin/error-routing` (Officer+, screen key `error_routing`)
- Top section: routing rules table with inline toggles
- Bottom section: unresolved errors table with resolve button, grouped by type
- Sidebar entry under Data Quality

**Tests:** API response shape tests

---

### Phase 6.3 — guild_portal: Discord Notification + Weekly Digest

**Immediate notification** (in scheduler, where bot is available):
- After `report_error()`, check routing rule
- If `dest_discord=TRUE` and (`first_only=FALSE` or `is_first_occurrence=TRUE`): post to audit channel via `send_error()`

**Weekly digest job** — `run_weekly_error_digest()` in `scheduler.py`:
- Runs Sunday 8:00 AM UTC
- Calls `get_unresolved(pool)`
- If empty: silent
- If any: grouped embed posted to audit channel

**Digest format:**
```
📋 Weekly Error Digest — 3 open issues

🔑 Battle.net Token Expired (2)
• sevin1979#1865 — first seen 2026-03-15, 7 occurrences
• Shadowedvaca#1947 — first seen 2026-03-16, 6 occurrences

🔴 WCL Sync Failed (1)
• API rate limit — first seen 2026-03-10, 14 occurrences

Manage at Admin → Error Routing
```

**Tests:** digest builder unit tests (given error list, verify embed structure)

---

### Phase 6.4 — Migration of Existing Callsites

Replace ad-hoc error handling throughout `sv_common` and `guild_portal` with `report_error()` / `resolve_issue()`.

**Callsites to migrate:**

| Module | Current behavior | New behavior |
|--------|-----------------|--------------|
| `scheduler.run_bnet_character_refresh` | `logger.warning` + skip | `report_error("bnet_token_expired")` + `resolve_issue` on success |
| `bnet_character_sync._refresh_token` | `logger.error` + return None | `report_error("bnet_token_expired")` |
| `scheduler.run_blizzard_sync` | `send_error` to channel | `report_error("blizzard_sync_failed")` |
| `scheduler.run_crafting_sync` | `send_error` to channel | `report_error("crafting_sync_failed")` |
| `scheduler.run_wcl_sync` | `logger.error` | `report_error("wcl_sync_failed")` |
| `scheduler.run_attendance_processing` | `logger.error` | `report_error("attendance_sync_failed")` |
| `scheduler.run_ah_sync` | `logger.error` | `report_error("ah_sync_failed")` |
| `admin_pages.admin_bnet_sync_user` | HTTP error response only | `report_error(...)` + resolve on success |
| `admin_pages.admin_bnet_sync_all` | HTTP error response only | `report_error(...)` per failure |

**guild_identity.audit_issues stays for:**
- `orphan_wow`, `orphan_discord`, `role_mismatch`, `stale_character`, etc.
- These are data quality issues with their own resolution workflow — untouched by Phase 6

**Tests:** scheduler unit tests assert `report_error` called on failure paths; `resolve_issue` called on success paths

---

## Key Files (Read First in Each Phase)

- `src/sv_common/guild_sync/reporter.py` — existing Discord reporting patterns to mirror
- `src/sv_common/guild_sync/integrity_checker.py` — existing `_upsert_issue` + `make_issue_hash` for reference
- `src/sv_common/guild_sync/scheduler.py` — main error source; has `self._get_audit_channel()` and `self.db_pool`
- `src/sv_common/config_cache.py` — pattern for in-process TTL caching
- `src/guild_portal/pages/admin_pages.py` — where admin page routes live
- `alembic/versions/` — latest migration is 0041; next is 0042
- `TESTING.md` — test conventions

---

## Acceptance Criteria (Full Phase 6)

- [ ] `sv_common.errors.report_error(pool, ...)` has zero imports from Discord, FastAPI, or guild_portal
- [ ] `report_error()` correctly returns `is_first_occurrence=True` for new + re-opened errors
- [ ] `resolve_issue()` soft-deletes; next occurrence starts fresh
- [ ] `get_unresolved()` filterable by severity, type, module
- [ ] `common.error_log` deduplicates: same open hash = increment, not new row
- [ ] `guild_portal` routing config controls Discord notification per type/severity
- [ ] Discord only pinged on first occurrence when `first_only=TRUE`
- [ ] Weekly digest posts Sunday 8 AM UTC when unresolved errors exist
- [ ] All scheduler error paths use `report_error` (no silent swallows)
- [ ] Admin can view, filter, and manually resolve at `/admin/error-routing`
- [ ] `guild_identity.audit_issues` and integrity checker are unaffected
