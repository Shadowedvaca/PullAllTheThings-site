# Gear Plan — Guide UI Overhaul (Guide Rating Pill + Grid Redesign)

> **Status:** Planning
> **Depends on:** gear-plan-1-ugg-trinket-scraping, gear-plan-2-icyveins, gear-plan-3-archon — all sources must be scraped before this UI is meaningful
> **Branch:** new `feature/guide-ui-overhaul` off `main` after source scraping branches merge

---

## What This Is

The current trinket rankings section shows one column per source per content type — a wide,
hard-to-read layout that gives each source equal horizontal real estate and makes comparing
across sources awkward. This plan redesigns the trinket rankings section around a new visual
primitive — the **Guide Rating Pill** — and restructures the grid to be source-first instead
of content-type-first.

Two main deliverables:

1. **Guide Rating Pill** — a compact multi-segment badge where each segment represents one
   guide source, colored by source, showing the letter grade for that source. Forward-slash
   diagonal cuts between segments create a visual distinction that's more interesting than
   vertical bars and more readable at small sizes than icons.

2. **Grid overhaul** — one column per guide source, a single content-type dropdown at the
   top (Overall / Raid / M+), and the pill as both the column header legend and the per-row
   data display — establishing a consistent visual language between header and cell.

A companion deliverable is the **% → letter grade translation** for sources (u.gg, Archon)
that expose popularity percentages rather than editorial letter grades, plus a FAQ entry
explaining the methodology.

---

## Guide Sources and Trinket Coverage

| Source | Data type | Has trinket ranking? | Abbreviation |
|---|---|---|---|
| Wowhead | Editorial S/A/B/C/D | Yes | WH |
| Icy Veins | Editorial S/A/B/C/D | Yes | IV |
| u.gg | Popularity % → converted | Yes | u.gg |
| Archon.gg | Popularity % → converted | Yes | Arch |
| Method.gg | Editorial (gear BIS) | No — gear only | — |

The pill has 4 segments. Method.gg is excluded from the pill because it does not provide
trinket tier rankings. If Method adds trinket rankings in a future patch, add a 5th segment
at that time.

---

## The Guide Rating Pill

### Visual Design

A compact inline pill divided into 4 color-coded segments with forward-slash diagonal cuts
between them. Each segment shows a single letter grade (S/A/B/C/D) or `—` if no data is
available for the current content type filter.

```
╔══════════╗
║WH║IV║u.gg║Arch║   ← column header variant (abbreviations)
╚══════════╝

╔══════════╗
║ S ║ A ║ B ║ S ║   ← data row variant (grades)
╚══════════╝
```

The diagonal cuts tilt approximately 12–15 degrees (skewX). Segments are roughly equal width.
The pill is short/compact — approximately 80px wide × 20px tall in the header, matching the
current tier badge size in the data rows.

### CSS Technique

The forward-slash effect uses `transform: skewX(-12deg)` on each segment container, with
`transform: skewX(12deg)` counter-applied to the inner text span to keep the letter upright.
The outer pill wrapper clips overflow with `border-radius: 3px; overflow: hidden`.

```css
.guide-pill {
    display: inline-flex;
    overflow: hidden;
    border-radius: 3px;
    gap: 1px;            /* thin gap between segments = visible slash line */
    background: #1a1a1c; /* gap color = dark = the "slash" itself */
}

.guide-pill__seg {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    padding: 2px 0;
    transform: skewX(-12deg);
    font-size: 0.7rem;
    font-weight: 700;
}

.guide-pill__seg span {
    transform: skewX(12deg);
    display: block;
}
```

Segment colors come from the `guide_sites` table's existing color field. The pill simply
reads those values — no new color definitions needed.

### Tier letter colors

Letter grades inside segments use the same tier color palette already used by the
existing tier badge:

| Grade | Text color |
|---|---|
| S | #FFD700 (gold) |
| A | #FF8C00 (orange) |
| B | #6DB33F (green) |
| C | #888888 (grey) |
| D | #AA4444 (muted red) |
| — | #555555 (dim, no data) |

### Two Pill Variants

**Header pill** — appears in the column header row. Shows site abbreviations (WH / IV / u.gg / Arch).
Each segment has a `title` attribute for the tooltip (see below). No grade coloring — segments
use their site color at ~40% opacity as background.

**Data pill** — appears in each trinket row. Shows the grade letter for that item from each
source. Segment background uses the site color at ~60% opacity. Grade text colored per tier.

### Tooltips and Guide Links

Each segment in both pill variants has a `title` attribute that shows on mouseover:

```
Wowhead — Balance Druid Trinket Rankings
[click to open guide]
```

In the header pill, the abbreviation is wrapped in an `<a>` that opens the source's BIS
guide URL for the current spec in a new tab. The URL comes from `bis_scrape_targets.url`
for the active spec + source combination.

In data row pills, segments are not links (too small, too many). Tooltip only.

---

## Grid Overhaul

### Current layout (before)

The current section shows sources as column groups with sub-columns per content type.
Reading across a row requires tracking which column-group you're in. Comparing the same
source across content types (Raid vs M+) requires scanning left-right across the row.

### New layout (after)

```
Content type: [ Overall ▾ ]

  Trinket                    WH  IV  u.gg  Arch   Source
  ─────────────────────────────────────────────────────
  Shard of Violent Cognition  S   A   A     S     Vault · Broodtwister Ovi'nax
  Treacherous Transmitter     A   S   B     A     Vault · Silken Court
  Ovinax's Mercurial Egg      B   —   C     B     Vault · Broodtwister Ovi'nax
  ...
```

Column headers are the Guide Rating Pill (header variant) rather than plain text.

- One column per guide source (4 columns: WH / IV / u.gg / Arch)
- Content type dropdown controls which data variant is loaded: Overall / Raid / M+
- `—` in a cell = this source does not have data for the selected content type
- A source column that is entirely `—` for the selected content type shows a subtle
  indicator in the header pill segment (dimmed, italic) rather than being hidden entirely,
  so users understand it's a coverage gap not a missing source

**Sort order:** default by Wowhead grade (S→A→B→C→D→unranked). Secondary sort by the
combined grade across all sources (sum of numeric equivalents). User can click any column
header to re-sort by that source.

### Content Type Dropdown

Positioned above the grid, right-aligned. Options: Overall / Raid / M+. Default: Overall
(matches the existing default behavior). Changing it reloads the grid data via the existing
trinket-ratings API endpoint with a `content_type` query param.

Sources that don't support the selected content type (e.g., Wowhead has no separate M+
trinket tier list) show `—` in all their cells. The header pill segment for that source is
visually dimmed.

---

## % → Letter Grade Conversion

### Why Convert

u.gg and Archon expose popularity percentages, not editorial letter grades. Showing raw
percentages (`34.1%`) alongside letter grades (`S`) in the same grid column creates a
confusing mixed-signal display. Converting to letter grades makes the grid scannable and
puts all sources on the same visual language.

### Analysis Step (Do Before Coding)What happ

Before hardcoding any thresholds, pull data for 3–5 specs and compare:

1. Export Wowhead letter grades for a spec (e.g., Balance Druid, Shadow Priest, Resto Shaman)
2. Export u.gg and Archon popularity % for the same spec + content type
3. Rank trinkets by Wowhead grade (S at top)
4. See where natural % cut-points align with grade boundaries

Expected question: does the same % threshold work across specs? A spec with 3 dominant
trinkets will cluster differently than a spec with 8 viable ones. The analysis should check
whether a single global threshold table works, or whether we need per-spec normalization.

**Deliverable from analysis:** a threshold table like:

| Grade | Popularity % ≥ |
|---|---|
| S | TBD |
| A | TBD |
| B | TBD |
| C | TBD |
| D | anything ranked |

Document the actual cut points found in the analysis here once complete.

### Storage

Thresholds are stored in `site_config` as a JSONB field `popularity_grade_thresholds` — not
hardcoded in Python. This allows tuning without a deploy.

```json
{
    "S": 30,
    "A": 20,
    "B": 10,
    "C": 5
}
```

Items with any popularity data below the C threshold still receive D (they're ranked, just
low). Items with zero popularity data for the selected content type receive `—`.

The conversion happens at the API layer in `bis_routes.py` — raw % stored in DB, converted
to grade on read.

### FAQ Entry

A collapsible FAQ section below the trinket rankings grid explains the conversion:

> **Why do some sources show letter grades and others show percentages?**
>
> Wowhead and Icy Veins use editorial letter grades assigned by human curators. u.gg and
> Archon.gg measure observed popularity — what percentage of top-performing players of your
> spec are using each trinket. Because these are fundamentally different signals, we convert
> popularity percentages to letter grades so all four sources are comparable at a glance.
>
> The conversion uses usage thresholds validated against Wowhead's editorial grades:
> trinkets that Wowhead rates S typically cluster above X% usage on u.gg and Archon, A-tier
> above Y%, and so on. Thresholds are updated when major patch changes shift the meta.
>
> A `—` means the source does not have data for the selected content type — not that the
> trinket is bad.

Fill in X and Y from the analysis step.

---

## Source and Spec Context in the API

The trinket-ratings endpoint needs to know the active spec's guide URLs to populate the
header pill tooltips and links. Extend the API response to include:

```json
{
  "ok": true,
  "data": {
    "spec_id": 102,
    "slot": "trinket_1",
    "sources": [
      {
        "source_id": 1,
        "name": "Wowhead",
        "short_label": "WH",
        "color": "#1a9ee0",
        "guide_url": "https://www.wowhead.com/..."
      },
      ...
    ],
    "items": [ ... ]
  }
}
```

`guide_url` comes from `bis_scrape_targets.url` for the active spec + source. If no scrape
target exists for this spec + source, `guide_url` is null and the header segment renders
without a link.

---

## Implementation Steps

### Analysis (prerequisite — no code)

| Step | Work |
|---|---|
| AN-1 | Pull Wowhead grades + u.gg % + Archon % for 3–5 specs, same content type |
| AN-2 | Identify natural cut points; document in this file |
| AN-3 | Decide: global thresholds or per-spec normalization |

### Backend

| Step | Scope | Size |
|---|---|---|
| BE-1 | Migration — `site_config` + `popularity_grade_thresholds JSONB` column | Tiny |
| BE-2 | `bis_routes.py` — `_pct_to_grade(pct, thresholds)` helper; apply on read in `get_trinket_ratings()` | Small |
| BE-3 | `bis_routes.py` — extend trinket-ratings response with `sources[]` array including `guide_url` | Small |
| BE-4 | `bis_routes.py` — content_type query param wired through to u.gg + Archon data joins | Small |

### Frontend — Member UI

| Step | Scope | Size |
|---|---|---|
| FE-1 | `my_characters.css` — `.guide-pill`, `.guide-pill__seg` styles with skew + tier text colors | Small |
| FE-2 | `my_characters.js` — `buildGuidePill(sources, grades)` — renders both header and data pill variants | Small |
| FE-3 | `my_characters.js` — replace current column headers with header pill; wire tooltip + link | Small |
| FE-4 | `my_characters.js` — replace per-row source cells with data pill | Small |
| FE-5 | `my_characters.js` — content type dropdown; re-fetch on change with `content_type` param | Small |
| FE-6 | `my_characters.js` — column sort on header click | Small |
| FE-7 | `my_characters.js` + `my_characters.css` — FAQ accordion below grid | Small |

FE-1 and FE-2 first — the pill component is the dependency for everything else.

---

## Key Files

| File | Change |
|---|---|
| `alembic/versions/NNNN_popularity_grade_thresholds.py` | BE-1 |
| `src/guild_portal/api/bis_routes.py` | BE-2, BE-3, BE-4 |
| `src/guild_portal/static/js/my_characters.js` | FE-2 through FE-7 |
| `src/guild_portal/static/css/my_characters.css` | FE-1, FE-7 |

---

## Open Questions

1. **Wowhead Overall vs Raid vs M+ trinket pages** — Wowhead's trinket tier page is a single
   page with no content-type split. When the user selects Raid or M+, does Wowhead show `—`
   for all cells, or do we treat its rating as applicable to all content types? Recommend:
   treat Wowhead as "Overall" — it shows for all content type selections unless we get a
   Wowhead source that is explicitly raid/m+ split in a future patch.

2. **Pill width at small viewport sizes** — at 320px viewport the pill may compress
   uncomfortably. Decide: collapse to icon-only segments on mobile, or lock a minimum
   column width on the grid and allow horizontal scroll.

3. **Analysis timing** — AN-1 through AN-3 require that u.gg and Archon scraping are live
   and populated on dev. Schedule the analysis session after those source branches merge.

4. **Threshold update cadence** — popularity distributions shift with each major patch
   (item nerfs, tuning passes). Decide whether to revisit thresholds after each major patch
   or treat them as stable across the expansion. Lean toward stable unless obvious drift.
