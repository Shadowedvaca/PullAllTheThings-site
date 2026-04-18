# Gear Plan — Schema Overhaul Plan

> **Status:** Phase 0 complete (`prod-v0.19.1`). Phase A next.  
> **Trigger:** Repeated slot drawer bugs traced to transform logic running inside live UI queries,
> with categorization heuristics (tooltip HTML parsing, NOT EXISTS checks, suffix derivation)
> fighting each other every time data state changes.  
> **Scope:** Gear plan data pipeline and query layer. Platform-wide tables (players, characters,
> raid attendance, etc.) follow the same principles but are a separate effort.

---

## Problem Statement

The current gear plan data model conflates three distinct responsibilities in the same tables
and the same Python service:

1. Storing raw API data
2. Deriving structured facts from that data
3. Serving the UI

When transform logic changes (or breaks), it affects live queries immediately. When migrations
clean up data, `NOT EXISTS` conditions in UI queries see a different world and produce different
results. There is no clean reset path — raw and derived data live in the same rows.

The immediate symptom that forced this plan: catalyst items leaking into the Raid section, crafted
items gaining boss source rows, tier detection colliding with crafted set tooltips, and every fix
creating new breakage elsewhere.

---

## Target Architecture

Three schemas with strict ownership. A layer owns all code that puts data into its rested position.
Data only flows downward — landing → enrichment → visualization. Python only moves data into
landing and reads from visualization.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL APIs                                                          │
│  Blizzard Journal · Blizzard Item · Wowhead · Appearance API · BIS      │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ fetch + store JSON blob
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SCHEMA: landing                                                        │
│                                                                         │
│  One table per API source. Stores the raw response as JSONB.            │
│  No structure applied at ingest. Never modified after insert.           │
│  Python owns: fetch → INSERT. That's the entire job.                    │
│                                                                         │
│  landing.blizzard_journal_encounters                                     │
│  landing.blizzard_items                                                  │
│  landing.wowhead_tooltips                                                │
│  landing.blizzard_appearances                                            │
│  landing.bis_scrape_raw                                                  │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ sproc reads JSONB, writes structured rows
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SCHEMA: enrichment                                                     │
│                                                                         │
│  Structured, categorized, fully derived from landing. Never written     │
│  by Python directly — owned entirely by stored procedures.              │
│  Safe to truncate and repopulate at any time.                           │
│  If logic breaks: fix the sproc, call sp_rebuild_*, done.               │
│                                                                         │
│  enrichment.items          (structured item facts)                      │
│  enrichment.item_sources   (structured source facts + quality tracks)   │
│  enrichment.bis_entries    (structured BIS recommendations)             │
│  enrichment.item_recipes   (item → recipe relationships)                │
│  enrichment.trinket_ratings (tier ratings per spec/source)              │
└────────────────────────────┬────────────────────────────────────────────┘
                             │ views join enrichment tables
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SCHEMA: viz                                                            │
│                                                                         │
│  A small set of views. No tables, no sprocs, no logic.                  │
│  Python reads these. Nothing else.                                      │
│                                                                         │
│  viz.slot_items            (available items for a slot, pre-grouped)    │
│  viz.tier_piece_sources    (tier piece → token → boss chain)            │
│  viz.crafters_by_item      (item → guild crafters, sorted by rank)      │
│  viz.bis_recommendations   (BIS recs with source + tracks)              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: `landing` Schema

### Design rules

- One table per API source endpoint / crawl type.
- Every table has: `id`, `fetched_at TIMESTAMPTZ`, `payload JSONB`.
- Add a natural key where it exists (e.g. `blizzard_item_id`) so deduplication is possible,
  but **do not enforce referential integrity between landing tables**.
- Never `UPDATE` a landing row. If data changes, insert a new row.
- Schema changes in this layer are rare and only happen when a new API source is added.

### Tables

```sql
-- Raw Blizzard Journal encounter data (loot tables)
landing.blizzard_journal_encounters (
    id              SERIAL PRIMARY KEY,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    encounter_id    INTEGER NOT NULL,   -- natural key from API
    instance_id     INTEGER NOT NULL,
    payload         JSONB NOT NULL
)

-- Raw Blizzard item metadata (name, icon, slot, armor type, etc.)
landing.blizzard_items (
    id              SERIAL PRIMARY KEY,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    blizzard_item_id INTEGER NOT NULL,
    payload         JSONB NOT NULL
)

-- Raw Wowhead tooltip HTML (class restrictions, set membership, stat detection)
landing.wowhead_tooltips (
    id              SERIAL PRIMARY KEY,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    blizzard_item_id INTEGER NOT NULL,
    html            TEXT NOT NULL       -- raw HTML, not JSONB
)

-- Raw Blizzard Appearance API responses (for catalyst item discovery)
landing.blizzard_appearances (
    id              SERIAL PRIMARY KEY,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    appearance_id   INTEGER NOT NULL,
    payload         JSONB NOT NULL
)

-- Raw BIS scrape content (Wowhead, Archon, Icy Veins HTML/JSON pages)
landing.bis_scrape_raw (
    id              SERIAL PRIMARY KEY,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          VARCHAR(50) NOT NULL,   -- 'wowhead', 'archon', 'icyveins'
    url             TEXT NOT NULL,
    content         TEXT NOT NULL           -- raw HTML or JSON string
)
```

### Python's job at this layer

```python
# Fetch from API → serialize payload → INSERT. Nothing else.
await conn.execute(
    "INSERT INTO landing.blizzard_items (blizzard_item_id, payload) VALUES ($1, $2)",
    item_id, json.dumps(api_response)
)
```

No parsing. No field extraction. No conditional logic. If the API response structure changes,
the insert still succeeds — the payload blob just looks different. The break is discovered
downstream in the enrichment sproc, not in the ingest job.

---

## Layer 2: `enrichment` Schema

### Design rules

- Owned entirely by stored procedures. Python never `INSERT`s or `UPDATE`s here.
- Python triggers enrichment by calling `CALL enrichment.sp_rebuild_items()` etc.
- Every table can be fully repopulated from `landing` at any time.
- Add-only logic: if a source field is removed, the sproc handles the NULL gracefully and
  logs a data quality warning rather than crashing.
- Referential integrity is enforced within this schema.

### Tables

#### `enrichment.items`

The structured, categorized fact table for items. Replaces the mixed `guild_identity.wow_items`.

```sql
enrichment.items (
    blizzard_item_id    INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    icon_url            TEXT,
    slot_type           VARCHAR(30),        -- 'head', 'back', 'trinket_1', etc.
    armor_type          VARCHAR(20),        -- 'cloth', 'leather', 'mail', 'plate', NULL for accessories
    primary_stat        VARCHAR(10),        -- 'str', 'agi', 'int', NULL for non-weapons
    item_category       VARCHAR(20) NOT NULL
                        CHECK (item_category IN ('tier', 'catalyst', 'crafted', 'drop', 'unknown')),
    tier_set_suffix     TEXT,               -- ' of the Luminous Bloom' — set during tier classification
    quality_track       VARCHAR(1),         -- 'C' for catalyst items that carry a single track; NULL otherwise
    enriched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_blizzard_at  TIMESTAMPTZ,        -- when the landing.blizzard_items row was fetched
    source_wowhead_at   TIMESTAMPTZ         -- when the landing.wowhead_tooltips row was fetched
)
```

**`item_category` classification rules (owned by sproc):**

| Category | Rule |
|----------|------|
| `'crafted'` | Has a row in `enrichment.item_recipes` |
| `'catalyst'` | Wowhead tooltip absent/has-set-link AND slot in (back/wrist/waist/feet) AND name matches a tier set suffix AND NOT crafted |
| `'tier'` | Wowhead tooltip has `/item-set=` AND slot in (head/shoulder/chest/hands/legs) AND NOT crafted |
| `'drop'` | Has rows in `enrichment.item_sources` with instance_type in (raid/dungeon/world_boss) AND NOT crafted AND NOT tier |
| `'unknown'` | Does not meet any above criteria |

Rules are applied in order, crafted first. The sproc owns this logic — not the UI query.

#### `enrichment.item_sources`

Structured source rows with quality tracks pre-computed. Replaces `guild_identity.item_sources`.

```sql
enrichment.item_sources (
    id                  SERIAL PRIMARY KEY,
    blizzard_item_id    INTEGER NOT NULL REFERENCES enrichment.items,
    instance_type       VARCHAR(20) NOT NULL
                        CHECK (instance_type IN ('raid', 'dungeon', 'world_boss', 'catalyst')),
    encounter_name      TEXT,
    instance_name       TEXT,
    blizzard_instance_id INTEGER,
    blizzard_encounter_id INTEGER,
    quality_tracks      TEXT[] NOT NULL,    -- ['V','C','H','M'] — pre-computed from instance_type
    is_junk             BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (blizzard_item_id, instance_type, encounter_name)
)
```

`quality_tracks` is computed by the sproc at row insertion time — never derived at query time.
The `source_config.TRACKS_BY_TYPE` mapping belongs in the sproc, not in Python.

#### `enrichment.bis_entries`

Structured BIS recommendations. Replaces `guild_identity.bis_list_entries`.

```sql
enrichment.bis_entries (
    id              SERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL,   -- FK to bis_list_sources
    spec_id         INTEGER NOT NULL,
    hero_talent_id  INTEGER,
    slot            VARCHAR(30) NOT NULL,
    blizzard_item_id INTEGER NOT NULL REFERENCES enrichment.items,
    priority        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_id, spec_id, hero_talent_id, slot, blizzard_item_id)
)
```

#### `enrichment.item_recipes`

Item → craftable recipe relationship. Replaces `guild_identity.item_recipe_links`.

```sql
enrichment.item_recipes (
    id              SERIAL PRIMARY KEY,
    blizzard_item_id INTEGER NOT NULL REFERENCES enrichment.items,
    recipe_id       INTEGER NOT NULL,
    match_type      VARCHAR(50),
    confidence      INTEGER CHECK (confidence BETWEEN 0 AND 100),
    UNIQUE (blizzard_item_id, recipe_id)
)
```

#### `enrichment.trinket_ratings`

Trinket tier ratings per spec/source. Replaces `guild_identity.trinket_tier_ratings`.

```sql
enrichment.trinket_ratings (
    id              SERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL,
    spec_id         INTEGER NOT NULL,
    hero_talent_id  INTEGER,
    blizzard_item_id INTEGER NOT NULL REFERENCES enrichment.items,
    tier            VARCHAR(2) NOT NULL CHECK (tier IN ('S','A','B','C','D','F')),
    sort_order      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_id, spec_id, hero_talent_id, blizzard_item_id)
)
```

### Sprocs

Each sproc is responsible for one transformation step. They are idempotent — safe to call
repeatedly. They read from `landing`, write to `enrichment`.

```
enrichment.sp_rebuild_items()
    Reads: landing.blizzard_items, landing.wowhead_tooltips
    Writes: enrichment.items
    Logic: extract fields from JSONB, parse tooltip for armor_type/primary_stat/
           set membership, classify item_category, set tier_set_suffix

enrichment.sp_rebuild_item_sources()
    Reads: landing.blizzard_journal_encounters, enrichment.items
    Writes: enrichment.item_sources
    Logic: extract encounters and loot tables from JSONB, classify instance_type,
           compute quality_tracks, flag catalyst rows

enrichment.sp_rebuild_catalyst_items()
    Reads: landing.blizzard_appearances, enrichment.items
    Writes: enrichment.items (updates item_category='catalyst', tier_set_suffix)
            enrichment.item_sources (inserts catalyst source rows)
    Logic: suffix matching, quality_track='C' tagging

enrichment.sp_rebuild_bis_entries()
    Reads: landing.bis_scrape_raw, enrichment.items
    Writes: enrichment.bis_entries
    Logic: parse scrape content per source format, match items to enrichment.items

enrichment.sp_flag_junk_sources()
    Reads: enrichment.item_sources, enrichment.items
    Writes: enrichment.item_sources (sets is_junk=TRUE)
    Logic: tier piece direct drops, broad catch-all sources, crafted items with drop rows
```

---

## Layer 3: `viz` Schema

### Design rules

- Views only. No tables, no sprocs, no indexes.
- Each view is named for a UI use case, not for a DB concept.
- If a view gets complex, the complexity belongs in the enrichment sproc, not here.
- The entire schema should be readable in one sitting.

### Views

#### `viz.slot_items`

Everything `get_available_items()` currently builds across ~150 lines of Python + 3 SQL queries.

```sql
CREATE VIEW viz.slot_items AS
SELECT
    i.blizzard_item_id,
    i.name,
    i.icon_url,
    i.slot_type,
    i.armor_type,
    i.item_category,        -- 'tier', 'catalyst', 'crafted', 'drop'
    i.tier_set_suffix,
    s.instance_type,
    s.encounter_name,
    s.instance_name,
    s.blizzard_instance_id,
    s.quality_tracks,
    s.is_junk
FROM enrichment.items i
LEFT JOIN enrichment.item_sources s ON s.blizzard_item_id = i.blizzard_item_id
WHERE NOT COALESCE(s.is_junk, FALSE)
```

The Python service queries this with `WHERE slot_type = $1 AND (armor_type = $2 OR armor_type IS NULL)`
and groups the flat result by `item_category`. No heuristics. No tooltip parsing. No branching.

#### `viz.tier_piece_sources`

Already exists as `guild_identity.v_tier_piece_sources`. Moves here unchanged.

#### `viz.crafters_by_item`

Replaces the 7-table JOIN that runs on every plan detail load.

```sql
CREATE VIEW viz.crafters_by_item AS
SELECT
    ir.blizzard_item_id,
    r.name          AS recipe_name,
    p.name          AS profession_name,
    wc.id           AS character_id,
    wc.character_name,
    gr.level        AS rank_level,
    gr.name         AS rank_name
FROM enrichment.item_recipes ir
JOIN guild_identity.recipes r       ON r.id = ir.recipe_id
JOIN guild_identity.professions p   ON p.id = r.profession_id
JOIN guild_identity.character_recipes cr ON cr.recipe_id = r.id
JOIN guild_identity.wow_characters wc ON wc.id = cr.character_id
  AND wc.in_guild = TRUE
JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
JOIN guild_identity.players pl      ON pl.id = pc.player_id
JOIN common.guild_ranks gr          ON gr.id = pl.guild_rank_id
ORDER BY ir.blizzard_item_id, gr.level DESC, wc.character_name ASC
```

#### `viz.bis_recommendations`

```sql
CREATE VIEW viz.bis_recommendations AS
SELECT
    be.spec_id,
    be.hero_talent_id,
    be.slot,
    be.priority,
    be.source_id,
    bls.name        AS source_name,
    bls.origin      AS source_origin,
    i.blizzard_item_id,
    i.name,
    i.icon_url,
    i.item_category,
    i.tier_set_suffix,
    i.quality_tracks    -- aggregate from enrichment.item_sources if needed
FROM enrichment.bis_entries be
JOIN enrichment.items i     ON i.blizzard_item_id = be.blizzard_item_id
JOIN guild_identity.bis_list_sources bls ON bls.id = be.source_id
```

---

## Python's Role After the Overhaul

`gear_plan_service.py` shrinks to transport and session logic:

```python
# get_available_items() — was ~150 lines
rows = await conn.fetch("""
    SELECT * FROM viz.slot_items
     WHERE slot_type = $1
       AND (armor_type = $2 OR armor_type IS NULL)
       AND (blizzard_instance_id = ANY($3) OR item_category IN ('crafted', 'catalyst', 'tier'))
""", slot_type, armor_type, season_instance_ids)

# Group by item_category in Python — that's fine, it's presentation logic
raid_items    = [r for r in rows if r["item_category"] == "drop" and r["instance_type"] == "raid"]
dungeon_items = [r for r in rows if r["item_category"] == "drop" and r["instance_type"] == "dungeon"]
tier_items    = [r for r in rows if r["item_category"] in ("tier", "catalyst")]
crafted_items = [r for r in rows if r["item_category"] == "crafted"]
```

The remaining Python logic (is_equipped, is_bis, target ilvl, upgrade tracks) is correct at
this layer because it depends on player-specific state (what they have equipped, what they've
set as their goal). That stays in Python. Everything that is a fact about the item itself moves
to enrichment.

---

## Migration Path

This is a multi-phase migration. Nothing breaks until Phase 5. Each phase can ship independently.

### Phase 0 — Quick Fixes (no schema change, ship immediately) ✓ prod-v0.19.1

**Roster Needs — duplicated / out-of-order raid list**

Symptoms: the Roster Needs raid section on `/roster` shows entries in arbitrary order; the
drill panel for an instance appears to duplicate players across bosses.

Root causes:

1. **Server-side** — `_serialize_tracks()` in `gear_needs_routes.py` iterates a Python dict in
   insertion order (i.e. whatever order DB rows arrived). No sort is applied before the response
   is returned, so entry order is effectively random.

2. **Client-side** — `_gatherInstEntries()` in `roster_needs.js` merges per-boss entries into a
   plain JS object keyed by `player_id`, then returns `Object.values(playerMap)`. JS object
   property order is insertion order (first boss the player appeared under), not alphabetical.
   Combined with the unsorted server response, this produces a visually scrambled list that
   can look like duplicates.

Fix: sort by `player_name` (with `player_id` as a stable tiebreaker) in both locations.  
No migration, no schema change, no API contract change.

Files:
- `src/guild_portal/api/gear_needs_routes.py` — `_serialize_tracks()` (~line 297)
- `src/guild_portal/static/js/roster_needs.js` — `_gatherInstEntries()` (~line 137) and
  `_renderByPlayer()` (~line 290)

### Phase A — Create schemas and landing tables ✓ complete (2026-04-13, migration 0104)
- Created `landing`, `enrichment`, `viz` schemas
- Created 5 landing tables with JSONB payload columns (blizzard_journal_encounters, blizzard_items, wowhead_tooltips, blizzard_appearances, bis_scrape_raw)
- Added dual-write to all 5 ingest paths (item_source_sync.py, item_service.py, bis_sync.py)
- `_extract_archon()` and `_extract_wowhead()` return raw HTML for landing insert
- No UI changes, no existing functionality affected

### Phase B — Build enrichment sprocs ✓ complete (2026-04-14, migration 0105)
- Created 5 enrichment tables (items, item_sources, item_recipes, bis_entries, trinket_ratings)
- Created 2 helper functions: `_quality_tracks(TEXT)`, `_tooltip_slot(TEXT)`
- Created 8 stored procedures: sp_rebuild_items, sp_rebuild_item_sources, sp_rebuild_item_recipes, sp_rebuild_bis_entries, sp_rebuild_trinket_ratings, sp_update_item_categories, sp_flag_junk_sources, sp_rebuild_all
- Admin UI: Step 6 "Rebuild Enrichment" on `/admin/gear-plan`; `POST /api/v1/admin/bis/rebuild-enrichment`
- **Parity validated on dev:** enrichment counts match guild_identity exactly — 6884 items, 8481 sources, 5524 BIS entries, 2517 trinket ratings, 43 recipe links
- Transitional note: sprocs read from guild_identity.* (Phase B); full landing-based reads in Phase D+

### Phase C — Build viz views ✓ complete (2026-04-14, migration 0106)
- Created 4 views in the viz schema:
  - **`viz.slot_items`** — items + all non-junk source rows; Phase D read target for `get_available_items()`
  - **`viz.tier_piece_sources`** — tier piece → token → boss chain; uses `enrichment.items` (item_category='tier') + `enrichment.item_sources`, bridges token items via `guild_identity.tier_token_attrs` (legacy bridge, removed in Phase E)
  - **`viz.crafters_by_item`** — craftable item → in-guild crafters, sorted by rank level DESC; joins `enrichment.item_recipes` → `guild_identity` recipe/character/player/rank tables
  - **`viz.bis_recommendations`** — BIS entries with source metadata and aggregated quality_tracks (UNNEST dedup from non-junk item_sources)
- 51 unit tests in `tests/unit/test_viz_views.py` covering all 4 views + downgrade
- Deployed to dev — migration ran cleanly

### Phase D — Switch Python to read from viz ✓ complete (2026-04-14, migration none)
- `get_available_items()`: replaced 4 separate guild_identity queries (Q1 drops, Q2 crafted, Q2b trinket ratings, Q3 tier/catalyst) with a single `viz.slot_items` query. `item_category` discriminates groups; `quality_tracks` pre-computed in enrichment; armor type from direct column not tooltip HTML; tier/catalyst from `item_category` not anchor query heuristic.
- `get_plan_detail()`: BIS recs from `viz.bis_recommendations`; crafters from `viz.crafters_by_item`; tier sources from `viz.tier_piece_sources`; sources lookup from `enrichment.item_sources` (quality_tracks pre-computed); trinket badges from `enrichment.trinket_ratings`; craftable/tier detection from `enrichment.item_recipes` / `enrichment.items.item_category`; ht_source_ids from `enrichment.bis_entries`.
- `get_trinket_ratings()`: all 5 queries from enrichment tables; keyed on `blizzard_item_id` throughout (no internal item_id).
- `_filter_by_primary_stat()`: uses `primary_stat` column not tooltip HTML.
- Net: -458 lines, zero heuristic detection, zero tooltip HTML parsing in the service layer.
- Deployed to dev — all 1376 unit tests pass.

### Phase E — Enrichment classification overhaul + item_seasons bridge ✓ complete (migration 0107 + patches 0108–0129)
- **`enrichment.item_seasons`** — many-to-many bridge (item × season). Items not in active season are invisible.
- **`item_category`** updated: `('raid','dungeon','world_boss','crafted','tier','catalyst','unclassified')`.
- **`sp_rebuild_item_seasons()`**, **`sp_update_item_categories()`** rewritten. Tier detection uses token chain, not Wowhead HTML.
- **`viz.slot_items`** adds `item_seasons` JOIN; source filter restricted to active season instance IDs.
- **`ref` schema created** — `guild_identity.classes` moved to `ref.classes` + `blizzard_class_id` added. All 12 source files updated.
- **Key bug fixes in patches:** Evoker mail armor type (0124); tier_set_ids on raid_seasons (0125); ROBE→chest + playable_class_ids + quality (0125–0126); source instance filter in viz.slot_items (0128); CLOAK→back slot + BIS hero_talent null-safe filter (0129).

---

### Phase F — Complete `ref` schema (3 tables) ✓ complete (2026-04-17, migration 0130)

Moved the remaining pure reference / game config tables from `guild_identity` to `ref`.

- `guild_identity.specializations` → `ref.specializations`
- `guild_identity.hero_talents` → `ref.hero_talents`
- `guild_identity.bis_list_sources` → `ref.bis_list_sources`

Migration uses `ALTER TABLE ... SET SCHEMA ref` (3×); recreates `viz.bis_recommendations` to JOIN `ref.bis_list_sources`. All ForeignKey strings in `models.py` (13 occurrences) and raw SQL in 10 source files updated. FK constraints on other tables survive automatically (Postgres stores table OID in constraint, not schema-qualified name).

---

### Phase G — Retire `guild_identity.bis_list_entries` and `guild_identity.trinket_tier_ratings` ✓ complete (2026-04-17, migration 0131)

Both tables retired — enrichment equivalents (`enrichment.bis_entries`, `enrichment.trinket_ratings`) are now the sole source of truth.

- `bis_sync.py` — removed dead `_upsert_bis_entries()` and `_upsert_trinket_ratings()`; `cross_reference()` reads `enrichment.bis_entries + enrichment.items`
- `item_source_sync.py` — three EXISTS/JOIN subqueries switched to `enrichment.bis_entries`
- `bis_routes.py` — `/entries` CRUD, `/trinket-ratings-status`, and `p3_total` count use enrichment tables; `import_simc` docstring updated
- `gear_plan_auto_setup.py` — auto-plan slot population reads `enrichment.bis_entries`
- `gear_plan_service.py` — `populate_from_bis()` reads `enrichment.bis_entries`
- `item_service.py` — `enrich_blizzard_metadata()` filter uses `enrichment.bis_entries`
- `models.py` — `BisListEntry` ORM class removed
- Migration 0131 — `DROP guild_identity.bis_list_entries CASCADE`, `DROP guild_identity.trinket_tier_ratings CASCADE`; deployed to dev, tables confirmed absent.

---

### Phase H — Retire `guild_identity.wow_items` from the enrichment/viz process ✓ complete (2026-04-17, migration 0132)

**Track 1 — Fix the recipe bridge:**
- Added `blizzard_item_id INTEGER` to `guild_identity.item_recipe_links`; backfilled from wow_items via item_id FK.
- `item_recipe_link_sync.py` — all 3 INSERT paths (`build_item_recipe_links`, `_stub_and_link`, discover phase 2a) now write `blizzard_item_id` alongside `item_id`.
- `sp_rebuild_item_recipes` rewritten — uses `irl.blizzard_item_id` directly; `JOIN guild_identity.wow_items` eliminated.

**Track 2 — Replace `wow_items` reads in the enrichment/viz process:**
- `item_source_sync.py`:
  - **BUG FIX**: `enrich_catalyst_tier_items()` suffix_seed_rows was still JOINing `guild_identity.bis_list_entries` (dropped in Phase G) — switched to `enrichment.bis_entries`.
  - `tier_items` NOT EXISTS clauses: `irl.item_id = wi.id` → `irl.blizzard_item_id = wi.blizzard_item_id`.
  - `all_catalyst_bis` query: `enrichment.items` is now the primary source for `name`/`slot_type`; `wow_items` JOIN kept only for `item_id` FK resolution (needed for `item_sources` INSERT).
  - `flag_junk_sources` tier piece check: `wowhead_tooltip_html LIKE '%/item-set=%'` → `enrichment.items.item_category = 'tier'`.
- `bis_routes.py` — landing fill crafted items: eliminated `JOIN guild_identity.wow_items`; reads `irl.blizzard_item_id` directly.

**Out of scope:** `guild_identity.wow_items` is still used by non-enrichment code (admin tooltip fetching, Wowhead enrichment ingest, `process_tier_tokens`, `item_sources` FK writes). Those are not affected.

---

## Phase I — Documentation Updates

Final step. After Phase E is complete and the new stack is stable on prod, update the docs to match.

### Files to update

| File | What to change |
|------|---------------|
| `docs/ARCHITECTURE.md` | Section 4.1 data layer box: add `landing`, `enrichment`, `viz` schemas. Section 6.1 schema map: add all three new schemas below the existing table. Add "Gear Plan Data Pipeline" to section 2 process flows. |
| `docs/SCHEMA.md` | Remove the stale "current through migration 0044" note. Add a section for each new schema (`landing`, `enrichment`, `viz`) with the table definitions from this plan. Update the `guild_identity` gear plan tables section to reflect what moved to enrichment. Add a "Migration note" at the top pointing to this file for migration history. |
| `docs/OPERATIONS.md` | Add a **Gear Plan Admin** section covering: the correct order to run admin sync steps (Sync Loot Tables → Enrich Items → Process Tier Tokens → Sync BIS Lists), when each enrichment sproc needs to be rerun, and the new enrichment rebuild commands (`CALL enrichment.sp_rebuild_items()` etc.). |
| `CLAUDE.md` | Update "Current Build Status", "Last migration", and "What Exists" to reflect the new schema architecture. Remove gear plan notes from "Known Gaps" once the overhaul is complete. |

### What not to change
- `docs/DESIGN.md` — design language is unaffected
- `docs/DEPLOY.md` — deployment pipeline is unaffected
- `docs/BACKUPS.md` — backup procedures are unaffected
- `docs/DISCORD.md`, `docs/DISCORD-BOT-SETUP.md` — Discord bot is unaffected

---

## What Is Out of Scope

- `guild_identity.wow_characters`, `players`, `player_characters` — character/player identity
  pipeline follows the same principles but is a separate effort
- `patt.*` — raid/attendance/campaign tables are not affected
- `common.*` — auth/config tables are not affected
- WCL parse ingestion — follows the same pattern, separate effort

---

## Tables Affected (Current → Target)

| Current | Fate |
|---------|------|
| `guild_identity.wow_items` | Split: raw fields → `landing.blizzard_items` + `landing.wowhead_tooltips`; structured facts → `enrichment.items` |
| `guild_identity.item_sources` | Split: raw journal rows → `landing.blizzard_journal_encounters`; structured → `enrichment.item_sources` |
| `guild_identity.bis_list_entries` | → `enrichment.bis_entries` |
| `guild_identity.bis_scrape_targets` / `bis_scrape_log` | Scrape targets stay as config; raw scrape content → `landing.bis_scrape_raw` |
| `guild_identity.item_recipe_links` | → `enrichment.item_recipes` |
| `guild_identity.trinket_tier_ratings` | → `enrichment.trinket_ratings` |
| `guild_identity.hero_talents` | Reference data, stays in `guild_identity` |
| `guild_identity.v_tier_piece_sources` | Moves to `viz.tier_piece_sources` |
| `gear_plan_service.py` get_available_items() | Replaced by `SELECT * FROM viz.slot_items` + thin grouping |
| `gear_plan_service.py` get_plan_detail() sources | Replaced by `SELECT * FROM viz.item_sources_flat` |
| `item_source_sync.py` enrich_catalyst_tier_items() | Logic moves to `enrichment.sp_rebuild_catalyst_items()` |
