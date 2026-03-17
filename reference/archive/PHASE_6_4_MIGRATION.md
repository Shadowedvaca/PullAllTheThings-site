# Phase 6.4 — Migration of Existing Callsites

> **Branch:** `phase-6-error-handling` (continue from Phase 6.3)
> **Migration:** none
> **Depends on:** All of Phase 6.1–6.3 complete
> **Produces:** Every background job and admin-triggered operation surfaces errors through
> the new system. No more silent swallows.

---

## Goal

Replace all ad-hoc error handling throughout `sv_common` and `guild_portal` with
`report_error()` / `resolve_issue()`. After this phase, every error that happens in a
background job or admin action:

1. Is recorded in `common.error_log` (visible in `/admin/error-routing`)
2. Triggers an immediate Discord alert if routing config says so and it's a first occurrence
3. Appears in the Sunday weekly digest if still unresolved

---

## Prerequisites

- Phase 6.1: `sv_common.errors.report_error()`, `resolve_issue()`, `get_unresolved()` working
- Phase 6.2: `common.error_routing` seeded, `error_routing.get_routing_rule()` working
- Phase 6.3: `maybe_notify_discord()` working, `run_weekly_error_digest()` registered
- All unit tests from 6.1–6.3 passing

---

## Key Files to Read Before Starting

- `src/sv_common/guild_sync/scheduler.py` — primary target; most errors live here
- `src/sv_common/guild_sync/bnet_character_sync.py` — `_refresh_token` silent failure path
- `src/guild_portal/pages/admin_pages.py` — `admin_bnet_sync_user`, `admin_bnet_sync_all`
- `src/sv_common/errors/__init__.py` — `report_error`, `resolve_issue` signatures
- `src/guild_portal/services/error_routing.py` — `maybe_notify_discord` from Phase 6.3
- `src/sv_common/guild_sync/reporter.py` — `send_error` being REPLACED at scheduler
  callsites; keep the function, just stop calling it directly from these jobs

---

## What Changes and What Doesn't

### Changes: Error-Type Callsites

Background job failures, sync errors, token expiry — these become `report_error()` calls.

### Does NOT Change: Identity/Data Quality Issues

`guild_identity.audit_issues` and all code in `integrity_checker.py` is untouched.
`orphan_wow`, `orphan_discord`, `role_mismatch`, `stale_character`, `auto_link_suggestion` —
these are data quality observations, not errors. They have their own resolution workflow
on the `/admin/data-quality` page. Leave them exactly as they are.

---

## Callsite Migration Table

| Module | Location | Current behavior | New behavior |
|--------|----------|-----------------|--------------|
| `scheduler` | `run_bnet_character_refresh` — token None | `logger.warning` + skip | `report_error("bnet_token_expired", ...)` + `maybe_notify_discord(...)` |
| `scheduler` | `run_bnet_character_refresh` — sync exception | `logger.error` + count | `report_error("bnet_sync_error", ...)` + `maybe_notify_discord(...)` |
| `scheduler` | `run_bnet_character_refresh` — success per player | nothing | `resolve_issue("bnet_token_expired", identifier=battletag)` + `resolve_issue("bnet_sync_error", ...)` |
| `bnet_character_sync` | `_refresh_token` — no refresh token | `logger.warning` + return None | `report_error("bnet_token_expired", ...)` (pool passed in) |
| `bnet_character_sync` | `_refresh_token` — HTTP failure | `logger.error` + return None | `report_error("bnet_token_expired", ...)` |
| `scheduler` | `run_blizzard_sync` — exception | `send_error(channel, ...)` | `report_error("blizzard_sync_failed", ...)` + `maybe_notify_discord(...)` |
| `scheduler` | `run_crafting_sync` — exception | `send_error(channel, ...)` | `report_error("crafting_sync_failed", ...)` + `maybe_notify_discord(...)` |
| `scheduler` | `run_wcl_sync` — exception | `logger.error` | `report_error("wcl_sync_failed", ...)` + `maybe_notify_discord(...)` |
| `scheduler` | `run_attendance_processing` — exception | `logger.error` | `report_error("attendance_sync_failed", ...)` + `maybe_notify_discord(...)` |
| `scheduler` | `run_ah_sync` — exception | `logger.error` | `report_error("ah_sync_failed", ...)` + `maybe_notify_discord(...)` |
| `admin_pages` | `admin_bnet_sync_user` — token None | HTTP 422 only | `report_error("bnet_token_expired", ...)` + HTTP 422 |
| `admin_pages` | `admin_bnet_sync_user` — success | nothing | `resolve_issue("bnet_token_expired", ...)` + `resolve_issue("bnet_sync_error", ...)` |
| `admin_pages` | `admin_bnet_sync_all` — token None per player | HTTP count only | `report_error("bnet_token_expired", ...)` per player |
| `admin_pages` | `admin_bnet_sync_all` — sync exception per player | HTTP count only | `report_error("bnet_sync_error", ...)` per player |
| `admin_pages` | `admin_bnet_sync_all` — success per player | nothing | `resolve_issue(...)` per player |

---

## Migration Details

### 1. `bnet_character_sync._refresh_token`

**Problem:** Returns `None` silently on two failure paths — no refresh token available,
and HTTP request failure. Neither is surfaced anywhere.

**Change:** Pass `pool` into `_refresh_token` (it's already called from `get_valid_access_token`
which has pool). Call `report_error(...)` before returning `None`.

```python
# Existing signature:
async def _refresh_token(pool, player_id: int, row, now: datetime) -> str | None:

# Add at the "no refresh token" path:
from sv_common.errors import report_error
await report_error(
    pool,
    "bnet_token_expired",
    "warning",
    f"Battle.net token expired for player {player_id} — no refresh token stored. "
    f"Player must re-link their Battle.net account.",
    "bnet_character_sync",
    details={"player_id": player_id},
    identifier=str(player_id),
)
return None

# Add at the HTTP failure path:
await report_error(
    pool,
    "bnet_token_expired",
    "warning",
    f"Battle.net token refresh failed for player {player_id}: {exc}",
    "bnet_character_sync",
    details={"player_id": player_id, "error": str(exc)},
    identifier=str(player_id),
)
return None
```

Note: `_refresh_token` doesn't have the battletag — use `str(player_id)` as identifier here.
The scheduler will use battletag as identifier since it has that data.

### 2. `scheduler.run_bnet_character_refresh`

This is the highest-value change. Full updated method:

```python
async def run_bnet_character_refresh(self):
    from sv_common.errors import report_error, resolve_issue
    from sv_common.guild_sync.bnet_character_sync import get_valid_access_token, sync_bnet_characters
    from guild_portal.services.error_routing import maybe_notify_discord

    try:
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT player_id, battletag FROM guild_identity.battlenet_accounts"
            )

        if not rows:
            logger.info("Battle.net character refresh: no linked accounts")
            return

        refreshed = 0
        new_chars = 0
        errors = 0

        for row in rows:
            player_id = row["player_id"]
            battletag = row["battletag"] or f"player#{player_id}"

            try:
                access_token = await get_valid_access_token(self.db_pool, player_id)
                if access_token is None:
                    logger.warning(
                        "Battle.net refresh: no valid token for %s — skipping", battletag
                    )
                    errors += 1
                    result = await report_error(
                        self.db_pool,
                        "bnet_token_expired",
                        "warning",
                        f"Battle.net token expired for {battletag} — player must re-link their "
                        f"Battle.net account at /profile.",
                        "scheduler",
                        details={"player_id": player_id, "battletag": battletag},
                        identifier=battletag,
                    )
                    await maybe_notify_discord(
                        self.db_pool, self.discord_bot, self.audit_channel_id,
                        "bnet_token_expired", "warning",
                        f"Battle.net token expired for **{battletag}** — player must re-link.",
                        result["is_first_occurrence"],
                    )
                    continue

                stats = await sync_bnet_characters(self.db_pool, player_id, access_token)
                refreshed += 1
                new_chars += stats.get("new_characters", 0)

                # Clear any open errors for this player on success
                await resolve_issue(self.db_pool, "bnet_token_expired", identifier=battletag)
                await resolve_issue(self.db_pool, "bnet_sync_error",    identifier=battletag)

            except Exception as exc:
                logger.error(
                    "Battle.net character refresh failed for %s: %s", battletag, exc, exc_info=True
                )
                errors += 1
                result = await report_error(
                    self.db_pool,
                    "bnet_sync_error",
                    "warning",
                    f"Battle.net character sync failed for {battletag}: {exc}",
                    "scheduler",
                    details={"player_id": player_id, "battletag": battletag, "error": str(exc)},
                    identifier=battletag,
                )
                await maybe_notify_discord(
                    self.db_pool, self.discord_bot, self.audit_channel_id,
                    "bnet_sync_error", "warning",
                    f"Battle.net sync failed for **{battletag}**: {exc}",
                    result["is_first_occurrence"],
                )

        logger.info(
            "Battle.net character refresh: refreshed=%d new_chars=%d errors=%d",
            refreshed, new_chars, errors,
        )

    except Exception as exc:
        logger.error("Battle.net character refresh job failed: %s", exc, exc_info=True)
        result = await report_error(
            self.db_pool,
            "bnet_sync_error",
            "critical",
            f"Battle.net character refresh job crashed: {exc}",
            "scheduler",
            details={"error": str(exc)},
        )
        await maybe_notify_discord(
            self.db_pool, self.discord_bot, self.audit_channel_id,
            "bnet_sync_error", "critical",
            f"Battle.net character refresh job crashed: {exc}",
            result["is_first_occurrence"],
        )
```

Note: The scheduler now fetches `battletag` in addition to `player_id`.

### 3. Remaining Scheduler Jobs

For `run_blizzard_sync`, `run_crafting_sync`, `run_wcl_sync`, `run_attendance_processing`,
`run_ah_sync` — the existing `send_error(channel, title, detail)` calls in each should be
replaced with:

```python
result = await report_error(
    self.db_pool,
    "{issue_type}",      # e.g. "blizzard_sync_failed"
    "warning",
    str(exc),
    "scheduler",
    details={"error": str(exc)},
)
await maybe_notify_discord(
    self.db_pool, self.discord_bot, self.audit_channel_id,
    "{issue_type}", "warning",
    str(exc),
    result["is_first_occurrence"],
)
```

**Issue types to register in `reporter.py` (ISSUE_EMOJI + ISSUE_TYPE_NAMES):**

| issue_type | emoji | label |
|---|---|---|
| `blizzard_sync_failed`   | 🌐 | Blizzard API Sync Failed |
| `crafting_sync_failed`   | 🔨 | Crafting Sync Failed |
| `wcl_sync_failed`        | 📊 | Warcraft Logs Sync Failed |
| `attendance_sync_failed` | 📋 | Attendance Processing Failed |
| `ah_sync_failed`         | 💰 | AH Pricing Sync Failed |

Add these to `reporter.py` `ISSUE_EMOJI` and `ISSUE_TYPE_NAMES` dicts.

Also seed them in `common.error_routing` — add to the migration 0043 seed INSERT or as a
new `0043b` migration if 0043 is already deployed:

```sql
INSERT INTO common.error_routing
    (issue_type, min_severity, dest_audit_log, dest_discord, first_only, notes)
VALUES
    ('blizzard_sync_failed',   'warning', TRUE, TRUE, TRUE, 'Blizzard API sync failure'),
    ('crafting_sync_failed',   'warning', TRUE, TRUE, TRUE, 'Crafting recipe sync failure'),
    ('wcl_sync_failed',        'warning', TRUE, TRUE, TRUE, 'Warcraft Logs sync failure'),
    ('attendance_sync_failed', 'warning', TRUE, TRUE, TRUE, 'Attendance processing failure'),
    ('ah_sync_failed',         'warning', TRUE, TRUE, TRUE, 'AH pricing sync failure')
ON CONFLICT DO NOTHING;
```

### 4. `admin_pages.admin_bnet_sync_user`

```python
# Existing failure path (token is None):
return JSONResponse({"ok": False, "error": "Could not retrieve a valid access token..."}, status_code=422)

# Add BEFORE the return:
from sv_common.errors import report_error
await report_error(
    pool,
    "bnet_token_expired",
    "warning",
    f"Battle.net token expired — player must re-link their Battle.net account.",
    "admin_bnet_sync",
    details={"user_id": user_id, "player_id": player_id},
    identifier=str(player_id),
)

# Existing success path (after sync_bnet_characters returns):
# Add after the sync call:
from sv_common.errors import resolve_issue
await resolve_issue(pool, "bnet_token_expired", identifier=str(player_id))
await resolve_issue(pool, "bnet_sync_error",    identifier=str(player_id))
```

No Discord post from admin endpoints — the admin already sees the HTTP response.

### 5. `admin_pages.admin_bnet_sync_all`

Same pattern applied per-player in the loop. `report_error` on token failure or exception,
`resolve_issue` on success.

---

## Add New Issue Types to reporter.py Seed Routing

After adding new issue type strings to `ISSUE_EMOJI` and `ISSUE_TYPE_NAMES`, also ensure
`common.error_routing` has rows for them (via migration or manual insert on dev/test). The
wildcard catch-all rules already cover any type not explicitly listed, so this is optional
but good practice.

---

## Tests

### `tests/unit/test_bnet_character_sync.py` (additions)

**`test_refresh_token_reports_error_on_no_refresh_token`**
Mock `report_error` (or verify pool.execute called with "INSERT INTO common.error_log").
When `row["refresh_token_encrypted"]` is None, `report_error` is called with
`issue_type="bnet_token_expired"`.

**`test_refresh_token_reports_error_on_http_failure`**
HTTP mock raises. `report_error` called with `issue_type="bnet_token_expired"`.

### `tests/unit/test_scheduler_bnet.py` (new file or additions)

**`test_run_bnet_refresh_reports_expired_token`**
`get_valid_access_token` returns None. `report_error` called with `"bnet_token_expired"`.
`maybe_notify_discord` called with `is_first_occurrence` from `report_error` result.

**`test_run_bnet_refresh_resolves_on_success`**
`get_valid_access_token` returns a token. `sync_bnet_characters` succeeds.
`resolve_issue` called for both `"bnet_token_expired"` and `"bnet_sync_error"`.

**`test_run_bnet_refresh_reports_sync_exception`**
`sync_bnet_characters` raises. `report_error` called with `"bnet_sync_error"`.

**`test_run_bnet_refresh_suppresses_repeat_discord_notification`**
`report_error` returns `is_first_occurrence=False`. `maybe_notify_discord` is called
but routing rule has `first_only=True` so `send_error` is NOT called.

### `tests/unit/test_admin_pages_bnet.py` (additions)

**`test_admin_bnet_sync_user_reports_expired_token`**
Token is None. `report_error` called. HTTP 422 still returned.

**`test_admin_bnet_sync_user_resolves_on_success`**
Sync succeeds. `resolve_issue` called for `"bnet_token_expired"` and `"bnet_sync_error"`.

---

## Final State After Phase 6.4

All error paths produce records in `common.error_log`. Officers can:

- Visit `/admin/error-routing` to see all open errors
- Click Resolve on any fixed issue
- Configure which errors go to Discord and on what conditions
- Receive a Sunday morning digest if anything lingers

No background job silently fails anymore. The audit channel gets pinged on new issues
(per routing config) and the weekly digest catches anything that never got cleaned up.

---

## Deliverables Checklist

- [ ] `bnet_character_sync._refresh_token` — `report_error` on both failure paths
- [ ] `scheduler.run_bnet_character_refresh` — fetches battletag, `report_error` + `resolve_issue` per player
- [ ] `scheduler.run_blizzard_sync` — `report_error` replaces `send_error`
- [ ] `scheduler.run_crafting_sync` — `report_error` replaces `send_error`
- [ ] `scheduler.run_wcl_sync` — `report_error` replaces `logger.error`
- [ ] `scheduler.run_attendance_processing` — `report_error` replaces `logger.error`
- [ ] `scheduler.run_ah_sync` — `report_error` replaces `logger.error`
- [ ] `admin_pages.admin_bnet_sync_user` — `report_error` on failure, `resolve_issue` on success
- [ ] `admin_pages.admin_bnet_sync_all` — same per-player
- [ ] `reporter.py` — new issue types added to `ISSUE_EMOJI` and `ISSUE_TYPE_NAMES`
- [ ] `common.error_routing` — new issue types seeded (migration addendum or manual)
- [ ] `tests/unit/test_bnet_character_sync.py` — refresh failure paths test `report_error`
- [ ] `tests/unit/test_scheduler_bnet.py` — all four scheduler behavior tests
- [ ] `tests/unit/test_admin_pages_bnet.py` — token failure + success resolution tests
- [ ] `pytest tests/unit/ -v` — all tests pass
- [ ] Manual smoke test on dev: trigger a BNet sync with expired token, verify error appears
  in `/admin/error-routing` and (if first occurrence + routing allows) in audit channel
