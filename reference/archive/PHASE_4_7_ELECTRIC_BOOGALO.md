# Phase 4.7 — Voice Channel Attendance Tracking

> Electric Boogaloo: because apparently we're building a full guild management platform now.

## Goal

Track raid attendance automatically using **two complementary sources**: Warcraft Logs
(who was in the encounter) and Discord voice (who was in the channel). WCL is the primary
source of truth for "did you raid." Discord voice fills in bench/watching players who
never log a parse, and is the *only* source of late-arrival and early-departure timing.

Collect raw voice presence data per event, derive attendance verdicts with WCL reconciliation,
and display results on an officer attendance screen — a season-wide grid growing
left-to-right as raids progress. Flag players who are habitually late or leave early
so officers can address it before it becomes a confrontation.

---

## Why This Is Valuable

- **Zero friction**: no one has to take roll, no sign-in form, no manual check. Bot watches and WCL confirms.
- **Objective**: voice presence + WCL parses are unambiguous facts.
- **Fair credit**: bench/watching players (in voice but not parsed) still get credit for showing up.
- **Timing accountability**: catches the player who's always 20 minutes late or dips before last boss without anyone having to say anything.
- **Historical**: gives officers a real picture of attendance trends over a season, not just vibes.
- **Accountability without toxicity**: data surfaces concerns before they become confrontations.

---

## Source Hierarchy

Two sources feed into `patt.raid_attendance`. They are complementary, not competing.

| Source value | Meaning |
|---|---|
| `'wcl'` | Character appeared in WCL `raid_reports.attendees` — confirmed in-raid |
| `'voice'` | In Discord voice channel only — benched, watching, or WCL not yet synced |
| `'wcl+voice'` | Appeared in both — the normal case for active raiders once both sources are live |
| `'raid_helper'` | From Raid-Helper sign-up confirmation (pre-existing, preserved) |
| `'manual'` | Manually set by an officer (pre-existing) |

**WCL is authoritative for "attended."** If someone is in WCL but not in voice (they raided
from the couch, voice crashed, whatever) they still get `attended = TRUE`. Voice fills
in people WCL missed (bench, watchers) and adds timing data.

---

## Existing Infrastructure (No Re-Work Needed)

| Existing piece | What we get for free |
|---|---|
| `discord_config.raid_voice_channel_id` | Default voice channel already stored |
| `patt.raid_events` with `start_time_utc` / `end_time_utc` | Raid window bounds |
| `patt.raid_attendance` with `attended BOOL` + `source VARCHAR` | Verdict + origin columns |
| `guild_identity.discord_users` | Maps Discord user IDs → players |
| `guild_identity.raid_reports` with `attendees JSONB` | WCL character attendee list |
| `guild_identity.wow_characters` + `player_characters` | Character name → player bridge |
| `guild_identity.audit_issues` | Officer notification mechanism |
| Bot `on_voice_state_update` event | discord.py fires natively |

---

## Prerequisites

- Phase 4.3 complete (migration 0034)
- Phase 4.5 complete (migration 0039, WCL integration live)
- `patt.raid_events` populated with upcoming events
- Discord bot running with `GUILD_VOICE_STATES` intent enabled

---

## Database Migration: 0041_voice_attendance

> Migration 0041 (0040 was AH Pricing).

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
    ADD COLUMN minutes_present SMALLINT,        -- NULL until voice-processed
    ADD COLUMN first_join_at   TIMESTAMPTZ,     -- first VC join during event window
    ADD COLUMN last_leave_at   TIMESTAMPTZ,     -- last VC departure (or end_time_utc if never left)
    ADD COLUMN joined_late     BOOLEAN,         -- TRUE if first_join > start + grace; NULL if no voice data
    ADD COLUMN left_early      BOOLEAN;         -- TRUE if last_leave < end - grace; NULL if no voice data
```

**Design note:** `joined_late` and `left_early` are derived from voice data at processing
time. They are NULL for WCL-only rows where no voice data was recorded. This lets the
attendance screen accurately distinguish "no voice data" from "was on time."

#### `common.discord_config`

```sql
ALTER TABLE common.discord_config
    ADD COLUMN attendance_min_pct            SMALLINT NOT NULL DEFAULT 75,
    ADD COLUMN attendance_late_grace_min     SMALLINT NOT NULL DEFAULT 10,
    ADD COLUMN attendance_early_leave_min    SMALLINT NOT NULL DEFAULT 10,
    ADD COLUMN attendance_trailing_events    SMALLINT NOT NULL DEFAULT 8,
    ADD COLUMN attendance_habitual_window    SMALLINT NOT NULL DEFAULT 5,
    ADD COLUMN attendance_habitual_threshold SMALLINT NOT NULL DEFAULT 3,
    ADD COLUMN attendance_feature_enabled    BOOL     NOT NULL DEFAULT FALSE;
```

**Field meanings:**

| Field | Default | Meaning |
|---|---|---|
| `attendance_min_pct` | 75 | Must be in VC for ≥ 75% of the (adjusted) raid window to count as attended via voice |
| `attendance_late_grace_min` | 10 | First 10 min excluded from effective window — arrivals within 10 min aren't penalized |
| `attendance_early_leave_min` | 10 | Last 10 min excluded — leaving up to 10 min early isn't penalized |
| `attendance_trailing_events` | 8 | Rolling window for "at risk" status on Player Manager |
| `attendance_habitual_window` | 5 | How many recent events to check for habitual late/early behavior |
| `attendance_habitual_threshold` | 3 | N occurrences within window triggers an officer flag |
| `attendance_feature_enabled` | FALSE | Master switch — tracking and report inactive until enabled |

> **Grace period mechanics:** A 2-hour raid with 10-min late grace and 10-min early
> leave grace has an **effective window** of 100 minutes. You need to be present for
> 75% of 100 minutes = 75 minutes to count as attended via voice. If you join at minute 11,
> you have 89 of 100 effective minutes available — you still need 75 of them.

> **`joined_late` vs. grace:** Grace periods determine whether voice attendance *counts*.
> `joined_late = TRUE` is a factual flag — set whenever `first_join_at > start + grace`,
> regardless of whether the player still cleared the threshold. An on-time arrival is
> `joined_late = FALSE` even if it didn't help them clear the threshold.

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

## Task 2: Attendance Processor (Two-Pass Reconciliation)

### File: `src/sv_common/guild_sync/attendance_processor.py` (new)

Runs after a raid ends. Two sequential passes produce the final verdict in
`patt.raid_attendance`.

---

### Pass 1: WCL Reconciliation

**What it does:** Reads `guild_identity.raid_reports` for reports matching the event
date and marks those characters as attended.

**WCL → event matching:**
- Match `raid_reports.raid_date::date = raid_events.event_date`
- If `raid_events.log_url` is set, prefer matching by report code extracted from the URL
- Multiple reports on the same date (split runs) are all included

**WCL → player resolution:**
```
raid_reports.attendees[*].name (case-insensitive)
  → guild_identity.wow_characters.name (LOWER() match)
  → player_characters.player_id
  → guild_identity.players.id
```
Characters not found in `wow_characters` are logged as warnings (unsynced alts, pugs).

**Upsert logic:**
```sql
INSERT INTO patt.raid_attendance (event_id, player_id, attended, source, ...)
VALUES (...)
ON CONFLICT (event_id, player_id) DO UPDATE
  SET attended = TRUE,
      source   = CASE
                   WHEN raid_attendance.source = 'voice' THEN 'wcl+voice'
                   ELSE 'wcl'
                 END
-- Preserves signed_up, noted_absence; does NOT overwrite minutes_present/timing
```

If no WCL report exists for this event (WCL not configured, sync hasn't run yet, or
the report hasn't been uploaded), Pass 1 is a no-op and Pass 2 handles attendance alone.

---

### Pass 2: Discord Voice Processing

**What it does:** Reads `patt.voice_attendance_log` for this event, reconstructs
presence spans, and applies the configured threshold rules.

**Algorithm:**

```
For each (discord_user_id, event_id) pair in the log:

1. Reconstruct presence spans:
   - Sort log rows by occurred_at
   - Pair join → leave events into spans [join_time, leave_time]
   - If last action is a 'join' (user still in VC at raid end),
     treat end_time_utc as the implicit leave

2. Compute timing flags (before grace clipping):
   first_join = earliest join_time for this user
   last_leave = latest leave_time (or implicit end_time_utc)
   joined_late = first_join > (start_time_utc + attendance_late_grace_min)
   left_early  = last_leave < (end_time_utc - attendance_early_leave_min)

3. Clip spans to the effective window:
   effective_start = event.start_time_utc + grace_late
   effective_end   = event.end_time_utc   - grace_early_leave
   Clip each span to [effective_start, effective_end]

4. Sum total effective seconds present → minutes_present

5. effective_window_seconds = (effective_end - effective_start).total_seconds()
   presence_pct = total_present / effective_window_seconds * 100
   voice_attended = presence_pct >= attendance_min_pct

6. Resolve discord_user_id → player_id via discord_users table
   If no match: log warning, skip (unlinked Discord user in VC)

7. UPSERT into patt.raid_attendance:
   - On INSERT: attended = voice_attended, source = 'voice'
   - On UPDATE (row already exists from WCL pass or raid_helper):
     * attended stays TRUE if already TRUE (WCL credit preserved)
     * source: 'wcl' → 'wcl+voice', 'raid_helper' → 'raid_helper+voice', else 'voice'
     * minutes_present, first_join_at, last_leave_at, joined_late, left_early always SET

8. Signed_up and noted_absence are NEVER overwritten
```

**Edge cases handled:**

| Situation | Handling |
|---|---|
| DC + rejoin | Multiple spans summed correctly |
| Was in VC before raid started | Span clipped to effective_start — no bonus |
| Never left at raid end | Last span closed at `end_time_utc` |
| Joined after raid ended | All spans outside window → 0% → voice_attended=FALSE; timing flags still set |
| Signed up but never joined VC | `signed_up=TRUE`, attended depends on WCL; no voice timing data |
| Joined VC but not signed up | Walk-ins counted; `signed_up=FALSE`, `attended=TRUE`, `source='voice'` |
| In WCL but not in voice | `attended=TRUE`, `source='wcl'`, timing fields NULL |
| Unlinked Discord user | Logged as warning; stored in `voice_attendance_log` for re-processing |
| No WCL report uploaded yet | Pass 1 skipped; voice is sole source; source='voice'; re-process after WCL syncs |

---

### Pass 3: Habitual Behavior Check

After both passes complete for an event, scan the roster for habitual late/early patterns.

```python
def check_habitual_patterns(pool, event_id, config):
    """
    For each player in today's processed attendance:
    - Count joined_late=TRUE in last attendance_habitual_window events
    - Count left_early=TRUE in last attendance_habitual_window events
    - If count >= attendance_habitual_threshold: raise officer flag
    """
```

**Flag delivery:** Post an embed to the audit Discord channel (existing `reporter.py`
pattern) listing:
```
⏰ Habitual Attendance Patterns — [Event Date]

Joined late (3/5 recent raids):
  • Trogmoon — late 20m, 15m, 8m on Mar 25 / Apr 1 / Apr 8

Left early (3/5 recent raids):
  • Rocketfuel — left 30m early on Mar 25 / Apr 1 / Apr 8
```

**Important:** This is an informational flag, not an automated penalty. Officers decide
whether to act on it. The flag is re-evaluated after every event — it will fire again
next raid if the pattern continues. The audit channel is the right delivery point: it's
officer-only, logged, and the existing audit embed pattern fits naturally.

**No duplicate flood guard needed** for Phase 4.7 — the flag fires at most once per event
(post-processing). If that becomes noisy, a "flag acknowledged" state can be added later.

---

### Scheduler Integration

```python
# Run 30 minutes after each scheduled raid end time
# Looks for events where end_time_utc < NOW() AND attendance_processed_at IS NULL
# AND voice_tracking_enabled IS TRUE
```

Officers can also trigger manual re-processing from the attendance page (useful for
threshold tuning after the fact, or after a delayed WCL upload).

---

## Task 3: Attendance Screen

### Route: `GET /admin/attendance`

New admin page. Officer+ required (screen permission: `attendance_report`).

---

### Primary View: Season Grid

The main panel of the page. Two-dimensional grid: players on rows, raid nights on columns.

```
┌─────────────────────────┬──────────┬──────────┬──────────┬──────────┬──────────────┐
│ Player                  │ Mar 18   │ Mar 25   │ Apr 1    │ Apr 8    │  Total       │
├─────────────────────────┼──────────┼──────────┼──────────┼──────────┼──────────────┤
│ 🟡 Trogmoon  [Officer]  │    ✓     │  ✓ ⏰   │    ✓     │  [live]  │  3/3  100%   │
│ ⚪ Rocketfuel [Member]  │    ✓     │    ~     │  ✓ 🚪   │  [live]  │  2/3   66%   │
│ ⚪ Shadowvaca [Member]  │    ✗     │    ✓     │    ✓     │  [live]  │  2/3   66%   │
│ ⚪ Newguy    [Initiate] │    —     │    —     │    ✓     │  [live]  │  1/1  100%   │
└─────────────────────────┴──────────┴──────────┴──────────┴──────────┴──────────────┘
```

**Cell values:**

| Symbol | Meaning | Color |
|---|---|---|
| `✓` | Attended (WCL confirmed or voice threshold met) | Green |
| `✓ ⏰` | Attended but joined late | Green + amber clock icon |
| `✓ 🚪` | Attended but left early | Green + amber door icon |
| `✓ ⏰🚪` | Attended but both late and early | Both icons |
| `✗` | Absent (no WCL presence, no qualifying voice presence) | Red |
| `~` | Excused (`noted_absence = TRUE`) | Amber — counts neutral |
| `—` | No data (player not yet in roster at event date, or event pre-dates tracking) | Grey dim |
| `[live]` | Event is today/in-progress — tracking active, not yet processed | Blue pulse |
| `[pending]` | Event ended, processing not yet run | Grey italic |

**Timing icons are only shown when voice data is available.** WCL-only rows (`source='wcl'`,
`joined_late IS NULL`) show a plain `✓` — no timing claim is made.

**Clicking any ✓ or ✗ cell** opens a tooltip/popover with:
- Source: "WCL + Voice" / "WCL only" / "Voice only"
- Minutes present / total minutes (when voice data exists)
- Presence %
- Joined at / left at (or "no voice data")
- Toggle "Mark as Excused" (officer action, updates `noted_absence`)

**Column headers** are clickable — show a per-event summary panel (see below).

**Player row — habitual flag:** If a player has `joined_late` or `left_early` on ≥
`attendance_habitual_threshold` of the last `attendance_habitual_window` events, show
a `⚠ Habitual` badge on their row. Tooltip: "Late to 3 of last 5 raids."

**Row sorting:** default by rank level desc, then player name alpha. Sortable by total
%, name, streak, or habitual flag.

**Season selector:** top-right dropdown. Defaults to current active season.

---

### Per-Event Detail Panel

Clicking a column header slides open a detail panel below the grid (or a modal):

- Event title, date, start → end times, voice channel used
- WCL report link (if `raid_events.log_url` set)
- Summary line: "17 attended · 3 absent · 2 excused · 20 signed up · source: WCL+Voice"
- Full table: player | source | joined_at | left_at | minutes | pct | result
- "Re-process" button (Officer+) — re-runs both passes for this event
- `voice_tracking_enabled` toggle — disable retroactively if it was an alt night

---

### Player Summary Row (footer)

Guild-wide attendance rate for each raid night (e.g., "17/20 = 85%"). Useful for
spotting problem nights vs. problem players.

---

### Unlinked Users Panel

Collapsed panel, shown only when there are unresolved Discord users in the log.
Shows Discord user IDs from `voice_attendance_log` that couldn't be resolved to a player.
Officers can dismiss (not a guild member) or note for manual linking.

---

### Settings Panel (Guild Leader only)

Collapsible section at the bottom of the page:

```
[x] Enable voice attendance tracking
    The bot must have View Channel + Connect permission on the raid voice channel.

Minimum presence         [75] % of the adjusted raid window
Late arrival grace       [10] minutes  (first N min excluded from the window)
Early leave grace        [10] minutes  (last N min excluded from the window)
Trailing window          [ 8] events   (used for "at risk" status in Player Manager)

Habitual behavior alerts
  Flag window            [ 5] events   (how many recent events to check)
  Flag threshold         [ 3] occurrences  (N within window = officer alert)
```

PATCH `/api/v1/admin/attendance/settings` on save. Reloads the config cache.

---

## Task 4: Player Manager Integration

### File: `src/guild_portal/static/js/players.js`

Add a small attendance badge to each player card in the Player Manager (`/admin/players`).
Officer-visible only (already behind auth).

**Badge appearance:** Colored dot with tooltip, adjacent to the rank badge.

| Dot color | Meaning |
|---|---|
| Green | ≥ threshold over trailing N events |
| Amber | Between 50% and threshold |
| Red | < 50% |
| Grey | Fewer than 3 events in the trailing window — not enough data yet |
| (none) | `attendance_feature_enabled = FALSE` |

The dot data comes from the `/admin/players-data` endpoint. Add `attendance_status`
(`good` | `at_risk` | `concern` | `new` | `none`) and `attendance_summary` (`"6/8 raids"`)
to each player record in the response.

**This is informational only** — it does not block sign-ups, change Discord roles, or
automatically bench anyone.

---

## Task 5: API Endpoints

### New Routes in `src/guild_portal/api/admin_routes.py`

```
GET  /api/v1/admin/attendance/season               → Season grid data (players × events matrix)
GET  /api/v1/admin/attendance/event/{id}           → Per-event breakdown with raw presence
POST /api/v1/admin/attendance/event/{id}/reprocess → Trigger re-processing (both passes)
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
            {"id": 42, "date": "2026-03-18", "title": "Heroic Raid", "processed": true, "has_wcl": true},
            {"id": 43, "date": "2026-03-25", "title": "Heroic Raid", "processed": true, "has_wcl": true},
            {"id": 44, "date": "2026-04-01", "title": "Heroic Raid", "processed": false, "has_wcl": false}
        ],
        "players": [
            {
                "id": 7,
                "name": "Trogmoon",
                "rank": "Officer",
                "rank_level": 4,
                "attendance": {
                    "42": {"status": "attended", "source": "wcl+voice", "minutes_present": 118, "pct": 98, "joined_late": false, "left_early": false},
                    "43": {"status": "attended", "source": "wcl+voice", "minutes_present": 95,  "pct": 79, "joined_late": true,  "left_early": false},
                    "44": {"status": "pending"}
                },
                "total_attended": 2,
                "total_eligible": 2,
                "pct": 100,
                "streak": 2,
                "attendance_status": "good",
                "habitual_late": true,
                "habitual_early": false,
                "habitual_summary": "Late to 3 of last 5 raids"
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

[Scheduler: 30 min after end_time_utc]  [OR: manual reprocess via admin UI]
        │
        ▼
attendance_processor.process_event(event_id)
        │
        ├─ PASS 1 — WCL Reconciliation
        │   ├─ Load raid_reports where raid_date::date = event_date
        │   ├─ Resolve attendees[*].name → player_id (case-insensitive, wow_characters bridge)
        │   └─ UPSERT raid_attendance (attended=TRUE, source='wcl')
        │
        ├─ PASS 2 — Voice Processing
        │   ├─ Reconstruct spans from voice_attendance_log
        │   ├─ Compute timing flags (joined_late, left_early)
        │   ├─ Apply grace periods + threshold
        │   ├─ Resolve discord_user_id → player_id
        │   └─ UPSERT raid_attendance (preserve WCL credit, add voice timing)
        │
        ├─ PASS 3 — Habitual Check
        │   ├─ Scan last N events for joined_late / left_early patterns
        │   └─ If threshold met: post officer alert to audit channel
        │
        └─ Mark event.attendance_processed_at = NOW()

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
minutes_present   : Optional[int]       # derived from voice log spans; NULL for WCL-only rows
first_join_at     : Optional[datetime]  # earliest VC join during window
last_leave_at     : Optional[datetime]  # latest VC departure (or implicit raid end)
joined_late       : Optional[bool]      # NULL when no voice data
left_early        : Optional[bool]      # NULL when no voice data
```

**`DiscordConfig`** — add:
```python
attendance_min_pct            : int  = 75
attendance_late_grace_min     : int  = 10
attendance_early_leave_min    : int  = 10
attendance_trailing_events    : int  = 8
attendance_habitual_window    : int  = 5
attendance_habitual_threshold : int  = 3
attendance_feature_enabled    : bool = False
```

---

## Tests

### Unit Tests

- `attendance_processor.py` Pass 1: WCL attendee resolves to player, marks `attended=TRUE, source='wcl'`
- `attendance_processor.py` Pass 1: character name not in `wow_characters` → warning, skipped
- `attendance_processor.py` Pass 1: voice row already exists → source upgrades to `'wcl+voice'`
- `attendance_processor.py` Pass 1: no WCL report for event date → no-op, Pass 2 proceeds
- `attendance_processor.py` Pass 2: span reconstruction — DC+rejoin, late joiner, early leaver, grace boundary, no-show
- `attendance_processor.py` Pass 2: `joined_late` and `left_early` computed from raw times (not clipped spans)
- `attendance_processor.py` Pass 2: WCL-credited player gets timing flags added, `attended` stays TRUE
- `attendance_processor.py` Pass 2: `presence_pct` calculation, threshold boundary (exactly at min_pct)
- `attendance_processor.py` Pass 2: unlinked discord_user handled gracefully (no exception, warning logged)
- `attendance_processor.py` Pass 2: `signed_up` and `noted_absence` preserved across both passes
- `attendance_processor.py` Pass 3: habitual_late fires when joined_late count ≥ threshold in window
- `attendance_processor.py` Pass 3: habitual check doesn't fire below threshold
- `voice_attendance.py`: handler ignores wrong channel, ignores events outside window
- `voice_attendance.py`: handler correctly identifies active event from cache
- `voice_attendance.py`: cog not loaded when `attendance_feature_enabled = FALSE`
- Season grid API: correct matrix shape, correct cell statuses, correct totals
- Season grid API: `joined_late` / `left_early` flags propagated correctly to response

### Integration Tests (requires live DB)

- End-to-end WCL: insert synthetic `raid_reports` row → run processor → verify `raid_attendance` rows with `source='wcl'`
- End-to-end voice: insert synthetic `voice_attendance_log` rows → run processor → verify rows and timing flags
- Combined: WCL + voice both present → `source='wcl+voice'`, timing flags set, `attended=TRUE`
- Re-process: change threshold config, re-run, verify rows updated
- WCL + voice — WCL uploaded after initial voice run: re-process upgrades source to `'wcl+voice'`
- Noted absence: toggle via API, verify `~` status in grid response
- Unlinked user: log row with unknown discord_id → appears in unlinked panel, not in grid
- Habitual check: 3 events with `joined_late=TRUE` → audit channel message sent

### Regression

- Existing `raid_attendance` rows with `source='raid_helper'` unaffected by processor
- `/admin/players-data` includes `attendance_status` field (returns `"none"` when feature off)
- Player Manager renders without error when no attendance data exists yet
- WCL admin page attendance grid unaffected (uses `compute_attendance()` from `wcl_sync.py`, separate data path)

---

## Deliverables Checklist

- [ ] Migration `0041_voice_attendance`:
  - [ ] `patt.voice_attendance_log` table + indexes
  - [ ] `patt.raid_events`: `voice_channel_id`, `voice_tracking_enabled`, `attendance_processed_at`
  - [ ] `patt.raid_attendance`: `minutes_present`, `first_join_at`, `last_leave_at`, `joined_late`, `left_early`
  - [ ] `common.discord_config`: 7 new attendance config columns (including `habitual_window` + `habitual_threshold`)
  - [ ] `common.screen_permissions`: seed row for `attendance_report` (Officer+, level 4)
- [ ] ORM models updated (`VoiceAttendanceLog`, `RaidEvent`, `RaidAttendance`, `DiscordConfig`)
- [ ] `intents.voice_states = True` in `bot.py`
- [ ] `VoiceAttendanceCog` registered in `bot.py` (feature-flag-gated)
- [ ] `attendance_processor.py` with Pass 1 (WCL), Pass 2 (voice spans + timing flags), Pass 3 (habitual check + audit alert)
- [ ] Scheduler: post-raid processing job (30 min after `end_time_utc`)
- [ ] `GET /admin/attendance` page route + `attendance.html` template
- [ ] Season grid rendered as player × raid-night table
- [ ] Late/early timing icons on grid cells (only when voice data present)
- [ ] Habitual badge on player rows (only when pattern threshold met)
- [ ] Cell tooltip/popover with source, minutes detail, timing, excused toggle
- [ ] Per-event detail panel (on column header click, includes WCL report link)
- [ ] Unlinked users panel
- [ ] Settings panel (GL-only, inline save, includes habitual config fields)
- [ ] API routes (6 endpoints)
- [ ] `/admin/players-data` updated with `attendance_status` + `attendance_summary`
- [ ] Player Manager: attendance dot badge on player cards
- [ ] Admin nav: "Attendance" link added to sidebar
- [ ] Tests (unit + integration + regression)

---

## Open Questions / Future Considerations

**Q: What if someone is in WCL but voice data says they left 30 min early?**
WCL confirms they raided — `attended=TRUE` is never revoked. `left_early=TRUE` is still
set from voice data as a factual observation. The cell shows `✓ 🚪`. The player gets
credit but the timing data is visible. Officers can interpret this as "benched for last
boss" vs. "bailed early."

**Q: WCL report uploaded days after the raid — timing?**
The re-process button on the per-event detail panel handles this. An officer presses it
after WCL syncs; Pass 1 runs and upgrades `source='voice'` rows to `'wcl+voice'` where
applicable. The scheduler also runs re-processing on demand so no data is lost.

**Q: What about split runs or alt runs in different VC?**
Per-event `voice_channel_id` override handles this. Officer sets the correct channel when
creating the alt run event. Both runs are tracked independently.

**Q: Can members see their own attendance?**
Not in Phase 4.7 — data is officer-only. A future "My Attendance" settings page could
expose personal stats. The `/settings/availability` page is the natural home for this.

**Q: What about text channel activity as a proxy for bench players?**
Not in scope. Voice presence is the clear, unambiguous signal. The WCL check already
handles "active raider." Future phase could add optional text-channel lurker tracking
as a secondary source.

**Q: Attendance and loot / DKP?**
Not in scope for PATT (casual heroic guild). The data model is neutral — if someone
wanted to add loot-council attendance weighting, `attended`, `minutes_present`, and
`source` are all the inputs needed.

**Q: What about the existing `compute_attendance()` in `wcl_sync.py`?**
That function feeds the `/admin/warcraft-logs` page attendance grid. It is a separate,
independent display using raw WCL data. Phase 4.7 does NOT replace or modify it.
The two attendance systems coexist: WCL admin page = raw WCL stats; Phase 4.7 attendance
page = reconciled, season-oriented attendance record with voice + WCL combined.
