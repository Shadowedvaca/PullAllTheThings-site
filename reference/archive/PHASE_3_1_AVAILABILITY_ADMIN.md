# Phase 3.1 — Admin Availability Dashboard + Event Day System

## Goal

Move availability analysis from dead legacy pages into the admin panel. Let officers mark days as
event days with labels and times. This creates the single source of truth (`patt.recurring_events`)
that drives the front page schedule, roster view schedule tab, raid tools day cards, and auto-booking.

---

## Database Migration: 0013_recurring_events

### New table: `patt.recurring_events`

```sql
CREATE TABLE patt.recurring_events (
    id SERIAL PRIMARY KEY,
    label VARCHAR(100) NOT NULL,                        -- "Heroic Raid Night"
    event_type VARCHAR(30) NOT NULL DEFAULT 'raid',     -- 'raid', 'mythicplus', 'social', 'other'
    day_of_week INTEGER NOT NULL                        -- 0=Mon … 6=Sun (ISO)
        CHECK (day_of_week BETWEEN 0 AND 6),
    default_start_time TIME NOT NULL DEFAULT '21:00',   -- stored in EST (display timezone)
    default_duration_minutes INTEGER NOT NULL DEFAULT 120,
    discord_channel_id VARCHAR(25),                     -- Raid-Helper signup channel
    raid_helper_template_id VARCHAR(50) DEFAULT 'wowretail2',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    display_on_public BOOLEAN NOT NULL DEFAULT TRUE,    -- drives front page
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Additions to `common.discord_config`

```sql
ALTER TABLE common.discord_config
    ADD COLUMN raid_helper_api_key VARCHAR(200),
    ADD COLUMN raid_helper_server_id VARCHAR(25),
    ADD COLUMN raid_creator_discord_id VARCHAR(25),
    ADD COLUMN raid_channel_id VARCHAR(25),
    ADD COLUMN raid_voice_channel_id VARCHAR(25),
    ADD COLUMN raid_default_template_id VARCHAR(50) DEFAULT 'wowretail2';
```

---

## ORM Updates (`src/sv_common/db/models.py`)

### New model: `RecurringEvent`

```python
class RecurringEvent(Base):
    __tablename__ = "recurring_events"
    __table_args__ = {"schema": "patt"}

    id = Column(Integer, primary_key=True)
    label = Column(String(100), nullable=False)
    event_type = Column(String(30), nullable=False, default="raid")
    day_of_week = Column(Integer, nullable=False)
    default_start_time = Column(Time, nullable=False)
    default_duration_minutes = Column(Integer, nullable=False, default=120)
    discord_channel_id = Column(String(25))
    raid_helper_template_id = Column(String(50), default="wowretail2")
    is_active = Column(Boolean, nullable=False, default=True)
    display_on_public = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

### Update `DiscordConfig` model — add 6 Raid-Helper columns

```python
raid_helper_api_key = Column(String(200))
raid_helper_server_id = Column(String(25))
raid_creator_discord_id = Column(String(25))
raid_channel_id = Column(String(25))
raid_voice_channel_id = Column(String(25))
raid_default_template_id = Column(String(50), default="wowretail2")
```

---

## Admin Page: `/admin/availability`

**File:** `src/patt/templates/admin/availability.html`
**Nav block:** `nav_availability` (label: "Availability")
**Auth:** Officer+ required (same as all admin pages)

### Section 1 — Availability Grid (7-day analysis)

7 cards displayed in a responsive grid (2–3 per row). Each card:

- **Day name** (Monday … Sunday)
- **Available count** — number of players with `patt.player_availability` row for that `day_of_week`
- **Percentage bar** — `count / total_active_players * 100`
  - Green if ≥ 70%
  - Amber if 40–69%
  - Red if < 40%
- **Weighted score** — sum of `guild_ranks.scheduling_weight` for all available players on that day
  (weights: Initiate=0, Member=1, Veteran=3, Officer/GL=5)
- **Collapsible player list** — click to expand; shows each available player's:
  - Display name
  - Role icon (Tank/Healer/Melee/Ranged emoji or colored dot)
  - Rank name

**Data source:** Existing `get_all_availability_for_day()` in `src/patt/services/availability_service.py`.
Call for each of the 7 days (0–6) and aggregate in the page route.

### Section 2 — Event Day Configuration

Table with one row per day of week (Mon–Sun). Each row:

| Column | Control | Notes |
|--------|---------|-------|
| Day | Label (non-editable) | "Monday" … "Sunday" |
| Active | Checkbox | Maps to `is_active`. Unchecking deletes/deactivates the row. |
| Label | Text input | e.g. "Heroic Raid Night". Required when active. |
| Time (EST) | `<input type="time">` | Maps to `default_start_time`. |
| Duration | `<select>` | 60, 90, 120, 150, 180 min. Maps to `default_duration_minutes`. |
| Show on public | Toggle | Maps to `display_on_public`. Only relevant when active. |

**Auto-save:** On any change (checkbox, blur on text/time, select change) → PATCH the row.
If activating a day that has no DB row → POST to create.
If deactivating → PATCH `is_active=False` (keep row, don't delete — preserves history).

---

## API Endpoints (add to `src/patt/api/admin_routes.py`)

### `GET /api/v1/admin/recurring-events`

Returns all rows from `patt.recurring_events` ordered by `day_of_week`, including inactive ones.

```json
{
  "ok": true,
  "data": [
    {
      "id": 1,
      "label": "Heroic Raid Night",
      "event_type": "raid",
      "day_of_week": 4,
      "default_start_time": "21:00",
      "default_duration_minutes": 120,
      "discord_channel_id": "1234567890",
      "raid_helper_template_id": "wowretail2",
      "is_active": true,
      "display_on_public": true
    }
  ]
}
```

### `POST /api/v1/admin/recurring-events`

Create a new recurring event row.

```json
// Request body
{
  "label": "Heroic Raid Night",
  "event_type": "raid",
  "day_of_week": 4,
  "default_start_time": "21:00",
  "default_duration_minutes": 120,
  "discord_channel_id": null,
  "is_active": true,
  "display_on_public": true
}
```

Validation: `day_of_week` must be 0–6. Only one active row per `day_of_week` allowed (enforce in app logic).

### `PATCH /api/v1/admin/recurring-events/{id}`

Partial update — only fields present in body are updated.

```json
// Request body (any subset of fields)
{
  "label": "Mythic+ Push Night",
  "is_active": false
}
```

### `DELETE /api/v1/admin/recurring-events/{id}`

Hard delete (or set `is_active=False` — either acceptable). Use hard delete for clean data.

---

## New API Endpoint: `GET /api/v1/admin/availability-by-day`

**Shared by Phase 3.1 (availability page) and Phase 3.4 (raid tools).**

Returns per-day availability summary including role breakdown and weighted score.

```json
{
  "ok": true,
  "data": {
    "total_active_players": 38,
    "days": [
      {
        "day_of_week": 0,
        "day_name": "Monday",
        "available_count": 22,
        "availability_pct": 57.9,
        "weighted_score": 48,
        "recurring_event": {
          "id": 1,
          "label": "Heroic Raid Night",
          "default_start_time": "21:00",
          "default_duration_minutes": 120,
          "is_active": true,
          "display_on_public": true
        },
        "role_breakdown": {
          "Tank": 3,
          "Healer": 5,
          "Melee DPS": 8,
          "Ranged DPS": 6
        },
        "players": [
          {
            "player_id": 1,
            "display_name": "Trogmoon",
            "rank": "Guild Leader",
            "scheduling_weight": 5,
            "main_role": "Ranged DPS",
            "earliest_start": "21:00",
            "available_hours": 3.0
          }
        ]
      }
    ]
  }
}
```

Query logic:
1. Count `players WHERE is_active=TRUE` → `total_active_players`
2. For each day 0–6: query `player_availability JOIN players JOIN guild_ranks`
3. Resolve main role via `players.main_spec_id → specializations.default_role_id → roles.name`
4. LEFT JOIN `patt.recurring_events` on `day_of_week` (WHERE is_active=TRUE)

---

## Files Modified/Created

| File | Action |
|------|--------|
| `alembic/versions/0013_recurring_events.py` | NEW — creates recurring_events, alters discord_config |
| `src/sv_common/db/models.py` | ADD RecurringEvent model; UPDATE DiscordConfig with 6 columns |
| `src/patt/api/admin_routes.py` | ADD recurring-events CRUD + availability-by-day endpoints |
| `src/patt/pages/admin_pages.py` | ADD GET /admin/availability page route |
| `src/patt/templates/admin/availability.html` | NEW |
| `src/patt/templates/base_admin.html` | ADD Availability nav item (nav_availability block) |

---

## Verification Checklist

- [ ] `/admin/availability` loads as officer → 7-day grid renders with % bars
- [ ] Grid bars are color-coded (green/amber/red)
- [ ] Collapsible player list expands/collapses on click
- [ ] Check "Friday" as event day → POST creates DB row
- [ ] Set label "Heroic Raid Night" → auto-saved on blur
- [ ] Uncheck → PATCH sets is_active=FALSE
- [ ] `GET /api/v1/admin/availability-by-day` returns all 7 days with role breakdown
- [ ] Migration 0013 runs clean on fresh DB (`alembic upgrade head`)
