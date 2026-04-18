# gear-plan-2-icyveins — Icy Veins BIS Extraction

> **Status:** Implementation plan — ready to build in a fresh conversation.
> Branch: new `feature/iv-bis-extraction` off `main` after gear-plan-schema-overhaul merges.

---

## What We Confirmed (Pre-Investigation)

**IV pages are partially SSR — item data IS in static HTML.**

Original assumption (PHASE_Z): "fully JS-rendered, httpx returns no item data."
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
The text content is the human-readable label.

### Known Section ID → Content Type Mapping

| h3 id prefix | content_type | Notes |
|---|---|---|
| `overall-bis-list-for-` | `overall` | |
| `raid-bis-list-for-` | `raid` | |
| `mythic-gear-bis-list-for-` | `dungeon` | "Mythic+" = M+ = dungeon in our schema |
| anything else | `unknown` | Flagged as outlier — review before mapping |

Section IDs are spec-specific in the HTML (`-for-specspec-specclass`) but the prefix is the stable key.
Strip everything after `-for-` to get the canonical prefix for matching.

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
- **slot**: `td[0].get_text(strip=True)`
- **item_id**: `int(span['data-wowhead'].split('=')[1])` from `td[1] span[data-wowhead]`
- **item_name**: `span.get_text(strip=True)` from same span
- **encounter**: `td[2]` first anchor text (if present)
- **instance**: `td[2]` second anchor text (if present)

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

## What We're Building

### Goal

Implement `_extract_icy_veins()` in `bis_sync.py` to:

1. Fetch IV page HTML with `httpx`
2. Discover **all** BIS sections on the page (not just the three we expect)
3. Extract items per section, storing raw data in `landing` schema
4. Flag outlier sections (unknown IDs, unexpectedly short item lists)
5. Map known sections → `bis_list_entries` enrichment pipeline
6. Map trinket sections → `trinket_tier_ratings` as a bonus

### Design Principle: Capture Everything, Map What We Recognize

We do NOT cherry-pick only Raid/M+/Overall. We extract every section the page has.
After scraping, the admin UI shows a section inventory with outlier flags so we can
decide how to map anything unexpected before it enters the enrichment pipeline.

This protects against:
- IV adding a new section type (e.g., "Hero Talent BIS List")
- Specs with atypical guides (e.g., only one list, or extra lists)
- Layout anomalies in a handful of specs

---

## Phase Z.1 — New Landing Table

### Migration

Add `landing.iv_page_sections` — one row per scraped section per spec URL.

```sql
CREATE TABLE landing.iv_page_sections (
    id              BIGSERIAL PRIMARY KEY,
    spec_id         INTEGER NOT NULL REFERENCES guild_identity.specializations(id),
    source_id       INTEGER NOT NULL REFERENCES guild_identity.bis_list_sources(id),
    page_url        TEXT NOT NULL,
    section_h3_id   TEXT NOT NULL,          -- raw h3 id attr, e.g. "overall-bis-list-for-specspec-specclass"
    section_title   TEXT NOT NULL,          -- human label, e.g. "Overall BiS List for Balance Druid"
    content_type    VARCHAR(20),            -- 'raid'/'dungeon'/'overall'/NULL if unknown
    is_trinket_section BOOLEAN NOT NULL DEFAULT FALSE,
    items           JSONB NOT NULL,         -- see schema below
    items_found     INTEGER NOT NULL DEFAULT 0,
    is_outlier      BOOLEAN NOT NULL DEFAULT FALSE,
    outlier_reason  TEXT,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (spec_id, source_id, section_h3_id)
);
```

`items` JSONB schema for regular sections:
```json
[
  {
    "slot": "Helm",
    "item_id": 250024,
    "item_name": "Branches of the Luminous Bloom",
    "encounter": "Lightblinded Vanguard",
    "instance": "The Voidspire",
    "priority": 1
  }
]
```

`items` JSONB schema for trinket sections:
```json
[
  { "tier": "S", "item_id": 249346, "item_name": "Vaelgor's Final Stare", "sort_order": 1 },
  { "tier": "S", "item_id": 249809, "item_name": "Locus-Walker's Ribbon",  "sort_order": 2 }
]
```

---

## Phase Z.2 — Extraction Implementation

### Changes to `bis_sync.py`

**Remove (dead code):**
- `_IV_AREA_LINK_RE` regex
- `_IV_ITEM_ID_RE`, `_IV_ITEM_LINK_RE` (old commented-out regexes)
- `discover_iv_areas()` — no-op stub
- `_fetch_iv_areas()` — orphaned helper

**Keep unchanged:**
- `_iv_base_url()` — correct URL builder
- `_iv_bis_role()` — correct role mapper

**Rewrite `_extract_icy_veins()`:**

```python
async def _extract_icy_veins(url: str, spec_id: int, source_id: int, pool) -> list[dict]:
    """
    Fetch one IV BIS page. Extract all sections. Upsert into landing.iv_page_sections.
    Returns list of raw item dicts for the content_type matching source_id (for backward compat).
    """
```

Internal helpers (new, private to this module):
- `_iv_parse_sections(html: str) -> list[SectionRaw]` — pure BeautifulSoup parse, no DB
- `_iv_classify_section(h3_id: str) -> tuple[str | None, bool]` — returns `(content_type, is_trinket)`
- `_iv_is_outlier(section: SectionRaw) -> tuple[bool, str | None]` — returns `(flag, reason)`
- `_iv_extract_regular_rows(table) -> list[dict]` — parses standard 3-col BIS table
- `_iv_extract_trinket_rows(details_el) -> list[dict]` — parses tier-grouped trinket dropdown

### Outlier Detection Rules

A section is flagged as outlier if ANY of:
- `content_type is None` — section ID prefix not in known mapping
- `items_found < 5` — suspiciously short list (most slot lists have 14+ rows)
- `items_found == 0` — extraction produced nothing (parse failure)
- Trinket section has no tier labels found

### Two-Phase HTTP Fetch

Avoid holding DB connection during HTTP (lesson from PHASE_Z):
1. Fetch all pages with `httpx` (no DB) → collect `(url, html)` pairs
2. Open DB pool → parse and upsert all results

---

## Phase Z.3 — Admin UI: Section Inventory

### New section on `/admin/gear-plan`

**"IV Section Inventory"** panel — shows after "Sync BIS Lists" completes for IV sources.

Columns: Spec | Section Title | Content Type | Items | Outlier | Outlier Reason | Last Scraped

Filters:
- Outliers only toggle
- Source filter (IV Raid / IV M+ / IV Overall)

**Purpose:** After the first full scrape across all 40 specs, review this table to:
- Confirm known sections mapped correctly
- Identify any specs with unusual guide structures
- Decide how to handle `unknown` content_type sections

No action buttons needed in v1 — this is read-only diagnostics. Manual mapping decisions
feed back into code updates to `_iv_classify_section()`.

### API endpoint

`GET /api/v1/admin/bis/iv-sections?outliers_only=true`

Returns summary rows from `landing.iv_page_sections`.

---

## Phase Z.4 — Enrichment Pipeline

### BIS Entries

After scraping, the existing `bis_list_entries` pipeline reads from `landing.iv_page_sections`
where `content_type IS NOT NULL AND NOT is_outlier`.

Map `content_type` → `source_id` via `bis_list_sources`:
- `overall` → "Icy Veins Overall" source
- `raid` → "Icy Veins Raid" source
- `dungeon` → "Icy Veins M+" source

Slot normalization: IV slot names (e.g., "Helm", "Main Hand") → our `slot` enum values.
Build a mapping table in code (similar to how we handle Wowhead/u.gg slot names).

### Trinket Tier Ratings (Bonus)

Trinket sections feed directly into `trinket_tier_ratings`:
- `source_id` = the IV source that owns the page section
- `spec_id` = from the scrape target
- `hero_talent_id` = NULL (IV pages don't split by hero talent)
- `item_id` = from JSONB
- `tier` = S/A/B/C/D from JSONB
- `sort_order` = sequential within tier

This is a net-new capability — IV trinket tiers are not currently in the system.
Run upsert after BIS entries are populated, same transaction window.

---

## Code Locations

| File | Current State | Change |
|---|---|---|
| `src/sv_common/guild_sync/bis_sync.py` | `_extract_icy_veins()` stub, dead regex helpers | Rewrite extraction; remove dead code |
| `src/sv_common/guild_sync/bis_sync.py` | `_TECHNIQUE_ORDER['icy_veins']` = `'html_parse'` | Already correct technique name — keep |
| `src/sv_common/guild_sync/api/routes.py` | IV matrix cells show "— Coming Soon" | Update to real status after extraction works |
| `src/guild_portal/static/js/gear_plan_admin.js` | IV cells show coming-soon indicator | Same — update after Phase Z.3 endpoint exists |
| `alembic/versions/` | Through 0109 | New migration for `landing.iv_page_sections` |

---

## Schema Dependencies

- `landing` schema exists (Phase A, migration 0104)
- `bis_list_sources` has 3 IV rows (seeds from Phase 1B)
- `bis_scrape_targets` has IV rows (hero_talent_id=NULL, one per spec per IV source)
- `trinket_tier_ratings` exists (migration 0100, Phase 1F)
- `specializations` exists with all 40 specs

---

## Build Phases

| Phase | Scope | Migration |
|---|---|---|
| Z.1 | `landing.iv_page_sections` table | Yes |
| Z.2 | `_extract_icy_veins()` rewrite + dead code removal | No |
| Z.3 | Admin section inventory UI + API | No |
| Z.4 | Enrichment: `bis_list_entries` + `trinket_tier_ratings` from landing | No |

All four phases fit in one feature branch. Suggest tackling Z.1 + Z.2 together (extraction
with no visible results yet), Z.3 (make it observable), then Z.4 (wire into enrichment).

---

## Open Questions for Build Session

1. IV slot names — do they match our `slot` enum exactly, or do we need a mapping table?
   Check by looking at a few pages across different spec types (caster / melee / tank / healer).

2. Do some specs have only a single list (e.g., only "Overall", no Raid/M+ split)?
   If so, the outlier detection threshold of `items_found < 5` is the right gate,
   not "must have exactly 3 sections."

3. How often should IV scrapes run? Weekly is probably sufficient (they update per patch,
   not per day). Consider a separate `iv_sync` scheduler entry distinct from the daily BIS sync.
