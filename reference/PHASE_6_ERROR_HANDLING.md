# Phase 6 — Centralized Error Handling & Reporting

> **Status:** Planned — not yet started
> **Branch:** `phase-6-error-handling` (create fresh)
> **Goal:** Replace ad-hoc scattered error logging with a pluggable, configurable error reporting module that lives in `sv_common` and can be reused across projects.

---

## Why This Exists

The BNet token expiry incident (2026-03-16) exposed the pattern: errors deep in background jobs were logged to the server log file and nowhere else. No audit log entry, no Discord notification. Officers had no way to know something was broken until a user complained.

Every subsystem currently does its own thing:
- Some write to `guild_identity.audit_issues`
- Some call `reporter.send_error()` directly
- Some just `logger.error()` and move on
- Some silently swallow errors with `continue`

Phase 6 builds a single contract: **call `report_error(...)` and the system routes it correctly based on configuration.** All existing callsites get migrated.

---

## Design Principles

1. **Single entry point** — one function to call, everywhere, for everything
2. **Configurable routing** — per issue-type and per severity, admin controls which destinations receive the event (audit log, Discord, both, neither)
3. **Deduplication with recurrence tracking** — same error type+identifier = one record, not N rows; but recurrence count and last-seen are always updated
4. **First-occurrence distinction** — a new occurrence after a resolution is a fresh first-occurrence, not a continuation
5. **Self-healing** — call `resolve_issue(...)` on success and the record is soft-deleted; if it recurs it starts fresh
6. **Weekly digest** — Sunday morning Discord report of all open unresolved issues, to surface long-tails that aren't generating new noise
7. **Portable** — lives entirely in `sv_common`, no dependency on `guild_portal`

---

## Architecture

```
sv_common/
└── errors/
    ├── __init__.py          — public API: report_error(), resolve_issue(), get_unresolved()
    ├── models.py            — ErrorEvent dataclass, Severity enum, Destination enum
    ├── routing.py           — load + cache routing config from DB; match rules
    ├── db_sink.py           — write/upsert to common.error_log table
    ├── discord_sink.py      — post to Discord audit channel (immediate, first-occurrence or critical)
    └── digest.py            — weekly unresolved summary builder
```

### Public API (sv_common.errors)

```python
async def report_error(
    pool: asyncpg.Pool,
    bot: discord.Client | None,
    issue_type: str,           # e.g. "bnet_token_expired", "wcl_sync_failed"
    severity: str,             # "critical" | "warning" | "info"
    summary: str,              # one-line human-readable description
    source_module: str,        # e.g. "bnet_character_sync", "scheduler"
    details: dict | None = None,
    identifier: str | None = None,  # scopes dedup: e.g. str(player_id) or battletag
) -> None: ...

async def resolve_issue(
    pool: asyncpg.Pool,
    issue_type: str,
    identifier: str | None = None,
    resolved_by: str = "system",
) -> int: ...  # returns count of resolved records

async def get_unresolved(
    pool: asyncpg.Pool,
    severity: str | None = None,   # filter by min severity
    issue_type: str | None = None,
    limit: int = 100,
) -> list[dict]: ...
```

---

## Database Schema

### Migration 0042 — `common.error_log` + `common.error_routing`

> **Note:** The existing `guild_identity.audit_issues` table is NOT dropped — it stays for Phase 4.x integrity checker output until Phase 6.4 migrates those callsites. The new `common.error_log` table is the Phase 6+ home for all errors.

```sql
-- New table: common.error_log
CREATE TABLE common.error_log (
    id                  SERIAL PRIMARY KEY,
    issue_type          VARCHAR(80)  NOT NULL,
    severity            VARCHAR(10)  NOT NULL DEFAULT 'warning',
    source_module       VARCHAR(80),
    identifier          VARCHAR(255),          -- scopes dedup (e.g. player_id, battletag)
    summary             TEXT         NOT NULL,
    details             JSONB,
    issue_hash          VARCHAR(64)  NOT NULL,
    occurrence_count    INTEGER      NOT NULL DEFAULT 1,
    first_occurred_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    first_notified_discord_at  TIMESTAMPTZ,   -- when first posted to Discord
    last_notified_discord_at   TIMESTAMPTZ,   -- when most recently posted
    resolved_at         TIMESTAMPTZ,
    resolved_by         VARCHAR(80)
);

CREATE UNIQUE INDEX uq_error_log_hash_active
    ON common.error_log (issue_hash)
    WHERE resolved_at IS NULL;

CREATE INDEX idx_error_log_type    ON common.error_log (issue_type);
CREATE INDEX idx_error_log_severity ON common.error_log (severity);
CREATE INDEX idx_error_log_resolved ON common.error_log (resolved_at);

-- issue_hash = sha256("issue_type:identifier") — deterministic, no identifier = sha256("issue_type:")

-- New table: common.error_routing
CREATE TABLE common.error_routing (
    id              SERIAL PRIMARY KEY,
    issue_type      VARCHAR(80),     -- NULL = wildcard (matches all)
    min_severity    VARCHAR(10)  NOT NULL DEFAULT 'warning',
    dest_audit_log  BOOLEAN      NOT NULL DEFAULT TRUE,
    dest_discord    BOOLEAN      NOT NULL DEFAULT TRUE,
    -- Discord behavior:
    discord_on_first_occurrence_only  BOOLEAN NOT NULL DEFAULT FALSE,
    -- If TRUE: only post to Discord on first_occurrence; silent on recurrences
    -- If FALSE: post to Discord every time (subject to min_severity)
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    notes           TEXT,
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Seed default routing rules
INSERT INTO common.error_routing (issue_type, min_severity, dest_audit_log, dest_discord, discord_on_first_occurrence_only, notes) VALUES
    (NULL,                   'critical', TRUE,  TRUE,  FALSE, 'Catch-all: critical always goes everywhere'),
    (NULL,                   'warning',  TRUE,  TRUE,  TRUE,  'Catch-all: warnings go everywhere but Discord only on first'),
    (NULL,                   'info',     TRUE,  FALSE, FALSE, 'Catch-all: info only goes to audit log'),
    ('bnet_token_expired',   'warning',  TRUE,  TRUE,  TRUE,  'BNet token expiry: first occurrence to Discord'),
    ('bnet_sync_error',      'warning',  TRUE,  TRUE,  TRUE,  'BNet sync error: first occurrence to Discord');
```

### Routing Resolution Logic

When `report_error(...)` is called:
1. Look up routing rules matching `issue_type` (exact match first, then wildcard `NULL`)
2. Use the most-specific matching rule with `min_severity` <= event severity
3. `dest_audit_log=TRUE` → write/upsert to `common.error_log`
4. `dest_discord=TRUE` → check `discord_on_first_occurrence_only`:
   - If FALSE → always post to Discord
   - If TRUE → only post if this is a **first occurrence** (new record OR resolved+re-opened)

### Issue Hash

```python
def make_issue_hash(issue_type: str, identifier: str | None) -> str:
    raw = f"{issue_type}:{identifier or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

### Upsert Behavior

```sql
-- On report_error():
INSERT INTO common.error_log (issue_type, severity, source_module, identifier,
    summary, details, issue_hash, occurrence_count, first_occurred_at, last_occurred_at)
VALUES (...)
ON CONFLICT (issue_hash) WHERE resolved_at IS NULL
DO UPDATE SET
    occurrence_count  = error_log.occurrence_count + 1,
    last_occurred_at  = NOW(),
    summary           = EXCLUDED.summary,
    details           = EXCLUDED.details,
    severity          = EXCLUDED.severity;
-- RETURNING id, occurrence_count — used to decide whether to notify Discord
```

If `resolved_at IS NOT NULL` (resolved record exists), the partial unique index won't conflict — a fresh row is inserted (resets occurrence_count to 1, new first_occurred_at).

---

## Sub-Phases

### Phase 6.1 — Schema + Core Module

**Migration:** 0042 — `common.error_log` + `common.error_routing` (with seed rules)
**Code:**
- `sv_common/errors/__init__.py` — `report_error()`, `resolve_issue()`, `get_unresolved()`
- `sv_common/errors/models.py` — `Severity` enum, `Destination` enum, `ErrorEvent` dataclass
- `sv_common/errors/routing.py` — load rules from DB, cache with TTL (refresh every 5 min), resolve rule for a given issue_type+severity
- `sv_common/errors/db_sink.py` — upsert logic, returns `(is_first_occurrence: bool, occurrence_count: int)`
- `sv_common/errors/discord_sink.py` — `post_error_embed(bot, channel_id, event, is_first_occurrence)` — uses `send_error` pattern from existing `reporter.py`

**Tests:** unit tests for routing resolution, hash generation, upsert behavior (mock pool)

**No existing callsites changed in this phase.**

---

### Phase 6.2 — Admin UI: Error Routing Config + Unresolved Errors API

**API endpoints** (Officer+, in `admin_routes.py`):
- `GET /api/v1/admin/errors/unresolved` — returns list of unresolved errors from `common.error_log`, filterable by severity/type; used by the Discord weekly digest
- `GET /api/v1/admin/errors/routing` — returns current routing rules
- `PATCH /api/v1/admin/errors/routing/{id}` — update a rule (toggle dest_discord, dest_audit_log, etc.); triggers routing cache invalidation
- `POST /api/v1/admin/errors/{id}/resolve` — manually resolve an open error

**Admin page:** `/admin/error-routing` (new screen, Officer+, screen key `error_routing`)
- Table of routing rules: issue_type (or "All"), min_severity, toggles for Audit Log / Discord / First-Only
- Inline edit (toggle switches, no modal needed)
- Below: live table of current unresolved errors with resolve button

**Sidebar:** add "Error Routing" entry under Data Quality section

**Tests:** unit tests for API response shapes

---

### Phase 6.3 — Weekly Digest (Discord Bot)

**Scheduler:** New job in `scheduler.py` — `run_weekly_error_digest()`
- Runs Sundays at 8:00 AM UTC (configurable)
- Calls `get_unresolved(pool)`
- If none: skip silently
- If any: build a grouped embed by issue_type, showing occurrence_count and first_occurred_at
- Posts to audit channel

**Format:**
```
📋 Weekly Error Digest — N open issues

🔑 Battle.net Token Expired (2 issues)
• sevin1979#1865 — first seen 2026-03-15, occurred 7 times
• Shadowedvaca#1947 — first seen 2026-03-16, occurred 6 times

🔴 WCL Sync Error (1 issue)
• API rate limit on guild report fetch — first seen 2026-03-10, occurred 14 times

See Admin → Error Routing to manage.
```

**Tests:** unit tests for digest builder (given a list of errors, verify embed structure)

---

### Phase 6.4 — Migration of Existing Callsites

Replace all ad-hoc error handling throughout `sv_common` with `report_error()` / `resolve_issue()`. Existing `guild_identity.audit_issues` callsites for *errors* (not identity/integrity issues) are migrated. Identity integrity issues (orphan_wow, role_mismatch, etc.) stay in `audit_issues` — they are a different category (data quality, not errors).

**Callsites to migrate:**

| Module | Current behavior | New behavior |
|--------|-----------------|--------------|
| `scheduler.run_bnet_character_refresh` | `logger.warning` + skip | `report_error("bnet_token_expired", ...)` + `resolve_issue` on success |
| `bnet_character_sync._refresh_token` | `logger.error` + return None | `report_error("bnet_token_expired", ...)` |
| `scheduler.run_blizzard_sync` | `send_error` to channel | `report_error("blizzard_sync_failed", ...)` |
| `scheduler.run_crafting_sync` | `send_error` to channel | `report_error("crafting_sync_failed", ...)` |
| `scheduler.run_wcl_sync` | `logger.error` | `report_error("wcl_sync_failed", ...)` |
| `scheduler.run_attendance_processing` | `logger.error` | `report_error("attendance_processing_failed", ...)` |
| `scheduler.run_ah_sync` | `logger.error` | `report_error("ah_sync_failed", ...)` |
| `admin_pages.admin_bnet_sync_user` | HTTP error response only | `report_error(...)` + resolve on success |
| `admin_pages.admin_bnet_sync_all` | HTTP error response only | `report_error(...)` per failure |

**guild_identity.audit_issues stays for:**
- `orphan_wow`, `orphan_discord`, `role_mismatch`, `stale_character`, etc. (identity integrity, not errors)
- These are a different concept (data quality) and have their own resolution workflow

**Tests:** update scheduler unit tests to assert report_error is called on failure paths

---

## Open Questions (Decide Before Phase 6.1)

1. **Routing cache invalidation** — use a simple TTL (5 min) or hook into the admin PATCH endpoint to flush immediately? TTL is simpler and safe enough.
2. **Discord channel** — always the audit channel, or should routing rules also support per-issue-type channel overrides? Start with audit channel only; add channel overrides later if needed.
3. **Bot availability** — `report_error` is called from scheduler (has bot) and from admin routes (no bot). Solution: pass `bot=None` when unavailable; `discord_sink` is a no-op when bot is None, and `dest_discord=True` errors where bot is None are flagged in the next scheduler run via the `first_notified_discord_at IS NULL` check.
4. **`common.error_routing` seeding** — seed with sensible defaults in migration; admin can adjust from there.

---

## Key Files (Read First in Each Phase)

- `src/sv_common/guild_sync/reporter.py` — existing Discord reporting patterns
- `src/sv_common/guild_sync/integrity_checker.py` — existing `_upsert_issue` + `make_issue_hash`
- `src/sv_common/guild_sync/scheduler.py` — where most errors currently happen; has `self._get_audit_channel()` and `self.db_pool`
- `src/sv_common/discord/channels.py` — `post_embed_to_channel` helper
- `src/sv_common/config_cache.py` — pattern for in-process caching
- `alembic/versions/` — latest migration for numbering (currently 0041)
- `TESTING.md` — test conventions

---

## Acceptance Criteria (Full Phase 6)

- [ ] `sv_common.errors.report_error(...)` callable from any module with pool + optional bot
- [ ] Routing rules stored in DB, editable via admin UI, cached in-process
- [ ] `common.error_log` correctly deduplicates: same hash = increment count, not new row
- [ ] Resolution resets the record: next occurrence is a fresh first_occurrence
- [ ] Discord only pinged on first occurrence for `discord_on_first_occurrence_only=TRUE` rules
- [ ] Weekly digest posts Sunday 8 AM UTC if any unresolved errors exist
- [ ] All scheduler error paths use `report_error` (no silent swallows)
- [ ] Admin can see, filter, and manually resolve errors at `/admin/error-routing`
- [ ] No breaking changes to `guild_identity.audit_issues` (integrity checker unaffected)
