# Build Plan: Attendance Snapshot & Signup Capture
> Features 1+2 of the attendance enhancement suite.
> Scope: record availability + Raid-Helper status at event start; add auto-excuse settings.

---

## Goal

At the moment a raid event starts (signup "locks"), snapshot two pieces of data onto each
player's `raid_attendance` record:

1. **`was_available`** — did the player have availability set for that day/time? (from `patt.player_availability`)
2. **`raid_helper_status`** — what was their Raid-Helper signup state? (from Raid-Helper API)

Two new settings checkboxes on the attendance page control whether either of these
automatically counts as an excused absence when computing attendance stats.

The underlying data is frozen at snapshot time. The excuse logic is applied at query time
based on current settings — so changing settings immediately re-affects all past stats
without re-processing anything.

---

## New Database Columns

### Migration 0063

#### `patt.raid_events`
```sql
ALTER TABLE patt.raid_events
    ADD COLUMN signup_snapshot_at TIMESTAMP WITH TIME ZONE NULL;
```
Stamped when the signup snapshot job completes for this event. NULL = not yet snapshotted.

#### `patt.raid_attendance`
```sql
ALTER TABLE patt.raid_attendance
    ADD COLUMN was_available BOOLEAN NULL,
    ADD COLUMN raid_helper_status VARCHAR(20) NULL;
```
- `was_available`: TRUE if `player_availability` row exists for the event's `day_of_week` and
  the event start time falls within `[earliest_start, earliest_start + available_hours]`.
  FALSE if player has no availability set for that day OR the raid falls outside their window.
  NULL means snapshot hasn't run yet.
- `raid_helper_status`: One of `accepted`, `tentative`, `bench`, `absence`, `unknown`.
  Derived from Raid-Helper className: Tank/Healer/Melee/Ranged → `accepted`;
  Tentative → `tentative`; Bench → `bench`; Absence → `absence`; not in event → `unknown`.
  NULL means snapshot hasn't run yet or event has no `raid_helper_event_id`.

#### `common.discord_config`
```sql
ALTER TABLE common.discord_config
    ADD COLUMN attendance_excuse_if_unavailable BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN attendance_excuse_if_discord_absent BOOLEAN NOT NULL DEFAULT FALSE;
```

---

## Auto-Excuse Logic (query time, not stored)

A player is **auto-excused** for an event if either condition is true and its setting is
enabled:

```
auto_excused =
    (was_available = FALSE AND attendance_excuse_if_unavailable = TRUE)
    OR
    (raid_helper_status = 'absence' AND attendance_excuse_if_discord_absent = TRUE)
```

Throughout the codebase, "effectively excused" = `noted_absence = TRUE OR auto_excused = TRUE`.

An effectively excused event:
- Does **not** count as absent (does not hurt attendance %)
- Does **not** count as attended (does not pad attendance %)
- Denominator for % = total events − excused events

---

## New Scheduler Job: Signup Snapshot

### Trigger
Fold into the existing 30-minute `run_attendance_processing` loop. After processing
events that ended 30+ min ago, also look for events that **started** and need a snapshot.

Query: events where `start_time_utc <= NOW()` AND `signup_snapshot_at IS NULL`
AND `NOT is_deleted` AND in the active season.

### New Function: `snapshot_event_signups(pool, event_id)`
Location: `src/sv_common/guild_sync/attendance_processor.py`

Steps:
1. Load event row; if no `raid_helper_event_id`, skip Raid-Helper fetch (still snapshot
   `was_available` for each player in roster).
2. Fetch Raid-Helper event: `GET /api/v2/events/{raid_helper_event_id}`
   using `raid_helper_api_key` and `raid_helper_server_id` from `discord_config`.
3. Build a `discord_id → raid_helper_status` map from the response's signup list.
   The response has `signUps` array; each entry has `userId` and `className`.
   Map: Tank/Healer/Melee/Ranged → `accepted`; Tentative → `tentative`;
   Bench → `bench`; Absence → `absence`; missing → `unknown`.
4. Load the event's `day_of_week` from `event_date` (Python: `event_date.weekday()`).
5. Load `player_availability` rows for that `day_of_week`. Build set of player_ids
   with valid availability (availability row exists AND event falls within time window).
   Time window check: `event.start_time_utc` (converted to player local time) falls within
   `[earliest_start, earliest_start + available_hours]`.
   **Simplification:** Since player timezone is not stored, use a simpler heuristic:
   if the player has ANY availability row for that `day_of_week`, `was_available = TRUE`.
   If no row for that day, `was_available = FALSE`. Document this assumption in a comment.
   The scheduler already uses this same simplified check at event creation.
6. For each active roster player (active + main char set + not on hiatus):
   - Look up their `discord_user_id` via the `discord_users` join
   - Determine `raid_helper_status` from step 3 (or `unknown` if no discord linked)
   - Determine `was_available` from step 5
   - Upsert `raid_attendance(event_id, player_id)` setting `was_available` and
     `raid_helper_status`. Use INSERT ... ON CONFLICT DO UPDATE.
7. Stamp `raid_events.signup_snapshot_at = NOW()`.
8. Return summary dict: `{snapshotted: N, no_rh_id: bool}`.

### Error handling
If Raid-Helper API call fails (non-200, network error), still snapshot `was_available`
(that doesn't need Raid-Helper) and leave `raid_helper_status = NULL` for all records.
Log warning. Do NOT stamp `signup_snapshot_at` so it retries next cycle.

---

## API Changes

### `GET /api/v1/admin/attendance/season`
The response's per-cell data must include `was_available`, `raid_helper_status`, and a
computed `auto_excused` boolean (server computes it using current settings from discord_config).

The attendance % calculation must use the new "effectively excused" denominator logic:
```
pct = attended_count / max(1, total_events - excused_count)
```
where `excused_count` = `noted_absence = TRUE OR auto_excused = TRUE`.

### `GET /api/v1/admin/attendance/settings`
Add `attendance_excuse_if_unavailable` and `attendance_excuse_if_discord_absent` to
response payload.

### `PATCH /api/v1/admin/attendance/settings`
Accept and persist the two new boolean fields.

### `GET /api/v1/admin/attendance/event/{id}`
Include `was_available`, `raid_helper_status`, `auto_excused` per player row.

### `GET /api/v1/admin/attendance/export`
CSV `status` cell: treat `auto_excused = TRUE` same as `noted_absence` → emit `"excused"`.

---

## UI Changes (`templates/admin/attendance.html`)

### Grid cell display
- Auto-excused cells display `~` (same tilde as `noted_absence`)
- Tooltip/title on `~` cell should say "Auto-excused" vs "Excused" to differentiate

### Cell popover
Add a section when `was_available IS NOT NULL`:
```
Availability:     Available / Not available
Raid-Helper:      Accepted / Tentative / Bench / Absence / Unknown
Auto-excused:     Yes (unavailable) / Yes (absent in Discord) / No
```
Show this block only if snapshot has run (i.e. `was_available IS NOT NULL`).

### Settings panel — new checkboxes
Add to the existing settings card (above or below the habitual section):

```
─ Excuse Logic ─────────────────────────────────────────
☐  Auto-excuse if player was not available (schedule)
☐  Auto-excuse if player marked Absence in Discord
```

On save, PATCH the two new fields. On change, a note: "Changing these settings
immediately recalculates all stats using the stored snapshot data."

---

## Files to Create / Modify

| File | Change |
|---|---|
| `alembic/versions/0063_attendance_snapshot.py` | New migration (4 new columns) |
| `src/sv_common/guild_sync/attendance_processor.py` | Add `snapshot_event_signups()` function |
| `src/sv_common/guild_sync/scheduler.py` | Call `snapshot_event_signups` in `run_attendance_processing` loop |
| `src/guild_portal/api/admin_routes.py` | Update season/event/settings/export endpoints |
| `src/guild_portal/templates/admin/attendance.html` | Settings checkboxes + popover fields + auto-excuse display |

No new service files required — `snapshot_event_signups` lives alongside `process_event`
in `attendance_processor.py`. Raid-Helper HTTP calls reuse the existing `httpx` pattern
(same as `raid_helper_service.py` already uses).

---

## Testing

### Unit tests (no DB needed)
- `test_auto_excuse_logic`: given `was_available`, `raid_helper_status`, and settings bools,
  verify `auto_excused` computes correctly for all combinations.
- `test_raid_helper_status_mapping`: given a Raid-Helper `className`, verify correct status enum.
- `test_was_available_from_availability_rows`: given availability rows and event day_of_week,
  verify correct bool per player.

### Integration tests (DB needed, skip-marked if no pool)
- `test_snapshot_event_signups_no_rh_id`: event without raid_helper_event_id — snapshots
  `was_available` only, leaves `raid_helper_status NULL`, does NOT stamp `signup_snapshot_at`.
  Wait — actually we DO want to stamp it so it doesn't retry endlessly. Reconsider:
  stamp it if `was_available` snapshot completed, set `raid_helper_status = 'unknown'` for all.
- `test_snapshot_stamps_at_on_success`: verify `signup_snapshot_at` gets set.
- `test_attendance_pct_respects_auto_excuse`: create event + attendance records with
  `was_available = FALSE`, enable setting, verify % computation excludes the event.

### Manual smoke test
1. Deploy to dev.
2. Create a test raid event with `raid_helper_event_id` set.
3. Manually trigger snapshot via `POST /api/v1/admin/attendance/event/{id}/snapshot` (add this endpoint for manual trigger, same as reprocess).
4. Verify `was_available` and `raid_helper_status` populate on grid rows.
5. Toggle checkboxes, verify % updates immediately on page reload without re-processing.

---

## Open Questions to Resolve During Build

1. **Raid-Helper `signUps` response structure**: The `RAID_HELPER_API.md` documents the
   write API well but not the read (GET event) response. Before implementing the parser,
   fetch a live event and inspect `signUps` shape. It likely has `userId` and `className`
   per entry, but verify.
2. **Player timezone for availability window check**: Decide whether to implement the full
   time-window check or keep the simplified "has row for day_of_week = TRUE" logic. The
   simplified version matches what event creation already uses and is consistent.
3. **Manual snapshot endpoint**: Decide if a "Re-snapshot" button is useful alongside
   "Re-process" in the event detail panel. Recommend yes — add it.

---

## Version & Branch

- Branch: `feature/attendance-snapshot`
- Migration: 0063
- Expected version bump: MINOR (new feature) → `prod-v0.9.0`
