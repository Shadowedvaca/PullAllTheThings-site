# Build Plan: Configurable Attendance Rule Groups
> Features 3+4 of the attendance enhancement suite.
> Prerequisite: PLAN_ATTENDANCE_SNAPSHOT.md must be complete (requires `was_available`,
> `raid_helper_status`, and auto-excuse logic to be in place).

---

## Goal

A configurable rule engine that evaluates each player's attendance history against a set
of admin-defined rules, and surfaces the results as labeled groups at the top of the
attendance page. Rules can represent promotion suggestions, warnings, or any custom
grouping. Two seed rules ship with the migration.

---

## Concept Summary

Rules are stored in DB. Each rule has:
- A **group label** (e.g., "Promotion Suggestions") and **group type** (promotion / warning / info)
- **Target ranks** — which guild ranks this rule applies to
- **Conditions** — one or more metrics that must ALL be true for a player to match
- **Result rank** (promotion rules only) — the rank to suggest

On page load, the server evaluates all active rules against the current season's data
and returns a list of matches grouped by `group_label`. No background job needed — the
roster is small enough for synchronous evaluation.

When rule settings or excuse settings change, the page re-evaluates on next load.
No stored results — always computed fresh.

---

## New Database Table

### Migration 0064

```sql
CREATE TABLE patt.attendance_rules (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    group_label     VARCHAR(100) NOT NULL,
    group_type      VARCHAR(20)  NOT NULL CHECK (group_type IN ('promotion', 'warning', 'info')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    target_rank_ids INTEGER[] NOT NULL,
    result_rank_id  INTEGER NULL REFERENCES common.guild_ranks(id) ON DELETE SET NULL,
    conditions      JSONB NOT NULL DEFAULT '[]',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Seed Data (two built-in rules)

```sql
-- Rule 1: Initiate → Member
INSERT INTO patt.attendance_rules
    (name, group_label, group_type, is_active, target_rank_ids, result_rank_id, conditions, sort_order)
VALUES (
    'Consistent Initiate',
    'Promotion Suggestions',
    'promotion',
    TRUE,
    ARRAY(SELECT id FROM common.guild_ranks WHERE name = 'Initiate'),
    (SELECT id FROM common.guild_ranks WHERE name = 'Member'),
    '[
        {"type": "attendance_pct_in_window",  "window_days": 14, "operator": ">=", "value": 100},
        {"type": "min_events_per_week",        "window_days": 14, "operator": ">=", "value": 1}
    ]'::jsonb,
    10
);

-- Rule 2: Member → Veteran
INSERT INTO patt.attendance_rules
    (name, group_label, group_type, is_active, target_rank_ids, result_rank_id, conditions, sort_order)
VALUES (
    'Veteran Attendance',
    'Promotion Suggestions',
    'promotion',
    TRUE,
    ARRAY(SELECT id FROM common.guild_ranks WHERE name = 'Member'),
    (SELECT id FROM common.guild_ranks WHERE name = 'Veteran'),
    '[
        {"type": "attendance_pct_in_window",  "window_days": 56, "operator": ">=", "value": 95},
        {"type": "min_events_per_week",        "window_days": 56, "operator": ">=", "value": 1}
    ]'::jsonb,
    20
);
```

Note: The INSERT uses subselects on rank names. If the rank names differ in the actual DB,
the ARRAY() subselect returns an empty array and the rule is inactive. Verify rank names
before migrating; fall back to hardcoded IDs if needed.

---

## Condition Types

All conditions share the schema: `{"type": "...", "window_days": N, "operator": ">=", "value": N}`

### `attendance_pct_in_window`
- Look at all events in the active season with `start_time_utc >= NOW() - window_days days`
  and `NOT is_deleted`.
- For each player, compute:
  ```
  attended = COUNT where attended = TRUE
  excused  = COUNT where (noted_absence = TRUE OR auto_excused = TRUE)
  eligible = total_events - excused
  pct      = 100.0 * attended / GREATEST(eligible, 1)
  ```
- Player passes if `pct {operator} value`.
- If `eligible = 0` (all events excused or no events), player does NOT pass.

### `min_events_per_week`
- Same event window as above.
- Group events by ISO week (`DATE_TRUNC('week', event_date)`).
- For each week that has at least 1 eligible event (not excused for this player),
  check if the player attended >= `value` events that week.
- Player passes if ALL weeks with eligible events satisfy the threshold.
  (A week with 0 eligible events is skipped — doesn't break the streak.)
- If there are 0 weeks with eligible events, player does NOT pass.

### `consecutive_weeks_perfect` (reserved for future use)
Not needed for the two seed rules. Schema is defined so it can be added later.
Implementation: group by ISO week going backward from today, count consecutive weeks
where `attended = eligible` (100%) AND `eligible >= 1`. Pass if count >= value.

---

## Rule Evaluation Engine

### New Function: `evaluate_attendance_rules(pool) -> list[dict]`
Location: `src/sv_common/guild_sync/attendance_processor.py`

Returns a list of match objects:
```python
[
    {
        "rule_id": 1,
        "rule_name": "Consistent Initiate",
        "group_label": "Promotion Suggestions",
        "group_type": "promotion",
        "sort_order": 10,
        "result_rank_id": 3,
        "result_rank_name": "Member",
        "player_id": 42,
        "player_name": "Trogmoon",
        "current_rank_id": 2,
        "current_rank_name": "Initiate",
        "stats": {
            "pct": 100.0,
            "eligible": 4,
            "attended": 4,
            "weeks_checked": 2,
            "weeks_passed": 2
        }
    },
    ...
]
```

### Algorithm

```python
async def evaluate_attendance_rules(pool):
    async with pool.acquire() as conn:
        # Load settings
        settings = await conn.fetchrow("SELECT * FROM common.discord_config LIMIT 1")
        excuse_unavailable = settings["attendance_excuse_if_unavailable"]
        excuse_discord_absent = settings["attendance_excuse_if_discord_absent"]

        # Load active rules
        rules = await conn.fetch("SELECT * FROM patt.attendance_rules WHERE is_active ORDER BY sort_order")

        # Load active season
        season = await conn.fetchrow("SELECT * FROM patt.raid_seasons WHERE is_active LIMIT 1")
        if not season:
            return []

        # Load roster: active players with main char and not on hiatus
        players = await conn.fetch("""
            SELECT p.id, p.display_name, p.rank_id, gr.name as rank_name,
                   p.on_raid_hiatus, p.main_character_id
            FROM guild_identity.players p
            JOIN common.guild_ranks gr ON gr.id = p.rank_id
            WHERE p.is_active = TRUE
              AND p.main_character_id IS NOT NULL
              AND p.on_raid_hiatus = FALSE
        """)

        results = []
        for rule in rules:
            matching_players = [p for p in players if p["rank_id"] in rule["target_rank_ids"]]
            for player in matching_players:
                passes, stats = await _eval_player_rule(
                    conn, player["id"], rule, season["id"],
                    excuse_unavailable, excuse_discord_absent
                )
                if passes:
                    results.append({
                        "rule_id": rule["id"],
                        "rule_name": rule["name"],
                        "group_label": rule["group_label"],
                        "group_type": rule["group_type"],
                        "sort_order": rule["sort_order"],
                        "result_rank_id": rule["result_rank_id"],
                        "result_rank_name": ...,  # join or cache
                        "player_id": player["id"],
                        "player_name": player["display_name"],
                        "current_rank_id": player["rank_id"],
                        "current_rank_name": player["rank_name"],
                        "stats": stats,
                    })
        return results
```

### `_eval_player_rule(conn, player_id, rule, season_id, excuse_unavailable, excuse_discord_absent)`

1. Compute `window_start = NOW() - max(c["window_days"] for c in rule["conditions"]) days`
2. Fetch events: `patt.raid_events WHERE season_id = ? AND start_time_utc >= window_start AND NOT is_deleted ORDER BY start_time_utc`
3. Fetch attendance for player in those events: `patt.raid_attendance WHERE player_id = ? AND event_id IN (...)`
4. Build a dict: `event_id → attendance_row` (NULL if no row = absent, unexcused)
5. For each event, compute `auto_excused`:
   ```python
   auto_excused = (
       (row and row["was_available"] == False and excuse_unavailable)
       or (row and row["raid_helper_status"] == "absence" and excuse_discord_absent)
   )
   effectively_excused = (row and row["noted_absence"]) or auto_excused
   attended = row and row["attended"]
   ```
6. Evaluate each condition; collect stats. Return `(all_pass, stats_dict)`.

---

## API Endpoints

All under `/api/v1/admin/attendance/rules`:

### `GET /api/v1/admin/attendance/rules`
Returns all rules (active + inactive), sorted by sort_order. Used for settings UI.

### `POST /api/v1/admin/attendance/rules`
Create a new rule. Body: `{name, group_label, group_type, target_rank_ids, result_rank_id, conditions, sort_order, is_active}`.
Validate: `group_type` must be in `['promotion', 'warning', 'info']`; conditions must be
valid JSON array with known `type` values.

### `PATCH /api/v1/admin/attendance/rules/{id}`
Partial update any fields. Same validation as POST.

### `DELETE /api/v1/admin/attendance/rules/{id}`
Hard delete (these are config rows, not data rows).

### `GET /api/v1/admin/attendance/rule-matches`
Runs `evaluate_attendance_rules()` and returns matches. Called on page load from the
attendance page's initialization JS. Optional `?season_id=N` param (defaults to active season).

Response:
```json
{
  "ok": true,
  "data": {
    "groups": [
      {
        "group_label": "Promotion Suggestions",
        "group_type": "promotion",
        "matches": [
          {
            "rule_name": "Consistent Initiate",
            "player_id": 42,
            "player_name": "Trogmoon",
            "current_rank_name": "Initiate",
            "result_rank_name": "Member",
            "stats": {"pct": 100.0, "eligible": 4, "attended": 4, ...}
          }
        ]
      }
    ]
  }
}
```

---

## UI Changes (`templates/admin/attendance.html`)

### New "Rule Matches" section (above the attendance grid)

Render after `rule-matches` data loads. If no matches for any group, render nothing
(don't show empty cards).

Each group renders as a card:
```
┌─ Promotion Suggestions ──────────────────────────────────┐
│  Trogmoon      Initiate → Member    100% (4/4), 2 wks    │
│  Rocketfuel    Initiate → Member    100% (2/2), 2 wks    │
└──────────────────────────────────────────────────────────┘
```

Warning-type groups use the gold accent border instead of green.

Columns per row: Player name (links to Player Manager), Rule name, Stats summary string.
For promotion rules, show an arrow "→ Rank Name" in a badge.

### Rule Configuration — in Settings panel

Add a "Attendance Rules" section below the habitual check settings. Shows a table of
all rules with: Name, Group, Type, Ranks, Active toggle. "Edit" button opens a modal
or inline form.

**Rule editor fields:**
- Name (text input)
- Group Label (text input — freeform so admins can create new groups)
- Group Type (select: Promotion / Warning / Info)
- Active (toggle)
- Target Ranks (multi-select checkboxes from guild_ranks)
- Result Rank (select, only shown if group_type = promotion)
- Sort Order (number)
- Conditions (dynamic list):
  - Each condition row: Type dropdown | Window dropdown | Operator (fixed >=) | Value input
  - Type options: "Attendance % in window" | "Min events per week"
  - Window options: 7 days (1 wk) | 14 days (2 wks) | 28 days (4 wks) | 56 days (8 wks)
  - Add/remove condition buttons

On save, PATCH or POST. On delete, show confirmation before DELETE.
After any change, re-fetch rule-matches and re-render the top section.

---

## "Effectively Excused" Contract

Everywhere in the rule engine:
- `effectively_excused = noted_absence = TRUE OR (was_available = FALSE AND excuse_unavailable) OR (raid_helper_status = 'absence' AND excuse_discord_absent)`
- Excused events are **excluded** from the denominator (they don't count for or against)
- An event with `attended = FALSE AND NOT effectively_excused` is a real miss

This contract is identical to how the main grid's % is computed. Rules and the grid use
the same numbers — what you see in the grid drives what the rules see.

---

## Files to Create / Modify

| File | Change |
|---|---|
| `alembic/versions/0064_attendance_rules.py` | New table + 2 seed rules |
| `src/sv_common/guild_sync/attendance_processor.py` | Add `evaluate_attendance_rules()` + `_eval_player_rule()` |
| `src/guild_portal/api/admin_routes.py` | 5 new endpoints (rules CRUD + rule-matches) |
| `src/guild_portal/templates/admin/attendance.html` | Rule matches section + rule editor in settings |

No new service files. No scheduler changes needed (page-load evaluation).

---

## Testing

### Unit tests (no DB needed)
- `test_eval_pct_in_window`: Given synthetic event+attendance data, verify `attendance_pct_in_window`
  computes correctly with excused events excluded from denominator.
- `test_eval_pct_all_excused`: If all events are excused, player does NOT pass (eligible = 0).
- `test_eval_min_events_per_week_all_weeks_pass`: Player attended ≥1 event every week → pass.
- `test_eval_min_events_per_week_one_miss`: One week with eligible events but 0 attended → fail.
- `test_eval_min_events_skips_weeks_with_no_eligible`: Weeks where all events are excused
  for the player → those weeks are skipped, don't break the requirement.
- `test_eval_no_events_in_window`: No events → no pass (both conditions fail on eligible=0).
- `test_rule_only_applies_to_target_rank`: Player with wrong rank is not evaluated.

### Integration test (DB, skip-marked)
- `test_evaluate_rules_end_to_end`: Create players, events, attendance records in test DB,
  insert a rule, call `evaluate_attendance_rules()`, verify expected players appear in results.

### Manual smoke test
1. Deploy to dev.
2. Open Attendance page → verify "Promotion Suggestions" card appears if any initiates
   have 100% attendance over the past 2 weeks.
3. Edit a rule's window or threshold via settings UI → save → verify matches update.
4. Toggle a rule inactive → verify its group disappears from the top section.
5. Add a new "warning" group rule for players with < 50% attendance in 14 days →
   verify it groups correctly.

---

## Complexity Notes

- **ISO week boundary**: `DATE_TRUNC('week', event_date)` uses Monday as week start
  (PostgreSQL default). Ensure this matches the intuitive "raid week" for the guild.
  If raids are Wed+Sat, a week contains both. If window is 14 days and straddles a
  Monday, you get exactly 2 ISO weeks — as expected.
- **Season boundary**: Rules only look at the active season. A player who joined mid-season
  might have fewer events available than the window asks for. This is fine — `eligible`
  is based on what's actually in the DB, not a required count.
- **No auto-dismiss**: Once a promotion is made in the Player Manager (rank changes),
  the rule's `target_rank_ids` no longer matches that player — they naturally disappear
  from the suggestion list on next load. No special dismissal logic needed.
- **Performance**: For a 20-player roster and 50 events per season, the evaluation
  is trivial. If the roster ever grows to 100+ and season events grow to 200+, add a
  single query that bulk-fetches all attendance data for the season and pivots in Python
  instead of per-player queries.

---

## Open Questions to Resolve During Build

1. **Rank names in seed data**: Verify exact names in `common.guild_ranks` before writing
   the INSERT. Check: `SELECT id, name FROM common.guild_ranks ORDER BY level`.
2. **"Info" group type**: No result_rank needed, just a label. Useful for things like
   "Attendance Stars" or "Perfect Attendance" recognition. Design the card style for info
   groups (probably blue border or gold, not green or red).
3. **Rule editor UX**: The condition builder described above is intentionally simple —
   two fixed condition types, a window dropdown, and a value input. If more condition
   types are added later, the dropdown grows. Keep it simple for now.

---

## Version & Branch

- Branch: `feature/attendance-rules`
- Migration: 0064
- Prerequisite: migration 0063 from PLAN_ATTENDANCE_SNAPSHOT.md must be in main first
- Expected version bump: MINOR → `prod-v0.10.0` (or next available MINOR)
