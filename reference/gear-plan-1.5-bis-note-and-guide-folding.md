# Gear Plan 1.5 — BIS Note & Guide Folding

> **Status:** Planning — not yet started
> **Branch:** TBD (feature/bis-note-guide-folding or similar)
> **Depends on:** Phase Z complete (feature/iv-bis-extraction merged)

---

## Problem Statement

The BIS system has grown to four guide sources (u.gg, Wowhead, Method, Icy Veins). The logic
for *what to do with extracted items* — ring/trinket pairing, 2H/1H weapon handling, multi-item
slots, guide_order — is scattered between per-source branches and the shared upsert function.
Adding the next capability (guide merging with notes) would mean duplicating that logic again
across four code paths.

There are also structural gaps that guide folding can solve:
- **Blood DK IV** — area_1 and area_2 are hero-talent-specific Overall lists (different
  preferred stats/talents per hero tree). area_3 is Raid, area_4 is M+. IV did something unusual
  here: instead of one Overall BIS, they gave two hero-talent-tuned overalls. The system needs a
  way to let an admin declare "fold area_1 and area_2 into a single Overall, noting items that
  differ between the two builds."
- **Resto Shaman IV** and others — some specs have a raid section named after the actual raid
  wing instances rather than the word "raid" (e.g., "Dreamrift, Voidspire, and March on
  Quel'Danas BiS List"). This is a classifiable pattern that can be recognized automatically in
  the IV classifier.

The Raid and M+ specific guides on IV are genuinely different from Overall — they reflect
different talent builds, stat priorities, and item preferences for players who specialize in that
content. The point is to give those players more targeted advice, not just a filtered item list.

---

## Core Design Principle

**The cut from specific to generic is: extraction → `List[SimcSlot]`.**

- **Per-guide (stays custom forever):** HTML parsing, section classification, item ID extraction,
  returning `List[SimcSlot]`. Human-written guides will always have structural edge cases —
  section naming, layout quirks, hero-talent splits. This layer handles that.
- **Generic (universal engine):** Everything from `List[SimcSlot]` into `enrichment.bis_entries`.
  Ring/trinket pairing, weapon variant handling, multi-item ordering, note injection, merge
  conflict resolution. Same logic regardless of which guide produced the items.
- **Admin config (one place):** Section mapping, merge rules, and notes all live in the same
  table and UI. In the same place you say "area_3 = raid", you can also say "merge area_1 into
  area_2 for Overall, secondary note = San'layn variant".

This means: as IV or any guide changes structure each season, the fix is almost always a config
change in Section Inventory — not a code change.

---

## Proposed Features

### 1. `bis_note` Field

A short nullable text field on `enrichment.bis_entries`. Displays in the gear plan UI below the
BIS checkmark in a smaller font. Intended for things like:
- "San'layn build" / "Deathbringer build"
- "M+ variant"
- "Alt pick"

Admin-configured at the merge-rule level in `bis_section_overrides`, stamped onto entries at
insert time. Not user-editable — it comes from the data pipeline.

### 2. Guide Merge Config in `bis_section_overrides`

Rather than a separate merge table, merge behavior is added as optional columns on
`config.bis_section_overrides`. In the same Section Inventory UI where you map a section key to
a content_type, you can also declare a secondary section to merge in and the notes to apply.

**Merge logic (per slot, during Enrich & Classify):**
1. Items from the primary section insert normally, with `primary_note` if set
2. For each item in the secondary section:
   - Item ID already present in that slot → skip insertion; optionally stamp `match_note` on
     the existing entry
   - Item ID not present → add it with `secondary_note` at the next guide_order position
3. Result: the content_type is populated with a merged view; items unique to the secondary
   build carry a note identifying the variant

**Weapons follow the same generic logic as today** — if primary has a 2H and 1H, and secondary
also has a 2H and 1H, those four items go through the normal slot-pairing engine. No
weapon-specific merge code needed.

### 3. Insertion Engine Abstraction

Extract the shared insertion logic from `rebuild_bis_from_landing()` into a standalone function.

**What moves to the engine:**
- Ring/trinket slot pairing (ring_1 vs ring_2, trinket_1 vs trinket_2)
- 2H/1H weapon variant handling
- Multi-item-per-slot ordering (guide_order)
- FK validation (item must exist in enrichment.items)
- Note injection
- Merge conflict resolution (secondary pass)

**What stays in the per-guide scraper:**
- HTML parsing
- Section classification
- Item ID extraction
- Returning `List[SimcSlot]`

### 4. IV Classifier: Raid Instance Name Pattern

Icy Veins sometimes names the raid section after the actual raid wing instances rather than
using the word "raid" (e.g., "Dreamrift, Voidspire, and March on Quel'Danas BiS List"). This is
a recognizable season-specific pattern. The IV classifier (`_iv_classify_tab_label`) should be
extended to detect this:

- If the tab label contains names that match known raid instance names for the current season
  → classify as `raid`
- Season raid instance names are already in `landing.blizzard_journal_instances`
  (instance_type = 'raid')

This reduces the number of manual overrides needed for this pattern and handles it correctly
for future seasons automatically.

---

## Schema Changes

### Migration A: `bis_note` on `enrichment.bis_entries`

```sql
ALTER TABLE enrichment.bis_entries
    ADD COLUMN bis_note VARCHAR(100);
```

NULL = no note. All existing entries default to NULL with no data migration needed.

The note must also propagate through `viz.bis_recommendations` (the view the API reads) — the
view definition needs `be.bis_note` in the SELECT.

### Migration B: Merge columns on `config.bis_section_overrides`

```sql
ALTER TABLE config.bis_section_overrides
    ADD COLUMN secondary_section_key VARCHAR(100),   -- if set, merge this section in
    ADD COLUMN primary_note          VARCHAR(100),   -- note on primary-only items (NULL = no note)
    ADD COLUMN match_note            VARCHAR(100),   -- note when item appears in both (NULL = no note)
    ADD COLUMN secondary_note        VARCHAR(100);   -- note on secondary-only items
```

A row without `secondary_section_key` behaves exactly as today — just a section redirect.
A row with `secondary_section_key` triggers the merge pass.

No new table. No change to the PRIMARY KEY or UNIQUE constraint.

---

## Code Architecture

### `BisInsertionContext`

```python
@dataclass
class BisInsertionContext:
    pool: asyncpg.Pool
    spec_id: int
    source_id: int
    content_type: str
    slot_map: dict[str, str | None]
```

### Core functions

```
insert_bis_items(ctx, items, note=None, guide_order_start=1)
    → upserts List[SimcSlot] into enrichment.bis_entries
    → handles ring/trinket pairing, weapon variants, FK checks, note stamping
    → returns {inserted, skipped}

merge_bis_sections(ctx, primary_items, secondary_items, override_row)
    → runs insert_bis_items for primary with primary_note
    → for each secondary item: checks existing; adds with secondary_note or stamps match_note
    → returns combined stats
```

### `rebuild_bis_from_landing()` structure after refactor

```
TRUNCATE enrichment.bis_entries

for each target row in bis_scrape_raw:
    items = scraper_for_source(html, content_type, slot_map)  # per-guide, unchanged
    override = lookup bis_section_overrides(spec_id, source_id, content_type)

    if override and override.secondary_section_key:
        skip — handled in merge pass
    else:
        insert_bis_items(ctx, items)

# second pass: merge rules
for each override row where secondary_section_key IS NOT NULL:
    primary_html   = raw HTML for primary target
    secondary_html = raw HTML for secondary target
    primary_items   = scraper_for_source(primary_html, ...)
    secondary_items = scraper_for_source(secondary_html, ...)
    merge_bis_sections(ctx, primary_items, secondary_items, override)
```

---

## Frontend

### Gear Plan — BIS item display

Currently each BIS item shows a checkmark and item name. With `bis_note`:

```
✓ [Item Name]              ← existing
  San'layn build           ← new, smaller font, muted color (var(--color-text-muted))
```

Applies wherever BIS items are shown:
- BIS recs panel (slot drawer)
- Available items list (when item is flagged as BIS)
- The note is returned in the API response from `viz.bis_recommendations`

---

## Admin UI

Section Inventory gains merge fields when you expand a section row override:

- **Primary section** — existing section_key override (already there)
- **Secondary section** — new; triggers merge for this content_type
- **Primary note** — applied to items only in primary
- **Match note** — applied to items in both (optional)
- **Secondary note** — applied to items only in secondary

These fields are only shown/relevant when a secondary section is set. The "save override" API
call (`POST /api/v1/admin/bis/page-sections/override`) is extended to accept the new columns.

---

## Implementation Order

| Phase | Scope | Migration |
|-------|-------|-----------|
| 1 | `bis_note` column, propagate through viz view + API, display in frontend | Yes |
| 2 | Insertion engine extraction — pure refactor, no behavior change | No |
| 3 | Merge columns on `bis_section_overrides` + `merge_bis_sections()` | Yes |
| 4 | Admin UI for merge fields in Section Inventory | No |
| 5 | IV classifier: raid instance name pattern detection | No |

Phases 1 and 2 are independent. Phase 3 requires Phase 2. Phases 4 and 5 can be done in any
order after Phase 3.

Phase 2 (engine extraction) is a refactor with zero behavior change — it can be validated by
confirming BIS entry counts are identical before and after. This makes it safe to land
independently before adding merge logic on top.

---

## What Does Not Change

- Per-guide scraper code (bis_sync.py per-source parsing functions)
- `config.bis_scrape_targets` structure
- The `partial`/`success`/`failed` status logic
- The `_resolve_iv_section()` / `_resolve_method_section()` override lookup (these still work
  the same; the merge pass adds behavior on top)
- u.gg, Wowhead, Method scrapers — no changes to HTML parsing code

The extraction layer is already well-separated. The refactor only touches what happens after
extraction.
