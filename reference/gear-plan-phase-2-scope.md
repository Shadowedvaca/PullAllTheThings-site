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

**Solution:** Show Wowhead tooltips at a specific item level, derived from the player's
current equipped gear and the BIS recommendation logic below.

### Display rules

For each slot in the gear plan:

| Player state | Show BIS at |
|---|---|
| Does not have BIS item | Player's current quality tier, 6/6 |
| Has BIS item, rank < 6/6 | Same quality tier, 6/6 |
| Has BIS item at 6/6 | Next quality tier, 1/6 |
| Has BIS item at Mythic 6/6 | Mythic 6/6 (nothing higher) |

**Hard constraint:** The displayed quality tier must never be lower than the player's
currently equipped quality tier for that slot. Quality tier order: V < C < H < M.

If a player has a Hero item equipped, we never show a Champion recommendation — even if
the BIS item only exists at Champion quality. In that case, show the BIS item at Hero 1/6
minimum (the lowest Hero rank, which is still an upgrade over their current Hero rank if
they are below 6/6, or show Hero 6/6 if they are already at Hero 6/6 and the item doesn't
come at Mythic).

Formally: `display_tier = max(target_tier, equipped_tier)` where V=1, C=2, H=3, M=4.

For **equipped gear display**: show the item at its actual ilvl from `character_equipment.item_level`. This data is already synced from the Blizzard equipment API.

### Quality tier ilvl map

Season-specific. Store in `site_config` as JSON so it can be updated per patch without
a code deploy:

```json
"quality_ilvl_map": {
  "V": {"min": N, "max": N, "ranks": 6},
  "C": {"min": N, "max": N, "ranks": 6},
  "H": {"min": N, "max": N, "ranks": 6},
  "M": {"min": N, "max": N, "ranks": 6}
}
```

Future patches may add ranks to Hero and Mythic — update the `ranks` value and `max` ilvl
in site_config, no code change needed.

### Wowhead tooltip rendering

Wowhead's tooltip widget supports `?ilvl=N` on item links. The existing `power.js` widget
loaded on My Characters handles this automatically. No new tooltip storage needed — we
compute the target ilvl at render time and append it to the Wowhead item link.

```
https://www.wowhead.com/item=250024?ilvl=639
```

### API changes

- `GET /api/v1/me/gear-plan/{character_id}/available-items` — add `target_ilvl` and
  `equipped_ilvl` fields to each item in the `tier` group
- `GET /api/v1/me/gear-plan/{character_id}/slot-detail` (or drawer endpoint) — include
  quality context per item

### Migration

- `site_config`: add `quality_ilvl_map JSONB` column
- `wow_items`: add `quality_track VARCHAR(1)` (from 2B)
- No new tables

---

## Sequencing and dependencies

```
2A must complete before 2B:
  — appearance crawl provides all item IDs that need quality_track tagging

2B must complete before 2C:
  — quality_track on wow_items is required to resolve "Hero variant of item X"
  — quality_ilvl_map in site_config must be populated before display logic runs

2C has no hard blockers after 2B, but quality_ilvl_map values must be
confirmed from live Midnight season data before deploying display.
```

---

## Known open items at scope time

1. Does `/data/wow/item-appearance/{id}` return one item ID per appearance or multiple?
   Determines stub count per crawl hop.
2. Confirm current Midnight quality track ilvl ranges (needed to populate
   `quality_ilvl_map` in site_config).
3. Confirm the 4 appearance sets per suffix map to LFR/Normal/Heroic/Mythic (not
   armor types or something else). Verify by querying the 4 sets for a known suffix
   via the Blizzard API Explorer.
