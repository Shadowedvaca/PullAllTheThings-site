# Removing `guild_identity.wow_items` — Phased Retirement Plan

> **Status:** Phase D complete — deployed to dev, on branch `feature/gear-plan-1.0-wow_items-fix`
> **Branch:** `feature/gear-plan-1.0-wow_items-fix` (off `main` after `prod-v0.20.2`)
> **Migration sequence:** 0141 → 0145 (0141 = Phase A, 0142 = Phase B, 0143 = Phase C, 0144 = Phase D)
> **Last updated:** 2026-04-18
> **Rollback point:** `patt_db_pre_v020_20260417_213540.dump` on prod server at `/opt/guild-portal/backups/`

---

## Executive Summary

`guild_identity.wow_items` was created in Phase 1A (migration 0066) as a hybrid
cache-and-metadata table: it held Wowhead tooltip HTML, Blizzard API data, icon
URLs, and served as the FK anchor for four downstream tables
(`item_sources`, `character_equipment`, `gear_plan_slots`, `tier_token_attrs`).

The `landing → enrichment → viz` pipeline built in the schema overhaul
(migrations 0104–0139) has superseded `wow_items` as the canonical item store.
`enrichment.items` now holds the same fields (plus richer ones like `weapon_subtype`,
`primary_stat`, `playable_class_ids`), rebuilt deterministically from
`landing.blizzard_items`. The stored procedures `sp_rebuild_items`,
`sp_rebuild_item_sources`, and `sp_rebuild_item_recipes` already read from
`landing.*` and write to `enrichment.*` without touching `wow_items`.

The problem is that four tables still carry integer surrogate FKs into `wow_items`,
and several Python write paths still stub rows into `wow_items` as a prerequisite
to getting the integer PK needed for those FK columns. Until those FKs are cut,
`wow_items` cannot be dropped — and `sp_rebuild_items` cannot freely TRUNCATE
`enrichment.items` without risking cascade problems if the enrichment table were
ever to become the new FK target.

This plan retires `wow_items` in five phases. Each phase is independently
deployable and testable. The approach chosen is **Option A** (see Decision Points
below): convert FK columns to plain `blizzard_item_id` references with no
constraint, matching the pattern already in place on `item_recipe_links.blizzard_item_id`.

---

## Rollback Procedure

If any phase goes sideways on prod:

**DB restore:**
```bash
ssh hetzner
docker exec guild-portal-db-prod-1 psql -U patt_user -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='patt_db' AND pid <> pg_backend_pid();"
docker exec -i guild-portal-db-prod-1 pg_restore -U patt_user -d patt_db --clean --if-exists /tmp/backup.dump
# (copy the backup into the container first)
docker cp /opt/guild-portal/backups/patt_db_pre_v020_20260417_213540.dump guild-portal-db-prod-1:/tmp/backup.dump
```

**App rollback** (roll back to prod-v0.20.0 before wow_items work starts):
```bash
git tag prod-v0.20.1-rollback prod-v0.20.0
git push origin prod-v0.20.1-rollback  # triggers prod deploy of old code
```

The backup at `/opt/guild-portal/backups/patt_db_pre_v020_20260417_213540.dump`
is the clean restore point captured before migrations 0133–0139 went to prod.
It is in PostgreSQL custom format; restore with `pg_restore`, not `psql`.

---

## Decision Points — All Resolved

These were discussed and settled before Phase A work began.

---

### D1: FK Strategy — **RESOLVED: Option A (plain `blizzard_item_id` column, no FK constraint)**

`blizzard_item_id` is a stable, consistent natural key — Blizzard never recycles
item IDs. Each FK table gets a plain `INTEGER blizzard_item_id` column with no
foreign key constraint pointing at `enrichment.items`. This matches the pattern
already in place on `item_recipe_links` (migration 0132).

The only con is that the DB won't enforce referential integrity automatically.
This is acceptable because `enrichment.items` is a TRUNCATE-and-rebuild table —
a FK pointing at it would cascade-delete all downstream rows on every sproc run.
Application-layer integrity (the sync pipeline) is the right enforcement point.

---

### D2: Wowhead Tooltip HTML — **RESOLVED: D2-B — keep in `landing.wowhead_tooltips`, read from there**

`landing.wowhead_tooltips` was created in migration 0104 but is currently **empty**
— `item_service.py` was never updated to write there; it still writes exclusively
to `wow_items.wowhead_tooltip_html`. This is the primary task of Phase B:

1. Migration backfills `landing.wowhead_tooltips` from `wow_items.wowhead_tooltip_html`
2. `item_service.py` write path is updated to write to `landing.wowhead_tooltips`
3. All Python code that reads tooltip HTML switches from `wow_items` to `landing.wowhead_tooltips`
4. `enrichment.items` does NOT get a `wowhead_tooltip_html` column — raw HTML stays in landing

---

### D3: `item_sources` referential integrity — **RESOLVED: no FK, plain `blizzard_item_id` column**

`guild_identity.item_sources` is the write target for loot table sync; it is a
separate table from `enrichment.item_sources` (the read target rebuilt by sproc).
A plain `blizzard_item_id INTEGER NOT NULL` column replaces `item_id`. No FK needed.
The existing "Sync Loot Tables → Enrich & Classify" workflow is the cleanup mechanism.

---

### D4: `weapon_type` — **RESOLVED: drop it, nothing uses it**

`wow_items.weapon_type` (e.g. `"Sword"`, `"Axe"`) is only read inside
`item_service.py` itself — written during Wowhead fetch, read back in
`get_or_fetch_item()`, but never consumed by any gear plan route, template,
or other service. `enrichment.items.weapon_subtype` ("One-Handed Sword",
"Two-Handed Axe") is a richer superset already in use. `weapon_type` drops
silently with `wow_items`.

---

## Scope of Changes

### Python files with write paths into `wow_items`

| File | Nature of write |
|------|-----------------|
| `src/sv_common/guild_sync/equipment_sync.py` | Stubs rows; then SELECTs `id` for `character_equipment.item_id` |
| `src/sv_common/guild_sync/item_source_sync.py` | Stubs rows; then SELECTs `id` for `item_sources.item_id`; also reads `wowhead_tooltip_html` and `quality_track`; reads `wow_items` in `enrich_catalyst_tier_items`, `process_tier_tokens`, `flag_junk_sources` |
| `src/sv_common/guild_sync/item_recipe_link_sync.py` | Stubs rows; then SELECTs `id` for `item_recipe_links.item_id`; bulk INSERT in phase 2a also writes `wow_items` |
| `src/guild_portal/services/item_service.py` | Full enrichment writes (tooltip HTML, icon_url, slot_type, armor_type, weapon_type); also reads all columns for cache-hit path |

### Python files with read paths from `wow_items`

| File | Nature of read |
|------|----------------|
| `src/sv_common/guild_sync/gear_plan_auto_setup.py` | JOINs `wow_items` to resolve `desired_item_id` for `gear_plan_slots` |
| `src/sv_common/guild_sync/item_source_sync.py` | Multiple reads — see above |
| `src/guild_portal/api/bis_routes.py` | Already migrated to use `enrichment.*` for most paths; check for any remaining `wow_items` references |
| Various gear plan service files | May JOIN `wow_items` for icon/slot metadata; to be confirmed in Phase D audit |

### ORM models

`src/sv_common/db/models.py` — `WowItem`, `ItemSource`, `CharacterEquipment`,
`GearPlanSlot`, `TierTokenAttrs` all reference `guild_identity.wow_items.id`.

### Stored procedures already migrated away from `wow_items`

- `enrichment.sp_rebuild_items()` — reads `landing.blizzard_items` only ✓
- `enrichment.sp_rebuild_item_sources()` — reads `landing.*` only ✓
- `enrichment.sp_rebuild_item_recipes()` — reads `item_recipe_links.blizzard_item_id` directly ✓

### Stored procedures still touching `wow_items` (indirectly via Python callers)

None of the current stored procedures in `enrichment.*` reference `guild_identity.wow_items`.
However, several Python functions in `item_source_sync.py` execute ad-hoc SQL against
`wow_items` (not via stored procedures) — these are the primary cleanup targets.

---

## Phased Implementation Plan

> **Option A is assumed throughout:** `blizzard_item_id` replaces the integer FK.
> Each phase is independently deployable and must pass all unit tests before the
> next phase begins.

---

### Phase A — Migration 0141: Add `blizzard_item_id` to the four FK tables + backfill ✓ COMPLETE (dev)


**Goal:** Give each table a stable natural key column so Phase C (code rewrite)
can write `blizzard_item_id` without needing the `wow_items` integer id lookup.
The old `item_id` / `desired_item_id` / `token_item_id` columns are NOT dropped in
this phase — dual columns exist temporarily to allow zero-downtime cutover.

#### SQL (migration upgrade)

```sql
-- 1. item_sources: add blizzard_item_id
ALTER TABLE guild_identity.item_sources
    ADD COLUMN blizzard_item_id INTEGER;

UPDATE guild_identity.item_sources s
   SET blizzard_item_id = wi.blizzard_item_id
  FROM guild_identity.wow_items wi
 WHERE wi.id = s.item_id;

CREATE INDEX ON guild_identity.item_sources (blizzard_item_id);

-- 2. character_equipment: already has blizzard_item_id (added Phase 1A) — verify NOT NULL
-- No new column needed. item_id (FK) is the only vestige.

-- 3. gear_plan_slots: blizzard_item_id already exists (added migration 0086/0087) — verify.
-- desired_item_id is the FK to drop later.

-- 4. tier_token_attrs: add blizzard_item_id
ALTER TABLE guild_identity.tier_token_attrs
    ADD COLUMN blizzard_item_id INTEGER;

UPDATE guild_identity.tier_token_attrs t
   SET blizzard_item_id = wi.blizzard_item_id
  FROM guild_identity.wow_items wi
 WHERE wi.id = t.token_item_id;

CREATE INDEX ON guild_identity.tier_token_attrs (blizzard_item_id);
```

> Note: `character_equipment.blizzard_item_id` and `gear_plan_slots.blizzard_item_id`
> were already added in earlier migrations. Verify at migration time that both are
> populated (NOT NULL or very few NULLs) before proceeding.

#### Verification queries

```sql
-- item_sources: should be 0 nulls
SELECT COUNT(*) FROM guild_identity.item_sources WHERE blizzard_item_id IS NULL;

-- tier_token_attrs: should be 0 nulls (all tokens are in wow_items)
SELECT COUNT(*) FROM guild_identity.tier_token_attrs WHERE blizzard_item_id IS NULL;

-- gear_plan_slots: acceptable to have some NULLs (unset slots), but
--   desired_item_id should correlate with blizzard_item_id where both exist
SELECT COUNT(*) FROM guild_identity.gear_plan_slots
 WHERE desired_item_id IS NOT NULL AND blizzard_item_id IS NULL;
-- Should be 0 after backfill.
```

#### Files changed

- `alembic/versions/0140_add_blizzard_item_id_to_fk_tables.py` (new migration)

#### Risk

Low. Additive only. No code changes. If the backfill UPDATE misses rows
(wow_items.id orphans), those rows will have `blizzard_item_id IS NULL` after
Phase A — catch this with the verification queries above before proceeding.

---

### Phase B — Migration 0142: Backfill `landing.wowhead_tooltips` from `wow_items` ✓ COMPLETE (dev)

**Goal:** Ensure `landing.wowhead_tooltips` is the complete source of tooltip HTML
so Python code can stop reading from `wow_items.wowhead_tooltip_html`.

The `landing.wowhead_tooltips` table currently holds JSON payloads written by
`item_service.py` during live enrichment runs. It may be incomplete for:
- Items stubbed but never enriched via Wowhead (e.g. items from the Blizzard API
  discovery path that only wrote to `wow_items` directly)
- Items enriched before Phase A dual-write was introduced (migration 0104)

#### SQL (migration upgrade)

```sql
-- Backfill landing.wowhead_tooltips from wow_items.wowhead_tooltip_html
-- Only inserts rows not already present; wraps HTML in a minimal JSON envelope
-- that matches the real Wowhead API response shape: {"tooltip": "..."}
-- sp_rebuild_items does NOT read wowhead_tooltips, so this is purely for Python
-- code that needs to inspect tooltip HTML (process_tier_tokens, flag_junk_sources).

INSERT INTO landing.wowhead_tooltips (blizzard_item_id, payload)
SELECT wi.blizzard_item_id,
       jsonb_build_object('tooltip', wi.wowhead_tooltip_html)
  FROM guild_identity.wow_items wi
 WHERE wi.wowhead_tooltip_html IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM landing.wowhead_tooltips wt
        WHERE wt.blizzard_item_id = wi.blizzard_item_id
   );
```

> This backfill is idempotent — it only inserts rows not already present.

#### Python changes (alongside migration)

`item_source_sync.py` — `process_tier_tokens()`:

Replace:
```python
candidates = await conn.fetch(
    """
    SELECT id, blizzard_item_id, name, wowhead_tooltip_html
      FROM guild_identity.wow_items
     WHERE slot_type = 'other'
       AND wowhead_tooltip_html IS NOT NULL
       AND wowhead_tooltip_html != ''
    """
)
```

With:
```python
candidates = await conn.fetch(
    """
    SELECT ei.blizzard_item_id, ei.name,
           wt.payload->>'tooltip' AS wowhead_tooltip_html
      FROM enrichment.items ei
      JOIN landing.wowhead_tooltips wt ON wt.blizzard_item_id = ei.blizzard_item_id
     WHERE ei.slot_type = 'other'
    """
)
```

Also remove the `item_id` (wow_items PK) references inside `process_tier_tokens`:
the `tier_token_attrs` table will use `blizzard_item_id` as PK after Phase E.
For now, retain the dual-write (write both `token_item_id` from `wow_items.id`
and the new `blizzard_item_id`).

`item_source_sync.py` — `flag_junk_sources()` (tier piece check):

The current code:
```python
UPDATE guild_identity.item_sources s
   SET is_suspected_junk = TRUE
  FROM guild_identity.wow_items wi
 WHERE wi.id = s.item_id
   AND wi.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
   AND EXISTS (
         SELECT 1 FROM enrichment.items ei
          WHERE ei.blizzard_item_id = wi.blizzard_item_id
            AND ei.item_category = 'tier'
       )
```

Rewrite to use `item_sources.blizzard_item_id` (added in Phase A):
```sql
UPDATE guild_identity.item_sources s
   SET is_suspected_junk = TRUE
  FROM enrichment.items ei
 WHERE ei.blizzard_item_id = s.blizzard_item_id
   AND ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
   AND ei.item_category = 'tier'
```

`item_source_sync.py` — `enrich_catalyst_tier_items()`:

Several DELETE statements reference `wow_items`:

```sql
-- Remove stale "Revival Catalyst" rows for tier items with /item-set= tooltip
DELETE FROM guild_identity.item_sources
 WHERE encounter_name = 'Revival Catalyst'
   AND item_id IN (
       SELECT wi.id FROM guild_identity.wow_items wi
        WHERE wi.wowhead_tooltip_html LIKE '%/item-set=%'
   )
```

Rewrite using `landing.wowhead_tooltips`:
```sql
DELETE FROM guild_identity.item_sources
 WHERE encounter_name = 'Revival Catalyst'
   AND blizzard_item_id IN (
       SELECT wt.blizzard_item_id
         FROM landing.wowhead_tooltips wt
        WHERE wt.payload->>'tooltip' LIKE '%/item-set=%'
   )
```

The second DELETE (quality_track='C' filter) already reads from
`landing.blizzard_item_quality_tracks` — no change needed.

The third DELETE (craftable items) already reads `item_recipe_links` by
`item_id` FK. After Phase A we can switch it to use `blizzard_item_id`:
```sql
DELETE FROM guild_identity.item_sources
 WHERE instance_type IN ('raid', 'world_boss')
   AND blizzard_item_id IN (
       SELECT blizzard_item_id FROM guild_identity.item_recipe_links
        WHERE blizzard_item_id IS NOT NULL
   )
```

`item_source_sync.py` — `tier_items` query in Pass 1:

The `enrich_catalyst_tier_items()` Pass 1 query uses `JOIN guild_identity.wow_items`
to get `wi.id AS wow_item_id` (needed to INSERT into `item_sources.item_id`).
After Phase A, `item_sources.blizzard_item_id` is the target instead; replace:
```sql
SELECT DISTINCT wi.id AS wow_item_id, wi.blizzard_item_id, wi.name,
       COALESCE(NULLIF(wi.slot_type, 'other'), be.slot) AS eff_slot
  FROM enrichment.bis_entries be
  JOIN guild_identity.wow_items wi ON wi.blizzard_item_id = be.blizzard_item_id
 WHERE ...
```
With:
```sql
SELECT DISTINCT ei.blizzard_item_id, ei.name, ei.slot_type AS eff_slot
  FROM enrichment.bis_entries be
  JOIN enrichment.items ei ON ei.blizzard_item_id = be.blizzard_item_id
 WHERE be.slot = ANY($1::text[])
   AND ei.quality_track IS DISTINCT FROM 'C'
   AND NOT EXISTS (
           SELECT 1 FROM guild_identity.item_recipe_links irl
            WHERE irl.blizzard_item_id = ei.blizzard_item_id
       )
   AND (
       EXISTS (
           SELECT 1 FROM landing.wowhead_tooltips wt
            WHERE wt.blizzard_item_id = ei.blizzard_item_id
              AND wt.payload->>'tooltip' LIKE '%/item-set=%'
       )
       OR (
           ei.armor_type IS NOT NULL
           AND ei.name LIKE '% of %'
           AND NOT EXISTS (SELECT 1 FROM guild_identity.item_sources s
                            WHERE s.blizzard_item_id = ei.blizzard_item_id)
           AND NOT EXISTS (SELECT 1 FROM guild_identity.item_recipe_links irl
                            WHERE irl.blizzard_item_id = ei.blizzard_item_id)
       )
   )
```

The boss_rows query also JOINs `wow_items` — rewrite to JOIN `enrichment.items`.

The INSERT into `item_sources` in both Pass 1 and Pass 2 currently uses
`tier["wow_item_id"]` for `item_id`. With Phase A complete, the INSERT targets
`blizzard_item_id` column instead (still writing `item_id` too until Phase E).

`item_service.py` — `backfill_armor_type_from_tooltip()`:

This function still UPDATEs `guild_identity.wow_items`. In Phase B it can be
changed to write to `enrichment.items` instead (or left as-is until Phase D).
Since `sp_rebuild_items` will repopulate `enrichment.items` from Blizzard API
data on next rebuild, the backfill is only useful if the item has a tooltip but
no Blizzard API payload. **Defer to Phase D.**

`item_source_sync.py` — `sync_tier_set_completions()`:

Still reads `wow_items.wowhead_tooltip_html` (Path 1: extract set IDs from
tooltip). Rewrite to read from `landing.wowhead_tooltips`:
```sql
SELECT DISTINCT
       (regexp_match(wt.payload->>'tooltip', '/item-set=([0-9]+)/'))[1]::int AS set_id
  FROM landing.wowhead_tooltips wt
 WHERE wt.payload->>'tooltip' LIKE '%/item-set=%'
```

Also Path 2 (fallback to enrichment.items for candidates with no tooltip):
Already reads `enrichment.bis_entries` — this query also JOINs `wow_items`:
```sql
SELECT DISTINCT wi.blizzard_item_id
  FROM guild_identity.wow_items wi
 WHERE wi.slot_type IN ('head','shoulder','chest','hands','legs')
   AND wi.armor_type IS NOT NULL
   AND wi.wowhead_tooltip_html IS NULL
   AND NOT EXISTS (SELECT 1 FROM guild_identity.item_sources s WHERE s.item_id = wi.id)
   AND EXISTS (SELECT 1 FROM enrichment.bis_entries be WHERE be.blizzard_item_id = wi.blizzard_item_id)
```
Rewrite to use `enrichment.items` and `item_sources.blizzard_item_id`:
```sql
SELECT DISTINCT ei.blizzard_item_id
  FROM enrichment.items ei
 WHERE ei.slot_type IN ('head','shoulder','chest','hands','legs')
   AND ei.armor_type IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM landing.wowhead_tooltips wt WHERE wt.blizzard_item_id = ei.blizzard_item_id)
   AND NOT EXISTS (SELECT 1 FROM guild_identity.item_sources s WHERE s.blizzard_item_id = ei.blizzard_item_id)
   AND EXISTS (SELECT 1 FROM enrichment.bis_entries be WHERE be.blizzard_item_id = ei.blizzard_item_id)
```

#### Files changed

- `alembic/versions/0141_backfill_landing_wowhead_tooltips.py` (new migration)
- `src/sv_common/guild_sync/item_source_sync.py` — `process_tier_tokens`,
  `flag_junk_sources`, `enrich_catalyst_tier_items`, `sync_tier_set_completions`

#### Risk

Medium. Multiple SQL queries being rewritten. Each rewrite should be validated
against real dev data before proceeding. Key verification:

```sql
-- Tier token detection should still find the same tokens
SELECT COUNT(*) FROM guild_identity.tier_token_attrs;
-- Before and after Phase B code deploy — numbers should not drop.

-- item_sources junk flags should still be applied correctly
SELECT instance_type, is_suspected_junk, COUNT(*)
  FROM guild_identity.item_sources
 GROUP BY 1, 2 ORDER BY 1, 2;
```

---

### Phase C — Migration 0143: Rewrite write paths — stop writing to `wow_items` ✓ COMPLETE (dev)

**Goal:** Stop all Python code from INSERTing or UPDATEing `wow_items`.
Instead, write directly to `landing.blizzard_items` (for Blizzard API data)
and `landing.wowhead_tooltips` (for Wowhead data). Let the enrichment rebuild
sproc propagate data to `enrichment.items`.

This phase is the heaviest code change. After Phase C, `wow_items` becomes
read-only from Python's perspective. The enrichment pipeline already reads
from landing, so no sproc changes are needed.

#### `equipment_sync.py` — Stop stubbing `wow_items`

The stub + id-lookup pattern:
```python
await conn.execute(
    "INSERT INTO guild_identity.wow_items (blizzard_item_id, name, slot_type) "
    "VALUES ($1, $2, 'other') ON CONFLICT (blizzard_item_id) DO NOTHING",
    slot_data.blizzard_item_id, slot_data.item_name,
)
item_row = await conn.fetchrow(
    "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
    slot_data.blizzard_item_id,
)
wow_item_id = item_row["id"] if item_row else None
```

Becomes:
```python
# Write to landing.blizzard_items if not already present.
# item_service / Enrich Items step will fill enrichment.items.
# No stub needed in wow_items.

await conn.execute(
    """
    INSERT INTO guild_identity.character_equipment
        (character_id, slot, blizzard_item_id, item_name,
         item_level, quality_track, bonus_ids, enchant_id, gem_ids, synced_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    ON CONFLICT (character_id, slot) DO UPDATE
        SET blizzard_item_id = EXCLUDED.blizzard_item_id,
            item_name        = EXCLUDED.item_name,
            ...
    """,
    char_id, slot_data.slot, slot_data.blizzard_item_id, slot_data.item_name,
    ...
)
```

Note: `character_equipment.item_id` is set to NULL in the INSERT (or omitted)
after Phase A since the column still exists but is no longer populated.

#### `item_source_sync.py` — Stop stubbing `wow_items` in `_sync_encounter`

The stub pattern in `_sync_encounter()`:
```python
await conn.execute(
    "INSERT INTO guild_identity.wow_items (blizzard_item_id, name, slot_type) "
    "VALUES ($1, $2, 'other') ON CONFLICT ...",
    blizzard_item_id, item_name,
)
row = await conn.fetchrow(
    "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
    blizzard_item_id,
)
wow_item_id = row["id"]

await conn.execute(
    "INSERT INTO guild_identity.item_sources (item_id, ...) VALUES ($1, ...)",
    wow_item_id, ...
)
```

Becomes: write only `blizzard_item_id` to `item_sources` (Phase A column):
```python
# No wow_items stub needed.
await conn.execute(
    """
    INSERT INTO guild_identity.item_sources
           (blizzard_item_id, instance_type, encounter_name, instance_name,
            blizzard_encounter_id, blizzard_instance_id)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (blizzard_item_id, instance_type, encounter_name)
    DO UPDATE SET ...
    """,
    blizzard_item_id, instance_type, encounter_name, instance_name,
    encounter_id, instance_id,
)
```

The UNIQUE constraint on `item_sources` is currently
`(item_id, instance_type, encounter_name)`. Phase C needs the constraint
changed to `(blizzard_item_id, instance_type, encounter_name)`.

Migration 0142 SQL:
```sql
-- Drop old unique constraint (based on item_id FK)
ALTER TABLE guild_identity.item_sources
    DROP CONSTRAINT uq_item_source;

-- Add new unique constraint (based on blizzard_item_id)
ALTER TABLE guild_identity.item_sources
    ADD CONSTRAINT uq_item_source_bid
    UNIQUE (blizzard_item_id, instance_type, encounter_name);
```

#### `item_recipe_link_sync.py` — Stop stubbing `wow_items`

Phase 2a uses a bulk INSERT into `wow_items`:
```sql
INSERT INTO guild_identity.wow_items (blizzard_item_id, name, slot_type)
SELECT DISTINCT ON (ce.blizzard_item_id) ce.blizzard_item_id, ce.item_name, ce.slot
  FROM guild_identity.character_equipment ce
  JOIN guild_identity.recipes rec ON LOWER(rec.name) = LOWER(ce.item_name)
  JOIN guild_identity.profession_tiers pt ON pt.id = rec.tier_id
 WHERE pt.expansion_name = $1
   AND ce.blizzard_item_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM guild_identity.item_recipe_links irl WHERE irl.recipe_id = rec.id)
ON CONFLICT (blizzard_item_id) DO NOTHING
```

Remove the `wow_items` INSERT entirely. The link INSERT only needs
`blizzard_item_id`:
```sql
INSERT INTO guild_identity.item_recipe_links
    (blizzard_item_id, recipe_id, confidence, match_type)
SELECT DISTINCT ce.blizzard_item_id, rec.id, 100, 'equipment_name_match'
  FROM guild_identity.character_equipment ce
  JOIN guild_identity.recipes rec ON LOWER(rec.name) = LOWER(ce.item_name)
  JOIN guild_identity.profession_tiers pt ON pt.id = rec.tier_id
 WHERE pt.expansion_name = $1
   AND ce.blizzard_item_id IS NOT NULL
ON CONFLICT (blizzard_item_id, recipe_id) DO NOTHING
```

Note: `item_recipe_links.item_id` is the old FK column. It should be omitted
from new INSERTs but retains data for existing rows. The UNIQUE constraint
`(item_id, recipe_id)` needs to be migrated:

Migration 0142 SQL:
```sql
-- Drop old unique constraint on item_recipe_links
ALTER TABLE guild_identity.item_recipe_links
    DROP CONSTRAINT uq_item_recipe_link;  -- check actual constraint name

-- Add new unique on blizzard_item_id + recipe_id
ALTER TABLE guild_identity.item_recipe_links
    ADD CONSTRAINT uq_item_recipe_link_bid
    UNIQUE (blizzard_item_id, recipe_id);
```

The `_stub_and_link()` helper function in `item_recipe_link_sync.py` must also
be refactored: it currently writes to `wow_items` and returns `item_db_id`.
After Phase C it writes a minimal Blizzard item payload to `landing.blizzard_items`
and creates the link using `blizzard_item_id` only.

`build_item_recipe_links()` scans `wow_items` for items with names:
```sql
SELECT id, blizzard_item_id, name FROM guild_identity.wow_items
 WHERE name IS NOT NULL AND name != ''
```
Change to scan `enrichment.items`:
```sql
SELECT blizzard_item_id, name FROM enrichment.items
 WHERE name IS NOT NULL AND name != 'Unknown Item'
```

#### `item_service.py` — Stop writing to `wow_items`; write to `landing.*`

`get_or_fetch_item()`:
- Remove the `INSERT/ON CONFLICT DO UPDATE INTO guild_identity.wow_items`
- Return data from the `landing.wowhead_tooltips` payload + `enrichment.items`
- If the item is not yet in `enrichment.items`, return None (it will be there
  after the next "Enrich & Classify" run)

`enrich_unenriched_items()`:
- Remove the `UPDATE guild_identity.wow_items ... SET ...` call
- The `INSERT INTO landing.wowhead_tooltips` write (already dual-writing) becomes
  the only write; the enrichment sproc will pick it up on next rebuild.

`enrich_null_icons()`:
- Writes `icon_url` to `wow_items` — instead, write to `landing.blizzard_item_icons`:
  ```sql
  INSERT INTO landing.blizzard_item_icons (blizzard_item_id, icon_url)
  VALUES ($1, $2)
  ON CONFLICT (blizzard_item_id) DO UPDATE SET icon_url = EXCLUDED.icon_url
  ```

`enrich_blizzard_metadata()`:
- Writes `armor_type`, `slot_type`, `wowhead_tooltip_html` to `wow_items`
- Instead: write payload to `landing.blizzard_items` (already partially done);
  the sproc derives these fields from the payload.

`backfill_armor_type_from_tooltip()`:
- Reads + writes `wow_items.wowhead_tooltip_html` — this logic is now handled
  by `sp_rebuild_items` from the Blizzard payload. Remove this function entirely,
  or keep it targeting `enrichment.items` as a one-time backfill tool.

#### Files changed

- `alembic/versions/0142_retire_wow_items_write_paths.py` (new migration — constraint changes)
- `src/sv_common/guild_sync/equipment_sync.py`
- `src/sv_common/guild_sync/item_source_sync.py`
- `src/sv_common/guild_sync/item_recipe_link_sync.py`
- `src/guild_portal/services/item_service.py`

#### Risk

High — this is the most invasive phase. Key risks:

1. **item_sources unique constraint change** — any in-flight sync that tries to
   INSERT by `item_id` (old path) will fail if `item_id` is NULL and the new
   constraint is on `blizzard_item_id`. The constraint swap must be atomic with
   the code deploy.

2. **item_recipe_links unique constraint change** — same concern. The old
   constraint is `(item_id, recipe_id)`. Rows where `item_id IS NULL` (new path)
   would not be caught by the old constraint. Must swap before Phase C code ships.

3. **enrichment.items may lag** — after Phase C, `wow_items` no longer has stub
   rows, so `enrichment.items` is the only item store. If `sp_rebuild_items` has
   not been run since the last Blizzard API sync, new items will not appear in
   gear plan drawers. The admin runbook must be updated: after "Sync Loot Tables"
   or "Sync Crafted Items", operators must always run "Enrich & Classify".

   Post-Phase C, the `get_or_fetch_item()` cache path in `item_service.py` returns
   data from `enrichment.items` instead of `wow_items`. If an item is in landing
   but not yet enriched, it returns None. This may cause empty icon slots in
   gear plan drawers until the next enrichment run.

---

### Phase D — Migration 0144: Rewrite all reads from `wow_items` to use `enrichment.items` ✓ COMPLETE (dev)

**Goal:** Eliminate all SELECT queries against `guild_identity.wow_items` from
Python code. After this phase, `wow_items` is dead weight in the DB — no code
reads or writes it.

This phase requires a full audit of all Python files for any `wow_items` reference.

#### Known reads to migrate

`item_service.py` — `get_or_fetch_item()`:
The current cache-hit path reads from `wow_items`. Rewrite to read from
`enrichment.items`:
```python
row = await conn.fetchrow(
    """
    SELECT blizzard_item_id, name, icon_url, slot_type, armor_type, weapon_subtype
      FROM enrichment.items
     WHERE blizzard_item_id = $1
    """,
    blizzard_item_id,
)
```

Return shape may need adjustment: callers expecting `id` (the `wow_items` integer
PK) will break. Audit all callers of `get_or_fetch_item()`.

`item_source_sync.py` — `get_item_sources()`:
Currently JOINs `wow_items`:
```sql
SELECT s.id, ..., wi.blizzard_item_id, wi.name AS item_name, wi.slot_type, wi.icon_url
  FROM guild_identity.item_sources s
  JOIN guild_identity.wow_items wi ON wi.id = s.item_id
```
Rewrite to JOIN `enrichment.items` on `blizzard_item_id`:
```sql
SELECT s.id, ..., s.blizzard_item_id, ei.name AS item_name, ei.slot_type, ei.icon_url
  FROM guild_identity.item_sources s
  JOIN enrichment.items ei ON ei.blizzard_item_id = s.blizzard_item_id
```

`gear_plan_auto_setup.py` — `auto_setup_gear_plan()`:
The BIS slots query JOINs `wow_items`:
```sql
LEFT JOIN guild_identity.wow_items wi ON wi.blizzard_item_id = be.blizzard_item_id
```
and uses `wi.id AS item_id` to populate `gear_plan_slots.desired_item_id`.
After Phase E, `desired_item_id` is dropped. For now, set it to NULL in inserts
and rely on `blizzard_item_id` (already populated):
```python
await conn.execute(
    """
    INSERT INTO guild_identity.gear_plan_slots
        (plan_id, slot, blizzard_item_id, item_name, is_locked)
    VALUES ($1, $2, $3, $4, FALSE)
    ON CONFLICT (plan_id, slot) DO NOTHING
    """,
    plan_id, row["slot"], row["blizzard_item_id"], row["item_name"],
)
```

`item_source_sync.py` — `process_tier_tokens()`:
The tier piece backfill step:
```python
tier_piece_rows = await conn.fetch(
    "SELECT id, blizzard_item_id, name, wowhead_tooltip_html FROM guild_identity.wow_items "
    "WHERE slot_type = ANY($1::text[]) AND wowhead_tooltip_html LIKE '%/item-set=%' "
    "AND (armor_type IS NULL OR armor_type = '')",
    list(_TIER_SLOTS),
)
for tp_row in tier_piece_rows:
    at = _armor_type_from_tooltip(tp_row["wowhead_tooltip_html"] or "")
    if at:
        await conn.execute(
            "UPDATE guild_identity.wow_items SET armor_type = $1 WHERE id = $2",
            at, tp_row["id"],
        )
```
Since `enrichment.items.armor_type` is populated by `sp_rebuild_items()` from
the Blizzard API payload directly, this tooltip-based backfill is redundant.
Remove or replace with a noop log message directing operators to run
"Enrich & Classify" instead.

#### Full audit command

```bash
grep -rn "wow_items" src/ --include="*.py"
```

Run this after Phase D code changes and verify zero results.

#### Files changed

- `alembic/versions/0144_audit_reads_prep.py` (minimal migration if needed —
  may just be a code-only phase)
- `src/sv_common/guild_sync/equipment_sync.py`
- `src/sv_common/guild_sync/item_source_sync.py`
- `src/sv_common/guild_sync/item_recipe_link_sync.py`
- `src/sv_common/guild_sync/gear_plan_auto_setup.py`
- `src/guild_portal/services/item_service.py`
- Any other files discovered by the audit

#### Risk

Medium. The `get_or_fetch_item()` refactor may affect callers that expect
the `id` column. Trace all call sites before merging.

---

### Phase E — Migration 0145/0146: Drop integer id FKs; drop `wow_items`

**Goal:** Physically remove the old FK columns, the `WowItem` ORM model, and
finally the `guild_identity.wow_items` table itself.

This phase should only proceed after Phase D has been deployed and verified
on dev for at least one full sync cycle (loot tables + enrichment + BIS sync
+ equipment sync).

#### Step 1 — Migration 0144: Drop FK columns

```sql
-- item_sources: make blizzard_item_id NOT NULL (now the only source of truth)
--   then drop item_id FK column
UPDATE guild_identity.item_sources SET is_suspected_junk = TRUE
 WHERE blizzard_item_id IS NULL;  -- safety: mark any orphaned rows

ALTER TABLE guild_identity.item_sources
    ALTER COLUMN blizzard_item_id SET NOT NULL;

ALTER TABLE guild_identity.item_sources
    DROP COLUMN item_id;

-- character_equipment: drop item_id FK column
ALTER TABLE guild_identity.character_equipment
    DROP COLUMN item_id;

-- gear_plan_slots: drop desired_item_id FK column
ALTER TABLE guild_identity.gear_plan_slots
    DROP COLUMN desired_item_id;

-- tier_token_attrs: change PK from token_item_id (FK to wow_items) to blizzard_item_id
-- This is a PK change — requires recreating the table or dropping/adding constraints.

-- Option: add new PK on blizzard_item_id, drop old PK
ALTER TABLE guild_identity.tier_token_attrs
    DROP CONSTRAINT tier_token_attrs_pkey;

ALTER TABLE guild_identity.tier_token_attrs
    ALTER COLUMN blizzard_item_id SET NOT NULL;

ALTER TABLE guild_identity.tier_token_attrs
    ADD PRIMARY KEY (blizzard_item_id);

ALTER TABLE guild_identity.tier_token_attrs
    DROP COLUMN token_item_id;

-- item_recipe_links: drop item_id column (blizzard_item_id is already the key)
ALTER TABLE guild_identity.item_recipe_links
    DROP COLUMN item_id;
```

#### Step 2 — Migration 0145: Drop `wow_items`

```sql
-- Verify no FKs remain pointing at wow_items before dropping
SELECT conname, conrelid::regclass, confrelid::regclass
  FROM pg_constraint
 WHERE confrelid = 'guild_identity.wow_items'::regclass;
-- Expected: 0 rows

DROP TABLE guild_identity.wow_items;
```

#### ORM changes

Remove from `src/sv_common/db/models.py`:
- `class WowItem` — the entire class
- `ForeignKey("guild_identity.wow_items.id", ...)` references in `ItemSource`,
  `CharacterEquipment`, `GearPlanSlot`, `TierTokenAttrs`
- The `item: Mapped[Optional["WowItem"]]` relationship attributes

Update `ItemSource`, `CharacterEquipment`, `GearPlanSlot`, `TierTokenAttrs`
ORM models:
- Remove `item_id` / `desired_item_id` / `token_item_id` columns
- Ensure `blizzard_item_id` is defined as a plain `Integer` column (no FK)
- For `TierTokenAttrs`: `blizzard_item_id` becomes the `primary_key=True` column

#### Files changed

- `alembic/versions/0145_drop_fk_columns.py`
- `alembic/versions/0146_drop_wow_items.py`
- `src/sv_common/db/models.py` — remove `WowItem` and all FK references

#### Risk

Low by this phase — `wow_items` is no longer read or written by any code.
The DROP is purely cleanup. The PK change on `tier_token_attrs` is the most
complex SQL operation; consider doing it as a separate migration if there is
concern about locking on a large table.

---

## Files Changed Per Phase — Summary

| Phase | Migration | Python files | SQL changes |
|-------|-----------|--------------|-------------|
| A ✓ | 0141 | None | ADD COLUMN blizzard_item_id + backfill (item_sources, tier_token_attrs) |
| B ✓ | 0142 | item_source_sync.py | Backfill landing.wowhead_tooltips; migrate tooltip reads off wow_items |
| C ✓ | 0143 | equipment_sync.py, item_source_sync.py, item_recipe_link_sync.py, item_service.py | Swap unique constraints on item_sources + item_recipe_links |
| D ✓ | 0144 | gear_plan_service.py, gear_plan_auto_setup.py, gear_needs_routes.py, gear_plan_routes.py, item_source_sync.py | Convert excluded_item_ids from wow_items.id to blizzard_item_id |
| E | 0145 + 0146 | models.py | DROP item_id FKs; DROP tier_token_attrs PK; DROP wow_items |

---

## Risk Notes

### What can go wrong

1. **Constraint swap timing (Phase C)** — The unique constraint on `item_sources`
   changes from `(item_id, instance_type, encounter_name)` to
   `(blizzard_item_id, instance_type, encounter_name)`. A sync job running during
   deployment could hit a constraint violation if it's writing the new shape while
   the old constraint is still enforced. Mitigate: apply the migration during a
   low-activity window; the swap takes milliseconds on small tables.

2. **enrichment.items lag (Phase C+)** — After Phase C, new items added by sync
   jobs (equipment sync, item source sync) land in `landing.*` but are not visible
   in gear plan drawers until `sp_rebuild_items` runs. The admin UX for "Enrich &
   Classify" must trigger `sp_rebuild_items` automatically after Wowhead data is
   fetched. Verify that the "Enrich Items" button in the gear plan admin already
   calls the sproc at the end of its pipeline. If not, add the call.

3. **wowhead_tooltip_html completeness (Phase B)** — The backfill migration inserts
   tooltip HTML from `wow_items` into `landing.wowhead_tooltips` for items not
   already present. If `wow_items.wowhead_tooltip_html` has rows that were never
   dual-written to landing (items enriched before migration 0104), those rows must
   be captured in the Phase B migration. After Phase B, run the verification:
   ```sql
   -- Rows in wow_items with HTML that are NOT in landing
   SELECT COUNT(*) FROM guild_identity.wow_items wi
    WHERE wi.wowhead_tooltip_html IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM landing.wowhead_tooltips wt
           WHERE wt.blizzard_item_id = wi.blizzard_item_id
      );
   -- Expected: 0
   ```

4. **tier_token_attrs PK change (Phase E)** — If the table has manual override rows
   (is_manual_override = TRUE), they must be preserved through the PK change.
   Verify data before running 0144:
   ```sql
   SELECT COUNT(*) FROM guild_identity.tier_token_attrs WHERE is_manual_override = TRUE;
   ```
   All such rows should have `blizzard_item_id IS NOT NULL` after Phase A backfill.

5. **`get_or_fetch_item()` callers (Phase D)** — This function is called from
   multiple routes and services. Its return shape currently includes `id` (the
   `wow_items` integer PK). If any caller uses this `id` to construct a further
   DB query, those callers will break after Phase D removes the `id` field.
   Full caller audit required before Phase D.

### What to verify at each phase

| Phase | Verification |
|-------|--------------|
| A | Zero NULLs in new blizzard_item_id columns after backfill |
| B | Landing tooltip count >= wow_items tooltip count; tier tokens still detected correctly |
| C | Full sync cycle: Sync Loot Tables → Enrich & Classify → check gear plan drawers |
| D | `grep -rn "wow_items" src/ --include="*.py"` returns zero results |
| E | `SELECT * FROM guild_identity.wow_items` → "relation does not exist" error |

---

## Open Questions

1. **Does `get_or_fetch_item()` need to survive Phase C?** — After Phase C, item
   data lives only in `enrichment.items` (after rebuild). `get_or_fetch_item()`
   currently provides a live cache-or-fetch path. If a brand-new item appears in
   a player's equipped gear before the next enrichment run, it returns None.
   Decide: is "run Enrich & Classify after each sync" an acceptable operational
   requirement, or does Phase C need an on-demand enrichment fallback?

2. **"Enrich & Classify" calls `sp_rebuild_items()`?** — Confirm that Step 2
   of the admin pipeline ("Enrich Items") calls `CALL enrichment.sp_rebuild_items()`
   at the end. If not, Phase C must add this to prevent gear plan drawers going
   dark after new items are synced.

3. **Migration numbering** — 0140–0145 assumed. Confirm next available number
   before writing Phase A; other feature branches may add migrations in between.

4. **Timeline** — each phase needs at least one full sync cycle on dev before
   proceeding. No urgency assumed; this is a `prod-v0.21.0` candidate.
