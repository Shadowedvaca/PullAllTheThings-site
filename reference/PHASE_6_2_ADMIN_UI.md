# Phase 6.2 — Error Routing Config & Admin UI

> **Branch:** `phase-6-error-handling` (continue from Phase 6.1)
> **Migration:** 0043
> **Depends on:** Phase 6.1 complete (`common.error_log` exists, `sv_common.errors` works)
> **Produces:** Routing config table, admin page to view/manage errors, HTTP API

---

## Goal

Give officers visibility into catalogued errors and control over how they're routed.
Two deliverables:

1. **`common.error_routing` table** — per-issue-type rules controlling which destinations
   (audit log, Discord) receive an event and under what conditions. Owned by `guild_portal`.

2. **`/admin/error-routing` page** — view unresolved errors, manage routing rules, manually
   resolve issues.

Phase 6.3 will read `error_routing` to decide whether to post to Discord. This phase
just builds the config store and UI.

---

## Prerequisites

- Phase 6.1 complete and merged
- `common.error_log` table exists (migration 0042)
- `sv_common.errors.report_error()`, `resolve_issue()`, `get_unresolved()` all working
- Familiar with admin page pattern: pages extend `base_admin.html`, routes in
  `src/guild_portal/pages/admin_pages.py`, API routes in `src/guild_portal/api/admin_routes.py`
- Familiar with screen permission gating: `_require_screen(screen_key, request, db)`

---

## Key Files to Read Before Starting

- `src/guild_portal/pages/admin_pages.py` — all admin page routes; add new route here
- `src/guild_portal/api/admin_routes.py` — Officer+ API routes; add new endpoints here
- `src/guild_portal/templates/admin/users.html` — good template reference (table + inline actions)
- `src/guild_portal/templates/base_admin.html` — admin shell; sidebar nav lives here
- `alembic/versions/` — copy migration pattern; next number is 0043

---

## Database Migration: 0043

### New Table: `common.error_routing`

```sql
CREATE TABLE common.error_routing (
    id           SERIAL PRIMARY KEY,
    issue_type   VARCHAR(80),   -- NULL = wildcard, matches all issue types
    min_severity VARCHAR(10)  NOT NULL DEFAULT 'warning',
    -- 'info' | 'warning' | 'critical'
    -- Rule applies to events at this severity OR HIGHER
    dest_audit_log  BOOLEAN NOT NULL DEFAULT TRUE,
    dest_discord    BOOLEAN NOT NULL DEFAULT TRUE,
    first_only      BOOLEAN NOT NULL DEFAULT TRUE,
    -- TRUE:  only route to dest_discord on is_first_occurrence (suppress repeat noise)
    -- FALSE: route to dest_discord on every occurrence
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    notes        TEXT,
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default rules (conservative: Discord only on first occurrence for warnings)
INSERT INTO common.error_routing
    (issue_type, min_severity, dest_audit_log, dest_discord, first_only, notes)
VALUES
    (NULL, 'critical', TRUE, TRUE,  FALSE, 'Critical: always everywhere, every time'),
    (NULL, 'warning',  TRUE, TRUE,  TRUE,  'Warning: audit log + Discord, first occurrence only'),
    (NULL, 'info',     TRUE, FALSE, TRUE,  'Info: audit log only, no Discord'),
    ('bnet_token_expired', 'warning', TRUE, TRUE, TRUE,
        'BNet token expired — player must re-link Battle.net'),
    ('bnet_sync_error',    'warning', TRUE, TRUE, TRUE,
        'BNet character sync error');

-- Seed screen permission for the new admin page
INSERT INTO common.screen_permissions (screen_key, min_rank_level)
VALUES ('error_routing', 4)  -- Officer+
ON CONFLICT (screen_key) DO NOTHING;
```

### Routing Resolution Logic

Rules are evaluated in specificity order: **exact `issue_type` match beats wildcard.**
Within the same specificity, the rule with the **highest `min_severity`** that still
matches the event severity wins.

```
Given: issue_type="bnet_token_expired", severity="warning"

1. Find all enabled rules where issue_type = "bnet_token_expired" OR issue_type IS NULL
2. Keep only rules where min_severity <= event_severity (info≤warning≤critical)
3. Prefer exact issue_type match over wildcard
4. Among ties, prefer highest min_severity (most specific)
5. Apply: dest_audit_log, dest_discord, first_only
```

If no rule matches: default to `dest_audit_log=TRUE, dest_discord=FALSE` (safe fallback —
always log, never spam Discord).

---

## guild_portal Routing Helper

### New File: `src/guild_portal/services/error_routing.py`

```python
"""
Routing config cache for sv_common error events.

Loads common.error_routing from DB, caches in-process with a 5-minute TTL.
Call invalidate_cache() from the admin PATCH endpoint to flush immediately.
"""
```

**Functions:**

```python
async def get_routing_rule(
    pool: asyncpg.Pool,
    issue_type: str,
    severity: str,
) -> dict:
    """
    Return the resolved routing rule for a given issue_type + severity.
    Always returns a dict — falls back to safe defaults if no rule matches.

    Return shape:
    {
        "dest_audit_log": bool,
        "dest_discord":   bool,
        "first_only":     bool,
    }
    """

def invalidate_cache() -> None:
    """Force next call to get_routing_rule to reload from DB."""
```

**Cache implementation:** module-level `_cache: list[dict] | None = None` and
`_cache_loaded_at: datetime | None = None`. If cache is older than 5 minutes or None,
reload from DB. This is the same pattern as `sv_common/config_cache.py`.

---

## API Endpoints

Add to `src/guild_portal/api/admin_routes.py` (Officer+ auth already applied by router).

### `GET /api/v1/admin/errors/unresolved`

Returns open error records from `common.error_log`. Delegates to
`sv_common.errors.get_unresolved(pool, ...)`.

Query params:
- `severity` — filter to this level or above (`info` | `warning` | `critical`)
- `issue_type` — exact match
- `source_module` — exact match
- `limit` — default 100
- `offset` — default 0

Response:
```json
{
    "ok": true,
    "data": {
        "errors": [
            {
                "id": 12,
                "issue_type": "bnet_token_expired",
                "severity": "warning",
                "source_module": "scheduler",
                "identifier": "sevin1979#1865",
                "summary": "Battle.net token expired for sevin1979#1865 — player must re-link",
                "occurrence_count": 3,
                "first_occurred_at": "2026-03-15T03:15:00Z",
                "last_occurred_at": "2026-03-17T03:15:00Z"
            }
        ],
        "total": 1
    }
}
```

### `GET /api/v1/admin/errors/routing`

Returns all rows from `common.error_routing`, ordered by `issue_type NULLS LAST, min_severity`.

Response: `{"ok": true, "data": {"rules": [ {...} ]}}`

### `PATCH /api/v1/admin/errors/routing/{rule_id}`

Update a routing rule. Accepts any subset of:
```json
{"dest_audit_log": true, "dest_discord": false, "first_only": true, "enabled": true, "notes": "..."}
```
Calls `invalidate_cache()` after saving.
Returns updated rule: `{"ok": true, "data": {...}}`.
Returns 404 if rule not found.

### `POST /api/v1/admin/errors/{error_id}/resolve`

Manually resolve a specific open error by its `id`. Sets `resolved_at = NOW()`,
`resolved_by = "officer:{admin_display_name}"`.
Returns `{"ok": true, "data": {"resolved": true}}`.
Returns 404 if not found or already resolved.

---

## Admin Page

### Route

Add to `src/guild_portal/pages/admin_pages.py`:

```python
@router.get("/error-routing", response_class=HTMLResponse)
async def admin_error_routing(request: Request, db: AsyncSession = Depends(get_db)):
    player = await _require_screen("error_routing", request, db)
    if player is None:
        return _redirect_login("/admin/error-routing")
    ...
```

Page loads both unresolved errors and routing rules and passes to template.
Use `request.app.state.guild_sync_pool` to call `get_unresolved(pool)`.

### Template: `src/guild_portal/templates/admin/error_routing.html`

Extends `base_admin.html`.

**Layout: two sections stacked vertically.**

---

#### Section 1: Unresolved Errors

Header: "Open Errors" + count badge. Refresh button (reloads page).

Table columns: `Severity` | `Type` | `Module` | `Identifier` | `Summary` | `Count` | `First Seen` | `Last Seen` | `Actions`

- Severity shown as a colored badge: red=critical, amber=warning, blue=info
- `occurrence_count > 1` shown as `N×` in a muted badge
- `first_occurred_at` / `last_occurred_at` in relative time (`3 days ago`) with ISO tooltip
- Actions column: "Resolve" button — calls `POST /api/v1/admin/errors/{id}/resolve`,
  removes row on success, shows toast

Empty state: "No open errors — all clear."

---

#### Section 2: Routing Rules

Header: "Routing Configuration" with a muted note: "Controls how errors are directed to the
audit log and Discord. More specific rules (exact issue type) override wildcard rules."

Table columns: `Issue Type` | `Min Severity` | `Audit Log` | `Discord` | `First Only` | `Enabled` | `Notes`

- `Issue Type` column: show `—` (all types) for wildcard rows, else the type string
- Toggle columns (`Audit Log`, `Discord`, `First Only`, `Enabled`): render as toggle
  switches. On change, PATCH the rule immediately, show toast on success/failure.
- `Notes`: plain text, truncated at 60 chars with title tooltip for full text

No add/delete rule from UI in Phase 6.2 — rules are seeded by migration, managed by DB
if needed. This can be added later.

---

### Sidebar Navigation

Add entry to `base_admin.html` sidebar under the Data Quality section:

```html
{ "label": "Error Routing", "url": "/admin/error-routing", "screen_key": "error_routing" }
```

---

## CSS & JS Notes

Follow the existing admin page style (`.ua-*` classes in `users.html`, `.wr-*` in
warcraft_logs admin, etc.). Use a new prefix `er-` for this page.

Severity badge colors:
- `critical` → `var(--color-danger)` (#f87171)
- `warning`  → `var(--color-warning)` (#fbbf24)
- `info`     → `var(--color-text-muted)` (no strong color)

Toggle switch: use a `<button role="switch" aria-checked="true/false">` styled with CSS.
On click, fire PATCH, update `aria-checked` and visual state on success.

---

## Tests

### `tests/unit/test_error_routing.py`

**`test_routing_resolution_exact_match_wins`**
Rules: wildcard warning→discord=TRUE, exact `bnet_token_expired` warning→discord=FALSE.
`get_routing_rule(pool, "bnet_token_expired", "warning")` returns `dest_discord=False`.

**`test_routing_resolution_wildcard_fallback`**
No exact match for `some_new_type`. Wildcard rule applies.

**`test_routing_resolution_severity_filter`**
Rule has `min_severity="warning"`. Querying with `severity="info"` should NOT match it.
Querying with `severity="warning"` or `"critical"` should match.

**`test_routing_resolution_no_match_returns_safe_default`**
No rules in DB for this type/severity. Returns `{"dest_audit_log": True, "dest_discord": False, "first_only": True}`.

**`test_routing_resolution_disabled_rule_ignored`**
Rule exists but `enabled=FALSE`. Falls back to wildcard or safe default.

**`test_cache_invalidation`**
After `invalidate_cache()`, next call reloads from DB (mock called twice, not once).

### `tests/unit/test_admin_routes_errors.py`

**`test_get_unresolved_returns_list`**
Mock `get_unresolved` returns two records. Endpoint returns `{"ok": true, "data": {"errors": [...], "total": 2}}`.

**`test_resolve_error_marks_resolved`**
POST to `/errors/{id}/resolve` with a valid open error: returns 200 `{"ok": true}`.

**`test_resolve_error_404_on_missing`**
POST to `/errors/9999/resolve`: returns 404.

---

## Deliverables Checklist

- [ ] Migration `0043_error_routing.py` — `common.error_routing` + seed rules + screen permission
- [ ] `src/guild_portal/services/error_routing.py` — routing cache helper
- [ ] API endpoints in `admin_routes.py`:
  - [ ] `GET /api/v1/admin/errors/unresolved`
  - [ ] `GET /api/v1/admin/errors/routing`
  - [ ] `PATCH /api/v1/admin/errors/routing/{rule_id}`
  - [ ] `POST /api/v1/admin/errors/{error_id}/resolve`
- [ ] `src/guild_portal/pages/admin_pages.py` — `GET /admin/error-routing` route
- [ ] `src/guild_portal/templates/admin/error_routing.html` — full page
- [ ] `base_admin.html` — sidebar entry added
- [ ] `tests/unit/test_error_routing.py` — routing resolution tests
- [ ] `tests/unit/test_admin_routes_errors.py` — API endpoint tests
- [ ] `pytest tests/unit/ -v` — all existing tests still pass

---

## What This Phase Does NOT Do

- No Discord notifications (Phase 6.3)
- No changes to any existing error callsite (Phase 6.4)
- `error_routing` rules can be viewed and toggled but no add/delete from UI
- Routing cache is used by Phase 6.3 — the helper exists but nothing calls it yet
