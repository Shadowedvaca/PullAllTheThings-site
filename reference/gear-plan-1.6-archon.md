# gear-plan-3-archon — Archon.gg BIS Extraction

> **Status:** Implementation plan — ready to build in a fresh conversation.
> Branch: new `feature/archon-bis-extraction` off `main` after prior branches merge.

---

## What We Confirmed (Pre-Investigation)

**Archon.gg is Next.js SSR. All data is in `__NEXT_DATA__` JSON. No Playwright needed.**

The full page props — item IDs, names, popularity percentages, slot labels — are embedded
in a `<script id="__NEXT_DATA__" type="application/json">` block in the static HTML.
A plain `httpx.get()` returns everything. The "likely fully JS-rendered" assumption in
the original PHASE_Z doc was wrong, same as Icy Veins.

Confirmed via view-source on two real pages (Balance Druid, Midnight S1, 2026-04):
- M+ gear URL: `https://www.archon.gg/wow/builds/balance/druid/mythic-plus/gear-and-tier-set/10/all-dungeons/this-week`
- Raid gear URL: `https://www.archon.gg/wow/builds/balance/druid/raid/gear-and-tier-set/.../...`

---

## Data Model (What Archon Provides)

### Methodology

Archon data is **parse-based popularity** — "what % of top players are running this item."
NOT simulation-based (the PHASE_Z doc guessed sim; it's actually WCL parse aggregation,
similar to u.gg). The page shows `totalParses: 71309` for M+ Balance Druid at time of check.

### Content Types

Archon has **Raid and M+ only — no Overall guide.** Two `bis_list_sources` rows needed.

### URL Structure

```
https://www.archon.gg/wow/builds/{spec_slug}/{class_slug}/{zone_type}/gear-and-tier-set/{difficulty_slug}/{encounter_slug}/this-week
```

- M+:  `zone_type=mythic-plus`, `difficulty_slug=10`, `encounter_slug=all-dungeons`
- Raid: `zone_type=raid`, difficulty/encounter slugs TBD (check during build)

One gear URL per spec per content type covers all 14 gear slots. Trinkets are also
included in the gear tables (no need for a separate trinket URL).

### Change Detection: `page.lastUpdated`

Every page embeds `page.lastUpdated` in the `__NEXT_DATA__` JSON:
```json
"lastUpdated": "2026-04-16T12:00:00Z"
```
Mike confirmed updates appear to be weekly. Use this timestamp to short-circuit scraping:
fetch the page, pull `lastUpdated`, compare to stored value — only re-process if changed.

### `__NEXT_DATA__` Structure (gear page)

```
props.pageProps.page
  .lastUpdated        TIMESTAMPTZ string — change detection key
  .totalParses        INT         — parse count for context
  .sections[]
    [0] BuildsGearTablesSection   (navigationId: "gear-tables")
        .props.tables[]           — 14 tables, one per slot
          .columns.item.header    — slot name e.g. "Head", "Trinket", "Main-Hand"
          .data[]                 — rows sorted by popularity DESC
            .item                 — JSX string: <ItemIcon id={XXXXX} ...>Name</ItemIcon>
            .popularity           — JSX string: <Styled type='legendary'>59.6%</Styled>
            .maxKey               — JSX string: highest key level (M+ only)
            .dps                  — JSX string: DPS value
    [5] BuildsBestInSlotGearSection (navigationId: "gear-overview")
        .props.gear[]             — compact BIS summary (redundant with tables)
        .props.trinkets[]
        .props.weapons[]
    (other sections: crafted gear, embellishments, tier set — lower priority)
```

### Row Extraction (enrichment layer does this)

**Item ID:** regex `id=\{(\d+)\}` on the `item` JSX string
**Item name:** regex on text node in `<ItemIcon>` or `<GearIcon>`
**Popularity %:** regex `([\d.]+)%` on the `popularity` JSX string
**BIS determination:** row index 0 in each table = highest popularity = BIS (priority=1)

### Trinket Slot Handling

Archon presents trinkets as a single "Trinket" table — not split into trinket_1 / trinket_2.
Rankings are by individual trinket popularity (% of parses running this item in either slot).
Map to both `trinket_1` and `trinket_2` in `enrichment.bis_entries` with same priority/pct.
(This matches how u.gg handles paired slots.)

---

## Schema Changes

### New `bis_list_sources` rows (seeded in migration)

| name | short_label | origin | content_type | is_default | is_active |
|---|---|---|---|---|---|
| Archon.gg M+ | Archon M+ | archon_gg | dungeon | false | true |
| Archon.gg Raid | Archon Raid | archon_gg | raid | false | true |

Note: `origin='archon_gg'` — distinct from `origin='archon'` which is the u.gg extractor's
legacy code identifier (renamed in code but kept in DB for backward compat).

### `landing.bis_scrape_raw` — add `source_updated_at`

```sql
ALTER TABLE landing.bis_scrape_raw
    ADD COLUMN source_updated_at TIMESTAMPTZ;
```

This stores the source's own `lastUpdated` timestamp alongside the scraped content.
It's source metadata (not derived), so it belongs in landing.
Used by the scraper to short-circuit re-processing when content hasn't changed.

**For archon rows:** `source_updated_at = page.lastUpdated` from `__NEXT_DATA__`.
**For other sources:** NULL (they don't expose an update timestamp).

**Existing landing.bis_scrape_raw schema (no change to core columns):**
```
id, fetched_at, source VARCHAR(50), url TEXT, content TEXT, source_updated_at TIMESTAMPTZ
```

The `content` column stores the extracted `__NEXT_DATA__` JSON string for archon
(not the full HTML page — just the data container). This is the right granularity.

### `enrichment.bis_entries` — add `popularity_pct`

```sql
ALTER TABLE enrichment.bis_entries
    ADD COLUMN popularity_pct NUMERIC(5,2);
```

Nullable. Populated by the enrichment SP for archon sources; NULL for all other sources
(u.gg, Wowhead, IV — they don't expose per-item popularity percentages).

**Full enrichment.bis_entries schema after change:**
```
id, source_id, spec_id, hero_talent_id, slot, blizzard_item_id, priority, popularity_pct
UNIQUE (source_id, spec_id, hero_talent_id, slot, blizzard_item_id)
```

---

## Extraction Pipeline

### Phase 3.1 — Scraper (`bis_sync.py`)

**New function: `_extract_archon(url, spec_id, source_id, pool)`**

Two-phase (no DB held during HTTP — learned from PHASE_Z):
1. Fetch page HTML with `httpx`
2. Extract `__NEXT_DATA__` JSON: `re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)`
3. Parse JSON → `page = data['props']['pageProps']['page']`
4. Extract `lastUpdated` string
5. Compare to `MAX(source_updated_at)` in `landing.bis_scrape_raw` WHERE `source='archon_gg' AND url=url`
6. If unchanged → return early (no-op, log "skipped — unchanged since {lastUpdated}")
7. If changed → extract all 14 slot tables from `BuildsGearTablesSection`
8. Insert one row into `landing.bis_scrape_raw`:
   - `source = 'archon_gg'`
   - `url = page_url`
   - `content = json.dumps(page)` (the page object, not full HTML)
   - `source_updated_at = datetime.fromisoformat(page['lastUpdated'].replace('Z', '+00:00'))`

**URL builder: `_archon_gear_url(spec_slug, class_slug, content_type)`**

Existing `bis_sync.py` has a `discover_targets()` function that builds scrape target URLs.
Add archon.gg URL building alongside the existing u.gg and IV builders.

### Phase 3.2 — Enrichment SP: `sp_rebuild_bis_entries()`

Extend the existing stored procedure to handle `origin='archon_gg'` sources.

For each archon row in `landing.bis_scrape_raw` (latest per url):
1. Parse `content` JSON → `page` object
2. Find `BuildsGearTablesSection` in `page.sections`
3. For each of the 14 tables:
   a. Extract slot name from `columns.item.header` — strip `<ImageIcon ...>` tags
   b. Map archon slot name → our `slot` enum (see mapping table below)
   c. For each row in `data`:
      - Extract `blizzard_item_id` via `re.search(r'id=\{(\d+)\}', row['item'])`
      - Extract `popularity_pct` via `re.search(r'([\d.]+)%', row['popularity'])`
      - `priority` = row index + 1 (1-based, row 0 = BIS)
      - Upsert into `enrichment.bis_entries`

For trinket slot: insert same row for both `trinket_1` and `trinket_2`.

**Archon → our slot name mapping:**

| Archon label | Our slot |
|---|---|
| Head | head |
| Neck | neck |
| Shoulders | shoulder |
| Back | back |
| Chest | chest |
| Wrist | wrist |
| Gloves | hands |
| Belt | waist |
| Legs | legs |
| Feet | feet |
| Trinket | trinket_1 + trinket_2 |
| Rings | finger_1 + finger_2 |
| Main-Hand | main_hand |
| Off-Hand | off_hand |

Note: Rings table is also combined (like trinkets). Same pattern: insert for both
`finger_1` and `finger_2`.

### Phase 3.3 — Weekly Scheduler

Add a separate `archon_sync` scheduler job distinct from the daily BIS sync.
- Frequency: weekly (e.g., Sunday midnight UTC)
- Scope: all active `bis_scrape_targets` WHERE `source_id` IN archon_gg source IDs
- Change detection short-circuits most runs — only 40 specs × 2 content types = 80 fetches
  but most will be no-ops if content hasn't changed since last week

The existing `run_bis_sync()` scheduler entry runs daily for u.gg/Wowhead/IV.
Archon should run separately to keep schedules decoupled.

---

## Code Locations

| File | Current state | Change |
|---|---|---|
| `src/sv_common/guild_sync/bis_sync.py` | archon stubs (original u.gg code, renamed) | Add `_extract_archon()`, `_archon_gear_url()` |
| `src/sv_common/guild_sync/bis_sync.py` | `discover_targets()` | Add archon URL generation |
| `alembic/versions/` | Through 0109 | New migration: `landing.bis_scrape_raw` +col, `enrichment.bis_entries` +col, `bis_list_sources` 2 new rows |
| `src/sv_common/guild_sync/scheduler.py` | `run_bis_sync()` daily job | Add `run_archon_sync()` weekly job |
| `src/guild_portal/static/js/gear_plan_admin.js` | Matrix shows archon sources as stubbed | Update once enrichment pipeline is live |

---

## Build Phases

| Phase | Scope | Migration |
|---|---|---|
| 3.1 | Migration: schema additions + seed rows | Yes |
| 3.2 | `_extract_archon()` + `_archon_gear_url()` in `bis_sync.py` | No |
| 3.3 | Extend `sp_rebuild_bis_entries()` for archon_gg origin | No |
| 3.4 | Weekly scheduler job + change detection logic | No |
| 3.5 | Admin UI: archon columns in BIS matrix, show popularity_pct | No |

---

## Open Questions for Build Session

1. **Raid URL difficulty/encounter slugs** — the M+ URL uses `10/all-dungeons`. What are the
   equivalent segments for Raid? Check one Archon raid page to confirm URL pattern before
   building the URL generator.

2. **Spec/class slug format** — confirm the URL slugs Archon uses for all 40 specs. Some may
   use hyphenated names (`death-knight`, `beast-mastery`) or different casing. Build a mapping
   table analogous to `_iv_bis_role()`.

3. **How many rows per slot table?** — in the Balance Druid sample, each table had 12 rows.
   Is 12 a hard cap Archon imposes, or does it vary by slot/spec? If capped, we get the top 12
   most popular items, which is more than enough for BIS purposes.

4. **`bis_list_entries` (guild_identity schema) vs `enrichment.bis_entries`** — the viz layer
   (`viz.bis_recommendations`) reads from enrichment. Confirm `popularity_pct` surfaces
   correctly through the viz view to the gear plan UI, or update the viz view if needed.

5. **Embellishments and crafted gear sections** — `BuildsCraftedGearSection` and
   `BuildsEmbellishmentsSection` are present on the gear page. Leave them out of scope for v1
   but note they're available for future enrichment (e.g., "most popular embellishment" data).
