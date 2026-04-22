# Gear Plan Phase 1.2 — Delves + World Boss Loot Tables

> Status: Scoped, not started
> Last updated: 2026-04-19 (rewritten after schema overhaul + API investigation)

---

## Investigation Results (2026-04-19)

### World Boss

**17 unique Midnight world boss items** are fully synced and classified in `enrichment.items`
(`item_category = 'world_boss'`). The 4 world boss encounters (Cragpine, Lu'ashal, Predaxas,
Thorm'belan) live under instance 1312 ("Midnight"), which `item_source_sync.py` correctly
identifies as `world_boss` because the instance name equals the expansion name.

**Root blocker:** `sp_rebuild_item_seasons` has no `world_boss` branch. World boss items are
never inserted into `enrichment.item_seasons`, so `viz.slot_items` (which JOINs through
`item_seasons`) returns zero world boss rows. The items exist and are classified correctly —
only the season membership and display layers are missing.

### Delves

**Delves are not in the Blizzard journal API.** Confirmed by fetching
`GET /data/wow/journal-expansion/516` for Midnight — the response only has `dungeons` and `raids`
keys. There is no `delves` key and no delve instances in the Midnight data. Delves cannot be
implemented via the existing journal sync pipeline.

---

## Current State

| Component | World Boss | Delves |
|-----------|-----------|--------|
| Blizzard API source | ✅ journal (`instance_type='world_boss'`) | ❌ not in journal API |
| `item_source_sync.py` sync | ✅ works, 4 encounters / 17 items | ❌ N/A |
| `enrichment.item_sources` rows | ✅ 32 rows (items × encounters) | ❌ none |
| `enrichment.items.item_category` | ✅ `'world_boss'` (17 items) | ❌ `'delve'` not in CHECK |
| `sp_rebuild_item_seasons` branch | ❌ **missing — root blocker** | ❌ N/A |
| `enrichment.item_seasons` membership | ❌ 0 rows (sproc never inserts) | ❌ N/A |
| `viz.slot_items` rows | ❌ 0 (item_seasons empty) | ❌ N/A |
| `source_config.py` tracks | ✅ `world_boss: ["C","H","M"]` | ❌ N/A |
| `get_available_items()` query | ❌ `item_category IN (...)` excludes `world_boss` | ❌ N/A |
| `_contextual_sources()` display | ✅ world_boss handled already | ❌ N/A |
| `patt.raid_seasons` columns | ❌ no `world_boss_instance_ids` | ❌ no `current_delve_ids` |
| Reference Tables admin UI | ❌ no world boss select | ❌ out of scope |
| Slot drawer frontend | ❌ no World Bosses section | ❌ out of scope |

---

## Part 1 — World Boss (this phase)

Six changes required. All can be in a single PR.

### 1A. Schema Migration

**Two things in one migration:**

1. Add `world_boss_instance_ids INTEGER[]` to `patt.raid_seasons`
2. Rewrite `sp_rebuild_item_seasons` to add a world_boss branch
3. Update `viz.slot_items` view to filter world_boss by instance ID

```sql
-- 1. Add column
ALTER TABLE patt.raid_seasons
  ADD COLUMN world_boss_instance_ids INTEGER[] NOT NULL DEFAULT '{}';

-- Seed the Midnight S1 row
UPDATE patt.raid_seasons
   SET world_boss_instance_ids = ARRAY[1312]
 WHERE is_active = TRUE;
```

**Rewrite `sp_rebuild_item_seasons`** — add branch 6 (world_boss) after the existing crafted branch.
The new branch inserts all non-junk world_boss items whose source instance is in
`world_boss_instance_ids` for any season:

```sql
-- 6. World boss items
INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
SELECT DISTINCT eis.blizzard_item_id, rs.id
  FROM enrichment.item_sources eis
  JOIN patt.raid_seasons rs
    ON eis.blizzard_instance_id = ANY(rs.world_boss_instance_ids)
 WHERE eis.instance_type = 'world_boss'
   AND NOT eis.is_junk
ON CONFLICT DO NOTHING;
GET DIAGNOSTICS v_world_boss = ROW_COUNT;
```

Also add `v_world_boss BIGINT` to the DECLARE block and include it in the RAISE NOTICE at the end.

**IMPORTANT:** Copy the complete current sproc body from the DB first (it differs from migrations
due to subsequent patches). Run:
```sql
SELECT prosrc FROM pg_proc
 WHERE proname = 'sp_rebuild_item_seasons'
   AND pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'enrichment');
```
Then write the migration as a full `CREATE OR REPLACE FUNCTION`, with the world_boss branch added.

**Update `viz.slot_items` view** — add instance ID filtering for world_boss. The current view
joins world_boss with no instance filter (`s.instance_type = 'world_boss'`). Change to:

```sql
(s.instance_type = 'world_boss'
     AND s.blizzard_instance_id = ANY(rs.world_boss_instance_ids))
```

**IMPORTANT:** Read the full current view definition first. Check migration 0128 AND run
`SELECT definition FROM pg_views WHERE schemaname='viz' AND viewname='slot_items';` on dev
to get the live version (may differ from migrations). Copy it exactly, change only the world_boss
line, and write the migration as `DROP VIEW IF EXISTS viz.slot_items; CREATE VIEW viz.slot_items AS ...`.

Also add the **SQLAlchemy model column** — find the `RaidSeason` ORM model (search `current_raid_ids`
in `src/`). Add:
```python
world_boss_instance_ids = Column(ARRAY(Integer), nullable=False, server_default="{}")
```

### 1B. Update `get_available_items()` in `gear_plan_service.py`

**Change 1:** Add `'world_boss'` to the `item_category IN (...)` SQL filter (line ~1814):
```python
# Before:
AND item_category IN ('raid', 'dungeon', 'crafted', 'tier', 'catalyst')
# After:
AND item_category IN ('raid', 'dungeon', 'world_boss', 'crafted', 'tier', 'catalyst')
```

**Change 2:** Update the `empty` dict at the top of the function:
```python
empty: dict = {"tier": None, "raid": [], "dungeon": [], "world_boss": [], "crafted": []}
```

**Change 3:** Add `world_boss_map` grouping in the `for r in viz_rows` loop, after the dungeon
branch. World boss items share the same structure as raid/dungeon (multiple encounters per item):

```python
world_boss_map: dict[int, dict] = {}

# In the for r in viz_rows loop:
elif cat == "world_boss":
    if bid not in world_boss_map:
        world_boss_map[bid] = {
            "blizzard_item_id": bid,
            "name": r["name"],
            "icon_url": r["icon_url"],
            "primary_stat": r["primary_stat"],
            "sources": [],
            "popularity": pop_by_bid.get(bid, {}),
        }
    tracks = list(r["quality_tracks"] or [])
    src = {
        "source_name":     r["encounter_name"],
        "source_instance": r["instance_name"],
        "instance_type":   itype,
        "quality_tracks":  tracks,
    }
    if src not in world_boss_map[bid]["sources"]:
        world_boss_map[bid]["sources"].append(src)
```

**Change 4:** After the `raid_items` / `dungeon_items` conversion:
```python
world_boss_items = list(world_boss_map.values())
```

**Change 5:** Apply `primary_stat_filter` and strip `primary_stat` from world_boss items (same
as raid/dungeon, follow the exact same pattern at lines ~1976–1984).

**Change 6:** Apply `noncrafted_ilvl` as `target_ilvl` for world_boss items:
```python
for item in raid_items + dungeon_items + world_boss_items:
    item["target_ilvl"] = noncrafted_ilvl
```

**Change 7:** Return `"world_boss": world_boss_items` in the final result dict.

### 1C. Update `SeasonUpdate` Pydantic model and API handler

In `src/guild_portal/api/admin_routes.py`, add to `SeasonUpdate`:
```python
world_boss_instance_ids: list[int] | None = None
```

In the `PATCH /api/v1/admin/seasons/{season_id}` handler (~line 330), add handling for the new
field following the same pattern as `current_raid_ids`. Also update the `GET /api/v1/admin/seasons`
response to include `world_boss_instance_ids`.

### 1D. Reference Tables Admin UI

The reference_tables admin (`src/guild_portal/templates/admin/reference_tables.html`) already has
`<select multiple>` controls for Current Raids and M+ Dungeons. Add a third for World Bosses.

**Backend** (`src/guild_portal/pages/admin_pages.py` — find the route rendering `reference_tables.html`):

Add a query alongside `known_raids` and `known_instances`:
```python
known_world_boss = await conn.fetch(
    """SELECT DISTINCT blizzard_instance_id AS id, instance_name AS name
         FROM enrichment.item_sources
        WHERE instance_type = 'world_boss'
          AND NOT is_junk
          AND blizzard_instance_id IS NOT NULL
        ORDER BY name"""
)
```
Pass to template context.

**Template** — add a new `<th>World Boss Zones</th>` and corresponding `<td>` after M+ Dungeons,
following the exact same `<select multiple name="current_instance_ids" ...>` pattern. Use
`name="world_boss_instance_ids"` and `data-orig="{{ (season.world_boss_instance_ids or []) | tojson }}"`.

Verify the existing save JS handler picks up the new field — it should `PATCH` to
`/api/v1/admin/seasons/{id}` with all changed fields.

### 1E. Frontend Slot Drawer

Find the template and JS that render the slot drawer (search for "Available Items" or "raid"
section in the gear plan templates). Add a **World Bosses** section between Dungeon and Crafted,
using the same HTML/JS structure as Dungeon.

Display notes from existing `_contextual_sources()` logic (already handles world_boss correctly):
- World boss only shows when player needs Champion (C) track or higher
- Source label is "World Boss" (from `DISPLAY_NAME_BY_TYPE`)
- Items show encounter name (Cragpine / Lu'ashal / Predaxas / Thorm'belan) as `source_name`
- `target_ilvl` uses the same `noncrafted_ilvl` as raid/dungeon

---

## Part 2 — Delves (future phase, approach TBD)

Delves are **not in the Blizzard journal API**. Implementing delve loot requires a different data
source. Options, in order of preference:

### Option A: Wait for Blizzard
Blizzard may add delves to the journal API in a future patch. Monitor by re-checking
`GET /data/wow/journal-expansion/516` after major content patches. If a `delves` key appears,
implementation is trivial — same as dungeons with `instance_type='delve'`.

### Option B: Wowhead Scraping
Wowhead lists delve loot by instance. Could scrape delve item IDs the same way we scrape BIS lists,
then manually insert into `landing.blizzard_journal_encounters` / `landing.blizzard_journal_instances`
with `instance_type='delve'`. Requires:
- A new scrape target type in `config.bis_scrape_targets` or a separate scraper
- Manual delve instance ID assignment (Blizzard doesn't expose them, so use synthetic IDs)
- Adds maintenance burden — loot tables change between seasons

### Option C: Manual Maintenance
Maintain a hardcoded JSON or DB table of delve item IDs. Simplest but highest maintenance.

**Recommendation:** Do not implement until Option A is available (Blizzard API) or a major BIS
delve item emerges that users are asking for. Revisit before Season 2.

---

## Implementation Order (World Boss only)

1. **Migration** (single migration file):
   - Add `world_boss_instance_ids` column + seed Midnight row
   - Rewrite `sp_rebuild_item_seasons` with world_boss branch
   - Drop + recreate `viz.slot_items` with world_boss instance filter
2. **`gear_plan_service.py`** — 7 changes to `get_available_items()`
3. **`admin_routes.py`** — add field to `SeasonUpdate` + handler + GET response
4. **Reference Tables admin** — backend query + template column
5. **Frontend** — World Bosses section in slot drawer
6. **Run `sp_rebuild_all()`** on dev after migration to populate `item_seasons` and verify 17 items appear

---

## Known World Boss Data (Midnight S1)

- Instance ID: **1312** (`instance_name = 'Midnight'`, `instance_type = 'world_boss'`)
- Unique items: **17** (`item_category = 'world_boss'` in `enrichment.items`)
- Encounters: Cragpine, Lu'ashal, Predaxas, Thorm'belan (4 world bosses, shared loot pool)
- Quality tracks: `["C", "H", "M"]` — no Raid Finder tier (already in `source_config.py`)
- Source rows in `enrichment.item_sources`: 32 (items × encounters, many items on multiple bosses)
