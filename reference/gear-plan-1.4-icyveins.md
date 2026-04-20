# gear-plan-1.4-icyveins — Icy Veins BIS Extraction

> **Status:** Plan updated 2026-04-19 — ready to build.
> Branch: `feature/iv-bis-extraction` off `main`.
> Check `alembic/versions/` for the latest migration number before writing new ones.

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

### Section Headers

Every BIS list section is preceded by a heading div:

```html
<div class="heading_container heading_number_3">
  <span>2.1.</span>
  <h3 id="overall-bis-list-for-specspec-specclass">Overall BiS List for Balance Druid</h3>
</div>
<div class="heading_container heading_number_3">
  <span>2.2.</span>
  <h3 id="raid-bis-list-for-specspec-specclass">Raid BiS List for Balance Druid</h3>
</div>
<div class="heading_container heading_number_3">
  <span>2.3.</span>
  <h3 id="mythic-gear-bis-list-for-specspec-specclass">Mythic+ Gear BiS List for Balance Druid</h3>
</div>
```

The `id` attribute on `<h3>` is the machine-readable section key.

### Section ID → Content Type Mapping

| h3 id prefix | content_type | Notes |
|---|---|---|
| `overall-bis-list-for-` | `overall` | |
| `raid-bis-list-for-` | `raid` | |
| `mythic-gear-bis-list-for-` | `dungeon` | "Mythic+" = M+ = dungeon in our schema |
| anything else | `NULL` | Flagged as outlier |

Strip everything after `-for-` to get the canonical prefix.

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
- `landing.iv_page_sections` — section metadata only (h3 id, title, classification, outlier flag, row count). **No items JSONB.** Items are re-parsed from raw HTML during `rebuild_bis_from_landing()`, same pattern as Method.
- `enrichment.bis_entries` — final parsed slot → item assignments
- `enrichment.trinket_ratings` — trinket tier ratings, parsed from raw HTML during `rebuild_trinket_ratings_from_landing()`

**No translation tables in code.**

All slot label → slot key mappings live in `config.slot_labels` (see Phase Z.0).

---

## Phases

| Phase | Scope | Migrations |
|---|---|---|
| Z.0 | Unified slot label tables; shared `_resolve_text_slot` helper; remove hardcoded dicts | **0159, 0160 — COMPLETE** |
| Z.1 | `landing.iv_page_sections` metadata table | Yes (next) |
| Z.2 | `_extract_icy_veins()` rewrite + dead code removal | No |
| Z.3 | Admin section inventory UI + API endpoint | No |
| Z.4 | `rebuild_bis_from_landing()` + `rebuild_trinket_ratings_from_landing()` IV branches | No |

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

## Phase Z.3 — Admin Section Inventory

### API Endpoint

`GET /api/v1/admin/bis/iv-sections?outliers_only=false`

Returns rows from `landing.iv_page_sections` joined to `ref.specializations` and `ref.bis_list_sources`.

Response shape:
```json
{
  "sections": [
    {
      "spec_id": 1,
      "spec_name": "Balance",
      "class_name": "Druid",
      "source_name": "Icy Veins Overall",
      "section_h3_id": "overall-bis-list-for-balance-druid",
      "section_title": "Overall BiS List for Balance Druid",
      "content_type": "overall",
      "is_trinket_section": false,
      "row_count": 16,
      "is_outlier": false,
      "outlier_reason": null,
      "scraped_at": "2026-04-20T12:00:00Z"
    }
  ]
}
```

### UI Panel

New collapsible panel in `gear_plan_admin.html` — **"IV Section Inventory"** — below the existing BIS sync matrix.

Columns: Spec | Source | Section Title | Content Type | Rows | Outlier | Reason | Scraped

Filters:
- Outliers only toggle
- Source filter (IV Raid / IV M+ / IV Overall)

Read-only in v1. Outlier decisions feed back into code updates to `_iv_classify_section()`.

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
| `src/sv_common/guild_sync/bis_sync.py` | `_extract_icy_veins()` stub, dead code; hardcoded dicts removed (**Z.0 done**) | Z.2: rewrite extraction, remove dead code |
| `src/sv_common/guild_sync/bis_sync.py` | `_load_slot_labels(conn)` — no origin param, reads `config.slot_labels` (**Z.0 done**) | Z.2: call from `_extract_icy_veins` |
| `src/sv_common/guild_sync/bis_sync.py` | `_load_wowhead_invtypes(conn)` — reads `config.wowhead_invtypes` (**Z.0 done**) | No further change needed |
| `src/sv_common/guild_sync/bis_sync.py` | `_resolve_text_slot(raw_label, slot_map, ring_count, trinket_count)` — shared helper (**Z.0 done**) | Z.2: must be called from `_iv_extract_regular_rows` |
| `alembic/versions/` | 0160 is latest (**Z.0 done**) | Z.1: 0161 (iv_page_sections) |
| `src/guild_portal/api/bis_routes.py` | IV targets skipped in sync | Z.3: new `/iv-sections` endpoint |
| `src/guild_portal/templates/admin/gear_plan_admin.html` | IV cells show coming-soon | Z.3: IV Section Inventory panel |
| `src/guild_portal/static/js/gear_plan_admin.js` | IV cells show coming-soon indicator | Z.3: fetch + render iv-sections |

---

## Open Questions for Build Session

1. **IV ring/trinket row order** — does IV always show Ring followed by Ring, or does it use "Ring 1" / "Ring 2" labels explicitly? Check by fetching 2–3 specs across different class archetypes (caster, melee, tank). If IV already uses explicit labels, `ring` → NULL row in slot_labels may not be needed.

2. **Single-section specs** — do any specs have only one IV list (e.g., only "Overall", no Raid/M+ split)? Outlier detection `row_count < 5` gates against empty parse failures; a single-section spec with a full list is NOT an outlier — the absence of Raid/M+ sections is just handled by the enrichment layer finding no row for those content_types.

3. **Scrape frequency** — IV updates per patch, not daily. Consider a separate scheduler cadence (weekly) distinct from the nightly BIS sync. Low priority for v1; can reuse the existing sync trigger manually.

4. ~~**u.gg wowhead slot map loading**~~ — **RESOLVED in Z.0.** `_extract_ugg` and `_extract_wowhead` each load their respective maps from DB and pass them as parameters to the pure parse functions. `_parse_ugg_html`, `_parse_wowhead_html`, etc. are all pure — no DB access.
