# Gear Plan Phase 2 — Catalyst Items + Quality-Aware Display

> Status: Scoped, not started
> Depends on: Phase 1 complete (migrations through 0095, prod-v0.16.x)

---

## Overview

Three sub-steps that build on each other strictly. Must be delivered in order.

```
2A: Catalyst Item Discovery  →  2B: Full Variant Mapping  →  2C: Quality-Aware Display
```

---

## Sub-step 2A — Catalyst Item Discovery

**Problem:** Catalyst-slot tier pieces (back/wrist/waist/feet) are not in the Blizzard
Journal loot tables. They are obtained by converting any eligible item using the Creation
Catalyst. They never appear in `get_journal_encounter` and cannot be derived from
`get_item_set` (which only returns the 5 main tier pieces: head/shoulder/chest/hands/legs).

**Solution:** Use the Blizzard Item Appearance API to crawl from a known tier piece to its
full 9-slot appearance set, collecting all item IDs for each slot.

### Crawl chain

```
Known tier item (already in wow_items)
  → GET /data/wow/item/{id}
      read: item.appearances[].id  (appearance ID for this item)
  → GET /data/wow/item-appearance/{appearanceId}
      read: item_appearance_set.id  (parent appearance set ID)
  → GET /data/wow/item-appearance/set/{setId}
      read: appearances[].id  (all 9 appearance IDs in the set)
  → GET /data/wow/item-appearance/{appearanceId}  (×9)
      read: items[].id  (all item IDs sharing that appearance — one per quality variant)
  → stub all found item IDs into wow_items
```

Note: The appearance set index returns multiple sets per suffix (e.g., 4 sets for
"of the Luminous Bloom" — one per quality tier: LFR/Normal/Heroic/Mythic). Crawl ALL
matching sets to capture all quality variants of all 9 slots.

### Suffix derivation

Do NOT hardcode set names. Derive them from existing `wow_items` data:

1. Query `wow_items` for items in tier slots (`head/shoulder/chest/hands/legs`) that have:
   - `armor_type IS NOT NULL`
   - `NOT EXISTS(item_sources)` (not a direct boss drop)
   - `EXISTS(bis_list_entries)` (known BIS item)
   - `name LIKE '% of %'` (has a "of the X" suffix)
2. Extract the suffix from each item name (everything from " of " onward)
3. De-duplicate → one suffix per tier set
4. Search appearance set index for sets whose name contains each suffix
5. Proceed with crawl chain above for each matching appearance set

### New Blizzard API methods needed

- `get_item_appearances(item_id)` — GET /data/wow/item/{id}, read appearances field
- `get_item_appearance(appearance_id)` — GET /data/wow/item-appearance/{id}
- `get_item_appearance_set(set_id)` — GET /data/wow/item-appearance/set/{id}
- `get_item_appearance_set_index()` — GET /data/wow/item-appearance/set/index

### Storage

All discovered item IDs stubbed into `wow_items` with `slot_type='other'` (Enrich Items
fills the real slot). No new tables needed. Link back to tier set via the existing
`wow_items.name` suffix (the appearance crawl preserves item names).

### Admin integration

- Called from Sync Loot Tables (Step 1) after the existing journal walk
- Replaces `sync_tier_set_completions` (which is broken — depends on tooltip HTML that
  doesn't exist yet for new expansion items)
- Result count reported in Sync Loot Tables response

### Open question

Does `/data/wow/item-appearance/{id}` return one item ID per appearance or multiple?
If multiple, each corresponds to a quality variant — stub all of them. If one, each
appearance set crawl yields exactly 9 item IDs (one per slot).

---

## Sub-step 2B — Full Variant Mapping

**Problem:** Currently one item ID per slot per source (whichever variant the journal
encounter returns first). To support quality-aware display we need all quality variants
(LFR/Normal/Heroic/Mythic item IDs) linked so the UI can select the right one at render
time.

**Solution:** Tag each `wow_items` row with its quality track, and ensure all variants for
a given slot+source combination are present.

### Schema changes

Add `quality_track VARCHAR(1)` to `wow_items`:

```sql
ALTER TABLE guild_identity.wow_items
  ADD COLUMN quality_track VARCHAR(1) CHECK (quality_track IN ('V','C','H','M'));
```

### Population

- Journal encounter items: derive `quality_track` from the `quality_tracks` array already
  in `item_sources` for that item — if a source row has `quality_tracks = ['H','M']`, the
  item is available at H and M, but the item ID itself is specific to one track
- Appearance crawl items (2A): each item ID found via appearance is a specific quality
  variant — tag at stub time by which appearance set it came from (LFR/N/H/M)
- Enrich Items already handles fetching metadata for all stubs

### Linking variants

For the display logic in 2C, queries need to find "the Hero variant of this tier piece for
this slot." The link is: same slot + same tier set suffix + `quality_track = 'H'`.

No new join table needed — the suffix + slot_type + quality_track on `wow_items` is enough
to resolve variants at query time.

---

## Sub-step 2C — Quality-Aware Display

**Problem:** Gear plan slot drawer shows item names only. No ilvl context, no indication
of which quality to aim for. The current display is described as "distracting super low
stats."

**Solution:** Show Wowhead tooltips at a specific item level derived from the player's
current equipped gear. Two rules — one for equipped gear, one for BIS recommendations.

### Display rules

One consistent rule applies to **every item** in the detail view (BIS recommendation,
available-items drawer, any tooltip rendered for a slot):

#### Equipped gear (paperdoll slots)
- Show at the player's actual `character_equipment.item_level` for that slot.
- Slot empty → no `?ilvl`, base tooltip only.

#### All non-crafted items in the drawer

Determine the item's **minimum available quality track** from `item_sources.quality_tracks`
(the lowest track the item can be obtained at, e.g. `H` for a Heroic-only drop, `C` for a
Normal drop).

| Item's min quality vs equipped quality | Show at |
|---|---|
| Item min quality ≤ player's equipped quality | Player's actual `equipped_ilvl` — a direct swap comparison at equal ilvl |
| Item min quality > player's equipped quality | `quality_ilvl_map[item_min_quality]['max']` — ceiling of the tier required to obtain it |
| Slot is empty | No `?ilvl` — base tooltip only |

**What this gives the player:**
- Item available at their tier → "here's this item at your exact ilvl — would it be better?"
- Item requires a higher tier → "here's the ceiling of what you'd get if you reach that tier."

Track order for comparison: A(0) < V(1) < C(2) < H(3) < M(4)

#### Crafted items

Always show at `crafted_ilvl_map[item_crafted_track]['max']` — the 5-star ceiling.
No player-state dependency. Crafting at anything less than 5-star is a waste of mats,
so we always show the fully-crafted version.

If the item's crafted track has no entry in `crafted_ilvl_map` for this season (e.g.
Champion is absent in Midnight S1), fall back to base tooltip (no `?ilvl`).

### Ilvl map storage

Season-specific data belongs in `patt.raid_seasons`, not `site_config`.

**Migration 0099** adds two JSONB columns to `patt.raid_seasons`:

```sql
ALTER TABLE patt.raid_seasons
  ADD COLUMN quality_ilvl_map  JSONB,
  ADD COLUMN crafted_ilvl_map  JSONB;
```

Seed the active Midnight Season 1 row (`id = 1`) immediately:

```sql
UPDATE patt.raid_seasons SET
  quality_ilvl_map = '{
    "A": {"min": 220, "max": 237},
    "V": {"min": 233, "max": 250},
    "C": {"min": 246, "max": 263},
    "H": {"min": 259, "max": 276},
    "M": {"min": 272, "max": 289}
  }'::jsonb,
  crafted_ilvl_map = '{
    "A": {"min": 220, "max": 233},
    "V": {"min": 233, "max": 246},
    "H": {"min": 259, "max": 272},
    "M": {"min": 272, "max": 285}
  }'::jsonb
WHERE id = 1;
```

Notes:
- A (Adventurer) and V (Veteran) are stored for completeness but excluded from the gear
  plan display (green/blue items are already filtered out).
- No Champion crafted tier this season — intentionally absent from `crafted_ilvl_map`.
- Update `quality_ilvl_map` and `crafted_ilvl_map` at the start of each new season or
  when Blizzard adds upgrade ranks mid-patch. No code deploy needed.

### Wowhead tooltip rendering

Wowhead's `?ilvl=N` param on item links makes the tooltip widget (already loaded via
`power.js` on My Characters) render full scaled stats at that item level.

```
https://www.wowhead.com/item=250024?ilvl=276
```

No new tooltip storage needed — ilvl is computed at render time from the raid season map
and the player's `character_equipment` row for that slot.

### Service changes (`gear_plan_service.py`)

- Load `quality_ilvl_map` and `crafted_ilvl_map` from the active raid season (join
  `patt.raid_seasons WHERE is_active = TRUE`).
- `get_available_items()`: add `target_ilvl: int | None` per item.
  - For raid/dungeon/tier items: `quality_ilvl_map[slot_track]['max']` or `None`.
  - For crafted items: `crafted_ilvl_map[slot_track]['max']` or `None`.
- Equipped gear endpoint: already returns `item_level` from `character_equipment` — pass
  it through as `equipped_ilvl` so the frontend can append `?ilvl=equipped_ilvl`.

### API changes

- `GET /api/v1/me/gear-plan/{character_id}/available-items` — add `target_ilvl: int | None`
  to each item object in all groups.
- Equipped gear response — add `equipped_ilvl: int | None` (from `character_equipment.item_level`,
  `None` if slot is empty).

### Frontend changes (`my_characters.js` / slot drawer)

- BIS item Wowhead link: append `?ilvl={target_ilvl}` when `target_ilvl` is not null.
- Equipped item Wowhead link (paperdoll): append `?ilvl={equipped_ilvl}` when not null.
- No change to link structure when ilvl is null — base tooltip as before.
- Cache buster bump required on `my_characters.js` and `my_characters.css`.

---

## Sequencing and dependencies

```
2A must complete before 2B:
  — appearance crawl provides all item IDs that need quality_track tagging

2B must complete before 2C:
  — quality_track on wow_items is required for item variant identification
  — quality_track on character_equipment must be populated (already working)

2C has no hard blockers after 2B. Migration 0099 seeds Midnight S1 data
immediately — no manual admin step required after deploy.
```

---

## Known open items at scope time

1. Does `/data/wow/item-appearance/{id}` return one item ID per appearance or multiple?
   Determines stub count per crawl hop.
2. ✅ Midnight quality track ilvl ranges confirmed from Wowhead (April 2026) — seeded
   in migration 0099.
3. Confirm the 4 appearance sets per suffix map to LFR/Normal/Heroic/Mythic (not
   armor types or something else). Verify by querying the 4 sets for a known suffix
   via the Blizzard API Explorer.
