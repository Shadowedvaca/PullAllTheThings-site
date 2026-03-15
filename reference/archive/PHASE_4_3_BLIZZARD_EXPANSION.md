# Phase 4.3 — Blizzard API Expansion & Last-Login Optimization

## Goal

Add three new Blizzard Profile API endpoints (raid encounters, Mythic+ keystone, achievements)
and implement the last-login optimization to skip characters that haven't logged in since the
previous sync. Expected 50–70% reduction in API calls per cycle.

---

## Prerequisites

- Phase 4.0 complete (config_cache, site_config)
- Blizzard API credentials working
- Current scheduler running Blizzard sync pipeline

---

## Database Migration: 0032_progression_tracking

### New Table: `guild_identity.character_raid_progress`

```sql
CREATE TABLE guild_identity.character_raid_progress (
    id                SERIAL PRIMARY KEY,
    character_id      INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    raid_name         VARCHAR(100) NOT NULL,
    raid_id           INTEGER NOT NULL,            -- Blizzard encounter instance ID
    difficulty        VARCHAR(20) NOT NULL,         -- 'normal', 'heroic', 'mythic'
    boss_name         VARCHAR(100) NOT NULL,
    boss_id           INTEGER NOT NULL,             -- Blizzard encounter ID
    kill_count        INTEGER NOT NULL DEFAULT 0,
    last_synced       TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, boss_id, difficulty)
);
CREATE INDEX idx_raid_progress_char ON guild_identity.character_raid_progress(character_id);
```

### New Table: `guild_identity.character_mythic_plus`

```sql
CREATE TABLE guild_identity.character_mythic_plus (
    id                SERIAL PRIMARY KEY,
    character_id      INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    season_id         INTEGER NOT NULL,             -- Blizzard M+ season ID
    overall_rating    DECIMAL(7,1) DEFAULT 0,       -- e.g., 2450.5
    dungeon_name      VARCHAR(100) NOT NULL,
    dungeon_id        INTEGER NOT NULL,
    best_level        INTEGER DEFAULT 0,
    best_timed        BOOLEAN DEFAULT FALSE,        -- Was best run timed?
    best_score        DECIMAL(7,1) DEFAULT 0,
    last_synced       TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, season_id, dungeon_id)
);
CREATE INDEX idx_mplus_char ON guild_identity.character_mythic_plus(character_id);
CREATE INDEX idx_mplus_season ON guild_identity.character_mythic_plus(season_id);
```

### New Table: `guild_identity.character_achievements`

Track milestone achievements only (not all 20,000+ achievements):

```sql
CREATE TABLE guild_identity.character_achievements (
    id                SERIAL PRIMARY KEY,
    character_id      INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    achievement_id    INTEGER NOT NULL,             -- Blizzard achievement ID
    achievement_name  VARCHAR(200) NOT NULL,
    completed_at      TIMESTAMP,                    -- NULL if criteria met but not completed
    last_synced       TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, achievement_id)
);
CREATE INDEX idx_achievements_char ON guild_identity.character_achievements(character_id);
```

### New Table: `guild_identity.tracked_achievements`

Admin-configurable list of achievement IDs to track:

```sql
CREATE TABLE guild_identity.tracked_achievements (
    id                SERIAL PRIMARY KEY,
    achievement_id    INTEGER NOT NULL UNIQUE,
    achievement_name  VARCHAR(200) NOT NULL,
    category          VARCHAR(50) DEFAULT 'general', -- 'raid', 'mythic_plus', 'pvp', etc.
    is_active         BOOLEAN NOT NULL DEFAULT TRUE
);

-- Seed with common milestone achievements
INSERT INTO guild_identity.tracked_achievements (achievement_id, achievement_name, category) VALUES
    (19350, 'Ahead of the Curve (current tier)', 'raid'),
    (19351, 'Cutting Edge (current tier)', 'raid'),
    (17844, 'Keystone Master', 'mythic_plus'),
    (17845, 'Keystone Hero', 'mythic_plus'),
    (17846, 'Keystone Conqueror', 'mythic_plus');
-- NOTE: Achievement IDs above are placeholders — replace with current tier IDs at implementation time.
```

### New Table: `guild_identity.progression_snapshots`

Weekly snapshots for diff-based "this week's progress" tracking:

```sql
CREATE TABLE guild_identity.progression_snapshots (
    id                SERIAL PRIMARY KEY,
    character_id      INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    snapshot_date     DATE NOT NULL,
    raid_kills_json   JSONB,     -- {boss_id: {difficulty: kill_count, ...}, ...}
    mythic_rating     DECIMAL(7,1),
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, snapshot_date)
);
CREATE INDEX idx_snapshots_date ON guild_identity.progression_snapshots(snapshot_date);
```

### Alter: `guild_identity.wow_characters`

```sql
ALTER TABLE guild_identity.wow_characters
    ADD COLUMN last_progression_sync TIMESTAMP;
```

This tracks when we last synced raid/M+/achievement data for a character, independent of
the profile sync. Used by last-login optimization.

---

## Task 1: Last-Login Optimization

### Concept

Before calling any per-character Blizzard endpoint, compare the character's
`last_login_timestamp` (updated every roster sync) with `last_progression_sync`.
If `last_login_timestamp` hasn't changed, skip the character — nothing can have changed.

### File: `src/sv_common/guild_sync/blizzard_client.py`

Add a helper:

```python
def should_sync_character(
    last_login_ts: int | None,
    last_progression_sync: datetime | None,
    force_full: bool = False,
) -> bool:
    """Return True if character needs progression sync."""
    if force_full:
        return True
    if last_login_ts is None:
        return True  # No login data — sync to be safe
    if last_progression_sync is None:
        return True  # Never synced progression
    # Convert Blizzard ms timestamp to datetime
    last_login_dt = datetime.fromtimestamp(last_login_ts / 1000, tz=timezone.utc)
    return last_login_dt > last_progression_sync
```

### File: `src/sv_common/guild_sync/scheduler.py`

In `run_blizzard_sync()`, after roster sync completes:

1. Load all characters with `last_login_timestamp` and `last_progression_sync`
2. Filter to characters where `should_sync_character()` returns `True`
3. Pass only the filtered list to profession sync, raid sync, M+ sync, achievement sync
4. Log: "Syncing progression for {N} of {total} characters ({skipped} skipped — no login change)"

### Weekly Full Sweep

Every Sunday at the existing roleless-prune time (4 AM UTC), run a full sync with
`force_full=True` to catch any edge cases (transfers, backfills, etc.).

### Apply to Existing Profession Sync

Update `crafting_sync.py` to also use this optimization. Currently it syncs all characters
every run. With this change, it only syncs characters that have logged in since their
last profession sync.

---

## Task 2: Raid Encounters Endpoint

### File: `src/sv_common/guild_sync/blizzard_client.py`

New method:

```python
async def get_character_encounters_raids(
    self, realm_slug: str, character_name: str
) -> list[dict] | None:
    """
    GET /profile/wow/character/{realm}/{name}/encounters/raids

    Returns raid progress: boss-by-boss kill counts per difficulty.
    Response structure:
    {
        "expansions": [
            {
                "expansion": {"name": "The War Within", "id": 503},
                "instances": [
                    {
                        "instance": {"name": "Nerub-ar Palace", "id": 1273},
                        "modes": [
                            {
                                "difficulty": {"type": "HEROIC", "name": "Heroic"},
                                "status": {"type": "COMPLETE", "name": "Complete"},
                                "progress": {
                                    "completed_count": 8,
                                    "total_count": 8,
                                    "encounters": [
                                        {
                                            "encounter": {"name": "Ulgrax", "id": 2902},
                                            "completed_count": 14,
                                            "last_kill_timestamp": 1700000000000
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    """
    path = f"/profile/wow/character/{realm_slug}/{quote(character_name.lower())}/encounters/raids"
    return await self._get(path, namespace="profile-us")
```

### Sync Function: `src/sv_common/guild_sync/progression_sync.py` (new file)

```python
async def sync_raid_progress(pool, blizzard_client, characters: list[dict]) -> dict:
    """Fetch and store raid encounter data for a list of characters."""
    stats = {"synced": 0, "skipped": 0, "errors": 0}

    for batch in chunk(characters, 10):
        results = await asyncio.gather(
            *[blizzard_client.get_character_encounters_raids(c["realm_slug"], c["name"])
              for c in batch],
            return_exceptions=True,
        )
        # Parse results, upsert into character_raid_progress
        # ...
        await asyncio.sleep(0.5)

    return stats
```

Parse the nested response: iterate expansions → instances → modes → encounters.
For each encounter, upsert into `character_raid_progress` with ON CONFLICT UPDATE.

---

## Task 3: Mythic+ Keystone Profile Endpoint

### File: `src/sv_common/guild_sync/blizzard_client.py`

New method:

```python
async def get_character_mythic_keystone_profile(
    self, realm_slug: str, character_name: str, season_id: int | None = None
) -> dict | None:
    """
    GET /profile/wow/character/{realm}/{name}/mythic-keystone-profile
    GET /profile/wow/character/{realm}/{name}/mythic-keystone-profile/season/{seasonId}

    Returns M+ rating and best runs per dungeon.
    """
    path = f"/profile/wow/character/{realm_slug}/{quote(character_name.lower())}/mythic-keystone-profile"
    if season_id:
        path += f"/season/{season_id}"
    return await self._get(path, namespace="profile-us")
```

### Response Structure (season endpoint)

```json
{
    "season": {"id": 13},
    "best_runs": [
        {
            "completed_timestamp": 1700000000000,
            "duration": 1800000,
            "keystone_level": 12,
            "is_completed_within_time": true,
            "dungeon": {"name": "The Stonevault", "id": 1269},
            "mythic_rating": {"color": {...}, "rating": 185.5}
        }
    ],
    "mythic_rating": {"color": {...}, "rating": 2450.5}
}
```

### Sync Function: `src/sv_common/guild_sync/progression_sync.py`

```python
async def sync_mythic_plus(pool, blizzard_client, characters: list[dict]) -> dict:
    """Fetch and store M+ data for a list of characters."""
    # Fetch current season ID from Blizzard mythic-keystone/season/index endpoint
    # For each character, get season details
    # Upsert per-dungeon best runs into character_mythic_plus
    # Store overall rating
```

---

## Task 4: Achievements Endpoint

### File: `src/sv_common/guild_sync/blizzard_client.py`

New method:

```python
async def get_character_achievements(
    self, realm_slug: str, character_name: str
) -> dict | None:
    """
    GET /profile/wow/character/{realm}/{name}/achievements

    Returns all earned achievements. WARNING: Large payload.
    We filter to tracked_achievements IDs only when storing.
    """
    path = f"/profile/wow/character/{realm_slug}/{quote(character_name.lower())}/achievements"
    return await self._get(path, namespace="profile-us")
```

### Sync Function: `src/sv_common/guild_sync/progression_sync.py`

```python
async def sync_achievements(pool, blizzard_client, characters: list[dict]) -> dict:
    """Fetch achievements and store only tracked ones."""
    # Load tracked_achievements IDs from DB
    tracked_ids = set(...)

    for batch in chunk(characters, 10):
        results = await asyncio.gather(...)
        for char, result in zip(batch, results):
            if result is None:
                continue
            # Filter to tracked IDs only
            for ach in result.get("achievements", []):
                if ach["id"] in tracked_ids:
                    # Upsert into character_achievements
                    pass
        await asyncio.sleep(0.5)
```

**Important:** The achievements endpoint returns a large payload (all achievements).
We only store the ones in `tracked_achievements`. Sync weekly, not every 6 hours.

---

## Task 5: Progression Snapshots

### File: `src/sv_common/guild_sync/progression_sync.py`

```python
async def create_weekly_snapshot(pool) -> int:
    """Create progression snapshots for all characters. Run weekly (Sunday)."""
    # For each character:
    #   - Aggregate raid kill counts from character_raid_progress into JSON
    #   - Get current mythic_rating from character_mythic_plus
    #   - INSERT into progression_snapshots with today's date
    # Return count of snapshots created
```

Weekly snapshot enables "this week's progress" views:
- Compare current kill_count to last snapshot's kill_count → "killed 3 new bosses this week"
- Compare current mythic_rating to last snapshot → "gained 150 rating this week"

---

## Task 6: Scheduler Integration

### File: `src/sv_common/guild_sync/scheduler.py`

Update `run_blizzard_sync()` pipeline:

```
1. Fetch guild roster (existing)
2. Sync roster to DB (existing)
3. Fetch character profiles (existing, apply last-login filter)
4. Sync character profiles to DB (existing)
5. [NEW] Filter characters by last-login optimization
6. [NEW] Sync raid encounters (filtered characters)
7. [NEW] Sync M+ profiles (filtered characters)
8. Run profession sync (existing, now also filtered)
9. Run identity matching (existing)
10. Run integrity check (existing)
11. Run drift scan (existing)
12. Update last_progression_sync timestamp for synced characters
```

**Achievement sync** runs on a separate schedule (weekly, Sunday, after snapshots):

```
1. Create weekly progression snapshots
2. Sync achievements (all characters, force_full=True)
```

### Updated Sync Frequency

| Pipeline | Frequency | Characters Synced |
|----------|-----------|-------------------|
| Roster + profiles | Every 6 hours | All (~320) |
| Raid encounters | Every 6 hours | Only active (~100-160) |
| M+ profiles | Every 6 hours | Only active (~100-160) |
| Professions | Daily (or weekly) | Only active |
| Achievements | Weekly (Sunday) | All (force_full) |
| Snapshots | Weekly (Sunday) | All |

---

## Task 7: Admin Configuration

### Tracked Achievements Management

Add to existing `/admin/data-quality` or new `/admin/progression` page:

- List of tracked achievements with toggle (active/inactive)
- Add achievement by ID + name
- Common achievements pre-seeded (AOTC, KSM, etc.)
- Achievement IDs change each tier — admin must update when new raid tier launches

### Current M+ Season Configuration

Store current M+ season ID in `common.site_config` or `common.discord_config`:

```sql
ALTER TABLE common.site_config ADD COLUMN current_mplus_season_id INTEGER;
```

Auto-detect from Blizzard API if possible, or set manually in admin.

---

## API Estimates (Post-Optimization)

### Before Optimization

| Endpoint | Calls/Cycle | Daily (4 cycles) |
|----------|------------|-------------------|
| Roster | 1 | 4 |
| Profiles | ~320 | 1,280 |
| Professions | ~320 | 320 (1/day) |
| **Total** | **~641** | **~1,604** |

### After Optimization (assuming 40% active)

| Endpoint | Calls/Cycle | Daily (4 cycles) |
|----------|------------|-------------------|
| Roster | 1 | 4 |
| Profiles | ~320 | 1,280 (still all — needed for last_login check) |
| Professions | ~128 | 128 (1/day, filtered) |
| Raid encounters | ~128 | 512 |
| M+ profiles | ~128 | 512 |
| Achievements | ~320 | 46 (weekly) |
| **Total** | **~705** | **~2,482** |

Still well under 36,000/hour limit. ~103/hour average.

---

## Tests

- Unit test `should_sync_character()` with various timestamp combinations
- Unit test raid encounter response parsing
- Unit test M+ response parsing
- Unit test achievement filtering (only tracked IDs stored)
- Unit test snapshot creation
- Integration test: full pipeline with mock Blizzard responses
- Verify profession sync respects last-login filter
- All existing tests pass

---

## Deliverables Checklist

- [ ] Migration 0032 (5 new tables, 1 column addition)
- [ ] ORM models for all new tables
- [ ] `should_sync_character()` helper
- [ ] `get_character_encounters_raids()` method
- [ ] `get_character_mythic_keystone_profile()` method
- [ ] `get_character_achievements()` method
- [ ] `progression_sync.py` (sync functions for all 3 + snapshots)
- [ ] Scheduler updated with new pipeline steps
- [ ] Last-login optimization applied to professions + new endpoints
- [ ] Weekly snapshot job added
- [ ] Tracked achievements admin UI
- [ ] Tests
