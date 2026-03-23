# Detailed WCL Log Ingestion — Implementation Spec

> Written for a clean context window. Read this fully before touching any code.
> Current codebase state: prod-v0.5.7. Last migration: 0059 (character_parses.kill column, nullable, unused).

---

## Problem Being Solved

The existing WCL sync uses `characterData.character.zoneRankings` — a WCL endpoint
that only returns data for characters with a **public WCL profile** (i.e., they have
logged into warcraftlogs.com and their character rankings have been processed).

In practice this means: 7 of 385 guild characters have parse data. Everyone else has
raid kills in Blizzard's data (`character_raid_progress`) but zero WCL parses.

WCL's `reportData.report.rankings` endpoint returns parse percentiles for **every
player who appeared in a specific uploaded log**, regardless of whether they have a
WCL account. This is the correct way to populate parse data for the whole guild.

---

## Rate Limits — Clarification

**Current character-based approach:**
- One WCL API call per character: `characterData.character.zoneRankings`
- 351 characters = 351 API calls per sync run
- WCL rate-limits based on "points" — each character query costs ~1 point (~300/hour)
- Result: data only for the ~7 characters with public WCL profiles

**Report-based approach:**
- One call to get fight metadata per report (already done in `sync_guild_reports`)
- One call to `reportData.report.rankings(encounterID)` per boss per report
- Each `rankings` call returns parse data for **all ~20 raiders simultaneously**
- A raid night with 5 boss kills = 5 API calls total, gets everyone
- 5 calls vs 351 calls — and covers everyone in the log, not just WCL account holders

**Both run together:**
- Character-based sync runs first → gets all-time best parses for WCL account holders
  (at minimum the log uploader, who is typically the raid leader or an officer)
- Report-based sync runs second → fills in everyone else from specific report logs
- Upsert logic: a report-based parse can only **improve** a stored value, never worsen it

---

## Current Architecture (What Exists)

### Tables (relevant subset)

**`guild_identity.character_parses`** — current parse storage
```
character_id  encounter_id  encounter_name  zone_id  zone_name
difficulty    spec          percentile      amount   report_code
fight_id      kill(nullable) last_synced
UNIQUE(character_id, encounter_id, difficulty, spec)
```
Design: one row per character per encounter per spec per difficulty = **best parse only**.
The `kill` column (migration 0059) is nullable and currently unpopulated.

**`guild_identity.raid_reports`** — existing report metadata
```
report_code  title        raid_date  zone_id   zone_name
owner_name   boss_kills   duration_ms  attendees(JSONB)  report_url
last_synced  created_at
```
`attendees` is a JSON array of `{name, class, server}` objects.
**Does NOT currently store encounter IDs** — this needs to be added.

**`guild_identity.character_raid_progress`** — Blizzard API kill data
```
character_id  raid_id  boss_id  boss_name  difficulty(string)  kill_count
```
`difficulty` here is a string: `"normal"`, `"heroic"`, `"mythic"`.
This is the source of truth for whether a character has killed a boss.

### Key files

- `src/sv_common/guild_sync/warcraftlogs_client.py` — WCL GraphQL client
  - `get_guild_reports()` — fetches recent guild report list
  - `get_report_fights(report_code)` — fetches fight metadata + attendees for one report
  - `get_character_parses(name, server, region, zone_id)` — character zoneRankings
  - `get_world_zones()` — fetches zone id→name map
- `src/sv_common/guild_sync/wcl_sync.py` — sync logic
  - `sync_guild_reports(pool, client, guild, server, region)` — stores report metadata
  - `sync_character_parses(pool, client, characters, server, region)` — per-character rankings
  - `_parse_zone_rankings(zone_rankings, zone_name_map)` — extracts parses from zoneRankings response
- `src/sv_common/guild_sync/scheduler.py` — `run_wcl_sync()` orchestrates both
- `src/guild_portal/api/guild_routes.py` — `/api/v1/guild/roster` uses avg_parse
- `src/guild_portal/api/member_routes.py` — `/api/v1/me/character/{id}/parses` uses current_parses

---

## Proposed Changes

### 1. New table: `guild_identity.character_report_parses`

**Purpose:** Granular storage — one row per character per boss per report.
Aggregation (best, average, season avg, last-N-weeks) is computed at query time.
This is the primary source for report-derived parses.

```sql
CREATE TABLE guild_identity.character_report_parses (
    id               SERIAL PRIMARY KEY,
    character_id     INTEGER NOT NULL
                         REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    report_code      VARCHAR(20) NOT NULL,
    encounter_id     INTEGER NOT NULL,
    encounter_name   VARCHAR(100) NOT NULL,
    zone_id          INTEGER NOT NULL,
    zone_name        VARCHAR(100) NOT NULL,
    difficulty       INTEGER NOT NULL,   -- WCL int: 3=normal 4=heroic 5=mythic
    spec             VARCHAR(50),
    percentile       NUMERIC(5,1) NOT NULL,
    amount           NUMERIC(12,1),      -- DPS or HPS
    fight_id         INTEGER,            -- WCL fight ID within the report
    raid_date        TIMESTAMP WITH TIME ZONE,  -- copied from raid_reports.raid_date
    last_synced      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE(character_id, report_code, encounter_id)
);

CREATE INDEX idx_crp_character ON guild_identity.character_report_parses (character_id);
CREATE INDEX idx_crp_zone ON guild_identity.character_report_parses (zone_id);
CREATE INDEX idx_crp_raid_date ON guild_identity.character_report_parses (raid_date);
```

**Key design decisions:**
- `UNIQUE(character_id, report_code, encounter_id)` — one row per player per boss per night.
  WCL's `rankings` endpoint already returns one best-parse entry per player per encounter
  within a given report, so this maps cleanly.
- `raid_date` is denormalised from `raid_reports` so queries don't need a join.
- `difficulty` as WCL integer (not Blizzard string) — consistent with `character_parses`.
- No foreign key on `report_code` → `raid_reports` — the report row should exist (populated
  by `sync_guild_reports` which runs first), but don't enforce FK to keep the upsert simple.

### 2. Add `encounter_ids` to `guild_identity.raid_reports`

The `rankings(encounterID)` call requires a WCL encounter ID. We get these from the
`fights` array returned by `get_report_fights`. Currently the fights query does not
include `encounterID`. Add it, and store the deduplicated list on the report row.

**Migration 0060 SQL:**
```sql
ALTER TABLE guild_identity.raid_reports
    ADD COLUMN encounter_ids INTEGER[] NOT NULL DEFAULT '{}';

CREATE TABLE guild_identity.character_report_parses (
    -- (full DDL above)
);
```

**ORM model changes:**
- `RaidReport` model: add `encounter_ids: Mapped[list[int]]`
- New `CharacterReportParse` model mirroring the table above

### 3. Update `get_report_fights` in `warcraftlogs_client.py`

Add `encounterID` to the fights subquery:

```graphql
fights(killType: Kills) {
    id
    encounterID        # ← add this
    name
    kill
    startTime
    endTime
    difficulty
}
```

### 4. New WCL client method: `get_report_rankings`

```python
async def get_report_rankings(
    self,
    report_code: str,
    encounter_id: int,
) -> dict:
    """Get per-player parse rankings for one encounter within a report.

    Returns the raw JSON rankings blob from WCL.
    """
    query = """
    query ($code: String!, $encID: Int!) {
        reportData {
            report(code: $code) {
                rankings(encounterID: $encID)
            }
        }
    }
    """
    return await self._query(query, {"code": report_code, "encID": encounter_id})
```

**Response shape** (rankings is a JSON scalar, not typed GraphQL):
```json
{
  "reportData": {
    "report": {
      "rankings": {
        "data": {
          "roles": {
            "tanks":   { "characters": [ <character_entry>, ... ] },
            "healers": { "characters": [ <character_entry>, ... ] },
            "dps":     { "characters": [ <character_entry>, ... ] }
          }
        }
      }
    }
  }
}
```

Each `character_entry`:
```json
{
  "name": "Trogmoon",
  "class": "Druid",
  "spec": "Balance",
  "server": { "slug": "senjin", "region": "US" },
  "amount": 123456.7,
  "rankPercent": 75.5,
  "best": true
}
```

> **Verify this shape** in the WCL API explorer (https://www.warcraftlogs.com/api/v2/client)
> before implementing. Run a query against a known report code to confirm field names.
> `best: true` means this fight is the character's all-time best for this encounter.
> We store the parse regardless of `best` value.

**Character matching:** Match `entry.name` (case-insensitive) to
`guild_identity.wow_characters.character_name` where `in_guild = TRUE`.
Characters in the log who aren't in our DB are silently skipped.

### 5. New sync function: `sync_report_parses`

Add to `wcl_sync.py`:

```python
async def sync_report_parses(
    pool: asyncpg.Pool,
    wcl_client: WarcraftLogsClient,
    report_codes: list[str],           # report codes to process
    zone_name_map: dict[int, str],     # from get_world_zones(), pass in from caller
) -> dict:
    """Fetch per-player parse rankings from WCL report logs and store granularly.

    For each report:
      - Reads encounter_ids from raid_reports (populated by sync_guild_reports)
      - Calls rankings(encounterID) for each unique encounter
      - Matches character names to guild wow_characters
      - Upserts into character_report_parses

    Returns stats: reports_processed, encounters_queried, parse_records, errors.
    """
```

**Inner logic per report:**
1. Load `raid_date`, `zone_id`, `zone_name`, `encounter_ids` from `raid_reports`
2. Build a character name→id lookup from `wow_characters WHERE in_guild = TRUE`
3. For each encounter_id:
   a. Call `wcl_client.get_report_rankings(report_code, encounter_id)`
   b. Parse the rankings JSON — walk `roles.tanks/healers/dps.characters`
   c. For each character entry: look up `character_id` by name (case-insensitive)
   d. Upsert into `character_report_parses`:
      ```sql
      INSERT INTO guild_identity.character_report_parses
          (character_id, report_code, encounter_id, encounter_name,
           zone_id, zone_name, difficulty, spec, percentile, amount,
           fight_id, raid_date, last_synced)
      VALUES (...)
      ON CONFLICT (character_id, report_code, encounter_id)
      DO UPDATE SET
          percentile     = GREATEST(EXCLUDED.percentile, character_report_parses.percentile),
          spec           = EXCLUDED.spec,
          amount         = EXCLUDED.amount,
          last_synced    = NOW()
      ```
      Note `GREATEST()` — keeps the higher percentile if called twice on same report.
   e. Sleep 0.3s between encounter calls to space requests

**Encounter name lookup:** The `rankings` response has `data.roles.*.characters` but
does NOT include the encounter name — only `character_entry` data. The encounter name
must come from either:
- The `fights` array on the report (via `get_report_fights`) — `fights[].name` where
  `fights[].encounterID == encounter_id` — **preferred, already stored flow**
- Or looked up from `character_parses.encounter_name` WHERE `encounter_id = X`

Recommend: extend `raid_reports` to store fights as JSONB (or a separate lookup dict
built at sync time from the `get_report_fights` response).

### 6. Update `sync_guild_reports` in `wcl_sync.py`

Two additions to the existing function:

**a. Store encounter_ids on the report row:**
```python
# After parsing fights:
encounter_ids = list({f["encounterID"] for f in fights if f.get("encounterID")})

# In the INSERT:
"""INSERT INTO guild_identity.raid_reports
       (report_code, ..., encounter_ids)
   VALUES ($1, ..., $11)
   ON CONFLICT (report_code) DO UPDATE SET
       encounter_ids = EXCLUDED.encounter_ids, ..."""
```

**b. Build encounter_name lookup from fights:**
The `fights` array (with `killType: Kills`) returns one entry per boss kill.
Build a dict `{encounterID: name}` from this. Either store it as JSONB on the report
row, or pass it to `sync_report_parses` at call time.

Recommended: add `encounter_map JSONB` column to `raid_reports` storing
`{"encounterID": "Boss Name", ...}` — avoids needing a second API call later.

### 7. Update `run_wcl_sync` in `scheduler.py`

Add a third pipeline step after the existing two:

```python
# Step 3: Report-based parse sync
# Pull recent reports that have encounter_ids (i.e. fully processed)
async with self.db_pool.acquire() as conn:
    report_rows = await conn.fetch(
        """SELECT report_code
           FROM guild_identity.raid_reports
           WHERE zone_id = ANY($1)        -- current tier zones only
             AND array_length(encounter_ids, 1) > 0
           ORDER BY raid_date DESC
           LIMIT 20""",                   -- last ~20 raids, configurable
        current_wcl_zone_ids,             # derive same way as member_routes.py
    )
report_codes = [r["report_code"] for r in report_rows]

if report_codes:
    report_parse_stats = await sync_report_parses(
        self.db_pool, wcl_client, report_codes, zone_name_map
    )
    logger.info("WCL report parse sync: %s", report_parse_stats)
```

**Deriving `current_wcl_zone_ids` in the scheduler:**
Same pattern as `member_routes.py` — match `LOWER(encounter_name)` from
`character_parses` or `character_report_parses` against `LOWER(boss_name)` from
`character_raid_progress` WHERE `raid_id = ANY(current_raid_ids)`.

Or, once `character_report_parses` is populated, zone IDs come directly from
`SELECT DISTINCT zone_id FROM character_report_parses`.

---

## Aggregation Strategy

The granular `character_report_parses` table enables rule-based aggregation at query time.
No need to store pre-computed averages.

### Roster: avg raid parse (current implementation target)

```sql
SELECT cp.character_id, AVG(cp.percentile)::numeric(5,1) AS avg_pct
FROM guild_identity.character_report_parses cp
WHERE cp.character_id = ANY(:char_ids)
  AND cp.zone_id = ANY(:current_zone_ids)
  AND cp.percentile > 0
  AND LOWER(cp.encounter_name) IN (
      -- kills-only: only average bosses the character has killed (Blizzard data)
      SELECT LOWER(crp.boss_name)
      FROM guild_identity.character_raid_progress crp
      WHERE crp.character_id = cp.character_id
        AND crp.raid_id = ANY(:current_raid_ids)
        AND crp.kill_count > 0
  )
GROUP BY cp.character_id
```

Replace the current query in `guild_routes.py` `/api/v1/guild/roster` which reads from
`character_parses`. Read from `character_report_parses` instead.

### My Characters: best parse per boss (replaces character_parses read)

```sql
SELECT encounter_name, MAX(percentile) AS best_pct, spec
FROM guild_identity.character_report_parses
WHERE character_id = :character_id
  AND zone_id = ANY(:current_zone_ids)
GROUP BY encounter_name, spec
ORDER BY encounter_name
```

### Future aggregation examples (all SQL, no code change needed)

```sql
-- Season average (all kills, current tier)
AVG(percentile) WHERE zone_id = ANY(current) AND raid_date >= season_start

-- Last 4 weeks
AVG(percentile) WHERE zone_id = ANY(current) AND raid_date >= NOW() - INTERVAL '28 days'

-- Best ever per boss
MAX(percentile) GROUP BY encounter_name

-- This specific raid night
WHERE report_code = 'BXca6VCdMqhNJpg4'

-- Best difficulty only (e.g. heroic only once guild clears heroic)
WHERE difficulty = 4
```

---

## Migration: 0060

File: `alembic/versions/0060_character_report_parses.py`

```python
revision = "0060"
down_revision = "0059"

def upgrade():
    # 1. Add encounter_ids to raid_reports
    op.add_column("raid_reports",
        sa.Column("encounter_ids", postgresql.ARRAY(sa.Integer()), nullable=False,
                  server_default="{}"),
        schema="guild_identity")

    # 2. Add encounter_map JSONB to raid_reports (encounterID→name lookup)
    op.add_column("raid_reports",
        sa.Column("encounter_map", postgresql.JSONB(), nullable=True),
        schema="guild_identity")

    # 3. Create character_report_parses
    op.create_table("character_report_parses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("character_id", sa.Integer(),
                  sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("report_code", sa.String(20), nullable=False),
        sa.Column("encounter_id", sa.Integer(), nullable=False),
        sa.Column("encounter_name", sa.String(100), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("zone_name", sa.String(100), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("spec", sa.String(50)),
        sa.Column("percentile", sa.Numeric(5, 1), nullable=False),
        sa.Column("amount", sa.Numeric(12, 1)),
        sa.Column("fight_id", sa.Integer()),
        sa.Column("raid_date", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_synced", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=func.now()),
        sa.UniqueConstraint("character_id", "report_code", "encounter_id",
                            name="uq_crp_char_report_enc"),
        schema="guild_identity",
    )
    op.create_index("idx_crp_character", "character_report_parses",
                    ["character_id"], schema="guild_identity")
    op.create_index("idx_crp_zone", "character_report_parses",
                    ["zone_id"], schema="guild_identity")
    op.create_index("idx_crp_raid_date", "character_report_parses",
                    ["raid_date"], schema="guild_identity")
```

---

## ORM Models (sv_common/db/models.py)

### Update `RaidReport`

```python
encounter_ids: Mapped[list[int]] = mapped_column(
    ARRAY(Integer), nullable=False, server_default="{}"
)
encounter_map: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
```

### New `CharacterReportParse`

```python
class CharacterReportParse(Base):
    """WCL parse percentile per character per encounter per report (granular)."""

    __tablename__ = "character_report_parses"
    __table_args__ = (
        UniqueConstraint("character_id", "report_code", "encounter_id",
                         name="uq_crp_char_report_enc"),
        {"schema": "guild_identity"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    report_code: Mapped[str] = mapped_column(String(20), nullable=False)
    encounter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    encounter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_id: Mapped[int] = mapped_column(Integer, nullable=False)
    zone_name: Mapped[str] = mapped_column(String(100), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[Optional[str]] = mapped_column(String(50))
    percentile: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 1))
    fight_id: Mapped[Optional[int]] = mapped_column(Integer)
    raid_date: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_synced: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    character: Mapped["WowCharacter"] = relationship()
```

---

## Files to Touch

| File | Change |
|------|--------|
| `alembic/versions/0060_character_report_parses.py` | New migration |
| `src/sv_common/db/models.py` | Update `RaidReport`, add `CharacterReportParse` |
| `src/sv_common/guild_sync/warcraftlogs_client.py` | Add `encounterID` to `get_report_fights` query; add `get_report_rankings()` method |
| `src/sv_common/guild_sync/wcl_sync.py` | Update `sync_guild_reports` to store `encounter_ids`/`encounter_map`; add `sync_report_parses()` |
| `src/sv_common/guild_sync/scheduler.py` | Add Step 3 to `run_wcl_sync()` |
| `src/guild_portal/api/guild_routes.py` | Roster avg parse: read from `character_report_parses` |
| `src/guild_portal/api/member_routes.py` | My Characters parses: read from `character_report_parses` |
| `tests/unit/test_phase_45.py` | Add tests for `sync_report_parses` and `_parse_report_rankings` |

---

## Implementation Order

1. Migration 0060 + ORM models
2. `warcraftlogs_client.py` — add `encounterID` to fights query + `get_report_rankings`
3. Verify `rankings` response shape via WCL API explorer before parsing
4. `wcl_sync.py` — update `sync_guild_reports` + add `sync_report_parses`
5. `scheduler.py` — wire Step 3
6. Tests
7. Deploy
8. **Data reset and full repull (prod, post-deploy):**
   ```sql
   -- Wipe all existing WCL parse data so it's repopulated cleanly
   -- (removes the 24 rows from the pre-report-sync era)
   DELETE FROM guild_identity.character_parses;

   -- raid_reports rows exist but have empty encounter_ids — reset them
   -- so the backfill path re-fetches fight details for all existing reports
   UPDATE guild_identity.raid_reports SET encounter_ids = '{}', encounter_map = NULL;
   ```
   Then trigger a manual WCL sync via Admin → Warcraft Logs → Force Sync.
   The sync will:
   - Re-fetch fight details for all existing reports (backfill path), populating `encounter_ids` and `encounter_map`
   - Run `sync_character_parses` → repopulates `character_parses` for WCL account holders
   - Run `sync_report_parses` → populates `character_report_parses` for all raid attendees
9. Once `character_report_parses` is populated:
   - Update `guild_routes.py` roster query
   - Update `member_routes.py` parse panel query
   - Both switch from `character_parses` to `character_report_parses`

---

## Notes / Gotchas

- The existing `character_parses` table and sync remain unchanged. They continue to serve
  as a fallback for characters with public WCL profiles (all-time best parse, not
  report-scoped). Eventually `character_parses` may be deprecated in favour of
  `character_report_parses` + character-level best computed from the granular table.
- `character_parses.kill` column (migration 0059) is nullable, unused, harmless — ignore.
- `sync_guild_reports` currently skips reports already in the DB (`if existing: continue`).
  Update this check: if the report exists but `encounter_ids = '{}'`, re-fetch fight details
  to backfill. The data reset in step 8 of the implementation order sets all existing reports
  back to empty `encounter_ids`, triggering this backfill path on first sync post-deploy.
- WCL `rankings` response: the JSON blob structure **must be verified** in the WCL API
  explorer before writing the parser. The shape described above is based on known WCL v2
  behaviour but field names can vary.
- Character name matching is case-insensitive but must handle special characters
  (e.g. `Àléx` in the guild). Use `LOWER()` in both directions.
- `raid_date` on `character_report_parses` enables time-window aggregation without a join.
  Populate it from `raid_reports.raid_date` at sync time.
