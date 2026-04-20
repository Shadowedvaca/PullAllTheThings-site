# Weapon Build Variant Plan

## Problem

Guides (Method, u.gg, Wowhead) sometimes list multiple weapon builds for a spec
(e.g. Frost DK: 2H weapon OR 1H + off-hand; some Warrior specs; caster off-hands).
The current schema stores a single `main_hand` slot per spec/source, silently
dropping the second build. Text labels like "(2h)" and "(dw)" are unreliable and
author-dependent — they cannot be the source of truth for weapon classification.

## Core Principle

**Blizzard's item metadata is the source of truth for weapon type.**  
A slot label is only a hint that "this is a weapon slot." The item ID resolves
the actual type via `enrichment.items.slot_type`:
- `two_hand` → stored as `main_hand_2h`
- `one_hand` → stored as `main_hand_1h`

If the item is not in `enrichment.items` at parse time → **noisy error** (log +
skip). This surfaces a pipeline ordering bug (sp_rebuild_items must run before
rebuild_bis_from_landing), not a data or site problem. No silent fallback.

## Slot Key Changes

| Old | New | Notes |
|-----|-----|-------|
| `main_hand` | `main_hand_2h` | two-hand weapons |
| `main_hand` | `main_hand_1h` | one-hand weapons |
| `off_hand` | `off_hand` | unchanged — shield, frill, or DW off-hand |

`main_hand` is removed as a stored slot key. It may exist transiently as an
intermediate value during parsing (before item lookup), but is never written to
the DB.

## Guide Order

Rename `enrichment.bis_entries.priority` → `guide_order SMALLINT NOT NULL DEFAULT 1`.

For weapon slots, `guide_order` captures the position the weapon appears in the
guide (1 = listed first, 2 = listed second). For all other slots `guide_order = 1`.
This drives display logic: the lowest guide_order main hand entry determines the
preferred build.

## Storage (DB — no display logic)

`enrichment.bis_entries` stores everything the guide recommends:
- Frost DK (Method, Overall): `main_hand_2h` guide_order=1, `main_hand_1h`
  guide_order=2, `off_hand` guide_order=1
- Arms Warrior (2H only): `main_hand_2h` guide_order=1
- Fury Warrior (DW, possibly two 2H): `main_hand_2h` guide_order=1,
  `off_hand` guide_order=1 (if second weapon is also two_hand → off_hand still)

## Display Rules (front end — gear plan)

1. Find the lowest-guide_order main hand entry for this spec/source:
   - If `main_hand_2h` → show it; off_hand slot displays as empty unless
     off_hand item also has `slot_type = two_hand` (Titan's Grip)
   - If `main_hand_1h` → show it AND show off_hand
2. If no main hand entry → show both slots empty

## Populate All Plans

When assigning weapon slots to a gear plan, apply the same display rules.
Pick the guide_order=1 main hand, apply the 2H/1H display rule to determine
whether to also assign off_hand.

## Implementation Phases

### Phase 1 — Schema + Back End
**Migration 0155:**
- Rename `enrichment.bis_entries.priority` → `guide_order`
- Re-classify existing `main_hand` rows in `enrichment.bis_entries` using
  `enrichment.items.slot_type` JOIN (two_hand → main_hand_2h, one_hand → main_hand_1h)
- Re-classify `guild_identity.gear_plan_slots` where `slot_key = 'main_hand'`
  using same JOIN

**bis_sync.py changes:**
- `_resolve_weapon_slot(conn, item_id, raw_slot_key, guide_order)` — async helper
  that looks up `enrichment.items.slot_type` and returns `main_hand_2h` or
  `main_hand_1h`. Logs `ERROR` and returns `None` if item not found.
  Resolution table:
  - `slot_type = 'two_hand'` → `main_hand_2h`
  - `slot_type = 'ranged'` → `main_hand_2h` (all ranged weapons are 2H, main hand slot)
  - `slot_type = 'one_hand'` → `main_hand_1h`
- All three parsers (Method, u.gg, Wowhead) call this for any slot that maps to
  `main_hand` in `config.method_slot_labels`
- `guide_order` for weapons tracked by position in document (1-based counter per
  weapon slot encountered, same pattern as ring_1/ring_2)
- `INSERT INTO enrichment.bis_entries` updated to write `guide_order` column
- `config.method_slot_labels`: `main hand (2h)` and `main hand (dw)` stay
  mapped to `main_hand` (the intermediate value) — item lookup resolves further

**`quality_track.py` — SLOT_ORDER:**
- Replace `main_hand` with `main_hand_1h`, `main_hand_2h`
- Update coverage/status logic: a spec is "success" if it has at least one of
  the two main hand keys (not both required)

### Phase 2 — Gear Plan Display
- `gear_plan_service.py`: apply weapon display rules when building the slot table
- `my_characters.html` / JS: render weapon rows using guide_order=1 build;
  suppress off_hand when guide_order=1 weapon is main_hand_2h (unless Titan's Grip)
- `viz.slot_items` view: update to handle `main_hand_1h` / `main_hand_2h`
- Populate All Plans: apply same rules

### Phase 3 — Populate All Plans Weapon Logic
- When pushing BIS to gear plan: pick guide_order=1 main hand → assign slot;
  apply 2H/1H display rule for off_hand assignment

## Files Touched (estimated)

- `alembic/versions/0155_weapon_slot_split.py`
- `src/sv_common/guild_sync/bis_sync.py`
- `src/sv_common/guild_sync/quality_track.py`
- `alembic/versions/0156_viz_slot_items_weapon_split.py` (view rebuild)
- `src/guild_portal/services/gear_plan_service.py`
- `src/guild_portal/templates/admin/my_characters.html` (or equivalent)
- `src/guild_portal/static/js/my_characters.js`
- `tests/unit/test_bis_sync_method.py`

## Resolved Questions

- **Ranged weapons** (`ranged` slot_type) — all ranged weapons are 2H in current
  WoW and equip in main hand. Treat identically to `two_hand` → `main_hand_2h`.
  No separate `ranged` slot key needed.
- **MAINHANDONLY** inventory type (some wands/casters) — maps to `one_hand` in
  sp_rebuild_items. These become `main_hand_1h`. Correct.
- **Titan's Grip detection** — when off_hand item has slot_type=two_hand, display
  rule shows it. No special-casing needed in storage; display handles it.
