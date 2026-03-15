# Phase 4.7 — Voice Channel Attendance Tracking

> Electric Boogaloo: because apparently we're building a full guild management platform now.

## Goal

Automatically track raid attendance by watching who is in the configured Discord voice
channel during a scheduled raid window. Collect raw presence data per event, derive
attendance verdicts against configurable rules, and display the results on an officer
attendance screen — a grid showing every player against every raid night, growing over
the course of the season.

---

## Why This Is Valuable

- **Zero friction**: no one has to take roll, no sign-in form, no manual check. Bot just watches.
- **Objective**: voice presence is an unambiguous fact — no "I was there but didn't sign up."
- **Historical**: gives officers a real picture of attendance trends over a season, not just vibes.
- **Accountability without toxicity**: data surfaces concerns before they become confrontations.
- **Roster health**: helps identify who genuinely can't make raids vs. who keeps bailing.

---

## Existing Infrastructure (No Re-Work Needed)

The platform already has most of the plumbing:

| Existing piece | What we get for free |
|---|---|
| `discord_config.raid_voice_channel_id` | Default voice channel for raids already stored |
| `patt.raid_events` with `start_time_utc` / `end_time_utc` | Raid window bounds already exist |
| `patt.raid_attendance` with `attended BOOL` + `source VARCHAR` | Attendance record already has the verdict + origin columns |
| `discord_users` table | Maps Discord user IDs → players |
| Bot `on_voice_state_update` event | discord.py fires this natively — just need a handler |

The main new work is: a raw event log table, the bot handler, the processing logic,
the attendance rules config, and the attendance screen.

---

## Prerequisites

- Phase 4.3 complete (migration 0034 in place)
- `patt.raid_events` populated with upcoming events
- Discord bot running with `GUILD_VOICE_STATES` intent enabled

---

## Database Migration: 0038_voice_attendance

> Migration number is a placeholder — actual number depends on which of 4.4–4.6 ship first.

### New Table: `patt.voice_attendance_log`

Raw join/leave events — the unprocessed truth. Kept permanently for re-processing if
rules change. One row per join-or-leave event per user per event.

```sql
CREATE TABLE patt.voice_attendance_log (
    id              SERIAL PRIMARY KEY,
    event_id        INTEGER NOT NULL REFERENCES patt.raid_events(id) ON DELETE CASCADE,
    discord_user_id VARCHAR(25) NOT NULL,
    channel_id      VARCHAR(25) NOT NULL,
    action          VARCHAR(10) NOT NULL CHECK (action IN ('join', 'leave')),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_val_event    ON patt.voice_attendance_log(event_id);
CREATE INDEX idx_val_user     ON patt.voice_attendance_log(discord_user_id);
CREATE INDEX idx_val_occurred ON patt.voice_attendance_log(occurred_at);
```

**Design note:** We log every join/leave, not just the session. If someone DCs and
rejoins, we get two join rows and one leave row — total presence time is correctly
computed by summing contiguous spans.

### Columns Added to Existing Tables

#### `patt.raid_events`

```sql
ALTER TABLE patt.raid_events
    ADD COLUMN voice_channel_id VARCHAR(25),           -- per-event override; NULL = use discord_config default
    ADD COLUMN voice_tracking_enabled BOOL NOT NULL DEFAULT TRUE,
    ADD COLUMN attendance_processed_at TIMESTAMPTZ;    -- NULL = not yet processed
```

**Design note:** `voice_tracking_enabled = FALSE` lets officers disable tracking for
specific events (holiday raids, fun runs, alt nights) without touching the global config.

#### `patt.raid_attendance`

```sql
ALTER TABLE patt.raid_attendance
    ADD COLUMN minutes_present SMALLINT;    -- NULL until voice-processed
```

#### `common.discord_config`

```sql
ALTER TABLE common.discord_config
    ADD COLUMN attendance_min_pct          SMALLINT NOT NULL DEFAULT 75,
    ADD COLUMN attendance_late_grace_min   SMALLINT NOT NULL DEFAULT 10,
    ADD COLUMN attendance_early_leave_min  SMALLINT NOT NULL DEFAULT 10,
    ADD COLUMN attendance_trailing_events  SMALLINT NOT NULL DEFAULT 8,
    ADD COLUMN attendance_feature_enabled  BOOL     NOT NULL DEFAULT FALSE;
```

**Field meanings:**

| Field | Default | Meaning |
|---|---|---|
| `attendance_min_pct` | 75 | Must be in VC for ≥ 75% of the (adjusted) raid window to count as attended |
| `attendance_late_grace_min` | 10 | First 10 minutes don't count — late arrivals within 10 min aren't penalized |
| `attendance_early_leave_min` | 10 | Last 10 minutes don't count — leaving up to 10 min early isn't penalized |
| `attendance_trailing_events` | 8 | Rolling window for "at risk" status on Player Manager |
| `attendance_feature_enabled` | FALSE | Master switch — tracking and report inactive until enabled |

> **Grace period mechanics:** A 2-hour raid with 10-min late grace and 10-min early
> leave grace has an **effective window** of 100 minutes. You need to be present for
> 75% of 100 minutes = 75 minutes to count as attended. If you join at minute 11, you
> have 89 of 100 effective minutes available — you still need 75 of them.

---

## Task 1: Bot — Voice State Handler

### File: `src/sv_common/discord/voice_attendance.py` (new)

```python
"""
Voice channel attendance tracking for scheduled raid events.

Listens to Discord on_voice_state_update events. During an active raid window,
logs join/leave actions to patt.voice_attendance_log.

Active window detection: loads today's raid events at startup and after midnight.
Only logs events for the configured voice channel (discord_config.raid_voice_channel_id
or per-event override stored on patt.raid_events.voice_channel_id).
"""
```

**Core responsibilities:**

1. **Window cache** — Maintains an in-memory list of "active or upcoming today" raid
   events, refreshed at midnight (or on demand). Checks if `now()` falls between
   `start_time_utc` and `end_time_utc` to know whether tracking is live.

2. **Channel resolution** — For each voice state update, resolves the target channel:
   ```
   effective_channel = event.voice_channel_id OR discord_config.raid_voice_channel_id
   ```
   Ignores state updates for any other channel.

3. **Log insert** — Writes a row to `patt.voice_attendance_log` for each join/leave.
   Stores raw `discord_user_id` (not player_id) — player resolution happens at
   processing time so a new player-character link created after the raid still counts.

4. **No real-time verdict** — The handler is fire-and-forget. It only appends to the
   log. Processing happens post-raid (see Task 2). This keeps the hot path simple and
   avoids race conditions.

### Cog Registration

Register in `src/sv_common/discord/bot.py` as a new cog:
```python
await bot.add_cog(VoiceAttendanceCog(bot, db_pool))
```

Guarded by `attendance_feature_enabled` — cog is never loaded if the feature is off.

### Required Discord Intent

```python
intents.voice_states = True
```

Add to `bot.py` intents setup. **Note:** `voice_states` is a non-privileged intent —
no approval needed from Discord for bots in servers under 100 members. Above 100 you
need to enable it in the Developer Portal (not a verified intent, just a settings flag).

---

## Task 2: Attendance Processor

### File: `src/sv_common/guild_sync/attendance_processor.py` (new)

Runs after a raid ends. Reads the raw log, computes per-player presence time, applies
the configured rules, and writes verdict rows to `patt.raid_attendance`.

**Algorithm:**

```
For each (discord_user_id, event_id) pair in the log:

1. Reconstruct presence spans:
   - Sort log rows by occurred_at
   - Pair join → leave events into spans [join_time, leave_time]
   - If last action is a 'join' (user never left — still in VC at raid end),
     treat end_time_utc as the implicit leave

2. Clip spans to the effective window:
   effective_start = event.start_time_utc + grace_late
   effective_end   = event.end_time_utc   - grace_early_leave
   Clip each span to [effective_start, effective_end]

3. Sum total effective seconds present

4. effective_window_seconds = (effective_end - effective_start).total_seconds()
   presence_pct = total_present / effective_window_seconds * 100

5. attended = presence_pct >= attendance_min_pct

6. Resolve discord_user_id → player_id via discord_users table
   If no match: log warning, skip (unlinked Discord user in VC)

7. UPSERT into patt.raid_attendance:
   - INSERT if no row for (event_id, player_id)
   - UPDATE attended, source='voice', minutes_present=<int> if row exists
     (signed_up from Raid-Helper signup is preserved)

8. Mark event.attendance_processed_at = NOW()
```

**Edge cases handled:**

| Situation | Handling |
|---|---|
| DC + rejoin | Multiple spans summed correctly |
| Was in VC before raid started | Span clipped to effective_start — no bonus |
| Never left at raid end | Last span closed at `end_time_utc` |
| Joined after raid ended | All spans outside window → 0% → not attended |
| Signed up but never joined VC | `signed_up=TRUE`, `attended=FALSE`, `source='voice'` |
| Joined VC but not signed up | `signed_up=FALSE`, `attended=TRUE`, `source='voice'` — walk-ins counted |
| Unlinked Discord user | Logged as warning; stored in `voice_attendance_log` for future re-processing |

### Scheduler Integration

Add to `scheduler.py`:

```python
# Run 30 minutes after each scheduled raid end time
# Looks for events where end_time_utc < NOW() AND attendance_processed_at IS NULL
# AND voice_tracking_enabled IS TRUE
```

Officers can also trigger manual re-processing from the attendance page (useful for
threshold tuning after the fact).

---

## Task 3: Attendance Screen

### Route: `GET /admin/attendance`

New admin page. Officer+ required (screen permission: `attendance_report`).

---

### Primary View: Season Grid

The main panel of the page. A two-dimensional grid with players on rows and raid nights
on columns, growing left-to-right as the season progresses.

```
┌─────────────────────────┬──────────┬──────────┬──────────┬──────────┬──────────────┐
│ Player                  │ Mar 18   │ Mar 25   │ Apr 1    │ Apr 8    │  Total       │
├─────────────────────────┼──────────┼──────────┼──────────┼──────────┼──────────────┤
│ 🟡 Trogmoon  [Officer]  │    ✓     │    ✓     │    ✓     │  [live]  │  3/3  100%   │
│ ⚪ Rocketfuel [Member]  │    ✓     │    ~     │    ✗     │  [live]  │  1/3   33% ⚠ │
│ ⚪ Shadowvaca [Member]  │    ✗     │    ✓     │    ✓     │  [live]  │  2/3   66%   │
│ ⚪ Newguy    [Initiate] │    —     │    —     │    ✓     │  [live]  │  1/1  100%   │
└─────────────────────────┴──────────┴──────────┴──────────┴──────────┴──────────────┘
```

**Cell values:**

| Symbol | Meaning | Color |
|---|---|---|
| `✓` | Attended (voice presence met threshold) | Green |
| `✗` | Absent (no voice presence) | Red |
| `~` | Excused (`noted_absence = TRUE`) | Amber — counts neutral |
| `—` | No data (player not yet in roster at event date, or event pre-dates tracking) | Grey dim |
| `[live]` | Event is today/in-progress — tracking active, not yet processed | Blue pulse |
| `[pending]` | Event ended, processing not yet run | Grey italic |

**Clicking any ✓ or ✗ cell** opens a tooltip/popover with:
- Minutes present / total minutes
- Presence %
- Joined at / left at (or "never joined")
- Toggle "Mark as Excused" (officer action, updates `noted_absence`)

**Column headers** are clickable — show a per-event summary panel (see below).

**Row sorting:** default by rank level desc, then player name alpha. Sortable by any
column (total %, name, streak) via header click.

**Season selector:** top-right dropdown. Defaults to current active season. Historical
seasons are read-only.

---

### Per-Event Detail Panel

Clicking a column header slides open a detail panel below the grid (or a modal):

- Event title, date, start → end times, voice channel used
- Summary line: "17 attended · 3 absent · 2 excused · 20 signed up"
- Full table: player | joined_at | left_at | minutes | pct | result
- "Re-process" button (Officer+) — re-runs attendance processor for this event
- `voice_tracking_enabled` toggle — disable retroactively if it was an alt night

---

### Player Summary Row (footer / sidebar)

Pinned at the bottom of the grid or in a side column:

- Guild-wide attendance rate for each raid night (e.g., "17/20 = 85%")
- Useful for spotting problem nights vs. problem players

---

### Unlinked Users Panel

Collapsed panel, shown only when there are unresolved Discord users in the log.

Shows Discord user IDs from `voice_attendance_log` that couldn't be resolved to a player.
Officers can dismiss (not a player) or note for manual linking. This catches trialists
or alts of unclaimed characters who were in VC.

---

### Settings Panel (Guild Leader only)

Collapsible section at the bottom of the page. Inline edit form:

```
[x] Enable voice attendance tracking
    The bot must have View Channel + Connect permission on the raid voice channel.

Minimum presence     [75] % of the adjusted raid window
Late arrival grace   [10] minutes  (first N min excluded from the window)
Early leave grace    [10] minutes  (last N min excluded from the window)
Trailing window      [ 8] events   (used for "at risk" status in Player Manager)
```

PATCH `/api/v1/admin/attendance/settings` on save. Reloads the config cache.

---

## Task 4: Player Manager Integration

### File: `src/guild_portal/static/js/players.js`

Add a small attendance badge to each player card in the Player Manager
(`/admin/players`). Officer-visible only (already behind auth).

**Badge appearance:** Rendered as a colored dot with tooltip, adjacent to the rank badge.

| Dot color | Meaning |
|---|---|
| Green | ≥ threshold over trailing N events |
| Amber | Between 50% and threshold |
| Red | < 50% |
| Grey | Fewer than 3 events in the trailing window — not enough data yet |
| (none) | `attendance_feature_enabled = FALSE` — feature off |

The dot data comes from the `/admin/players-data` endpoint. Add `attendance_status`
(`good` | `at_risk` | `concern` | `new` | `none`) and `attendance_summary`
(`"6/8 raids"`) to each player record in the response.

**This is informational only** — it does not block sign-ups, change Discord roles, or
automatically bench anyone.

---

## Task 5: API Endpoints

### New Routes in `src/guild_portal/api/admin_routes.py`

```
GET  /api/v1/admin/attendance/season               → Season grid data (players × events matrix)
GET  /api/v1/admin/attendance/event/{id}           → Per-event breakdown with raw presence
POST /api/v1/admin/attendance/event/{id}/reprocess → Trigger re-processing
PATCH /api/v1/admin/attendance/record/{id}         → Toggle noted_absence
GET  /api/v1/admin/attendance/export               → CSV export of season grid
PATCH /api/v1/admin/attendance/settings            → Update discord_config attendance fields
```

All routes: Officer+ auth (Bearer token).

#### `GET /api/v1/admin/attendance/season` response shape

```json
{
    "ok": true,
    "data": {
        "season": {"id": 1, "display_name": "The War Within Season 2"},
        "events": [
            {"id": 42, "date": "2026-03-18", "title": "Heroic Raid", "processed": true},
            {"id": 43, "date": "2026-03-25", "title": "Heroic Raid", "processed": true},
            {"id": 44, "date": "2026-04-01", "title": "Heroic Raid", "processed": false}
        ],
        "players": [
            {
                "id": 7,
                "name": "Trogmoon",
                "rank": "Officer",
                "rank_level": 4,
                "attendance": {
                    "42": {"status": "attended", "minutes_present": 118, "pct": 98},
                    "43": {"status": "attended", "minutes_present": 95,  "pct": 79},
                    "44": {"status": "pending"}
                },
                "total_attended": 2,
                "total_eligible": 2,
                "pct": 100,
                "streak": 2,
                "attendance_status": "good"
            }
        ]
    }
}
```

---

## Data Flow Diagram

```
Discord on_voice_state_update
        │
        ▼
[Is now between start/end of an active raid event?]
        │ YES
        ▼
INSERT patt.voice_attendance_log
  (event_id, discord_user_id, channel_id, action, occurred_at)

        ... raid ends ...

[Scheduler: 30 min after end_time_utc]
        │
        ▼
attendance_processor.process_event(event_id)
        │
        ├─ Reconstruct spans from log
        ├─ Apply grace periods
        ├─ Compute presence_pct per user
        ├─ Resolve discord_user_id → player_id
        └─ UPSERT patt.raid_attendance
              attended = presence_pct >= min_pct
              source = 'voice'
              minutes_present = <int>

        ▼
/admin/attendance grid renders from raid_attendance × raid_events
```

---

## ORM Models

### New Model: `VoiceAttendanceLog`

```python
class VoiceAttendanceLog(Base):
    __tablename__ = "voice_attendance_log"
    __table_args__ = {"schema": "patt"}

    id              : int (PK)
    event_id        : int → patt.raid_events
    discord_user_id : str
    channel_id      : str
    action          : str  # 'join' | 'leave'
    occurred_at     : datetime
```

### Updates to Existing Models

**`RaidEvent`** — add:
```python
voice_channel_id          : Optional[str]
voice_tracking_enabled    : bool = True
attendance_processed_at   : Optional[datetime]
```

**`RaidAttendance`** — add:
```python
minutes_present   : Optional[int]   # derived from voice log spans; NULL for manual rows
```

**`DiscordConfig`** — add:
```python
attendance_min_pct           : int  = 75
attendance_late_grace_min    : int  = 10
attendance_early_leave_min   : int  = 10
attendance_trailing_events   : int  = 8
attendance_feature_enabled   : bool = False
```

---

## Tests

### Unit Tests

- `attendance_processor.py`: span reconstruction — DC+rejoin, late joiner, early leaver, grace-window boundary, no-show
- `attendance_processor.py`: presence_pct calculation, threshold boundary (exactly at min_pct)
- `attendance_processor.py`: unlinked discord_user handled gracefully (no exception, warning logged)
- `attendance_processor.py`: signed_up preserved when updating existing raid_attendance row
- `voice_attendance.py`: handler ignores wrong channel, ignores events outside window
- `voice_attendance.py`: handler correctly identifies active event from cache
- `voice_attendance.py`: cog not loaded when `attendance_feature_enabled = FALSE`
- Season grid API: correct matrix shape, correct cell statuses, correct totals

### Integration Tests (requires live DB)

- End-to-end: insert synthetic log rows → run processor → verify `raid_attendance` rows
- Re-process: change threshold config, re-run, verify rows updated
- Noted absence: toggle via API, verify `~` status in grid response
- Unlinked user: log row with unknown discord_id → appears in unlinked panel, not in grid

### Regression

- Existing `raid_attendance` rows with `source='raid_helper'` unaffected by processor
- `/admin/players-data` includes `attendance_status` field (returns `"none"` when feature off)
- Player Manager renders without error when no attendance data exists yet

---

## Deliverables Checklist

- [ ] Migration `0038_voice_attendance`:
  - [ ] `patt.voice_attendance_log` table + indexes
  - [ ] `patt.raid_events`: `voice_channel_id`, `voice_tracking_enabled`, `attendance_processed_at`
  - [ ] `patt.raid_attendance`: `minutes_present`
  - [ ] `common.discord_config`: 5 new attendance config columns
  - [ ] `common.screen_permissions`: seed row for `attendance_report` (Officer+, level 4)
- [ ] ORM models updated (`VoiceAttendanceLog`, `RaidEvent`, `RaidAttendance`, `DiscordConfig`)
- [ ] `intents.voice_states = True` in `bot.py`
- [ ] `VoiceAttendanceCog` registered in `bot.py` (feature-flag-gated)
- [ ] `attendance_processor.py` with full span-reconstruction algorithm
- [ ] Scheduler: post-raid processing job (30 min after `end_time_utc`)
- [ ] `GET /admin/attendance` page route + `attendance.html` template
- [ ] Season grid rendered as player × raid-night table
- [ ] Cell tooltip/popover with minutes detail + excused toggle
- [ ] Per-event detail panel (on column header click)
- [ ] Unlinked users panel
- [ ] Settings panel (GL-only, inline save)
- [ ] API routes (6 endpoints)
- [ ] `/admin/players-data` updated with `attendance_status` + `attendance_summary`
- [ ] Player Manager: attendance dot badge on player cards
- [ ] Admin nav: "Attendance" link added to sidebar
- [ ] Tests (unit + integration + regression)

---

## Open Questions / Future Considerations

**Q: What if Raid-Helper already tracks attendance?**
Raid-Helper has its own attendance tracking via sign-up confirmation. Our `source` column
distinguishes the two. Voice tracking is more reliable for "who actually showed up" vs.
"who clicked Accept." We can show both on the report.

**Q: What about text channel activity as a proxy for bench players?**
Some guilds allow benched players to be "on call" watching via stream. Not in scope for
4.7 — voice presence is the clear, unambiguous signal. Future phase could add optional
text-channel lurker tracking as a secondary source.

**Q: What about split runs or alt runs in different VC?**
Per-event `voice_channel_id` override handles this. Officer sets the correct channel when
creating the alt run event. Both runs are tracked independently.

**Q: Can members see their own attendance?**
Not in Phase 4.7 — data is officer-only. A future "My Attendance" settings page could
expose personal stats. The `/settings/availability` page is the natural home for this.

**Q: What about the Warcraft Logs integration (Phase 4.5)?**
WCL knows exactly who was in the raid. A future reconciliation step could cross-check
voice attendance against WCL raid roster — flagging cases where someone was in VC but
not in the logs (AFK? Watching from bench?). Nice to have, not in scope here.

**Q: Attendance and loot / DKP?**
Not in scope for PATT (casual heroic guild). The data model is neutral — if someone
wanted to add loot-council attendance weighting in the future, the `attended` flag and
`minutes_present` column are the inputs.
