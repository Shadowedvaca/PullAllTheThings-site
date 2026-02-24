# Phase 2.8 — Scheduling, Availability & Attendance Foundation

> **Status:** Current Phase
> **Prereqs:** Phase 2.7 complete — Player model live, 3NF schema deployed (migration 0007)
> **Goal:** Replace the old boolean availability system with time-window + weighted scheduling, add player preferences, and lay the attendance/season tables for the next feature build.

---

## Context

The old `patt.member_availability` table stored day-of-week booleans per guild_member. That data is garbage — it gave equal weight to everyone, causing raid times to optimize for members who never showed up while excluding officers. The 133 remaining rows have NULL player_ids and are unrecoverable. Drop the table entirely.

The new system:
- **Weighted scheduling** — rank determines how much a player's availability matters (Officers count 5x, Initiates count 0x)
- **Time windows** — instead of "available: yes/no", players specify earliest start time + hours available per day, enabling 2-hour raid slot optimization across timezones
- **Player preferences** — auto-invite to events (more preferences will come later)
- **Timezone-aware** — players set their timezone, all scheduling math converts to UTC
- **Attendance tables** — schema ready for tracking who shows up, seasonal resets, and automatic consequences (feature implementation is a future phase)

---

## Task 1: Migration 0008 — Schema Changes

Create `alembic/versions/0008_scheduling_and_attendance.py`.

### 1A: Modify existing tables

**`common.guild_ranks`** — add column:
```sql
ALTER TABLE common.guild_ranks
  ADD COLUMN scheduling_weight INTEGER NOT NULL DEFAULT 0;
```

Default seed values (update in Task 2):
| Rank | Level | scheduling_weight |
|------|-------|-------------------|
| Guild Leader | 5 | 5 |
| Officer | 4 | 5 |
| Veteran | 3 | 3 |
| Member | 2 | 1 |
| Initiate | 1 | 0 |

**`guild_identity.players`** — add columns:
```sql
ALTER TABLE guild_identity.players
  ADD COLUMN timezone VARCHAR(50) NOT NULL DEFAULT 'America/Chicago',
  ADD COLUMN auto_invite_events BOOLEAN NOT NULL DEFAULT FALSE;
```

### 1B: Create new tables

**`patt.player_availability`** — replaces member_availability:
```sql
CREATE TABLE patt.player_availability (
  id SERIAL PRIMARY KEY,
  player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
  day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
  -- 0=Monday, 1=Tuesday, ..., 6=Sunday (ISO weekday)
  earliest_start TIME NOT NULL,
  -- In the player's local timezone (stored as-is, converted at query time)
  available_hours NUMERIC(3,1) NOT NULL CHECK (available_hours > 0 AND available_hours <= 16),
  -- e.g. 2.0, 4.5, 12.0
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (player_id, day_of_week)
);
```

**`patt.raid_seasons`**:
```sql
CREATE TABLE patt.raid_seasons (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  -- e.g. "Season 2 - Liberation of Undermine"
  start_date DATE NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- The current season = MAX(start_date) WHERE start_date <= NOW() AND is_active = TRUE
-- No end_date column — seasons end when the next one starts
```

**`patt.raid_events`**:
```sql
CREATE TABLE patt.raid_events (
  id SERIAL PRIMARY KEY,
  season_id INTEGER REFERENCES patt.raid_seasons(id),
  title VARCHAR(200) NOT NULL,
  event_date DATE NOT NULL,
  start_time_utc TIMESTAMPTZ NOT NULL,
  end_time_utc TIMESTAMPTZ NOT NULL,
  raid_helper_event_id VARCHAR(30),
  -- Raid-Helper's event ID, nullable — populated when event is created via API
  discord_channel_id VARCHAR(25),
  log_url VARCHAR(500),
  -- Warcraft Logs / wipefest URL, nullable — populated after raid
  notes TEXT,
  created_by_player_id INTEGER REFERENCES guild_identity.players(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**`patt.raid_attendance`**:
```sql
CREATE TABLE patt.raid_attendance (
  id SERIAL PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES patt.raid_events(id),
  player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
  signed_up BOOLEAN NOT NULL DEFAULT FALSE,
  -- Did they sign up (via Raid-Helper or auto-invite)?
  attended BOOLEAN NOT NULL DEFAULT FALSE,
  -- Did they actually show up?
  character_id INTEGER REFERENCES guild_identity.wow_characters(id),
  -- Which character they played (nullable, populated from logs or manual)
  noted_absence BOOLEAN NOT NULL DEFAULT FALSE,
  -- TRUE = they told us ahead of time they couldn't make it (excused)
  source VARCHAR(20) NOT NULL DEFAULT 'manual',
  -- 'manual', 'raid_helper', 'warcraft_logs', 'auto'
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (event_id, player_id)
);
```

### 1C: Drop old table

```sql
DROP TABLE IF EXISTS patt.member_availability;
```

Remove the `MemberAvailability` model from `models.py` entirely.

---

## Task 2: Update Seed Data

Update `data/seed/ranks.json` to include `scheduling_weight`:
```json
[
  {"name": "Initiate", "level": 1, "description": "New member, probationary period", "scheduling_weight": 0},
  {"name": "Member", "level": 2, "description": "Full guild member", "scheduling_weight": 1},
  {"name": "Veteran", "level": 3, "description": "Long-standing core member", "scheduling_weight": 3},
  {"name": "Officer", "level": 4, "description": "Guild officer", "scheduling_weight": 5},
  {"name": "Guild Leader", "level": 5, "description": "Guild Leader", "scheduling_weight": 5}
]
```

Update `src/sv_common/db/seed.py` to handle the new field during upsert.

The migration itself should also UPDATE existing rows:
```sql
UPDATE common.guild_ranks SET scheduling_weight = 0 WHERE level = 1;
UPDATE common.guild_ranks SET scheduling_weight = 1 WHERE level = 2;
UPDATE common.guild_ranks SET scheduling_weight = 3 WHERE level = 3;
UPDATE common.guild_ranks SET scheduling_weight = 5 WHERE level = 4;
UPDATE common.guild_ranks SET scheduling_weight = 5 WHERE level = 5;
```

---

## Task 3: Update SQLAlchemy Models

### Modify `GuildRank` model:
Add `scheduling_weight: Mapped[int]` column.

### Modify `Player` model:
Add:
- `timezone: Mapped[str]` (VARCHAR(50), default 'America/Chicago')
- `auto_invite_events: Mapped[bool]` (default False)

Add relationship:
- `availability: Mapped[list["PlayerAvailability"]] = relationship(back_populates="player")`

### Remove `MemberAvailability` model entirely.

### Add new models:

**`PlayerAvailability`** (schema: patt):
```python
class PlayerAvailability(Base):
    __tablename__ = "player_availability"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    earliest_start: Mapped[time] = mapped_column(Time, nullable=False)
    available_hours: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    player: Mapped["Player"] = relationship(back_populates="availability")
```

**`RaidSeason`** (schema: patt):
```python
class RaidSeason(Base):
    __tablename__ = "raid_seasons"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    events: Mapped[list["RaidEvent"]] = relationship(back_populates="season")
```

**`RaidEvent`** (schema: patt):
```python
class RaidEvent(Base):
    __tablename__ = "raid_events"
    __table_args__ = {"schema": "patt"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("patt.raid_seasons.id")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    end_time_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    raid_helper_event_id: Mapped[Optional[str]] = mapped_column(String(30))
    discord_channel_id: Mapped[Optional[str]] = mapped_column(String(25))
    log_url: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by_player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    season: Mapped[Optional["RaidSeason"]] = relationship(back_populates="events")
    attendance: Mapped[list["RaidAttendance"]] = relationship(back_populates="event")
```

**`RaidAttendance`** (schema: patt):
```python
class RaidAttendance(Base):
    __tablename__ = "raid_attendance"
    __table_args__ = (
        UniqueConstraint("event_id", "player_id", name="uq_attendance_event_player"),
        {"schema": "patt"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("patt.raid_events.id"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.players.id"), nullable=False
    )
    signed_up: Mapped[bool] = mapped_column(Boolean, server_default="false")
    attended: Mapped[bool] = mapped_column(Boolean, server_default="false")
    character_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("guild_identity.wow_characters.id")
    )
    noted_absence: Mapped[bool] = mapped_column(Boolean, server_default="false")
    source: Mapped[str] = mapped_column(String(20), server_default="'manual'")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    event: Mapped["RaidEvent"] = relationship(back_populates="attendance")
    player: Mapped["Player"] = relationship()
    character: Mapped[Optional["WowCharacter"]] = relationship()
```

### Important: Update imports

Add `from datetime import time, date` and `from decimal import Decimal` and `from sqlalchemy import Numeric, Time, Date, UniqueConstraint` to models.py as needed.

---

## Task 4: Update guild_routes.py — Roster Submit

The existing `POST /api/v1/guild/roster` endpoint in `guild_routes.py` creates member_availability rows. This endpoint is used by the legacy roster form (`roster.html`).

**Update it to:**
1. Remove all `MemberAvailability` references
2. Instead of boolean day fields, accept the new format (or keep backward compatibility with the legacy form for now by accepting the old format and converting — if a day is marked "available" with no time data, store a sensible default like `earliest_start=18:00, available_hours=6.0`)
3. Write to `patt.player_availability` instead

**If maintaining backward compatibility with the legacy form is too complex**, it's acceptable to break the legacy form — we're replacing it anyway. Just make sure the new API endpoint accepts the new format and the legacy HTML form can be updated later.

---

## Task 5: Update Admin Routes — Reference Table Management

Add admin API endpoints for managing reference tables. These allow officers to edit configurable values without touching the database directly.

**New endpoints in `admin_routes.py`:**

```
GET    /api/v1/admin/ranks              — list all ranks with scheduling_weight
PATCH  /api/v1/admin/ranks/{id}         — update rank fields (name, level, description, scheduling_weight, discord_role_id)

GET    /api/v1/admin/roles              — list all roles (Tank, Healer, etc.)
PATCH  /api/v1/admin/roles/{id}         — update role fields

GET    /api/v1/admin/classes             — list all WoW classes
GET    /api/v1/admin/specializations     — list all specs (with class + role)

GET    /api/v1/admin/seasons             — list raid seasons
POST   /api/v1/admin/seasons             — create new season (name, start_date)
PATCH  /api/v1/admin/seasons/{id}        — update season (name, is_active)
```

These are all Officer+ (rank level 4) protected, same as existing admin routes.

**Note:** Classes and specializations are read-only for now (they come from WoW game data). Roles and ranks are editable.

---

## Task 6: Admin Page — Reference Table Editor

Create a new admin page at `/admin/reference-tables` that displays editable tables for:
- **Guild Ranks** — name, level, description, scheduling_weight, discord_role_id (editable)
- **Roles** — name, display_name (editable)
- **Raid Seasons** — name, start_date, is_active (editable, with "create new season" button)

Use the same dark tavern aesthetic and inline-edit pattern as the existing admin pages.

Page route in `admin_pages.py`, template in `templates/admin/reference_tables.html`.

**For classes and specializations:** display as read-only reference (collapsible section showing all 13 classes and their specs). Useful for officers to look up spec IDs when needed.

---

## Task 7: Availability Service

Create `src/sv_common/identity/availability.py` (or `src/patt/services/availability_service.py` — follow whichever pattern is already established):

```python
async def get_player_availability(db, player_id) -> list[PlayerAvailability]
async def set_player_availability(db, player_id, day_of_week, earliest_start, available_hours) -> PlayerAvailability
async def clear_player_availability(db, player_id)  # wipe all days
async def get_all_availability_for_day(db, day_of_week) -> list[dict]
    # Returns availability + player + rank (with scheduling_weight) for scoring
```

The scoring algorithm (find optimal raid time) is a **future task** — not in this phase. This phase just gets the data in place and queryable.

---

## Task 8: Season Service

Create a basic season service:

```python
async def get_current_season(db) -> RaidSeason | None
    # MAX(start_date) WHERE start_date <= NOW() AND is_active = TRUE
async def create_season(db, name, start_date) -> RaidSeason
async def get_all_seasons(db) -> list[RaidSeason]
```

---

## Task 9: Update Smoke Tests

Update `test_smoke.py`:
- Add imports for `PlayerAvailability`, `RaidSeason`, `RaidEvent`, `RaidAttendance`
- Verify tablenames and schemas
- Verify `GuildRank` has `scheduling_weight` field
- Verify `Player` has `timezone` and `auto_invite_events` fields
- Remove any `MemberAvailability` references

---

## Task 10: Unit Tests

Create `tests/unit/test_availability.py`:
- test_set_availability_creates_row
- test_set_availability_updates_existing (upsert on same day)
- test_clear_availability_removes_all_days
- test_get_availability_includes_scheduling_weight
- test_availability_day_range_validation (0-6 only)

Create `tests/unit/test_seasons.py`:
- test_get_current_season_returns_latest_started
- test_get_current_season_ignores_future_start_dates
- test_get_current_season_ignores_inactive
- test_create_season

---

## Task 11: Cleanup — Remove Dead References

Remove any remaining references to:
- `MemberAvailability` model — delete from models.py
- `member_availability` table — remove from any queries, routes, services
- The `relink_member_availability.sql` script — no longer needed, delete it

Also check `guild_routes.py` for the old availability-related logic in the roster submit endpoint and update per Task 4.

---

## Dormant Code Note

The following modules still reference old schema names (`persons`, `discord_members`, `identity_links`) from before Phase 2.7. They are NOT wired up in production and should NOT be updated in this phase:
- `src/sv_common/guild_sync/identity_engine.py`
- `src/sv_common/guild_sync/integrity_checker.py`
- `src/sv_common/guild_sync/discord_sync.py`
- `src/sv_common/guild_sync/db_sync.py`
- `src/sv_common/guild_sync/onboarding/*.py`

These will be updated when we activate the guild sync and onboarding features.

---

## Acceptance Criteria

- [ ] Migration 0008 applies cleanly on top of 0007
- [ ] `member_availability` table is dropped
- [ ] `player_availability`, `raid_seasons`, `raid_events`, `raid_attendance` tables exist
- [ ] `guild_ranks` has `scheduling_weight` column populated with correct defaults
- [ ] `players` has `timezone` and `auto_invite_events` columns
- [ ] Seed data includes scheduling weights
- [ ] Admin endpoints for reference tables work (ranks, roles, seasons)
- [ ] Admin page displays and allows editing of reference tables
- [ ] Availability service CRUD works
- [ ] Season service works (current season = latest started)
- [ ] All existing tests still pass
- [ ] New tests pass
- [ ] App runs healthy (200 OK on /api/health)

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-2.8: scheduling, availability, attendance schema"`
- [ ] Update CLAUDE.md "Current Build Status" section
- [ ] Update CLAUDE.md database schema section with new tables
