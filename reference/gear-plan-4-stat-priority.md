# Phase 4 — Stat Priority

> **Branch:** `feature/stat-priority` (not yet created)
> **Depends on:** Phase 1.7 daily sync infrastructure (scheduling out of scope here — added to 1.7 later)
> **Status:** Planning — mockups complete (`reference/mockups/stat-priority-mock-5-final.html`)

---

## Overview

Adds per-spec stat priority display to the gear plan section of My Characters. Each of the five guide
sources we already scrape for BIS data also publishes a stat priority list. We capture all of them,
compute a consensus view, and display it as a sticky strip above the gear plan slot table.

Key properties:
- **Multi-source consensus** — all five sources (Wowhead, Icy Veins, u.gg, Method, Archon) are scraped
- **All dimensions captured** — content type (Raid/M+/General), hero talent path where sources split
- **Agreement signaling** — block borders and pips show how many sources agree on each stat's rank
- **Operator tracking** — captures `>`, `>>`, `≥`, `≈`, `=` between stats; flags when sources disagree on the operator
- **Archon quantitative data** — weight percentages and target rating numbers scraped and stored
- **Sticky display** — priority strip pins to the top of the gear plan view while slots scroll below
- **Drill-down** — "Sources" button expands a per-source stacked view for full detail
- **Hover tooltips** — hovering any consensus block shows which sources placed the stat at which rank
- **Scheduling:** out of scope — these scrapes will be wired into Phase 1.7's daily scheduler once both phases are stable

---

## Design Decisions

### Stat name normalization

Use Blizzard's canonical stat keys as stored identifiers. Display labels match the game UI exactly.

| `stat_key`       | Display label      | Notes                        |
|------------------|--------------------|------------------------------|
| `intellect`      | Intellect          | Primary — casters            |
| `strength`       | Strength           | Primary — physical melee     |
| `agility`        | Agility            | Primary — hunters/rogues/etc |
| `stamina`        | Stamina            | Rarely in priority lists     |
| `mastery`        | Mastery            |                              |
| `haste`          | Haste              |                              |
| `critical_strike`| Critical Strike    |                              |
| `versatility`    | Versatility        |                              |
| `leech`          | Leech              | Tertiary — rare              |
| `speed`          | Speed              | Tertiary — rare              |
| `avoidance`      | Avoidance          | Tertiary — rare              |

Each guide uses different label text ("Crit", "Critical Strike", "Crit Rating", etc.). Parsers normalize
to the canonical key on ingest.

### Operator vocabulary

Stored as enum-like strings, rendered as symbols in the UI.

| Stored value | Display | Meaning                      |
|--------------|---------|------------------------------|
| `much_gt`    | `>>`    | Significantly better         |
| `gt`         | `>`     | Better                       |
| `gte`        | `≥`     | Slightly preferred or equal  |
| `approx`     | `≈`     | Approximately equal          |
| `eq`         | `=`     | Equal (rare)                 |

When a source gives only an ordered list with no explicit operator, default to `gt` between all positions.

### Scrape strategy per source

Some stat priority pages are separate URLs; others are sections on the existing BIS pages we already
fetch. The extraction technique follows the existing patterns in `bis_sync.py`.

| Source     | URL strategy                             | Data location                     | Technique         |
|------------|------------------------------------------|-----------------------------------|-------------------|
| Wowhead    | Separate stat-priority URL per spec      | HTML prose + ordered list         | html_parse        |
| Icy Veins  | Separate stat-priority URL per spec      | HTML tables, splits by hero talent| html_parse        |
| u.gg       | Same URL as BIS (existing scrape)        | Embedded in page JS/JSON          | json_embed (reuse)|
| Method     | Separate stats-races-consumables URL     | HTML ordered list                 | html_parse        |
| Archon     | Same URL as BIS (existing scrape)        | `#stats` section in __NEXT_DATA__ | json_embed_archon |

For u.gg and Archon, the stat priority data is extracted during the existing BIS scrape pass — no
additional HTTP request needed. The `sync_target()` function gains a second extraction path for these.

For the three HTML sources (Wowhead, IV, Method), new `config.stat_priority_targets` rows drive
separate fetches. These targets follow the same `source_id + spec_id + content_type` pattern as
`config.bis_scrape_targets` but are a separate table since the URL structure and frequency needs differ.

### Consensus computation

Consensus is computed in Python (not SQL views) during enrichment, following the same
TRUNCATE-and-rebuild pattern as BIS entries.

**Agreement count**: for each (spec, content_type, hero_talent) combination, count how many sources
place each stat at each rank. The consensus rank for a stat is the rank where it has the highest count.
In a tie, use alphabetical source order as a tiebreaker (deterministic).

**Operator consensus**: for each gap between consecutive consensus ranks, tally which operator each
source uses. The majority operator wins. If sources are exactly tied on the operator, prefer the less
strong one (e.g., `gte` over `gt`). Flag `operator_unanimous = FALSE` when there is any disagreement.

**Agreement tiers** (for UI color coding):

| Sources agreeing | Border color | UI label |
|-----------------|--------------|----------|
| 5/5             | `#4ade80`    | tier-full    |
| 4/5             | `#86efac`    | tier-most    |
| 3/5             | `#fbbf24`    | tier-partial |
| 2/5             | `#f97316`    | tier-split   |
| 1/5             | `#f87171`    | tier-split   |

With fewer than 5 sources for a given (spec, content_type, hero_talent) the tier thresholds scale
proportionally (e.g., with 3 sources: 3/3 = full, 2/3 = most, 1/3 = split).

### Archon quantitative data

Archon's `#stats` section includes relative stat weights (percentages summing to ~100%) and sometimes
a recommended target rating per stat. These are stored as:
- `quantitative_weight NUMERIC(5,2)` — the percentage weight (e.g., `24.3`)
- `target_rating INTEGER` — suggested rating cap/target if Archon publishes one (e.g., `2450`)

The target line ("Archon targets: Haste ~2,450 · Mastery ~2,150 · ...") is rendered in the detail
drill panel below Archon's source row. Target ratings are not aggregated into the consensus view.

### Hero talent handling

Hero talent is a **free selector** in the stat priority section — it is not locked to the character's
equipped hero talent. Some players theorycraft different setups and want to be able to check freely.

The selector is scoped to the stat priority strip only. The page-level content type selector (Raid/M+/Overall)
is already present on the page and the stat priority section reacts to it.

When a source does not publish hero-talent-split priority (most sources do not), its single list is
used regardless of which hero talent is selected. The hero talent selector only changes the display
when at least one source provides hero-talent-specific data.

### What is NOT duplicated from the existing page

- **Content type tabs** (Raid/M+/Overall) — already on the page; stat priority reacts to selection
- **Guide links** — already shown on the page in the guides bar
- **Character identity header** — already at top of page

---

## New Schema Objects

### `config.stat_priority_targets` (new table, Phase A)

Drives scraping for sources that require a separate URL fetch. Sources where data comes from the
existing BIS page (u.gg, Archon) do not have rows here — their extraction is added to the existing
`sync_target()` flow.

```sql
CREATE TABLE config.stat_priority_targets (
    id               SERIAL PRIMARY KEY,
    source_id        INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
    spec_id          INTEGER NOT NULL REFERENCES ref.specializations(id),
    hero_talent_id   INTEGER REFERENCES ref.hero_talents(id),    -- NULL = applies to all HTs
    content_type     VARCHAR(20) NOT NULL,                        -- 'raid' | 'dungeon' | 'general'
    url              VARCHAR(500) NOT NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'pending',
    last_fetched     TIMESTAMPTZ,
    items_found      SMALLINT,
    UNIQUE (source_id, spec_id, hero_talent_id, content_type)
);
```

### `landing.stat_priority_raw` (new table, Phase A)

Append-only raw storage, same philosophy as `landing.bis_scrape_raw`. For HTML sources stores the
full page HTML; for Archon/u.gg stores the extracted JSON fragment (the stats section only, not
the full __NEXT_DATA__ blob).

```sql
CREATE TABLE landing.stat_priority_raw (
    id               SERIAL PRIMARY KEY,
    spec_id          INTEGER NOT NULL REFERENCES ref.specializations(id),
    source_id        INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
    content_type     VARCHAR(20) NOT NULL,
    hero_talent_id   INTEGER REFERENCES ref.hero_talents(id),
    raw_content      TEXT NOT NULL,          -- HTML or JSON string
    content_hash     VARCHAR(64) NOT NULL,   -- SHA-256 for dedup (same as bis_scrape_raw)
    scraped_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_updated_at TIMESTAMPTZ           -- from Archon page.lastUpdated where available
);
```

### `enrichment.stat_priority_lists` (new table, Phase A)

One row per (spec, source, content_type, hero_talent) combination after enrichment rebuild.
TRUNCATE-rebuilt like all enrichment tables — not a FK target from stable tables.

```sql
CREATE TABLE enrichment.stat_priority_lists (
    id               SERIAL PRIMARY KEY,
    spec_id          INTEGER NOT NULL,
    source_id        INTEGER NOT NULL,       -- NULL = consensus row
    content_type     VARCHAR(20) NOT NULL,
    hero_talent_id   INTEGER,
    context_note     VARCHAR(200),           -- e.g. "Single target" if source annotates
    scraped_at       TIMESTAMPTZ NOT NULL
);
```

### `enrichment.stat_priority_entries` (new table, Phase A)

Individual stat positions within a list. `operator_after` is the operator between this rank and
the next (NULL for the last stat in the list).

```sql
CREATE TABLE enrichment.stat_priority_entries (
    id                   SERIAL PRIMARY KEY,
    list_id              INTEGER NOT NULL REFERENCES enrichment.stat_priority_lists(id)
                             ON DELETE CASCADE,
    rank                 SMALLINT NOT NULL,
    stat_key             VARCHAR(30) NOT NULL,
    operator_after       VARCHAR(20),        -- 'much_gt' | 'gt' | 'gte' | 'approx' | 'eq' | NULL
    quantitative_weight  NUMERIC(5,2),       -- from Archon only
    target_rating        INTEGER             -- from Archon only
);
```

### `viz.stat_priority_consensus` (new view/table, Phase C)

Computed consensus output — one row per (spec, content_type, hero_talent, stat_key).
May be a materialized Python-written table (like `enrichment.item_popularity`) rather than a SQL view,
since the consensus logic is non-trivial.

```sql
CREATE TABLE viz.stat_priority_consensus (
    spec_id              INTEGER NOT NULL,
    content_type         VARCHAR(20) NOT NULL,
    hero_talent_id       INTEGER,
    stat_key             VARCHAR(30) NOT NULL,
    consensus_rank       SMALLINT NOT NULL,
    agree_count          SMALLINT NOT NULL,   -- sources placing stat at this rank
    source_count         SMALLINT NOT NULL,   -- total sources with data
    operator_after       VARCHAR(20),         -- majority operator after this rank
    operator_unanimous   BOOLEAN NOT NULL DEFAULT TRUE,
    target_rating        INTEGER,             -- from Archon if available
    PRIMARY KEY (spec_id, content_type, hero_talent_id, stat_key)
);
```

---

## API Endpoint

### `GET /api/v1/me/stat-priority/{char_id}`

**Auth:** logged-in member  
**Query params:** `content_type` (default `raid`), `hero_talent_id` (optional)

**Response:**
```json
{
  "ok": true,
  "data": {
    "spec_id": 102,
    "content_type": "raid",
    "hero_talent_id": 12,
    "source_count": 5,
    "consensus": [
      {
        "rank": 1,
        "stat_key": "intellect",
        "stat_label": "Intellect",
        "agree_count": 5,
        "source_count": 5,
        "operator_after": "much_gt",
        "operator_unanimous": true,
        "target_rating": null
      },
      {
        "rank": 2,
        "stat_key": "mastery",
        "stat_label": "Mastery",
        "agree_count": 3,
        "source_count": 5,
        "operator_after": "gt",
        "operator_unanimous": false,
        "target_rating": null
      }
    ],
    "by_source": [
      {
        "source_id": 1,
        "source_name": "Wowhead",
        "content_type": "raid",
        "hero_talent_id": null,
        "entries": [
          {"rank": 1, "stat_key": "intellect", "stat_label": "Intellect", "operator_after": "much_gt", "weight": null, "target_rating": null},
          {"rank": 2, "stat_key": "mastery",   "stat_label": "Mastery",   "operator_after": "gt",      "weight": null, "target_rating": null}
        ]
      },
      {
        "source_id": 5,
        "source_name": "Archon.gg",
        "content_type": "raid",
        "hero_talent_id": null,
        "entries": [
          {"rank": 1, "stat_key": "intellect", "stat_label": "Intellect", "operator_after": "much_gt", "weight": 28.0, "target_rating": null},
          {"rank": 2, "stat_key": "haste",     "stat_label": "Haste",     "operator_after": "gt",      "weight": 24.0, "target_rating": 2450}
        ]
      }
    ]
  }
}
```

The endpoint selects the character's spec, then queries consensus + by_source data filtered to that
spec + content_type + hero_talent_id. If no data exists for the requested hero_talent_id, falls back
to `hero_talent_id IS NULL` rows (the general list).

---

## UI — Gear Plan Stat Priority Strip

Location: My Characters page, Gear tab, between the plan mode controls and the slot table.
The strip is `position: sticky; top: <offset>` so it remains visible as slots scroll.

### Strip structure (collapsed)

```
[Stat Priority]  [Elune's Chosen | Keeper of the Grove]   Raid · 5 sources   [Sources ▾]
──────────────────────────────────────────────────────────────────────────────────────────
[Intellect]  >>  [Mastery]  >  [Haste]  ≈  [Critical Strike]  >  [Versatility]
  ●●●●●          ●●●○○          ●●●○○          ●●●●○               ●●●●●
  5/5            3/5 #2         3/5 #3         4/5 #4              5/5
──────────────────────────────────────────────────────────────────────────────────────────
Source agreement: [■■■■■ 5/5 | ■■■■ 4/5 | ■■■ 3/5 | ■■ 2/5 | ■ 1/5]   ● Operator varies
```

### Block color tiers

Block border color maps directly to the legend band segment color.

| Sources agreeing | Border         | Background     |
|-----------------|----------------|----------------|
| 5/5             | `#4ade80`      | `#0e1a12`      |
| 4/5             | `#86efac`      | `#0d1710`      |
| 3/5             | `#fbbf24`      | `#1a180a`      |
| 2/5             | `#f97316`      | `#1a1208`      |
| 1/5             | `#f87171`      | `#1a0e0e`      |

### Operator color tiers

The operator symbol between blocks uses the same tier color as the blocks, applied to the majority
operator's agreement level. A small amber dot (5×5px circle) below the operator signals when sources
disagree on the operator symbol itself (e.g. 4 say `>`, one says `≥`).

### Hover tooltip (per block)

```
┌─────────────────────────────────┐
│ Mastery — position varies       │  ← tt-title (gold)
├─────────────────────────────────┤
│ #2  Wowhead, u.gg, Method       │  ← tt-row
│ #3  Icy Veins (Elune's), Archon │  ← tt-row
├─────────────────────────────────┤
│ u.gg uses ≥ before Haste        │  ← tt-note (muted)
└─────────────────────────────────┘
        ▼  (caret pointing down to block)
```

Tooltip is shown on `:hover` via CSS. Fully-agreed blocks still get a confirming tooltip:
`"#1 — All 5 sources agree on rank and operator (>>)"`.

### Color band legend

Single horizontal bar, 200px wide, 16px tall, five equal segments:

```
[ 5/5 | 4/5 | 3/5 | 2/5 | 1/5 ]
 green  lt-grn  yellow  orange   red
```

Replaces individual legend swatches. The player's eye maps block border color directly to the band
segment, building intuition quickly.

### Detail drill panel ("Sources ▾")

Expands below the consensus strip. Shows all five sources as compact rows. In each row:
- Source name + content type + hero talent label
- Mini stat blocks in that source's order
- Blocks that differ from the majority order have a dim red border (`#f8717166`)
- Operators that differ from consensus show in dim red (`#f8717188`)
- Archon's row includes weight bars and the target rating summary line

The detail panel is part of the sticky area — when open, the combined height of strip + panel is
sticky. A close toggle collapses it back.

---

## Sub-Phase Breakdown

---

### Phase A — Schema Foundations

**Scope:** Migrations, model classes, no behavior changes.

**New tables:**
- `config.stat_priority_targets`
- `landing.stat_priority_raw`
- `enrichment.stat_priority_lists`
- `enrichment.stat_priority_entries`
- `viz.stat_priority_consensus`

**Models to add** (`bis_models.py` or new `stat_priority_models.py`):
- `StatPriorityTarget`
- `StatPriorityRaw`
- `StatPriorityList`
- `StatPriorityEntry`
- `StatPriorityConsensus`

**Tests:** confirm tables exist, confirm model round-trips.

---

### Phase B — Scraping (per-source parsers)

**Scope:** One parser per source. Admin trigger to run them. Raw data in `landing.stat_priority_raw`.
No enrichment yet.

#### Wowhead parser (`_parse_wowhead_stat_priority(html)`)

URL pattern: `/guide/classes/{class}/{spec}/stat-priority-pve-{role}`  
Parses the ordered list in the stat priority section. Extracts operator from prose text ("much better
than", "slightly better", "approximately equal") or list markers. Returns
`list[dict(rank, stat_key, operator_after)]`.

#### Icy Veins parser (`_parse_iv_stat_priority(html)`)

URL pattern: `/wow/{spec}-{class}-pve-{role}-stat-priority`  
IV may split by hero talent within the same page (separate `<section>` or heading). The parser must:
1. Detect if page contains hero-talent-specific sections
2. If yes: parse one list per hero talent, emit separate records
3. If no: parse single general list
Uses the `>>` / `>` / `≈` symbols directly in the source HTML.

#### u.gg extractor (`_extract_ugg_stat_priority(json_data)`)

No new HTTP request — called during the existing `_extract_ugg()` pass with the same JSON blob.
The stats section is typically a ranked array of stat objects. Extracts rank order + any weight
values. Returns `list[dict(rank, stat_key, operator_after, weight)]`.

#### Method parser (`_parse_method_stat_priority(html)`)

URL pattern: `/guides/{class}-{spec}/stats-races-and-consumables`  
Parses the stat section (not the full page). Method typically uses `>` as separator between stat
names in a single sentence or ordered list.

#### Archon extractor (`_extract_archon_stat_priority(next_data)`)

No new HTTP request — called during the existing `_extract_archon()` pass.
The `#stats` section in `__NEXT_DATA__` contains stat objects with relative weights and sometimes
target values. Extracts: rank order, weight percentages, target ratings.

#### `sync_stat_priority(spec_id, source_id, content_type, hero_talent_id=None)`

Wrapper that:
1. Fetches the URL (if needed — u.gg and Archon reuse existing fetch)
2. Calls the appropriate parser
3. Checks content hash against latest `landing.stat_priority_raw` row — skips insert if unchanged
4. Writes to `landing.stat_priority_raw`
5. Updates `config.stat_priority_targets.last_fetched` and `status`

#### Admin trigger

New button on the BIS sync admin page (or the existing "Sync BIS Lists" section):
**"Sync Stat Priority"** — runs `sync_stat_priority` for all targets in `config.stat_priority_targets`,
plus triggers the embedded extraction for u.gg and Archon on their existing raw rows.

**Tests:** unit tests for each parser using fixture HTML/JSON. No network calls in tests.

---

### Phase C — Enrichment (consensus computation)

**Scope:** `rebuild_stat_priority_from_landing()` function. Populates `enrichment.stat_priority_lists`,
`enrichment.stat_priority_entries`, and `viz.stat_priority_consensus`.

#### `rebuild_stat_priority_from_landing(conn)`

1. TRUNCATE all three enrichment/viz tables (same as other rebuild functions)
2. For each distinct (spec_id, source_id, content_type, hero_talent_id) in `landing.stat_priority_raw`:
   - Take the most recent row (by `scraped_at`)
   - Parse the raw content through the appropriate parser
   - Normalize stat names to canonical keys
   - Insert into `enrichment.stat_priority_lists` + `enrichment.stat_priority_entries`
3. For each distinct (spec_id, content_type, hero_talent_id):
   - Gather all source lists for this combination
   - Compute consensus rank for each stat (by vote count)
   - Compute majority operator for each gap
   - Determine `operator_unanimous` for each gap
   - Insert/upsert into `viz.stat_priority_consensus`

**Edge cases:**
- A source has data for `hero_talent_id=NULL` but not for a specific hero talent → use the general list
  when the requested hero talent has no data
- A spec has fewer than 5 sources with data → `source_count` reflects actual count; tier thresholds
  scale proportionally
- A stat appears in some sources but not others → only stats appearing in a majority of source lists
  are included in the consensus; minority-only stats are visible in the detail drill only

**Admin trigger:** "Enrich Stat Priority" button on BIS sync admin page (runs after scrape step).

**Tests:** unit tests for consensus computation covering agree/disagree cases, operator tie-breaking,
hero talent fallback, fewer-than-5-sources scaling.

---

### Phase D — API + UI

**Scope:** New endpoint + stat priority strip on My Characters gear tab.

#### API

`GET /api/v1/me/stat-priority/{char_id}` in `gear_plan_routes.py` (or new `stat_priority_routes.py`).

- Resolves char → spec via `guild_identity.wow_characters`
- Queries `viz.stat_priority_consensus` for the consensus list
- Queries `enrichment.stat_priority_lists` + `enrichment.stat_priority_entries` for the by-source detail
- Falls back `hero_talent_id IS NULL` if no HT-specific consensus exists
- Returns the response shape defined above

#### UI — My Characters gear tab

New section between the plan controls and the slot table in the Gear tab.

**JS additions** (`my_characters.js`):
- `_loadStatPriority(charId, contentType, heroTalentId)` — fetches endpoint, renders strip
- `_renderStatPriorityStrip(data)` — builds consensus strip HTML
- `_renderStatPriorityDetail(data)` — builds per-source drill panel HTML
- Hero talent flipper click handler — re-calls `_loadStatPriority` with new HT ID
- "Sources" toggle — shows/hides detail panel, updates button state
- Stat priority re-loads when the existing Raid/M+/Overall tab changes

**CSS additions** (`my_characters.css` or `main.css`):
- `.sp-strip` — sticky positioning, dark background, border-bottom
- `.sp-block` + tier modifier classes (`.tier-full`, `.tier-most`, `.tier-partial`, `.tier-split`)
- `.sp-op` — operator between blocks
- `.sp-pip-row` + `.sp-pip` — pip dots below each block
- `.sp-agree-label` — the N/N text below pips
- `.sp-tooltip` — positioned tooltip, shown on `:hover` of `.sp-block-wrapper`
- `.sp-legend-band` + `.sp-band-seg` — color band legend
- `.sp-detail-panel` — source drill-down container
- `.sp-source-row` + `.sp-mini-block` — per-source compact rows

**Tests:** API endpoint unit tests (spec → consensus data → response shape). UI testing manual.

---

## Mockups

All four initial design options plus the final combined design are in `reference/mockups/`:

| File | Description |
|------|-------------|
| `stat-priority-mock-1-majority.html` | Majority view + expandable disagree badge |
| `stat-priority-mock-2-source-tabs.html` | Tab-per-source with IV hero talent sub-tabs |
| `stat-priority-mock-3-consensus-color.html` | Color-coded consensus with pip indicators |
| `stat-priority-mock-4-stacked.html` | All sources stacked for direct comparison |
| `stat-priority-mock-5-final.html` | **Selected design** — Option 3 consensus + Option 4 drill + tooltips + color band legend |

The selected design (mock-5) is Option 3 as the default view with Option 4 accessible via the
"Sources" toggle. Key additions over the base Option 3: hover tooltips per block, single color-band
legend (green→red, 5 segments), and operator color-coding with disagreement dot.

---

## Implementation Notes

- Parsers follow the same pattern as `_extract_icy_veins()`, `_extract_ugg()`, etc. in `bis_sync.py`.
  Consider whether stat priority parsing belongs in `bis_sync.py` or a new `stat_priority_sync.py`.
  Given the file is already large, a new file is probably cleaner.
- The `rebuild_stat_priority_from_landing()` function should live alongside the other rebuild functions
  (`rebuild_bis_from_landing`, `rebuild_trinket_ratings_from_landing`, etc.).
- u.gg and Archon extraction happens as additional return values from `_extract_ugg()` /
  `_extract_archon()` — or as a second pass on the already-stored raw row in `landing.stat_priority_raw`.
  Second-pass is cleaner (no coupling between BIS and stat priority extraction paths).
- The hero talent list for the flipper comes from `ref.hero_talents` filtered to the character's spec.
  The API should return available hero talents with the response so the UI can build the flipper without
  a second request.
- If a spec has zero stat priority data (no sources scraped yet), the strip renders a graceful empty
  state: "Stat priority data not yet available for this spec."
