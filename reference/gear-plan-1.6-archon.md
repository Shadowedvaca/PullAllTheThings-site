# gear-plan-1.6-archon — Archon.gg BIS Extraction

> **Status:** Implementation plan — build after Icy Veins (gear-plan-1.4) is complete.
> Branch: new `feature/archon-bis-extraction` off `main` after IV branch merges.

---

## What We Confirmed (Pre-Investigation)

**Archon.gg is Next.js SSR. All data is in `__NEXT_DATA__` JSON. No Playwright needed.**

The full page props — item IDs, names, popularity percentages, slot labels — are embedded
in a `<script id="__NEXT_DATA__" type="application/json">` block in the static HTML.
A plain `httpx.get()` returns everything.

Confirmed via view-source on two real pages (Balance Druid, Midnight S1, 2026-04):
- M+ gear URL: `https://www.archon.gg/wow/builds/balance/druid/mythic-plus/gear-and-tier-set/10/all-dungeons/this-week`
- Raid gear URL: `https://www.archon.gg/wow/builds/balance/druid/raid/gear-and-tier-set/.../...`

**Historical note:** All earlier references to "archon" in the codebase were renamed to
"ugg" when u.gg was implemented, to avoid confusion. There are no archon stubs in the
current code — this is a fresh implementation. Use `origin='archon'` throughout.

---

## Data Model (What Archon Provides)

### Methodology

Archon data is **parse-based popularity** — "what % of top players are running this item."
WCL parse aggregation, similar to u.gg. The page shows `totalParses: 71309` for M+ Balance
Druid at time of check.

### Content Types

Archon has **Raid and M+ only — no Overall guide.** Two `bis_list_sources` rows needed.

### URL Structure

```
https://www.archon.gg/wow/builds/{spec_slug}/{class_slug}/{zone_type}/gear-and-tier-set/{difficulty_slug}/{encounter_slug}/this-week
```

- M+:  `zone_type=mythic-plus`, `difficulty_slug=10`, `encounter_slug=all-dungeons`
- Raid: `zone_type=raid`, difficulty/encounter slugs TBD — **verify against a real page before building the URL generator**

One gear URL per spec per content type covers all 14 gear slots. Trinkets are included in
the gear tables — no separate trinket URL needed.

### Change Detection: `page.lastUpdated`

Every page embeds `page.lastUpdated` in the `__NEXT_DATA__` JSON:
```json
"lastUpdated": "2026-04-16T12:00:00Z"
```
Updates appear to be weekly. Use this timestamp to short-circuit scraping: fetch the page,
pull `lastUpdated`, compare to stored `source_updated_at` — only write a new landing row
and re-process enrichment if the timestamp has changed.

### `__NEXT_DATA__` Structure (gear page)

```
props.pageProps.page
  .lastUpdated        TIMESTAMPTZ string — change detection key
  .totalParses        INT         — total parse count for this spec × content type
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
        .props.gear[]             — compact BIS summary (redundant with tables; skip)
        .props.trinkets[]
        .props.weapons[]
    (other sections: crafted gear, embellishments, tier set — out of scope for v1)
```

### Row Extraction (Python enrichment layer does this, not a stored proc)

**Item ID:** regex `id=\{(\d+)\}` on the `item` JSX string  
**Popularity %:** regex `([\d.]+)%` on the `popularity` JSX string  
**BIS determination:** row index 0 in each table = highest popularity = BIS (priority=1)

### Paired Slot Handling

Archon presents trinkets as a single "Trinket" table and rings as a single "Rings" table.
Expand both to both paired slots during enrichment:
- Trinket → `trinket_1` + `trinket_2`
- Rings → `ring_1` + `ring_2`

Same item, same priority, same popularity data written for each.

---

## Design Principles

**Landing = raw. Enrichment = parsed.**

- `landing.bis_scrape_raw` stores `json.dumps(page)` — the extracted `page` object from
  `__NEXT_DATA__` (not full HTML). This is the smallest self-contained unit of source data.
- Slot label → slot key mapping lives in `config.slot_labels` (origin='archon') — seeded
  in migration, not hardcoded in Python.
- Enrichment rebuild is Python (`rebuild_bis_from_landing()` in `bis_sync.py`), same as
  all other sources. There is no stored proc that processes BIS entries.
- Popularity data goes to `enrichment.item_popularity` (existing, migration 0148), not to
  a new column on `enrichment.bis_entries`.

---

## Schema Changes

### `ref.bis_list_sources` — 2 new rows (seeded in migration)

| name | short_label | origin | content_type | is_default | is_active |
|---|---|---|---|---|---|
| Archon M+ | Archon M+ | archon | dungeon | false | true |
| Archon Raid | Archon Raid | archon | raid | false | true |

### `landing.bis_scrape_raw` — add `source_updated_at`

```sql
ALTER TABLE landing.bis_scrape_raw
    ADD COLUMN source_updated_at TIMESTAMPTZ;
```

Stores the source's own `lastUpdated` timestamp. Belongs in landing — it is source metadata,
not derived data.

- **Archon rows:** `source_updated_at = page.lastUpdated` (parsed from `__NEXT_DATA__`)
- **All other sources:** NULL (they do not expose an update timestamp)

The scraper checks `MAX(source_updated_at)` for the target URL before inserting a new row.
If unchanged, skip — no new landing row, no enrichment rebuild triggered.

### `config.slot_labels` — archon seed rows

Seeded in the same migration (Phase A). Depends on IV Z.0 having already created
`config.slot_labels`. Run after IV branch merges.

| page_label | slot_key | Notes |
|---|---|---|
| Head | head | |
| Neck | neck | |
| Shoulders | shoulder | |
| Back | back | |
| Chest | chest | |
| Wrist | wrist | |
| Gloves | hands | |
| Belt | waist | |
| Legs | legs | |
| Feet | feet | |
| Trinket | NULL | Expand to trinket_1 + trinket_2 in code |
| Rings | NULL | Expand to ring_1 + ring_2 in code |
| Main-Hand | main_hand | |
| Off-Hand | off_hand | |

`NULL` slot_key signals "expand to both paired slots" in `_parse_archon_page()`.

### No change to `enrichment.bis_entries`

Do not add `popularity_pct` to `enrichment.bis_entries`. That table tracks ranking order
(priority). Popularity statistics belong in `enrichment.item_popularity`.

### Popularity → `enrichment.item_popularity` (existing, migration 0148)

Schema: `source_id, spec_id, slot, blizzard_item_id, count INTEGER, total INTEGER`

For each archon item row:
```
count = round(popularity_pct / 100 * totalParses)
total = totalParses
```

Using the actual `totalParses` from the page gives real absolute counts, not synthetic
fractions. The `viz.item_popularity` view aggregates all sources via `SUM(count)/SUM(total)`,
so archon and u.gg combine naturally into the Overall popularity % shown in the gear plan UI.
No weighting — both sources contribute their raw parse counts. Since Archon and u.gg draw
from overlapping but not identical player populations, combining them gives a more complete
picture than either alone.

---

## Extraction Pipeline

### `_extract_archon(url, spec_id, source_id, pool)` in `bis_sync.py`

Two-phase (no DB held during HTTP):

1. Fetch page HTML with `httpx`
2. Extract `__NEXT_DATA__` JSON:
   ```python
   re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
   ```
3. Parse JSON → `page = data['props']['pageProps']['page']`
4. Extract `lastUpdated` string
5. Compare to `MAX(source_updated_at)` in `landing.bis_scrape_raw`
   WHERE `source='archon' AND target_id=target_id`
6. If unchanged → return early (log "skipped — unchanged since {lastUpdated}")
7. If changed → insert one row into `landing.bis_scrape_raw`:
   - `source = 'archon'`
   - `url = page_url`
   - `content = json.dumps(page)` (page object only, not full HTML)
   - `target_id = target_id`
   - `source_updated_at = datetime.fromisoformat(page['lastUpdated'].replace('Z', '+00:00'))`

### `_archon_gear_url(spec_slug, class_slug, content_type)` in `bis_sync.py`

Called by `discover_targets()` for `origin='archon'`. Archon targets are one per spec per
content type, `hero_talent_id = NULL` (one page covers all builds).

**Verify the raid URL pattern against a real page before writing this function.**
M+ pattern is confirmed: `mythic-plus/gear-and-tier-set/10/all-dungeons/this-week`

### `_parse_archon_page(page, slot_map, total_parses)` — pure function

No DB or network. Called from `rebuild_bis_from_landing()`.

```python
def _parse_archon_page(
    page: dict,
    slot_map: dict[str, str | None],
    total_parses: int,
) -> tuple[list[SimcSlot], list[ArchonPopularityItem]]:
    """Parse archon page object → BIS slots + popularity rows.
    
    Finds BuildsGearTablesSection in page['sections'].
    For each table in section['props']['tables']:
        raw_label = strip JSX tags from columns['item']['header']
        slot_key = slot_map.get(raw_label.lower())
        
        If slot_key is None (Trinket / Rings): expand to both paired slots.
        
        For each row in table['data']:
            item_id = int(re.search(r'id=\\{(\\d+)\\}', row['item']).group(1))
            pct = float(re.search(r'([\\d.]+)%', row['popularity']).group(1))
            priority = row_index + 1  (1-based)
            count = round(pct / 100 * total_parses)
            
            Append SimcSlot(slot_key, item_id, ..., priority=priority)
            Append ArchonPopularityItem(slot_key, item_id, count, total_parses)
    
    Returns (slots, popularity_items)
    """
```

### `rebuild_bis_from_landing()` — add archon branch

```python
elif source == 'archon':
    page = json.loads(content)
    total_parses = page.get('totalParses', 0)
    slot_map = await _load_slot_labels(conn, 'archon')
    slots, popularity_items = _parse_archon_page(page, slot_map, total_parses)
    # slots → enrichment.bis_entries (same upsert path as all other sources)
    # popularity_items → enrichment.item_popularity (same upsert path as u.gg)
```

### `_load_slot_labels(conn, origin)` — already exists (IV Z.0)

No change needed. Pass `origin='archon'` to load archon seed rows from `config.slot_labels`.

---

## `discover_targets()` — archon branch

```python
elif origin == 'archon':
    for spec in specs:
        for content_type in ('raid', 'dungeon'):
            url = _archon_gear_url(spec.spec_slug, spec.class_slug, content_type)
            if url is None:
                continue
            technique = 'json_embed_archon'  # or reuse 'json_embed' with source dispatch
            hero_talent_id = None  # one page covers all builds
            INSERT config.bis_scrape_targets ... ON CONFLICT DO NOTHING
```

---

## Scheduler

Add a weekly `run_archon_sync()` job in `scheduler.py`, distinct from the daily
`run_bis_sync()`. Archon updates weekly; daily polling would waste fetches.

- Frequency: weekly (e.g., Sunday midnight UTC)
- Scope: all `config.bis_scrape_targets` WHERE source_id IN archon source IDs
- Change detection inside `_extract_archon()` short-circuits most runs

After scraping, trigger `rebuild_bis_from_landing()` and `rebuild_item_popularity_from_landing()`
for archon sources (same functions, archon branch now active).

---

## Build Phases

| Phase | Scope | Migration | Prerequisite |
|---|---|---|---|
| A | Migration: `source_updated_at` on `landing.bis_scrape_raw`; `config.slot_labels` archon rows; `ref.bis_list_sources` archon rows; `config.bis_scrape_targets` archon targets | Yes | IV Z.0 merged (slot_labels table exists) |
| B | `_extract_archon()` + `_archon_gear_url()` in `bis_sync.py`; archon branch in `discover_targets()` | No | |
| C | `_parse_archon_page()` pure function; archon branch in `rebuild_bis_from_landing()` + `rebuild_item_popularity_from_landing()` | No | |
| D | Weekly scheduler job + change detection wired end-to-end | No | |
| E | Admin UI: archon columns in BIS matrix; popularity % visible in gear plan | No | |

---

## Code Locations

| File | Current state | Change |
|---|---|---|
| `src/sv_common/guild_sync/bis_sync.py` | No archon code (all renamed to ugg) | Add `_extract_archon()`, `_archon_gear_url()`, `_parse_archon_page()`; archon branches in `rebuild_*_from_landing()` and `discover_targets()` |
| `src/sv_common/guild_sync/scheduler.py` | `run_bis_sync()` daily job | Add `run_archon_sync()` weekly job |
| `alembic/versions/` | Check latest before writing | Phase A migration |
| `src/guild_portal/static/js/gear_plan_admin.js` | No archon columns | Phase E: archon columns in BIS matrix |

---

## Open Questions for Build Session

1. **Raid URL pattern** — M+ is confirmed: `mythic-plus/gear-and-tier-set/10/all-dungeons/this-week`.
   Fetch one Archon raid page and confirm the difficulty/encounter slug segments before writing
   `_archon_gear_url()`.

2. **Spec/class slug format** — confirm slugs for all 40 specs. Some class names use hyphens
   (`death-knight`, `demon-hunter`). Build a mapping in `discover_targets()` analogous to how
   `_iv_bis_role()` handles IV's role-based URL variation.

3. **Rows per slot table** — the Balance Druid sample had 12 rows per table. Confirm whether
   12 is a hard Archon cap or varies. All rows are stored in `enrichment.item_popularity`;
   only row 0 (priority=1) feeds `enrichment.bis_entries` as BIS.

4. **Embellishments and crafted gear sections** — `BuildsCraftedGearSection` and
   `BuildsEmbellishmentsSection` are present on the gear page. Out of scope for v1.
