# Gear Plan 1.5 — BIS Note & Guide Folding

> **Status:** Planning — not yet started
> **Branch:** TBD (feature/bis-note-guide-folding or similar)
> **Depends on:** Phase Z complete (feature/iv-bis-extraction merged)

---

## Problem Statement

The BIS system has grown to four guide sources (u.gg, Wowhead, Method, Icy Veins), each with custom scraping code. The logic for *what to do with extracted items* — ring/trinket pairing, 2H/1H weapon handling, multi-item slots, guide_order — is scattered between per-source branches and the shared upsert function. It works today, but adding the next capability (guide folding with notes) would mean duplicating that logic again across four code paths.

There are also structural gaps that merging can solve:
- Blood DK IV has no 'overall' section — the page only has raid-specific and M+-specific tabs
- Resto Shaman IV has no 'raid' section — same issue
- Synthesizing an 'overall' from two sub-guides is the right answer for these cases

---

## Proposed Features

### 1. `bis_note` Field

A short nullable text field on `enrichment.bis_entries`. Displays in the gear plan UI below the BIS checkmark in a smaller font. Intended for things like:
- "Raid variant" / "M+ variant"
- "2H build" / "1H build"
- "Alt pick"

Admin-configured at the merge-rule level, stamped onto entries at insert time. Not a user-editable field — it comes from the data pipeline.

### 2. Insertion Engine Abstraction

Extract the shared insertion logic from `rebuild_bis_from_landing()` into a standalone engine. This separates:

**What stays per-guide (scraper layer):**
- HTML parsing
- Section classification
- Item ID extraction
- `List[SimcSlot]` output

**What moves to the engine (universal layer):**
- Ring/trinket slot pairing (ring_1 vs ring_2, trinket_1 vs trinket_2)
- 2H/1H weapon variant handling
- Multi-item-per-slot ordering (guide_order)
- FK validation (item must exist in enrichment.items)
- Note injection
- Guide merge conflict resolution

The engine receives a typed input and a rule set; the scraper is irrelevant to it.

### 3. Guide Folding

Allows two guides to be merged into a single content_type (e.g., raid + M+ → overall). Admin-configured via a new table.

**Merge logic (per slot):**
1. Primary guide items insert normally, with an optional `primary_note`
2. For each secondary guide item in a slot:
   - If that item ID is already present in that slot → nothing added (or optionally stamp `match_note` on the existing entry)
   - If item is not present → add it with `secondary_note`
3. Result: a combined list where items unique to secondary carry a note distinguishing them

**Weapons are handled correctly:** if primary has a 2H and 1H, and secondary also has a 2H and 1H, those four items follow the same slot-pairing logic as today — the engine handles this generically, not with weapon-specific code.

**Example — Blood DK IV Overall (synthesized):**
- Primary: IV Raid guide → inserts as normal
- Secondary: IV M+ guide
  - Item already in Raid list → skip (no note needed, it's the consensus pick)
  - Item unique to M+ → add with note "M+ variant"
- Result: `overall` content_type is populated synthetically; any M+-specific picks are noted

---

## Proposed Schema

### Migration A: `bis_note` on `enrichment.bis_entries`

```sql
ALTER TABLE enrichment.bis_entries
    ADD COLUMN bis_note VARCHAR(100);
```

No data migration needed — NULL = no note, which is the correct default for all existing entries.

### Migration B: `config.bis_merge_rules`

```sql
CREATE TABLE config.bis_merge_rules (
    id              SERIAL PRIMARY KEY,
    spec_id         INTEGER NOT NULL REFERENCES ref.specializations(id),
    source_id       INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
    content_type    VARCHAR(20) NOT NULL,           -- the synthetic output content_type
    primary_target_id   INTEGER NOT NULL REFERENCES config.bis_scrape_targets(id),
    secondary_target_id INTEGER NOT NULL REFERENCES config.bis_scrape_targets(id),
    primary_note    VARCHAR(100),                   -- stamped on primary-only items (NULL = no note)
    match_note      VARCHAR(100),                   -- stamped when item appears in both (NULL = no note)
    secondary_note  VARCHAR(100) NOT NULL,          -- stamped on secondary-only items
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (spec_id, source_id, content_type)
);
```

One row per synthetic content_type per spec. The UNIQUE constraint enforces that a given spec+source+content_type can only have one merge rule (the same constraint as `bis_section_overrides`).

---

## Proposed Code Architecture

### `BisInsertionContext`

```python
@dataclass
class BisInsertionContext:
    pool: asyncpg.Pool
    spec_id: int
    source_id: int
    content_type: str
    slot_map: dict[str, str | None]   # slot label → slot key
```

### `BisInsertionRules`

```python
@dataclass
class BisInsertionRules:
    note: str | None = None           # applied to all inserted items
    guide_order_start: int = 1        # for secondary items in a merge
```

### `insert_bis_items(ctx, items, rules)`

Core function of the engine. Receives `List[SimcSlot]`, applies pairing/FK/ordering/note logic, upserts into `enrichment.bis_entries`. Returns `{inserted, skipped, errors}`.

Handles today's concerns generically:
- Rings: first unoccupied of ring_1/ring_2 (existing logic, centralized here)
- Trinkets: same
- Weapons: existing 2H/1H logic, centralized here
- Note: stamps `rules.note` on each inserted row

### `merge_bis_guides(ctx, primary_items, secondary_items, merge_rule)`

Runs the folding logic:
1. Calls `insert_bis_items(ctx, primary_items, rules=BisInsertionRules(note=merge_rule.primary_note))`
2. Queries what was just inserted for this spec/source/content_type
3. For each secondary item:
   - If item_id already in inserted set for that slot → optionally update bis_note to match_note (if not already noted)
   - If item_id not present → calls insert_bis_items for just that item with `note=merge_rule.secondary_note` and incremented guide_order

### `rebuild_bis_from_landing()` after refactor

```
for each target row:
    extract items → List[SimcSlot]   (per-source custom code, unchanged)
    
    check if merge rule exists for (spec_id, source_id, content_type):
        → if yes: skip (will be handled in merge pass)
    
    ctx = BisInsertionContext(...)
    insert_bis_items(ctx, items, BisInsertionRules())

# second pass: merge rules
for each merge rule:
    primary_items  = extract from primary target's raw HTML
    secondary_items = extract from secondary target's raw HTML
    merge_bis_guides(ctx, primary_items, secondary_items, rule)
```

---

## Frontend

### Gear Plan — BIS item display

Currently each BIS item in the slot list shows a checkmark and item name. With `bis_note`:

```
✓ [Item Name]              ← existing
  M+ variant               ← new, smaller font, muted color
```

Applies wherever BIS items are shown:
- BIS recs panel (slot drawer)
- Available items list (when item is BIS)
- Paperdoll BIS badge tooltip (lower priority)

Note is returned from `viz.bis_recommendations` → needs `bis_note` column propagated through the view and API response.

---

## Implementation Order

| Phase | Scope | Migration |
|-------|-------|-----------|
| 1 | `bis_note` column + display in frontend | Yes — add column |
| 2 | Insertion engine extraction (refactor only, no behavior change) | No |
| 3 | Merge rule table + `merge_bis_guides()` | Yes — new table |
| 4 | Admin UI for merge rules (Section Inventory or new panel) | No |
| 5 | Seed Blood DK Overall + Resto Shaman Raid merge rules | No |

Phases 1 and 2 are independent and can be done in either order. Phase 3 depends on Phase 2 (engine must exist before merge logic is added). Phase 4 and 5 can overlap.

---

## What This Doesn't Change

- Scraper code per guide — stays exactly as-is
- `config.bis_scrape_targets` structure — unchanged
- `config.bis_section_overrides` — unchanged
- The `partial`/`success`/`failed` status logic — unchanged
- u.gg, Wowhead, Method scrapers — no changes needed

The extraction layer is already well-separated (each scraper returns `List[SimcSlot]`). The refactor only touches what happens *after* extraction.
