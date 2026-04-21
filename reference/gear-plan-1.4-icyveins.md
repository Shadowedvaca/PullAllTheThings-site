# gear-plan-1.4-icyveins — Icy Veins BIS Extraction

> **Status:** Plan updated 2026-04-20 — Z.0 through Z.3 complete; Z.4 next.
> Branch: `feature/iv-bis-extraction` off `main`.
> Check `alembic/versions/` for the latest migration number before writing new ones.
> Last migration on dev: **0162**. Next migration number: **0163**.

---

## What We Know (Confirmed)

**IV pages are partially SSR — item data IS in static HTML.**

Original assumption: "fully JS-rendered, httpx returns no item data."  
That was wrong. Confirmed via view-source:

- `<h3>` section headers ARE in static HTML
- `<details class="trinket-dropdown" open>` IS in static HTML
- Item IDs are in `data-wowhead="item=XXXXX"` attributes on every item span
- All section tabs (Raid / M+ / Overall) are present in the DOM simultaneously — CSS-hidden, not JS-loaded
- **No Playwright needed. Plain `httpx.get()` + BeautifulSoup is sufficient.**

---

## Page Structure

### Section Structure (confirmed after Z.2b investigation)

All IV pages use an **image_block tab structure** as the outer wrapper:

```html
<div class="image_block">
  <div class="image_block_header">
    <div class="image_block_header_buttons">
      <span id="area_1_button">Overall BiS List</span>
      <span id="area_2_button">Raid Gear BiS List</span>
      <span id="area_3_button">Mythic+ Gear BiS List</span>
    </div>
  </div>
  <div class="image_block_content" id="area_1">
    <div class="heading_container heading_number_3">
      <h3 id="overall-bis-list-for-specspec-specclass">Overall BiS List for Balance Druid</h3>
    </div>
    <table>...</table>
  </div>
  <div class="image_block_content" id="area_2">...</div>
  <div class="image_block_content" id="area_3">...</div>
</div>
```

**The h3 inside each area_N pane is NOT always present** (Blood DK, Vengeance DH, Marksmanship, Protection Paladin, Survival Hunter have no h3). The primary classification source is the **button label** on `area_N_button`.

### Button Label → Content Type Mapping

`_iv_classify_tab_label(label)` — keyword matching on button text:

| Keyword in label | content_type |
|---|---|
| `mythic` | `mythic_plus` |
| `raid` | `raid` |
| `overall`, `bis`, `best` | `overall` |
| none of the above | `NULL` — tab skipped |

### Section Key

The `section_key` (stored in `landing.bis_page_sections`) is:
- The h3's `id` attribute when present — backward-compatible with existing DB rows
- The `area_N` id when no h3 exists (Blood DK etc.)

### Blood DK — 4 Tabs

Blood DK has an unusual 4-tab layout:

| Tab | Label | content_type |
|---|---|---|
| area_1 | "BiS Raid (San'layn)" | `raid` |
| area_2 | "BiS Raid (Deathbringer)" | `raid` |
| area_3 | "Dreamrift, Voidspire, and March on Quel'Danas Gear" | `NULL` — skipped (no keyword) |
| area_4 | "Mythic+" | `mythic_plus` |

This produces two `raid` sections (area_1 and area_2) and no `overall`. Blood DK is the primary driver for needing **section overrides** in Z.2.5 — an admin will manually assign one of the raid tabs as the canonical `overall` or `raid` source.

### Regular BIS Table Row

```html
<tr>
  <td>Helm</td>
  <td>
    <span class="spell_icon_span">
      <img ... alt="Item Name Icon" />
      <span data-wowhead="item=250024" class="q4">Branches of the Luminous Bloom</span>
    </span>
  </td>
  <td>
    <a href="//www.icy-veins.com/wow/lightblinded-vanguard-raid-guide">Lightblinded Vanguard</a>
    in
    <a href="//www.icy-veins.com/wow/midnight-season-1-raid-guide">The Voidspire</a>
  </td>
</tr>
```

Extraction per row:
- **slot**: `td[0].get_text(strip=True)` → lowercased → looked up in `config.slot_labels` (origin='icy_veins')
- **item_id**: `int(span['data-wowhead'].split('=')[1])` from `td[1] span[data-wowhead]`
- **item_name**: `span.get_text(strip=True)` from same span

### Trinket Dropdown Table Row

```html
<details class="trinket-dropdown" open>
  ...
  <tr>
    <td><span style="color:#e6cc80"><strong>S Tier</strong></span></td>
    <td>
      <ul>
        <li><span class="spell_icon_span">
          <img ... />
          <span data-wowhead="item=249346" class="q4">Vaelgor's Final Stare</span>
        </span></li>
        <li>...</li>
      </ul>
    </td>
  </tr>
```

Extraction per row:
- **tier**: `td[0].get_text(strip=True)` → strip " Tier" suffix → `S`, `A`, `B`, `C`, `D`
- **items**: all `span[data-wowhead]` in `td[1]` — multiple items per tier row

---

## Design Principles

**Landing = raw. Enrichment = parsed.**

- `landing.bis_scrape_raw` — raw page HTML (same as every other source)
- `landing.bis_page_sections` — unified section metadata for **all** sources (Method + IV + any future source). Section key, title, classification, outlier flag, row count. **No items JSONB.** Items are re-parsed from raw HTML during `rebuild_bis_from_landing()`. Replaces `landing.method_page_sections` and `landing.iv_page_sections` (Z.2.5).
- `config.bis_section_overrides` — manual content_type → section_key mappings for any source. Replaces `config.method_section_overrides` (Z.2.5).
- `enrichment.bis_entries` — final parsed slot → item assignments
- `enrichment.trinket_ratings` — trinket tier ratings, parsed from raw HTML during `rebuild_trinket_ratings_from_landing()`

**No translation tables in code.**

All slot label → slot key mappings live in `config.slot_labels` (see Phase Z.0).

---

## Phases

| Phase | Scope | Migrations |
|---|---|---|
| Z.0 | Unified slot label tables; shared `_resolve_text_slot` helper; remove hardcoded dicts | **0159, 0160 — COMPLETE** |
| Z.1 | `landing.iv_page_sections` metadata table | **0161 — COMPLETE** |
| Z.2 | `_extract_icy_veins()` rewrite + dead code removal | **No — COMPLETE** |
| Z.2b | image_block tab parsing; `_iv_classify_tab_label` + `_iv_parse_from_image_blocks` | **No — COMPLETE** |
| Z.2.5 | Unified `landing.bis_page_sections` + `config.bis_section_overrides`; override support for IV | **0162 — COMPLETE** |
| Z.3 | Admin section inventory UI + API endpoint (reads `bis_page_sections`); removed IV Coming Soon placeholders from matrix/targets/xref | **No — COMPLETE** |
| Z.4 | `rebuild_bis_from_landing()` + `rebuild_trinket_ratings_from_landing()` IV branches | **No — COMPLETE** |

---

## Phase Z.0 — COMPLETE (migrations 0159–0160)

### What shipped

**Two tables replace all hardcoded slot-label dicts:**

`config.slot_labels(page_label PK, slot_key)` — 43 rows, universal text labels shared by Method, u.gg, and Icy Veins. No origin column — labels like "back", "cloak", "belt" mean the same thing across all text-based guides.

`config.wowhead_invtypes(invtype_id PK, slot_key)` — 20 rows, Blizzard inventory_type integer codes used only by Wowhead's WH.Gatherer metadata. Kept separate because integers cannot conflict with text labels.

**Migration history:**
- 0153–0158: `config.method_slot_labels` (Method-only, now retired)
- 0159: Created `config.slot_labels` with origin column (first attempt)
- 0160: Dropped origin column; split Wowhead codes into `config.wowhead_invtypes`; final design

**Code changes in `bis_sync.py`:**
- Removed `_UGG_SLOT_MAP` and `_WOWHEAD_SLOT_MAP` hardcoded dicts
- `_load_slot_labels(conn)` — loads all text labels, no origin param
- `_load_wowhead_invtypes(conn)` — loads integer invtype map
- All text-label parsers (UGG, Method) call `_load_slot_labels(conn)`; Wowhead calls `_load_wowhead_invtypes(conn)`
- Slot maps threaded as parameters through all pure parse functions

### `_resolve_text_slot` — shared helper

```python
def _resolve_text_slot(
    raw_label: str,
    slot_map: dict[str, str | None],
    ring_count: int = 0,
    trinket_count: int = 0,
) -> tuple[str | None, int, int]:
```

Handles NULL map entries (ring, trinket) by positional assignment. Call at the **per-item** level (not per-row) so pool rows with multiple links each get their own ring_1/ring_2 assignment.

Resolution rules (in order):
1. Label in map with non-NULL value → return directly
2. Label in map with NULL → positional ring_1/ring_2 or trinket_1/trinket_2
3. Label absent but contains "ring" or "trinket" → positional (handles novel variants)
4. Label absent and unrelated → None (caller should skip and log)

**Used by:** `_parse_method_table`. **Must be used by:** `_extract_icy_veins` (Z.2) — do not re-implement ring/trinket resolution inline.

**Row-level positional check (Method-specific HTML logic):**
```python
direct_key = slot_map.get(raw_slot)
known = raw_slot in slot_map
is_positional = direct_key is None and (known or "ring" in raw_slot or "trinket" in raw_slot)
```
This peek does NOT consume ring/trinket counts. The actual count increment happens inside the per-link loop via `_resolve_text_slot`.

### Slot label NULL semantics

`slot_key = NULL` in `config.slot_labels` means **"I know this label exists; resolve by occurrence order."**  This is different from a label being absent from the table (which means "unrecognised"). The distinction matters for logging — absent labels get a debug warning; NULL labels are silently resolved positionally.

### IV slot lookup (Z.2)

IV parser calls `_load_slot_labels(conn)` — same as Method and u.gg. No origin argument needed. IV uses bare "Ring" and "Trinket" labels (title-cased → lowercased before lookup), which hit the NULL rows and are resolved positionally via `_resolve_text_slot`.

### Reference files
- `reference/slot_labels_0160.csv` — 43 universal text label rows
- `reference/wowhead_invtypes_0160.csv` — 20 Wowhead invtype rows

---

## Phase Z.1 — `landing.iv_page_sections`

### Migration (next after Z.0)

```sql
CREATE TABLE landing.iv_page_sections (
    id                 BIGSERIAL PRIMARY KEY,
    spec_id            INTEGER NOT NULL REFERENCES ref.specializations(id),
    source_id          INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
    page_url           TEXT NOT NULL,
    section_h3_id      TEXT NOT NULL,
    section_title      TEXT NOT NULL,
    content_type       VARCHAR(20),           -- 'raid'/'dungeon'/'overall'/NULL if unknown
    is_trinket_section BOOLEAN NOT NULL DEFAULT FALSE,
    row_count          INTEGER NOT NULL DEFAULT 0,
    is_outlier         BOOLEAN NOT NULL DEFAULT FALSE,
    outlier_reason     TEXT,
    scraped_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (spec_id, source_id, section_h3_id)
);
```

**No `items` column.** Raw HTML lives in `landing.bis_scrape_raw` (same as Method/Wowhead/u.gg). Items are re-parsed from there during enrichment rebuild.

---

## Phase Z.2 — `_extract_icy_veins()` Rewrite

### Dead Code to Remove

All of these are dead before any new code is written:

| Symbol | Location | Why dead |
|---|---|---|
| `_IV_AREA_LINK_RE` | ~line 322 | Old regex, never matches IV HTML |
| `_IV_ITEM_ID_RE` | ~line 2226 | Never matches IV static HTML (see comment in code) |
| `_IV_ITEM_LINK_RE` | ~line 2227 | Never matches IV static HTML |
| `discover_iv_areas()` | ~line 307 | No-op stub, logs and returns |
| `_fetch_iv_areas()` | ~line 329 | Orphaned helper for discover_iv_areas |
| `_categorize_iv_area()` | ~line 378 | Old hero-talent label-matching approach; replaced by h3 id prefix |

**Keep unchanged:**
- `_iv_base_url()` — correct URL builder
- `_iv_bis_role()` — correct role mapper

### New Implementation

```python
async def _extract_icy_veins(
    url: str,
    spec_id: int,
    source_id: int,
    pool: asyncpg.Pool,
) -> tuple[list[SimcSlot], str | None]:
    """Fetch one IV BIS page, extract all sections, upsert metadata to
    landing.iv_page_sections, store raw HTML in landing.bis_scrape_raw.

    Returns (slots_for_this_content_type, raw_html).
    """
```

Internal helpers (pure functions, no DB/network):

- `_iv_parse_sections(html: str) -> list[IVSection]` — BeautifulSoup parse; finds all h3 + following tables + trinket dropdowns
- `_iv_classify_section(h3_id: str) -> tuple[str | None, bool]` — returns `(content_type, is_trinket_section)` from h3 id prefix
- `_iv_is_outlier(section: IVSection) -> tuple[bool, str | None]` — returns `(flag, reason)` 
- `_iv_extract_regular_rows(table_el, slot_map) -> list[SimcSlot]` — parses 3-col BIS table using slot_labels
- `_iv_extract_trinket_rows(details_el) -> list[dict]` — parses tier-grouped trinket dropdown; returns `[{tier, item_id, sort_order}]`

### Outlier Detection Rules

A section is flagged as outlier if ANY of:
- `content_type is None` — h3 id prefix not in known mapping
- `row_count < 5` — suspiciously short list
- `row_count == 0` — extraction produced nothing
- Trinket section has no tier labels found

### Two-Phase HTTP Fetch

Avoid holding DB connection during HTTP:
1. Fetch all pages with `httpx` (no DB) → collect `(url, html)` pairs
2. Open DB pool → parse and upsert all results

### Slot Resolution (IV-specific)

IV shows "Ring" once for each ring slot (two rows with identical label). When `slot_map.get("ring") is None`, use occurrence order to assign `ring_1` / `ring_2`. Same logic for "Trinket".

`_iv_extract_regular_rows` **must call `_resolve_text_slot`** for every row — do not re-implement ring/trinket positional logic inline. The helper is already shared with Method and handles all four resolution rules including novel label variants.

### Update `_TECHNIQUE_ORDER`

```python
"icy_veins": ["html_parse"],  # no longer a stub
```

Update the comment; behavior is now real.

---

## Phase Z.2.5 — Unified Section Inventory Tables (migration 0162)

### Goal

Consolidate `landing.method_page_sections` and `landing.iv_page_sections` into a single `landing.bis_page_sections` table, and consolidate `config.method_section_overrides` into `config.bis_section_overrides`. Any future source (u.gg if we ever add section tracking, Wowhead, etc.) writes to the same table. The admin UI in Z.3 reads from one place.

### `landing.bis_page_sections` (replaces both existing tables)

```sql
CREATE TABLE landing.bis_page_sections (
    id                 BIGSERIAL PRIMARY KEY,
    spec_id            INTEGER NOT NULL REFERENCES ref.specializations(id),
    source_id          INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
    page_url           TEXT NOT NULL,
    section_key        TEXT NOT NULL,      -- h3 id attr for IV; heading text for Method
    section_title      TEXT NOT NULL,      -- human-readable display name
    sort_order         INTEGER,            -- table_index for Method; NULL for IV
    content_type       VARCHAR(20),        -- 'overall'/'raid'/'mythic_plus'/NULL
    is_trinket_section BOOLEAN NOT NULL DEFAULT FALSE,
    row_count          INTEGER NOT NULL DEFAULT 0,
    is_outlier         BOOLEAN NOT NULL DEFAULT FALSE,
    outlier_reason     TEXT,
    scraped_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (spec_id, source_id, section_key)
);
```

**Column notes:**
- `section_key` — the stable identifier within a scrape result. For IV: h3 id when present, area_N when not. For Method: heading text. The UNIQUE constraint uses this; the override table references it by the same value.
- `sort_order` — Method uses this for table_index (position order among tables on the page). IV leaves it NULL.
- `is_trinket_section` — IV-specific; always FALSE for Method since Method doesn't have a separate trinket structure.

### `config.bis_section_overrides` (replaces `config.method_section_overrides`)

```sql
CREATE TABLE config.bis_section_overrides (
    spec_id      INTEGER NOT NULL REFERENCES ref.specializations(id),
    source_id    INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
    content_type VARCHAR(20) NOT NULL CHECK (content_type IN ('overall','raid','mythic_plus')),
    section_key  TEXT NOT NULL,   -- which section_key to use for this content_type
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (spec_id, source_id, content_type)
);
```

**Key difference from `method_section_overrides`:** adds `source_id`. Method previously only had one source per spec so `(spec_id, content_type)` was sufficient. IV has three sources per spec (Overall/Raid/M+) pointing to the same page URL, so `source_id` is needed to disambiguate.

### Migration 0162 Steps

1. Create `landing.bis_page_sections`
2. Create `config.bis_section_overrides`
3. Migrate Method data:
   ```sql
   INSERT INTO landing.bis_page_sections
       (spec_id, source_id, page_url, section_key, section_title, sort_order,
        content_type, is_trinket_section, row_count, is_outlier, outlier_reason, scraped_at)
   SELECT
       mps.spec_id,
       bt.source_id,
       bsr.url,
       mps.section_heading,      -- section_key = heading text for Method
       mps.section_heading,      -- section_title same as key for Method
       mps.table_index,
       mps.inferred_content_type,
       FALSE,
       mps.row_count,
       mps.is_outlier,
       mps.outlier_reason,
       mps.fetched_at
   FROM landing.method_page_sections mps
   JOIN config.bis_scrape_targets bt ON bt.spec_id = mps.spec_id
       AND bt.source_id IN (SELECT id FROM ref.bis_list_sources WHERE name ILIKE '%method%')
   JOIN landing.bis_scrape_raw bsr ON bsr.target_id = bt.id
   ON CONFLICT (spec_id, source_id, section_key) DO NOTHING;
   ```
4. Migrate IV data:
   ```sql
   INSERT INTO landing.bis_page_sections
       (spec_id, source_id, page_url, section_key, section_title, sort_order,
        content_type, is_trinket_section, row_count, is_outlier, outlier_reason, scraped_at)
   SELECT
       spec_id, source_id, page_url,
       section_h3_id,   -- section_key = h3 id (or area_N) for IV
       section_title,
       NULL,            -- sort_order not used by IV
       content_type,
       is_trinket_section,
       row_count, is_outlier, outlier_reason, scraped_at
   FROM landing.iv_page_sections
   ON CONFLICT (spec_id, source_id, section_key) DO NOTHING;
   ```
5. Migrate overrides:
   ```sql
   INSERT INTO config.bis_section_overrides
       (spec_id, source_id, content_type, section_key, created_at)
   SELECT
       mso.spec_id,
       bt.source_id,
       mso.content_type,
       mso.section_heading,
       mso.created_at
   FROM config.method_section_overrides mso
   JOIN config.bis_scrape_targets bt ON bt.spec_id = mso.spec_id
       AND bt.source_id IN (SELECT id FROM ref.bis_list_sources WHERE name ILIKE '%method%')
   ON CONFLICT DO NOTHING;
   ```
6. Drop old tables: `landing.method_page_sections`, `landing.iv_page_sections`, `config.method_section_overrides`

### Code Changes

**`_upsert_method_sections()`** — change target table from `landing.method_page_sections` to `landing.bis_page_sections`. Column renames: `section_heading` → `section_key`+`section_title`, `table_index` → `sort_order`, `fetched_at` → `scraped_at`, `inferred_content_type` → `content_type`.

**`_upsert_iv_sections()`** — change target table from `landing.iv_page_sections` to `landing.bis_page_sections`. Column rename: `section_h3_id` → `section_key`.

**`_resolve_method_section()`** — change override lookup from `config.method_section_overrides` to `config.bis_section_overrides` (add `source_id` to WHERE clause).

**New: `_resolve_iv_section(pool, sections, spec_id, source_id, content_type)`** — same pattern as `_resolve_method_section`. Checks `config.bis_section_overrides` first; falls back to auto-classification from `_iv_classify_tab_label`. Used by `_extract_icy_veins` to pick the right section when overrides exist (Blood DK's dual-raid case).

### Override Semantics for IV

For Blood DK, an admin will set:
- `(spec_id=blood_dk, source_id=iv_overall, content_type='overall') → section_key='area_3'` — maps "Dreamrift, Voidspire, and March..." as the overall BIS list
- No override needed for raid — `_extract_icy_veins` with `content_type='raid'` will just use the first matching raid section (area_1)

---

## Phase Z.3 — Admin Section Inventory

Reads from `landing.bis_page_sections` (post-Z.2.5 unified table).

### API Endpoint

`GET /api/v1/admin/bis/page-sections?source=icy_veins&outliers_only=false`

Returns rows from `landing.bis_page_sections` joined to `ref.specializations` and `ref.bis_list_sources`. `source` param filters by `bis_list_sources.origin` ('icy_veins', 'method', etc.).

Response shape:
```json
{
  "sections": [
    {
      "spec_id": 1,
      "spec_name": "Balance",
      "class_name": "Druid",
      "source_name": "Icy Veins Overall",
      "source_origin": "icy_veins",
      "section_key": "overall-bis-list-for-specspec-specclass",
      "section_title": "Overall BiS List for Balance Druid",
      "content_type": "overall",
      "is_trinket_section": false,
      "row_count": 16,
      "is_outlier": false,
      "outlier_reason": null,
      "override": null,
      "scraped_at": "2026-04-20T12:00:00Z"
    }
  ],
  "gaps": [
    {
      "spec_id": 5,
      "spec_name": "Blood",
      "class_name": "Death Knight",
      "source_id": 3,
      "content_type": "overall",
      "available_sections": [
        {"section_key": "area_1", "section_title": "BiS Raid (San'layn)", "row_count": 14},
        {"section_key": "area_2", "section_title": "BiS Raid (Deathbringer)", "row_count": 15}
      ]
    }
  ]
}
```

`gaps` — (spec, source, content_type) triples where a scrape target exists but no non-outlier section matches AND no override is set. Lists available sections that could be assigned.

### POST Endpoint

`POST /api/v1/admin/bis/page-sections/override`

```json
{
  "spec_id": 5,
  "source_id": 3,
  "content_type": "overall",
  "section_key": "area_3"
}
```

Upserts to `config.bis_section_overrides`. Returns `{"ok": true}`.

`DELETE /api/v1/admin/bis/page-sections/override` — removes an override by (spec_id, source_id, content_type).

### UI Panel

Replaces the existing Method.gg Section Inventory panel and adds IV — both shown in one unified **"Section Inventory"** panel in `gear_plan_admin.html`.

Source tabs: **Icy Veins | Method**

Per tab:
- Table: Spec | Section Title | Rows | Content Type | Outlier Reason | Override | Actions
- "Outliers only" toggle
- Coverage Gaps section below the table (same as Method panel today)
- Per-gap: dropdown of available sections → "Set Override" button

**IV-specific:** show `is_trinket_section` badge on trinket rows.

---

## Phase Z.4 — Enrichment Pipeline

### BIS Entries (`rebuild_bis_from_landing`)

Add `elif source == "icy_veins":` branch after the method branch:

```python
elif source == "icy_veins":
    slot_map = await _load_slot_labels(conn)
    slots = _iv_parse_bis_from_raw(html, content_type, slot_map)
```

`_iv_parse_bis_from_raw(html, content_type, slot_map)` — pure function. Re-parses the stored HTML for the section matching `content_type`, applies slot_map, returns `list[SimcSlot]`.

Only sections where `content_type IS NOT NULL AND NOT is_outlier` (consulted from `landing.iv_page_sections`) are processed.

### Trinket Tier Ratings (`rebuild_trinket_ratings_from_landing`)

Add `elif source == "icy_veins":` branch:

```python
elif source == "icy_veins":
    trinket_rows = _iv_parse_trinkets_from_raw(html)
```

`_iv_parse_trinkets_from_raw(html)` — pure function. Finds `<details class="trinket-dropdown">`, extracts all tier rows, returns `[{tier, item_id, sort_order}]`.

Upsert to `enrichment.trinket_ratings`:
- `source_id` — from the scrape target row
- `spec_id` — from the scrape target row
- `hero_talent_id` — NULL (IV pages don't split by hero talent)
- `blizzard_item_id` — from the JSONB
- `tier` — S/A/B/C/D
- `sort_order` — sequential within tier

---

## Schema Dependencies (current, post-0154)

- `ref.specializations` — all 40 specs (FK target for iv_page_sections)
- `ref.bis_list_sources` — has IV Raid/M+/Overall rows (FK target for iv_page_sections)
- `landing` schema exists (Phase A, migration 0104)
- `config.bis_scrape_targets` — IV rows already seeded (hero_talent_id=NULL, one per spec per IV source)
- `enrichment.bis_entries` — target for Phase Z.4 BIS rebuild
- `enrichment.trinket_ratings` — target for Phase Z.4 trinket ratings

---

## Code Locations

| File | Current State | Change |
|---|---|---|
| `src/sv_common/guild_sync/bis_sync.py` | `_extract_icy_veins()` full impl; `_iv_parse_from_image_blocks` primary parser (**Z.2b done**) | Z.2.5: `_upsert_iv_sections` → `bis_page_sections`; add `_resolve_iv_section` |
| `src/sv_common/guild_sync/bis_sync.py` | `_upsert_method_sections()` writes to `landing.method_page_sections` | Z.2.5: retarget to `landing.bis_page_sections` |
| `src/sv_common/guild_sync/bis_sync.py` | `_resolve_method_section()` reads `config.method_section_overrides` | Z.2.5: retarget to `config.bis_section_overrides` (add source_id) |
| `alembic/versions/` | 0161 is latest (**Z.1 done**) | Z.2.5: 0162 (bis_page_sections + bis_section_overrides) |
| `src/guild_portal/api/bis_routes.py` | Method section inventory endpoints exist; IV not wired | Z.3: unified `/page-sections` endpoint + override POST/DELETE |
| `src/guild_portal/templates/admin/gear_plan_admin.html` | Method Section Inventory panel exists; IV not wired | Z.3: replace with unified panel + source tabs |
| `src/guild_portal/static/js/gear_plan_admin.js` | Method section inventory JS exists | Z.3: extend for unified table + IV tab |

---

## Open Questions for Build Session

1. **IV ring/trinket row order** — does IV always show Ring followed by Ring, or does it use "Ring 1" / "Ring 2" labels explicitly? Check by fetching 2–3 specs across different class archetypes (caster, melee, tank). If IV already uses explicit labels, `ring` → NULL row in slot_labels may not be needed.

2. **Single-section specs** — do any specs have only one IV list (e.g., only "Overall", no Raid/M+ split)? Outlier detection `row_count < 5` gates against empty parse failures; a single-section spec with a full list is NOT an outlier — the absence of Raid/M+ sections is just handled by the enrichment layer finding no row for those content_types.

3. **Scrape frequency** — IV updates per patch, not daily. Consider a separate scheduler cadence (weekly) distinct from the nightly BIS sync. Low priority for v1; can reuse the existing sync trigger manually.

4. ~~**u.gg wowhead slot map loading**~~ — **RESOLVED in Z.0.** `_extract_ugg` and `_extract_wowhead` each load their respective maps from DB and pass them as parameters to the pure parse functions. `_parse_ugg_html`, `_parse_wowhead_html`, etc. are all pure — no DB access.
