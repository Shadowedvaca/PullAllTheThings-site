# Gear Plan Phase — Delves + World Boss Loot Tables

> Status: Scoped, not started
> Depends on: fix/catalyst-display merged (PR #31)

---

## Overview

Add two new loot categories to the gear plan slot drawer:
- **Delves** — below Crafted
- **World Bosses** — below Delves

Each requires: schema changes, sync support, and display changes.

---

## Schema Changes (single migration)

```sql
-- patt.raid_seasons
ALTER TABLE patt.raid_seasons
  ADD COLUMN current_delve_ids   INTEGER[] NOT NULL DEFAULT '{}',
  ADD COLUMN world_boss_instance_ids INTEGER[] NOT NULL DEFAULT '{}';
```

Seed the active Midnight season:
- `world_boss_instance_ids = {1312}` — already partially synced, instance_name='Midnight'/'World Boss'
- `current_delve_ids` = populated once Blizzard instance IDs are found (see below)

---

## Delve Instance IDs

The 11 Midnight delves (Blizzard instance IDs unknown — need to look up via Blizzard API Explorer
or run `sync_item_sources` with delve detection and capture the IDs):

| Delve Name |
|---|
| Parhelion Plaza |
| Torment's Rise |
| The Grudge Pit |
| Collegiate Calamity |
| The Darkway |
| The Gulf of Memory |
| The Shadow Enclave |
| Sunkiller Sanctum |
| Shadowguard Point |
| Twilight Crypts |
| Atal'Aman |

**How to find IDs:** Use `/admin/blizzard-api` on prod — hit
`GET /data/wow/journal-expansion/index` with `namespace=static-us` to find Midnight expansion ID,
then `GET /data/wow/journal-expansion/{id}` to get the instance list. Delves appear as a separate
category from raids and dungeons. Note all instance IDs for the 11 delves above.

**Alternative:** Delves may appear in `item_sources` after the next full Sync Loot Tables run once
the detection logic is added (see Sync Changes below). Query the DB after sync to capture IDs.

---

## Sync Changes (`item_source_sync.py`)

### Delves

Delves are in the Blizzard journal as a separate instance type. Current code only walks
`exp_data["dungeons"]` and `exp_data["raids"]`. Need to also walk `exp_data["dungeons"]` where
instance name matches a known delve name, OR check for a dedicated `exp_data["delves"]` key if
Blizzard added one.

**New `instance_type` value:** `'delve'`

Detection logic: in `sync_item_sources`, check if the journal expansion data returns a `delves`
key. If not, identify delves by matching instance names against `current_delve_ids` from the
active season, or by a hardcoded name set for the expansion.

### World Bosses

Already working — `instance_type='world_boss'` is assigned when instance name == expansion name.
Instance 1312 syncs correctly. No sync code changes needed.

---

## Admin UI Changes

### Replace multi-select controls with `<select multiple>`

The current tag-input-style controls for `current_raid_ids`, `current_instance_ids` are hard to use.
Replace all three (raids, M+ dungeons, and new delves/world bosses) with simple HTML `<select multiple>`:

- **Raids** — populated from `known_raids` (CharacterRaidProgress distinct raid_name/raid_id)
- **M+ Dungeons** — populated from `item_sources WHERE instance_type='dungeon'`
- **Delves** — populated from `item_sources WHERE instance_type='delve'`
- **World Boss Zones** — populated from `item_sources WHERE instance_type='world_boss'`

Each `<select multiple>` renders instance names, submits IDs. Selected items are highlighted.

### Seeding the current season

Once migration runs, update the active Midnight season via the admin UI:
- `world_boss_instance_ids` → select "Midnight" / "World Boss" (ID 1312)
- `current_delve_ids` → select all 11 Midnight delves (once IDs are known)

---

## Gear Plan Display (`gear_plan_service.py` + frontend)

### `get_available_items()` — add two new source groups

Current groups: `raid`, `dungeon`, `crafted`, `tier`
New groups to add: `delve`, `world_boss`

**Delves query:** Same structure as dungeon query — filter `item_sources.instance_type='delve'`
AND `item_sources.blizzard_instance_id = ANY(current_delve_ids)`. Apply armor type + weapon stat
filters same as dungeons.

**World boss query:** Filter `item_sources.instance_type='world_boss'`
AND `item_sources.blizzard_instance_id = ANY(world_boss_instance_ids)`. World bosses drop 8 items
each, no tier tokens. Same filters as raid drops minus quality_tracks logic.

### API response (`available-items` endpoint)

Add `delve` and `world_boss` groups to the response JSON. Frontend slot drawer renders them
below the existing Crafted section.

### Frontend slot drawer

Current order: Raid → Dungeon → Crafted → (Tier — separate section)
New order: Raid → Dungeon → Crafted → Delves → World Bosses → (Tier)

---

## Prey / Rare Creature Drops (FUTURE — not this phase)

The "Prey" items (e.g. item 251782 "Withered Saptor's Paw") may be from hunting/tracking
rare creatures in the open world. These are NOT in the Blizzard journal. Approach TBD once
we know whether Wowhead wraps Prey and Delves together under the same journal instance.

**Trigger to revisit:** Before Season 2, or when a Prey drop becomes a hot BIS item.

---

## Open Questions

1. Does the Blizzard journal expansion data return a `delves` key, or are delves listed
   under `dungeons`? Check via `/admin/blizzard-api` on prod.
2. Do Prey items share the same Blizzard journal instance as some Delves? If yes,
   they may be captured automatically.
3. What ilvl do Delve items cap at? (Tier 11 Delves base ilvl seems low — confirm
   max ilvl with upgrade ranks for the `quality_ilvl_map`.)
