# Phase 1.7 — Daily BIS Updates

> **Branch:** `feature/daily-bis-updates`
> **Migrations:** 1.7-A introduces the only migrations; subsequent phases are code-only
> **Status:** Phase 1.7-E COMPLETE — branch pushed, deployed to dev

---

## Overview

Automates the BIS data pipeline to run daily without manual admin intervention. Currently, all BIS scraping and enrichment rebuilds are admin-triggered only. This phase adds:

1. A scheduled daily scrape that is smart about *when* to hit each source
2. Content-hash deduplication so landing data only grows when pages actually change
3. A patch-signal mechanism that detects new WoW content and temporarily increases check frequency
4. Delta tracking: what changed in enrichment before vs. after each rebuild
5. A daily email to the guild owner summarizing what changed (or confirming all is quiet)
6. Admin UI visibility into run history, patch signal state, and per-target suppression

---

## Design Decisions

### Scrape frequency by source

- **u.gg**: always daily — it's a player-data aggregation site that updates continuously
- **Icy Veins, Method.gg, Wowhead**: adaptive backoff — checked frequently after a patch, quiet between them
- **Backoff schedule** (per target, consecutive no-change checks): 1 → 3 → 6 → 12 → 14 days (double each time, cap at 14)
- **Patch signal reset**: when new WoW encounters are detected, all guide targets reset to 1-day interval

### Patch signal

- An **hourly lightweight probe** queries the Blizzard journal API for the current encounter count and compares to `site_config.bis_encounter_count` (cached baseline)
- If the count increases, all non-u.gg scrape targets have `next_check_at` reset to now and `check_interval_days` reset to 1
- The probe does **not** increase the full Blizzard sync frequency — the existing 4x/day sync is sufficient for character/equipment data
- The probe uses `landing.blizzard_journal_encounters` row count as a cheap local signal before hitting the API; if the local count already matches what we last cached, skip the API call entirely

### Content hashing

- `landing.bis_scrape_raw` is append-only (no kill-and-fill)
- Each `sync_target()` call computes `SHA-256(content)` in Python before writing
- If the hash matches the most recent stored row for that target: skip INSERT, just update `last_fetched` + `status` on `config.bis_scrape_targets`
- This keeps storage nearly flat week-to-week; only patch weeks generate new rows
- No pruning table is needed — hash dedup naturally limits accumulation

### Delta tracking

- Before calling `rebuild_bis_from_landing()` (which does TRUNCATE), snapshot the current `enrichment.bis_entries` item IDs per (source, spec) into a temp structure in Python
- After rebuild, compare — compute added and removed item sets
- Store counts + full item-level JSONB in `landing.bis_daily_runs`
- Item names resolved at snapshot time via JOIN to `landing.blizzard_items` (since enrichment.items gets truncated)

### Email reports

- Always send after daily job completes — even if nothing changed, confirmation that the job ran is valuable
- "No changes" email is brief; change emails show grouped deltas by spec
- Targets with `is_active = FALSE` are excluded from failure noise in the email
- SMTP config stored encrypted in `common.site_config`; recipient stored as `bis_report_email`
- New `sv_common/email.py` module with async send function using `aiosmtplib`

---

## New Schema Objects

### `config.bis_scrape_targets` additions (migration 1.7-A)

```sql
ALTER TABLE config.bis_scrape_targets
    ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN check_interval_days SMALLINT NOT NULL DEFAULT 3,
    ADD COLUMN next_check_at TIMESTAMPTZ;

-- Backfill: schedule next check based on last_fetched
UPDATE config.bis_scrape_targets
    SET next_check_at = COALESCE(last_fetched, NOW()) + (check_interval_days || ' days')::INTERVAL
    WHERE next_check_at IS NULL;
```

### `landing.bis_scrape_raw` addition (migration 1.7-A)

```sql
ALTER TABLE landing.bis_scrape_raw
    ADD COLUMN content_hash VARCHAR(64);

-- Backfill: compute SHA-256 of existing content rows in Python migration script
-- (hashlib.sha256(content.encode()).hexdigest() for each row)
```

### `landing.bis_daily_runs` (new table, migration 1.7-A)

```sql
CREATE TABLE landing.bis_daily_runs (
    id               SERIAL PRIMARY KEY,
    run_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggered_by     VARCHAR(20) NOT NULL DEFAULT 'scheduled',  -- 'scheduled' | 'manual'
    patch_signal     BOOLEAN NOT NULL DEFAULT FALSE,            -- was encounter count higher this run?
    targets_checked  INTEGER NOT NULL DEFAULT 0,
    targets_changed  INTEGER NOT NULL DEFAULT 0,                -- hash changed = new data
    targets_unchanged INTEGER NOT NULL DEFAULT 0,
    targets_failed   INTEGER NOT NULL DEFAULT 0,
    targets_skipped  INTEGER NOT NULL DEFAULT 0,                -- not yet due (backoff)
    bis_entries_before INTEGER NOT NULL DEFAULT 0,
    bis_entries_after  INTEGER NOT NULL DEFAULT 0,
    trinket_ratings_before INTEGER NOT NULL DEFAULT 0,
    trinket_ratings_after  INTEGER NOT NULL DEFAULT 0,
    delta_added      JSONB,   -- [{spec_name, source_name, slot, blizzard_item_id, item_name}]
    delta_removed    JSONB,   -- same structure
    duration_seconds NUMERIC(8,2),
    email_sent_at    TIMESTAMPTZ,
    notes            TEXT     -- warnings or non-fatal errors during the run
);
```

### `common.site_config` additions (migration 1.7-A)

```sql
ALTER TABLE common.site_config
    ADD COLUMN bis_encounter_count    INTEGER,        -- cached baseline for patch probe
    ADD COLUMN bis_report_email       VARCHAR(255),   -- recipient for daily BIS email
    ADD COLUMN smtp_host              VARCHAR(255),
    ADD COLUMN smtp_port              SMALLINT DEFAULT 587,
    ADD COLUMN smtp_user              VARCHAR(255),
    ADD COLUMN smtp_password_encrypted TEXT,          -- Fernet-encrypted, same key as bnet tokens
    ADD COLUMN smtp_from_address      VARCHAR(255);
```

---

## Sub-Phase Breakdown

---

### Phase 1.7-A — Schema Foundations

**Scope:** Migration + backfills. No behavior changes yet. Tests confirm schema state.

**Migration number:** next available (165 or higher depending on what lands before this)

**Steps:**

1. Write Alembic migration:
   - Add `is_active`, `check_interval_days`, `next_check_at` to `config.bis_scrape_targets`
   - Add `content_hash` to `landing.bis_scrape_raw`
   - Create `landing.bis_daily_runs`
   - Add 7 SMTP/email columns to `common.site_config`
   - Backfill `next_check_at` on existing targets (formula: `COALESCE(last_fetched, NOW()) + interval`)
   - Backfill `content_hash` on existing `bis_scrape_raw` rows — Python loop in migration, `hashlib.sha256(row.content.encode()).hexdigest()`
   - Set `is_active = FALSE` for any targets with `status = 'failed'` AND `items_found = 0` AND `last_fetched IS NOT NULL` — these are known-dead targets (e.g., Feral Druid/Method); leave them in DB but silenced

2. Update `SiteConfig` dataclass / model in `sv_common/db/models.py` to include new columns

3. Add getters to `config_cache.py`:
   - `get_smtp_config() → SmtpConfig | None`
   - `get_bis_report_email() → str | None`
   - `get_bis_encounter_baseline() → int | None`

4. Tests:
   - Migration applies cleanly
   - Backfill: all pre-existing `bis_scrape_raw` rows have non-null `content_hash`
   - Backfill: all `bis_scrape_targets` rows have non-null `next_check_at`
   - `SiteConfig` model round-trips new columns

**Acceptance criteria:** Migration runs on dev, all tests pass, no behavior change in existing BIS sync.

---

### Phase 1.7-B — Hourly Encounter Probe + Scheduler Wiring

**Scope:** Two new scheduler jobs wired into `GuildSyncScheduler`. No scrape logic changes yet.

**Steps:**

1. Add `run_encounter_probe()` to `scheduler.py`:
   - Runs hourly at `:05` past each hour (CronTrigger, `minute=5`)
   - Queries `SELECT COUNT(*) FROM landing.blizzard_journal_encounters WHERE instance_type = 'raid'`
   - Compares to `site_config.bis_encounter_count` (loaded from config cache)
   - If count is higher:
     - Log as info: "Patch signal: N new raid encounters detected"
     - UPDATE `config.bis_scrape_targets SET check_interval_days = 1, next_check_at = NOW()` WHERE the source is not u.gg (i.e., `source_id != <ugg_source_id>`) AND `is_active = TRUE`
     - UPDATE `site_config SET bis_encounter_count = <new_count>`
     - Invalidate config cache for `bis_encounter_count`
   - If count is same or lower: no-op, no log spam
   - Wraps in try/except; errors logged but do not crash the scheduler

2. Add `run_bis_daily_sync()` stub to `scheduler.py`:
   - Runs daily at 04:00 UTC (CronTrigger, `hour=4, minute=0`)
   - For now: just logs "BIS daily sync job triggered — not yet implemented"
   - Will be filled out in Phase 1.7-C and 1.7-D

3. Register both jobs in `GuildSyncScheduler.start()` alongside existing jobs

4. Tests:
   - Encounter probe: when DB count > cached baseline → targets reset, site_config updated
   - Encounter probe: when DB count == cached baseline → no-op
   - Encounter probe: exception does not propagate to scheduler loop
   - Daily sync stub: job is registered, fires without error

**Acceptance criteria:** Both jobs appear in scheduler on startup. Encounter probe correctly detects a synthetic count increase in tests. No regressions in existing scheduler jobs.

---

### Phase 1.7-C — Hash Dedup + Adaptive Backoff in sync_target()

**Scope:** Modify `sync_target()` in `bis_sync.py` to compute content hash, skip writes on no change, and update backoff fields. Fill in the scrape loop of `run_bis_daily_sync()`.

**Steps:**

1. Modify `sync_target()` in `bis_sync.py`:
   - After fetching content, compute `new_hash = hashlib.sha256(content.encode()).hexdigest()`
   - Query: `SELECT content_hash FROM landing.bis_scrape_raw WHERE target_id = $1 ORDER BY fetched_at DESC LIMIT 1`
   - If `new_hash == stored_hash`: skip INSERT to `bis_scrape_raw`; UPDATE `config.bis_scrape_targets` with `last_fetched = NOW()`, `status = 'unchanged'`; return `{status: "unchanged", items_found: <existing>}`
   - If hash differs (or no prior row): INSERT to `bis_scrape_raw` with `content_hash = new_hash` as before; return `{status: "success", ...}`
   - Add `content_hash` to all INSERT statements that write to `landing.bis_scrape_raw`

2. Add `_update_target_backoff(conn, target_id, changed: bool, source_id: int, ugg_source_id: int)` helper:
   - If source is u.gg: always set `check_interval_days = 1`, `next_check_at = NOW() + 1 day` — no backoff
   - If changed: `check_interval_days = 1`, `next_check_at = NOW() + 1 day`
   - If unchanged: `new_interval = min(current_interval * 2, 14)`, `next_check_at = NOW() + new_interval days`
   - Called at end of `sync_target()` in both hash-match and hash-differ branches

3. Implement scrape loop in `run_bis_daily_sync()`:
   - Fetch all targets: `SELECT * FROM config.bis_scrape_targets WHERE is_active = TRUE AND (next_check_at IS NULL OR next_check_at <= NOW()) ORDER BY source_id, spec_id`
   - For each due target: call `sync_target(pool, target)` with 2s delay between calls within the same source (rate limiting; skip delay between sources)
   - Track per-run counts: `targets_checked`, `targets_changed`, `targets_unchanged`, `targets_failed`
   - For targets not yet due: increment `targets_skipped` count (no fetch)
   - At end of scrape loop: INSERT a partial `bis_daily_runs` row with scrape stats (enrichment stats filled in Phase 1.7-D)

4. Tests:
   - `sync_target()` with matching hash: no new `bis_scrape_raw` row, backoff fields updated
   - `sync_target()` with differing hash: new row inserted, `check_interval_days` reset to 1
   - u.gg source: always `check_interval_days = 1` regardless of hash match
   - Backoff doubling: 3 → 6 → 12 → 14 (capped)
   - `is_active = FALSE` targets are never fetched
   - Targets with `next_check_at > NOW()` are skipped

**Acceptance criteria:** Running `sync_target()` twice in a row for an unchanged page produces only one `bis_scrape_raw` row. Backoff fields update correctly. All existing sync tests continue to pass.

---

### Phase 1.7-D — Delta Capture + Enrichment Integration

**Scope:** Wire enrichment rebuild into the daily job. Capture before/after delta. Persist full run record to `landing.bis_daily_runs`.

**Steps:**

1. Add `_snapshot_bis_entries(conn) → dict` helper in `bis_sync.py`:
   - `SELECT be.blizzard_item_id, be.spec_id, be.source_id, be.slot, bi.name FROM enrichment.bis_entries be JOIN landing.blizzard_items bi ON bi.id = be.blizzard_item_id`
   - Returns `{(spec_id, source_id, slot, blizzard_item_id): item_name}` dict
   - Called **before** `rebuild_bis_from_landing()` (before TRUNCATE destroys the data)

2. Add `_compute_delta(before: dict, after: dict) → tuple[list, list]` pure function:
   - `added = [items in after not in before]`
   - `removed = [items in before not in after]`
   - Each item: `{spec_id, source_id, slot, blizzard_item_id, item_name}`
   - For `after` snapshot: item names come from `landing.blizzard_items` JOIN (enrichment.items is rebuilt, so it's available; landing.blizzard_items is stable)

3. Complete `run_bis_daily_sync()`:
   - After scrape loop completes: `before_snapshot = await _snapshot_bis_entries(conn)`
   - `before_bis_count = len(before_snapshot)`
   - `before_trinket_count = SELECT COUNT(*) FROM enrichment.trinket_ratings`
   - Call `rebuild_bis_from_landing(pool)` (existing function, unchanged)
   - Call `rebuild_trinket_ratings_from_landing(pool)` (existing function, unchanged)
   - Call `rebuild_item_popularity_from_landing(pool)` (existing function, unchanged)
   - `after_snapshot = await _snapshot_bis_entries(conn)` using `landing.blizzard_items` for names
   - `added, removed = _compute_delta(before_snapshot, after_snapshot)`
   - `after_bis_count = SELECT COUNT(*) FROM enrichment.bis_entries`
   - `after_trinket_count = SELECT COUNT(*) FROM enrichment.trinket_ratings`
   - INSERT / UPDATE `landing.bis_daily_runs` with all counts + JSONB deltas + `duration_seconds`

4. Tests:
   - `_snapshot_bis_entries`: returns correct structure from mock enrichment data
   - `_compute_delta`: correctly identifies added + removed sets
   - `_compute_delta`: unchanged items in neither list
   - Full daily job integration test (using test DB): creates `bis_daily_runs` row with correct counts
   - Delta JSONB is valid and parseable

**Acceptance criteria:** After running the daily job end-to-end on dev, `landing.bis_daily_runs` contains a row with non-null `delta_added`, `delta_removed`, `bis_entries_before`, `bis_entries_after`, and `duration_seconds`.

---

### Phase 1.7-E — Email Report + SMTP Infrastructure

**Scope:** New `sv_common/email.py` module, HTML email template, scheduler integration, SMTP config in admin UI.

**Dependencies:** Requires `aiosmtplib` added to `requirements.txt`.

**Steps:**

1. Add `aiosmtplib` to `requirements.txt`

2. Create `src/sv_common/email.py`:
   ```python
   async def send_email(
       smtp_config: SmtpConfig,
       to: str,
       subject: str,
       html_body: str,
   ) -> None
   ```
   - Uses `aiosmtplib.send()` with STARTTLS on port 587 (or SSL on 465 if `smtp_port == 465`)
   - `SmtpConfig` dataclass: `host, port, user, password, from_address`
   - Raises `EmailError` on failure (caught by caller, logged, stored in `bis_daily_runs.notes`)

3. Create `src/sv_common/guild_sync/bis_email.py`:
   - `compose_bis_report(run: BisDailyRun, site_config: SiteConfig) → tuple[str, str]` (subject, html)
   - Subject: `[PATT BIS] Daily Update — {date} — {N changes}` or `[PATT BIS] Daily Update — {date} — No changes`
   - HTML sections (each section omitted if empty):
     - **Header**: run timestamp, duration, targets checked/changed/failed
     - **Patch Signal** (if `run.patch_signal = True`): "New WoW content detected — guide sources reset to daily monitoring"
     - **Guide Updates** (if `targets_changed > 0`): table listing source + spec combinations where content changed
     - **New BIS Items** (if `delta_added`): grouped by spec, `slot → item name`
     - **Removed BIS Items** (if `delta_removed`): same format
     - **Target Failures** (if any newly-failed targets): list of source + spec; note excludes `is_active = FALSE` targets
     - **Footer**: "No changes detected — pipeline healthy" (if nothing to report) + link to `/admin/gear-plan`
   - Plain-text fallback included as multipart/alternative

4. Wire email send at end of `run_bis_daily_sync()`:
   - Load SMTP config from config cache
   - If `smtp_config` and `bis_report_email` are set: call `compose_bis_report()` then `send_email()`
   - On success: UPDATE `bis_daily_runs.email_sent_at = NOW()`
   - On failure: log error, store in `bis_daily_runs.notes` — do not crash the job

5. Add SMTP config to **Admin → Site Config** page:
   - New "Email / Notifications" section
   - Fields: SMTP Host, Port, Username, Password (write-only input), From Address, BIS Report Email
   - Password stored via `crypto.encrypt()` (same Fernet key as BNet token encryption)
   - `PATCH /api/v1/admin/site-config` already handles arbitrary column updates — add new fields to the allowed list

6. Tests:
   - `compose_bis_report()`: subject line correct for change vs. no-change runs
   - `compose_bis_report()`: delta_added items appear in HTML, delta_removed items appear
   - `compose_bis_report()`: inactive targets excluded from failure section
   - `send_email()`: mock SMTP server receives message (use `aiosmtplib` test helpers)
   - SMTP config round-trip: password encrypted on save, decrypted on load

**Acceptance criteria:** With SMTP configured on dev, the daily job sends an email. An "all quiet" run still sends a brief confirmation email.

---

### Phase 1.7-F — Admin UI

**Scope:** Visibility and control surface in the BIS admin panel. No new migrations.

**Steps:**

1. Add **"Scrape Targets"** expandable section to `/admin/gear-plan`:
   - Table: source, spec, content_type, status, items_found, last_fetched, next_check_at, check_interval_days, is_active toggle
   - `is_active` toggle calls `PATCH /api/v1/admin/bis/targets/{target_id}` (new endpoint)
   - Rows with `is_active = FALSE` shown with muted styling
   - "Re-activate All" button sets `is_active = TRUE` + resets `next_check_at = NOW()` for all targets

2. Add **"Daily Run History"** card to the top of `/admin/gear-plan`:
   - Shows last 10 runs from `landing.bis_daily_runs`
   - Each row: run timestamp, triggered_by, targets changed/failed, bis_entries before→after, duration, email sent indicator
   - Expandable row: full delta_added/delta_removed item lists

3. Add **"Patch Signal"** indicator in BIS admin header:
   - Green dot + "Monitoring" if any guide targets have `check_interval_days = 1` AND `last_fetched > NOW() - 2 days`
   - Grey dot + "Quiet" otherwise
   - Shows last encounter baseline count + last probe time

4. Add **"Run Daily Job Now"** button in BIS admin:
   - `POST /api/v1/admin/bis/run-daily-sync` (new endpoint, GL only)
   - Calls `asyncio.create_task(run_bis_daily_sync(pool, triggered_by='manual'))`
   - Returns `{"ok": true, "message": "Daily sync started"}` immediately
   - Result visible in Run History card after refresh

5. New endpoints needed:
   - `PATCH /api/v1/admin/bis/targets/{target_id}` — update `is_active`, `check_interval_days`, `next_check_at`
   - `POST /api/v1/admin/bis/targets/reactivate-all` — bulk reset
   - `POST /api/v1/admin/bis/run-daily-sync` — manual trigger
   - `GET /api/v1/admin/bis/daily-runs?limit=10` — run history
   - `GET /api/v1/admin/bis/patch-signal` — current patch signal state

6. Tests:
   - `PATCH /targets/{id}`: `is_active = FALSE` correctly silences the target
   - `POST /run-daily-sync`: returns 200 immediately, task created
   - `GET /daily-runs`: returns correct structure from `bis_daily_runs`
   - Patch signal endpoint: returns correct state based on target `check_interval_days` + `last_fetched`

**Acceptance criteria:** Can toggle `is_active` on Feral Druid/Method target from the admin UI. Run history shows last N jobs. Manual trigger fires the daily job and the result appears in history within ~5 minutes.

---

## Migration Inventory

| Migration | Phase | Contents |
|-----------|-------|----------|
| 0165 (or next) | 1.7-A | `bis_scrape_targets` additions; `bis_scrape_raw.content_hash`; `landing.bis_daily_runs`; `site_config` SMTP/email additions; backfills |

All other phases: code-only, no migrations.

---

## New Files

| File | Phase | Purpose |
|------|-------|---------|
| `src/sv_common/email.py` | 1.7-E | Async SMTP send via aiosmtplib |
| `src/sv_common/guild_sync/bis_email.py` | 1.7-E | BIS delta email composer |
| `tests/unit/test_bis_daily_sync.py` | 1.7-C/D | Daily sync + delta capture tests |
| `tests/unit/test_bis_email.py` | 1.7-E | Email composition tests |

---

## Modified Files

| File | Phases | Changes |
|------|--------|---------|
| `src/sv_common/guild_sync/scheduler.py` | 1.7-B/C/D/E | Two new jobs; `run_encounter_probe()`; `run_bis_daily_sync()` |
| `src/sv_common/guild_sync/bis_sync.py` | 1.7-C/D | Hash dedup in `sync_target()`; `_update_target_backoff()`; `_snapshot_bis_entries()`; `_compute_delta()` |
| `src/sv_common/db/models.py` | 1.7-A | `SiteConfig` new fields |
| `src/sv_common/config_cache.py` | 1.7-A | New getters for SMTP + email config |
| `src/guild_portal/api/bis_routes.py` | 1.7-F | New endpoints for target management, run history, manual trigger |
| `src/guild_portal/templates/admin/gear_plan_admin.html` | 1.7-F | Run history card, patch signal, scrape targets section, manual trigger button |
| `src/guild_portal/static/js/gear_plan_admin.js` | 1.7-F | Run history rendering, is_active toggle, manual trigger |
| `src/guild_portal/templates/admin/site_config.html` | 1.7-E | SMTP config section |
| `requirements.txt` | 1.7-E | Add `aiosmtplib` |

---

## Deployment Notes

- **Migration 0165** runs automatically on container startup; backfills are safe (UPDATE + Python loop, no DROP or schema-breaking changes)
- After first deploy: set SMTP config + `bis_report_email` via Admin → Site Config
- After setting SMTP: manually trigger "Run Daily Job Now" to validate email delivery before relying on the scheduler
- `bis_encounter_count` in site_config is populated on first hourly probe run; no manual seed needed
- Targets auto-silenced by migration (status=failed + items_found=0 + has been fetched) should be reviewed in admin before re-activating — they may need updated URLs

---

## Testing Strategy

- All phases include unit tests using the existing `pytest + pytest-asyncio` setup
- Phases 1.7-C and 1.7-D require test DB fixtures for `bis_scrape_targets`, `bis_scrape_raw`, `enrichment.bis_entries`
- Email tests use mock SMTP (no real sends in CI)
- No integration tests against live third-party sites
- Target suite-wide count: approximately 1,700+ tests after all phases complete (currently 1,648)
