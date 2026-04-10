# Gear Plan Feature — Implementation Plan

## Context

The guild needs to answer "what should we run this week?" from a loot perspective. Today there's no way to see across the roster what items people need, from which bosses, at which quality tier. This feature gives each player a personal gear plan (what they're wearing vs. what they want per slot), then aggregates needs across the roster to show raid boss / M+ dungeon priority grids.

**Design decisions from Mike:**
- BIS lists support per-spec AND per-hero-talent variants
- MVP includes both personal gear plan + roster aggregation grid
- Upgrade logic: same track and above (non-BIS at Champion → Champion+ of BIS item are needs)
- All three BIS sources (Wowhead, Icy Veins, u.gg) stay — automate all, admin entry as backstop
- BIS data is **centralized by Mike for the entire network**, not per-guild. BIS lists are universal game data.
- Auto-publish scraped data immediately, admin reviews/corrects after (no draft→approve workflow)

---

## Current Status

| Phase | Status | Notes |
|-------|--------|-------|
| **1A** Foundation | ✅ COMPLETE | Migration 0066, equipment sync, quality tracks, item cache, ORM |
| **1B** BIS discovery + extraction | ✅ COMPLETE | Migrations 0067–0076, bis_sync.py, simc_parser.py, admin matrix UI |
| **1C** Item source mapping | ✅ ON PROD | item_source_sync.py, Journal API, admin Item Sources + Re-sync Errors, migrations 0077–0078 |
| **1D** Personal gear plan | ✅ COMPLETE | Gear plan integrated into `/my-characters` (UI redesign UI-1A–1H). `/gear-plan` redirects. Migration 0079–0081. |
| **1D.1** Small fixes | ✅ COMPLETE | Fix 1 (tier source via `enrich_catalyst_tier_items`), Fix 2 (M-track dungeon), Fix 3 (table row click) all done |
| **1D.2** Enhanced source display | ⬜ TODO | Instance / Boss / Minimum level in gear table; key thresholds |
| **1D.3** Crafted items | ✅ COMPLETE | H/M detection via bonus IDs + admin ilvl threshold fallback; crafted_source block in get_plan_detail; gear table shows Crafted+track pill; drawer shows track + Crafting Corner link. Crafter names deferred (need result_item_id on recipes table). |
| **1D.4** Loot table junk flagging | ✅ COMPLETE | Migration 0086: `is_suspected_junk` on `item_sources`; `flag_junk_sources(flag_tier_pieces=False)` — default flags only truly empty world boss stubs; tier piece flagging gated behind `flag_tier_pieces=True` (1D.5 only); `get_item_sources()`/`get_instance_names()` exclude junk by default; gear_plan_service filters junk; Show Junk toggle + Flag Junk Sources button in admin. Also: `sync_legacy_expansion_dungeons()` + `POST /sync-legacy-dungeons` background task + "Sync Legacy Dungeons" button for prior-expansion M+ dungeons. |
| **1D.5** Tier token pipeline | ✅ COMPLETE | Migration 0087: `tier_token_attrs` + `v_tier_piece_sources` view. `process_tier_tokens()`: 3 steps — (1) parse tokens + upsert tier_token_attrs, (2) backfill `wow_items.armor_type` for tier pieces from tooltip HTML via `_armor_type_from_tooltip()` (Wowhead jsonequip.subclass unreliable), (3) flag_junk_sources(flag_tier_pieces=True). Each tier piece now shows 2 bosses: slot-specific + Midnight Falls. gear_plan_service detects tier_piece_desired_bids and queries view. POST /process-tier-tokens endpoint (GL). TierTokenAttrs ORM model. 38 unit tests. |
| **1D.6** BIS admin page restructure | ⬜ TODO | 5-step workflow layout replacing flat control bar; Process Tier Tokens button; Tier Tokens section in Reference Tables. |
| **1E.1** Roster aggregation — backend + main table | ⬜ TODO | Two admin endpoints; hierarchical raid table (instance→boss) + flat M+ table; expand/collapse; auto-hide empty track columns; Initiates/Offspec filters; color scale |
| **1E.2** Roster aggregation — drill panel | ⬜ TODO | Slide-in panel; By Item + By Player spoke-list views; Wowhead tooltips; active chip highlight |
| **1E.3** Roster aggregation — auto-setup new members | ⬜ TODO | Hook in `equipment_sync.py`: create default Wowhead BIS plan for newly-discovered in-guild characters |

**Active branch:** `feature/gear-plan-phase-1d`
**Last migration:** 0087
**Last prod tag:** `prod-v0.11.2`

---

## Quality Track System

| Track | Letter | Color | Sources |
|-------|--------|-------|---------|
| Veteran | V | Green (#1eff00) | Raid Finder |
| Champion | C | Blue (#0070dd) | Normal Raid, M+ any key (direct drop) |
| Hero | H | Purple (#a335ee) | Heroic Raid, M+ 6+ direct drop, M+ 4+ vault |
| Mythic | M | Orange (#ff8000) | Mythic Raid, M+ 10+ vault |

All four tracks should be included in upgrade recommendations — V is relevant for players who have non-BIS or low-track items and can use Raid Finder to fill gaps.

M+ key thresholds (Midnight Season 1): hero vault = 4+, hero direct drop = 6+, mythic vault = 10+. These are stored in `patt.raid_seasons` (add columns if not present) or hardcoded per season. See Phase 1D.1.

Parse `name_description.display_string` from Blizzard equipment endpoint: `"Champion 4/8"` → `C`. Regex: `^(Veteran|Champion|Hero|Mythic)\s+\d+/\d+$`. Also detect via SimC `bonus_ids` (season-specific mapping in `site_config.simc_track_bonus_ids`).

**Upgrade logic:**
- Same item, lower track → need strictly higher tracks
- Different item (not BIS) → need same track and above (same-track BIS is still better due to stats)

---

## Data Model (10 tables, migration 0066)

### `guild_identity.wow_items`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| blizzard_item_id | INTEGER UNIQUE NOT NULL | |
| name | VARCHAR(200) NOT NULL | |
| icon_url | VARCHAR(500) | Wowhead CDN |
| slot_type | VARCHAR(20) | head, neck, shoulder, etc. |
| armor_type | VARCHAR(20) | cloth/leather/mail/plate/misc |
| weapon_type | VARCHAR(30) | NULL for armor |
| wowhead_tooltip_html | TEXT | |

### `guild_identity.item_sources`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| item_id | INTEGER FK→wow_items CASCADE | |
| source_type | VARCHAR(20) CHECK | raid_boss, dungeon, profession, world, pvp, other |
| source_name | VARCHAR(100) NOT NULL | "Ky'veza" or "The Stonevault" |
| source_instance | VARCHAR(100) | "Nerub-ar Palace" or NULL |
| blizzard_encounter_id | INTEGER | |
| blizzard_instance_id | INTEGER | |
| quality_tracks | TEXT[] | {C,H,M} for raid, {C,H} for dungeon |
| UNIQUE | uq_item_source | (item_id, source_type, source_name) |

Synced via `item_source_sync.py` → Blizzard Journal API. Raid bosses → C/H/M tracks; V added in-service for raid boss items (prepended when C present but V absent).

### `guild_identity.hero_talents`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| spec_id | INTEGER FK→specializations CASCADE | |
| name | VARCHAR(100) | "Elune's Chosen" |
| slug | VARCHAR(50) | "elunes_chosen" (for URL building) |
| UNIQUE | (spec_id, name) | |

72 rows seeded in migration 0067 (36 specs × 2). DH includes Devourer (3rd spec, migration 0073/0074).

### `guild_identity.bis_list_sources` — 9 rows, seeded in migration 0066 + updated 0072/0075
| id | name | origin | content_type | is_active |
|----|------|--------|--------------|-----------|
| 10 | u.gg Raid | archon | raid | ✅ |
| 11 | u.gg M+ | archon | mythic_plus | ✅ |
| 12 | u.gg Overall | archon | overall | ✅ |
| 13 | Wowhead Overall | wowhead | overall | ✅ |
| 14 | Wowhead Raid | wowhead | raid | ❌ (deactivated — Wowhead has one page per spec) |
| 15 | Wowhead M+ | wowhead | mythic_plus | ❌ (deactivated) |
| 16 | Icy Veins Raid | icy_veins | raid | ✅ (stubbed — IV out of scope v1) |
| 17 | Icy Veins M+ | icy_veins | mythic_plus | ✅ (stubbed) |
| 18 | Icy Veins Overall | icy_veins | overall | ✅ (stubbed) |

**Important:** `bis_list_sources.guide_site_id` FK → `common.guide_sites` determines `slug_separator` for URL building (`_` for u.gg, `-` for Wowhead/IV). Must be set on all rows after any DB restore.

### `guild_identity.bis_list_entries`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| source_id | INTEGER FK→bis_list_sources CASCADE | |
| spec_id | INTEGER FK→specializations CASCADE | |
| hero_talent_id | INTEGER FK→hero_talents SET NULL | NULL = all builds (Wowhead — migration 0076) |
| slot | VARCHAR(20) | |
| item_id | INTEGER FK→wow_items CASCADE | |
| priority | INTEGER DEFAULT 1 | |
| UNIQUE | (source_id, spec_id, hero_talent_id, slot, item_id) | |

### `guild_identity.character_equipment`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| character_id | INTEGER FK→wow_characters CASCADE | |
| slot | VARCHAR(20) | |
| blizzard_item_id | INTEGER | |
| item_id | INTEGER FK→wow_items SET NULL | lazy-populated |
| item_name | VARCHAR(200) | denormalized |
| item_level | INTEGER | |
| quality_track | VARCHAR(1) | V/C/H/M |
| bonus_ids | INTEGER[] | |
| enchant_id | INTEGER | |
| gem_ids | INTEGER[] | |
| UNIQUE | (character_id, slot) | |

**Note:** SHIRT and TABARD slots are deliberately excluded from `BLIZZARD_SLOT_MAP` in `quality_track.py` — these are cosmetic slots not tracked in character_equipment. The gear plan UI displays them as greyed-out inactive placeholders.

### `guild_identity.gear_plans`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| player_id | INTEGER FK→players CASCADE | |
| character_id | INTEGER FK→wow_characters CASCADE | |
| spec_id | INTEGER FK→specializations | |
| hero_talent_id | INTEGER FK→hero_talents SET NULL | |
| bis_source_id | INTEGER FK→bis_list_sources SET NULL | |
| simc_profile | TEXT | last-imported SimC text verbatim |
| is_active | BOOLEAN DEFAULT TRUE | |
| created_at | TIMESTAMPTZ DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ DEFAULT NOW() | |
| UNIQUE | (player_id, character_id) | |

### `guild_identity.gear_plan_slots`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| plan_id | INTEGER FK→gear_plans CASCADE | |
| slot | VARCHAR(20) | |
| desired_item_id | INTEGER FK→wow_items SET NULL | |
| blizzard_item_id | INTEGER | denormalized |
| item_name | VARCHAR(200) | denormalized |
| is_locked | BOOLEAN DEFAULT FALSE | user-confirmed, auto-sync won't overwrite |
| UNIQUE | (plan_id, slot) | |

### `guild_identity.bis_scrape_targets`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| source_id | INTEGER FK→bis_list_sources CASCADE | |
| spec_id | INTEGER FK→specializations CASCADE | |
| hero_talent_id | INTEGER FK→hero_talents SET NULL | NULL for Wowhead/IV (one page per spec) |
| content_type | VARCHAR(20) | overall, raid, mythic_plus |
| url | TEXT | |
| preferred_technique | VARCHAR(20) | json_embed, wh_gatherer, html_parse, manual, simc |
| status | VARCHAR(20) DEFAULT 'pending' | |
| items_found | INTEGER DEFAULT 0 | |
| last_fetched | TIMESTAMPTZ | |
| area_label | TEXT | discovered tab/section text (IV) |
| UNIQUE | uq_bis_scrape_targets_source_spec_url | **(source_id, spec_id, url)** — NOT 4-col after migration 0071+0077 |

**Constraint history:** Originally `(source_id, spec_id, hero_talent_id, content_type)` in migration 0066. Migration 0071 switched to `(source_id, spec_id, url)` but used the wrong old constraint name — the old one survived. Migration 0077 drops the old constraint by its actual PostgreSQL-generated name and clears stale rows.

### `guild_identity.bis_scrape_log`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| target_id | INTEGER FK→bis_scrape_targets CASCADE | |
| technique | VARCHAR(20) | |
| status | VARCHAR(20) CHECK | success, partial, failed |
| items_found | INTEGER DEFAULT 0 | |
| error_message | TEXT | |
| created_at | TIMESTAMPTZ DEFAULT NOW() | |

---

## BIS Extraction Sources

### u.gg (origin=`archon`) — WORKING
Fetches `window.__SSR_DATA__` JSON; uses direct `stats2.u.gg` data URL.
- URL pattern: `https://u.gg/wow/{spec}/{class}/gear?hero={hero_slug}&role={raid|mythicdungeon}`
- slug_separator = `_` (e.g., `death_knight`)
- One target per spec × hero talent × content_type
- `guide_site_id` on `bis_list_sources` must point to u.gg guide_sites row (slug_sep=`_`)

### Wowhead (origin=`wowhead`) — WORKING
Parses `WH.Gatherer.addData()` JS + `[item=ID]` markup.
- URL pattern: `https://www.wowhead.com/guide/classes/{class}/{spec}/bis-gear`
- slug_separator = `-` (e.g., `death-knight`)
- `hero_talent_id = NULL` — Wowhead has one page per spec, not per HT (migration 0076)
- Wowhead Raid + Wowhead M+ sources are deactivated; only Wowhead Overall is active

### Icy Veins (origin=`icy_veins`) — STUBBED (v1 out of scope)
IV pages are fully JS-rendered. Extraction deferred. Admin matrix shows "Coming Soon" for IV cells.
See `reference/PHASE_Z_ICY_VEINS_SCRAPE-idea-only.md`.

---

## SimulationCraft Integration

SimC profile format is the universal gear artifact (used by Archon, Wowhead, Raidbots).
- `simc_parser.py` — `SimcSlot`/`SimcProfile` dataclasses, parse_profile, parse_gear_slots, export_gear_plan, bonus_ids_to_quality_track
- All BIS extractors return `list[SimcSlot]`
- `gear_plans.simc_profile TEXT` caches last-imported SimC text for round-trip diffing
- Admin: "Import SimC" button → modal to set BIS entries for a spec, logged as `technique='simc'`
- Player: "Import SimC" (paste BIS from Archon) → populates gear_plan_slots; "Export SimC" → download for Raidbots

---

## Item Source Mapping (Phase 1C — COMPLETE)

`item_source_sync.py` — `sync_item_sources(pool, client, expansion_id=None)`:
1. Fetch Journal expansion index → pick highest `id` (latest expansion), or use `expansion_id`
2. Walk `dungeons` + `raids` from expansion data
3. Per instance: `get_journal_instance()` → encounters list
4. Per encounter: `get_journal_encounter()` → item drops
5. Stub `wow_items` (ON CONFLICT DO NOTHING), upsert `item_sources`
6. Raid boss → C/H/M tracks; dungeon → C/H tracks

**4 new BlizzardClient methods** (namespace=`static-us`): `get_journal_expansion_index`, `get_journal_expansion`, `get_journal_instance`, `get_journal_encounter`.

**Admin UI:** "Item Sources — Loot Tables" collapsible on `/admin/gear-plan`. Sync button (GL only), instance/type filters, item table.

---

## Phase 1D: Personal Gear Plan (IN PROGRESS)

### Branch: `feature/gear-plan-phase-1d`
### Migration: 0079 (adds `my_gear_plan` screen permission)

### Files created
| File | Purpose |
|------|---------|
| `alembic/versions/0079_member_gear_plan_nav.py` | Screen permission for `/gear-plan` nav entry |
| `src/guild_portal/services/gear_plan_service.py` | Plan CRUD, BIS population, upgrade computation |
| `src/guild_portal/api/gear_plan_routes.py` | Member gear plan API + per-character equipment sync |
| `src/guild_portal/pages/gear_plan_pages.py` | Page routes: `/gear-plan` (member) + `/admin/gear-plan` (admin) |
| `src/guild_portal/templates/member/gear_plan.html` | Paperdoll UI template |
| `src/guild_portal/static/css/gear_plan.css` | Paperdoll styles |
| `src/guild_portal/static/js/gear_plan.js` | Client interactions |
| `tests/unit/test_gear_plan_service.py` | 20 unit tests |

### API endpoints
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/me/gear-plan/{character_id}` | Full plan detail: equipped + desired + BIS + upgrade tracks per slot |
| POST | `/api/v1/me/gear-plan/{character_id}` | Create or retrieve plan |
| PATCH | `/api/v1/me/gear-plan/{character_id}/config` | Update spec / hero talent / BIS source |
| PUT | `/api/v1/me/gear-plan/{character_id}/slot/{slot}` | Set desired item for a slot |
| POST | `/api/v1/me/gear-plan/{character_id}/populate` | Fill unlocked slots from BIS source |
| DELETE | `/api/v1/me/gear-plan/{character_id}` | Reset plan |
| POST | `/api/v1/me/gear-plan/{character_id}/import-simc` | Paste SimC → populate slots |
| GET | `/api/v1/me/gear-plan/{character_id}/export-simc` | Download `.simc` file |
| POST | `/api/v1/me/gear-plan/{character_id}/sync-equipment` | Sync equipped gear from Blizzard for this character |
| GET | `/api/v1/items/{blizzard_item_id}` | Fetch/cache item metadata from Wowhead |

### UI: `/gear-plan` (paperdoll layout)

**Layout** — three-column grid (`200px 1fr 200px`, `align-items: stretch`):

| Left column (8 slots) | Centre panel | Right column (8 slots) |
|---|---|---|
| Head | Char badge (name · spec · realm) | Hands |
| Neck | Hero Talent dropdown | Waist |
| Shoulder | BIS Source dropdown | Legs |
| Back | Fill BIS / Sync Gear / Import SimC / Export SimC / Reset Plan buttons | Feet |
| Chest | Status bar | Ring 1 |
| Shirt *(inactive — cosmetic)* | **Main Hand + Off Hand** (bottom, `margin-top: auto`) | Ring 2 |
| Tabard *(inactive — cosmetic)* | | Trinket 1 |
| Wrist | | Trinket 2 |

**Slot cards:**
- Equipped item: icon + name (coloured by quality track: V=green, C=blue, H=purple, M=orange) + ilvl + quality badge
- Goal item row: small icon + name when desired ≠ equipped
- Upgrade track row: coloured pills (V/C/H/M) showing which tracks would be upgrades
- Green left border = already BIS; Red left border = needs upgrade
- Shirt + Tabard slots are `is-inactive` (opacity 0.45, not clickable) — cosmetic only, no BIS/upgrade logic

**Slot drawer** (expands below paperdoll on click):
- Equipped: icon (quality-coloured border), name (quality-coloured), ilvl, enchant
- BIS Recommendations: one row per source → "Use" button sets as goal
- Your Goal: selected item + lock/unlock + clear; Manual Lookup by item ID
- Drop Location: boss/dungeon name, available quality tracks, which are upgrade tracks

**Character selector:** defaults to player's main character on load.

**Sync Gear button:** calls `POST /gear-plan/{id}/sync-equipment` which:
1. Uses the scheduler's BlizzardClient if available (scheduler running)
2. Falls back to a per-request `BlizzardClient` created from `BLIZZARD_CLIENT_ID`/`BLIZZARD_CLIENT_SECRET` env vars if the scheduler isn't running (e.g. dev without audit channel configured)

### Known issues / bugs fixed during 1D

| Bug | Fix |
|-----|-----|
| `bonus_list` from Blizzard API is `list[int]`, not `list[dict]` | `blizzard_client.py` line 381: `[b.get("id",0) for b in ...]` → `item.get("bonus_list") or []` |
| `btn--primary` / `btn--sm` BEM classes used throughout | Changed to `btn-primary` / `btn-sm` to match `main.css` definitions |
| SimC modal visible on page load | `.gp-modal[hidden] { display: none !important }` in gear_plan.css |
| `chars.filter is not a function` | `/api/v1/me/characters` returns `resp.data.characters` (nested), not `resp.data` |
| Sync Gear called wrong endpoint | Was `/api/v1/me/refresh` → now `sync-equipment` endpoint |
| V track missing from upgrade display | `_RAID_TRACKS` in `item_source_sync.py` now includes V; service-layer also prepends V for raid_boss items lacking it |
| Guild sync scheduler skipped on dev | Scheduler requires audit_channel_id; sync-equipment endpoint bypasses scheduler entirely using env var credentials directly |
| Lock clears slot instead of locking | `update_slot` was deleting when `blizzard_item_id=None` regardless of `is_locked`; added lock-only path |
| Use button broken for items with apostrophe in name | BIS row onclick passed item_name as JS string literal — broken by `Ky'veza's Ring` etc.; dropped item_name from onclick, service resolves from wow_items |
| V recommended for non-Veteran equipped item | `_upgrade_tracks` returned all tracks when equipped_track=None (undetected); now returns [] when item is equipped but track unknown |
| Blank slots (no border) for non-BIS items without track data | `needs_upgrade` was `bool(upgrade_tracks)` — now `bool(desired_bid and not is_bis)`; red border fires whenever a goal exists but isn't worn |
| Icons missing for non-BIS equipped items | Items not in `wow_items` have no icon_url; equipment_sync now stubs `wow_items` rows; JS lazy-fetches icon via `/api/v1/items/{id}` |
| Icon quality glow not visible | `overflow:hidden` on card was clipping box-shadow; changed to inset box-shadow; removed overflow:hidden from card |

### Open issues / resolved decisions (as of 2026-04-08)

**Icon quality colour / quality_track detection (Midnight expansion)** — OPEN, external dependency
- `display_string` regex `^(Veteran|Champion|Hero|Mythic)\s+\d+/\d+$` may not match Midnight format
- TWW S2 bonus ID map is season-specific; Midnight uses different bonus IDs
- Icon coloring IS wired up — just needs correct detection data
- **Pending:** Pull a Midnight character equipment API response and confirm display_string format + supply bonus ID map when available. Not blocking other work.

**Crafted item quality track** — → See Phase 1D.3

**Tier set items** — → See Phase 1D.1 (source display fix only; BIS + upgrade logic confirmed working)

**Veteran (V) track exclusion** — ✅ DECISION MADE: Keep V in upgrade recommendations. LFR is a valid source for players filling gaps.

**Upgrade track recommendations** — ✅ CLOSED. Logic is correct. Will verify end-to-end once quality_track detection (Midnight) is resolved.

**Hover tooltip** — ✅ RESOLVED

**Ring/Trinket BIS sort** — ✅ RESOLVED

**Slot card hover state** — ✅ NOT AN ISSUE

**Slot drawer — BIS Recommendations grid redesign** — DEFERRED. Current flat list is functional. Revisit post-1E.

---

## Phase 1D.1: Small Fixes Bundle

### Fix 1 — Tier set source display ✅ COMPLETE

**Solution:** `enrich_catalyst_tier_items()` in `item_source_sync.py`. Tier pieces arrive from the Journal under catalyst-converted IDs, not the base drop IDs. The enrichment step queries `item_sources` for all raid/world_boss boss rows in the current expansion, matches tier slots (head/shoulder/chest/hands/legs) by armor type, and upserts matching source rows for the catalyst tier item IDs. Runs automatically at the end of each "Sync Loot Tables" call.

### Fix 2 — M+ dungeon items eligible for Mythic track (Great Vault) ✅ COMPLETE

**Change:** `item_source_sync.py` — `_DUNGEON_TRACKS` changed from `["C", "H"]` to `["C", "H", "M"]`. Season 1 thresholds documented in a comment (Hero=6+/4+ vault, Mythic=10+ vault). Existing rows update on next "Sync Loot Tables" run.

### Fix 3 — Gear table row click → slot detail panel ✅ COMPLETE

**Change:** `my_characters.js` `_gpRenderGearTable()` — added `onclick="_gpSelectSlotInCenter('${slotKey}')"` and `style="cursor:pointer"` to each `<tr>`.

---

## Phase 1D.2: Enhanced Source Display

**Purpose:** The gear table "Source" column currently shows "Boss • Instance" in a single line. Expand it to three lines: Instance / Boss / Minimum Level. This makes it immediately clear where to go and what difficulty/key is needed.

### Display format

**Raid item:**
```
March on Quel'Danas    ← source_instance
Midnight Falls          ← source_name (boss)
Normal+                 ← lowest difficulty where the item drops (C present → Normal)
```

**M+ dungeon item:**
```
Magisters' Terrace     ← source_instance (dungeon name)
Gemellus               ← source_name (boss, if applicable; else omit)
4+ Key                 ← minimum key for a meaningful track (see below)
```

**No source (crafted, world, other):** Handled by Phase 1D.3 or show "—".

### Minimum level derivation

**Raid:** derive from `quality_tracks` on the `item_sources` row:
- `C` present → "Normal+"
- `H` is lowest → "Heroic+"
- `M` is lowest → "Mythic+"

**M+ dungeon:** use key thresholds from `patt.raid_seasons` (added in Phase 1D.1 if column approach chosen):
- Show the threshold for the lowest track that is an upgrade for this player — or, if showing generically, show "4+ Key" (hero vault, the most accessible meaningful step for most players).
- If the player already has Hero track, show "10+ Key (Vault)" for the M track option.

Context-aware display (show the relevant minimum for the player's current gear) is ideal but optional for initial ship — generic minimum is acceptable.

### API changes

`get_plan_detail` in `gear_plan_service.py` already returns source info per slot. Extend it to include:
```python
"source_min_level": "Normal+"   # or "4+ Key", "Heroic+", etc.
```
This can be computed server-side from `quality_tracks` + season config.

### Files changed
- `src/guild_portal/services/gear_plan_service.py` — compute `source_min_level` per slot
- `src/guild_portal/api/gear_plan_routes.py` — expose in response
- `src/guild_portal/static/js/my_characters.js` — update `renderGearTable` cell render
- `src/guild_portal/static/css/my_characters.css` — three-line source cell styles
- `patt.raid_seasons` migration (if column approach used in 1D.1)

**Done when:** Each row in the gear table Source column shows three lines (instance / boss / min level) for raid and M+ items.

---

## Phase 1D.3: Crafted Items

**Purpose:** Crafted items currently show a "Crafted" badge but have no quality track (upgrade pills empty) and no source information. This phase fully wires up crafted items: detect H vs M track from the crafting crest used, show who in the guild can make the item, and link to the Crafting Corner for ordering.

This is the largest standalone fix — it touches quality detection, service layer, and UI. Own phase, own branch, own session.

### Part A — Crafted item quality track detection

**Goal:** Determine whether a crafted item is Hero-track or Mythic-track equivalent.

**Preferred method — crest bonus ID detection:**

Crafted items in WoW use specific bonus IDs to encode the quality of crest used. In TWW: Resonant Crests = Hero tier, Gilded Crests = Mythic tier. Midnight will have equivalent crests with new bonus IDs.

Steps:
1. Pull a Midnight crafted item from the Blizzard equipment endpoint. Inspect `bonus_ids` on the item.
2. Identify which bonus IDs correspond to Hero-crest crafting vs Mythic-crest crafting.
3. Add these to `quality_track.py` — either as a `_CRAFTED_BONUS_IDS` dict (mapping bonus_id → track letter H/M) or by updating `_DEFAULT_SIMC_BONUS_IDS` with the crafted-specific IDs.
4. Update `_detect_quality_track()` (or wherever track detection runs) to check crafted bonus IDs before falling back to null.

**Fallback method — admin-configured ilvl threshold:**

If crest bonus IDs are not identifiable from the API (or for early Midnight before the meta is established):
- Migration: add `crafted_m_ilvl_threshold INT` to `site_config` (nullable; null = use bonus ID method only).
- Admin → Site Config: expose this field with a label like "Crafted M-track minimum ilvl".
- `quality_track.py`: if `is_crafted` and `quality_track` still null after bonus ID check, compare `item_level` to threshold → H or M.

**Detection for `is_crafted`:** Currently uses bonus_id 1808. Confirm this is still the Midnight crafted marker, or find the new one.

### Part B — Crafted item source display

Crafted items have no `item_sources` row (source_type = 'profession' is not currently synced by `item_source_sync.py`). Rather than adding them to item_sources, handle them as a special case in the gear plan service.

**In `get_plan_detail`:**
- Detect `is_crafted` on the desired item (check `character_equipment.bonus_ids` or a flag on `wow_items`).
- If crafted: skip the normal source lookup; instead return a `crafted_source` block:
  ```python
  "crafted_source": {
      "track": "H",        # or "M" — derived from Part A
      "crafter_count": 3,  # guild members who can make this item
      "crafters": [        # list of player display names
          {"player_name": "Trogmoon", "character_name": "Trogmoon"},
          ...
      ],
      "crafting_corner_item_id": 12345  # blizzard_item_id for Crafting Corner link
  }
  ```
- **Crafter lookup query:** join `character_recipes` → `recipes` → `wow_items` on `blizzard_item_id`. Filter to `guild_identity.wow_characters.in_guild = TRUE` and linked to an active player. Return player display names.

### Part C — UI changes

**Gear table Source column:**
- If `crafted_source` present: show "Crafted Item" + "H-Crest" or "M-Crest" (two lines). Replace min level with crafter count: "3 crafters →" as a link opening the crafter popup.

**Slot detail panel (paperdoll click or table row click):**
- New "Crafters" section below the BIS grid (only visible when desired item is crafted).
- Shows a small list of guild members who can make it, with their character names.
- "Order in Crafting Corner" link — links to `/crafting-corner` with a query string or anchor pointing to the relevant recipe. (If Crafting Corner doesn't support deep links today, link to `/crafting-corner` plain for now with a TODO.)

### Migration

If fallback ilvl threshold is implemented:
- `alembic/versions/0082_crafted_m_ilvl_threshold.py` — add `crafted_m_ilvl_threshold INT` to `common.site_config`.

### Files changed
- `src/sv_common/guild_sync/quality_track.py` — crafted track detection
- `src/guild_portal/services/gear_plan_service.py` — crafted_source block in get_plan_detail
- `src/guild_portal/api/gear_plan_routes.py` — pass through crafted_source
- `src/guild_portal/static/js/my_characters.js` — crafted source display + crafter popup
- `src/guild_portal/static/css/my_characters.css` — crafted source styles
- `alembic/versions/0082_crafted_m_ilvl_threshold.py` (if fallback approach used)
- Admin Site Config template (if fallback approach used)

**Done when:** A crafted desired item shows H or M track, upgrade pills work, crafter count shows in the table, crafter popup opens from slot detail with guild member names, Crafting Corner link present.

---

## Phase 1D.4: Loot Table Data Quality — Junk Source Flagging

**Purpose:** The Blizzard Journal API includes item source rows that are incorrect or stale alpha/beta data. Specifically: tier gear pieces are listed as dropping directly from raid bosses and world bosses, when in reality no boss drops tier gear directly — they drop tier tokens. Additionally, some world boss entries have null encounter/instance IDs, indicating placeholder data that was never cleaned up. This phase adds a flag to mark suspected junk rows so they are stored (ELT — raw data stays) but silently excluded from gear plan display.

### What gets flagged

Two categories of junk, applied by a post-processing step:

1. **Null-ID world boss rows** — `item_sources` rows where `source_type = 'world_boss'` AND both `blizzard_encounter_id IS NULL` AND `blizzard_instance_id IS NULL`. These have no valid Blizzard encounter reference and are likely alpha/beta artifacts.

2. **Tier gear piece direct-source rows** — `item_sources` rows where the linked `wow_items` entry has set bonus data in its `wowhead_tooltip_html` (i.e., it is a tier gear piece, not a token). Tier pieces do not drop directly from bosses — they are obtained by exchanging tier tokens. These rows are wrong by definition and should not drive gear plan source display.

### Schema change

**Migration 0084** — add column to `item_sources`:

```sql
ALTER TABLE guild_identity.item_sources
    ADD COLUMN is_suspected_junk BOOLEAN NOT NULL DEFAULT FALSE;
```

No data changes in the migration itself — flagging runs via the post-processor (Phase 1D.5).

### Behavior

- All gear plan display queries (`v_tier_piece_sources`, `get_plan_detail`, `item_source_sync` reads) filter `WHERE NOT is_suspected_junk`.
- Junk rows are never deleted. If a flag is wrong, an admin can clear it manually in the Item Sources table on the BIS admin page.
- The BIS admin Item Sources collapsible gains a **Show Junk** toggle (default off) to make flagged rows visible for inspection.

### Files changed

- `alembic/versions/0084_item_sources_junk_flag.py`
- `src/sv_common/guild_sync/item_source_sync.py` — add junk filter to any reads that feed display logic
- `src/guild_portal/templates/admin/gear_plan.html` — Show Junk toggle in Item Sources collapsible
- `src/guild_portal/api/bis_routes.py` — respect junk filter in item sources API endpoint

**Done when:** Junk rows exist in `item_sources` with `is_suspected_junk = TRUE` (populated by Phase 1D.5 processor), gear plan display never shows them, and the BIS admin Show Junk toggle reveals them for manual review.

---

## Phase 1D.5: Tier Token Pipeline

**Purpose:** Bosses don't drop tier gear — they drop tier tokens. Players exchange tokens for the tier piece appropriate to their class and spec. The current gear plan has no model for this two-hop chain. This phase introduces a `tier_token_attrs` table (auto-populated from tooltip HTML), a view that resolves tier piece → token → boss, and wires the gear plan service to use it. It also runs the junk flagging defined in Phase 1D.4.

### How tier tokens work in Midnight Season 1

| Token name | Armor type | Boss | Instance | Slot granted |
|---|---|---|---|---|
| Aln**woven** Riftbloom | Cloth | Chimaerus the Undreamt God | The Dreamrift | Chest |
| Aln**cured** Riftbloom | Leather | Chimaerus the Undreamt God | The Dreamrift | Chest |
| Aln**cast** Riftbloom | Mail | Chimaerus the Undreamt God | The Dreamrift | Chest |
| Aln**forged** Riftbloom | Plate | Chimaerus the Undreamt God | The Dreamrift | Chest |
| Void[type] **Hungering** Nullcore | Per type | Vorasius | The Voidspire | Hands |
| Void[type] **Unraveled** Nullcore | Per type | Fallen-King Salhadaar | The Voidspire | Shoulder |
| Void[type] **Corrupted** Nullcore | Per type | Vaelgor & Ezzorak | The Voidspire | Legs |
| Void[type] **Fanatical** Nullcore | Per type | Lightblinded Vanguard | The Voidspire | Helm |
| Chiming Void Curio | Any | Midnight Falls (L'ura) | March on Quel'Danas | Any |

All token items already exist in `wow_items` with `slot_type = 'other'` and correct `item_sources` entries. Their `wowhead_tooltip_html` contains the data needed for auto-detection (see below).

### Auto-detection from tooltip HTML

Two patterns cover all cases:

**Slot** — the Use effect text contains the slot directly:
```
Use: Synthesize a soulbound set hand item appropriate for your class.
Use: Synthesize a soulbound set chest item appropriate for your class.
```
Regex: `r"soulbound set (\w+) item"` → normalize result (`hand` → `hands`).

**Eligible class IDs** — standard Wowhead Classes div:
```html
<div class="wowhead-tooltip-item-classes">
  Classes: <a href="/class=5/priest">Priest</a>, <a href="/class=8/mage">Mage</a> ...
</div>
```
Parse href class IDs → derive armor type from class (Priest/Mage/Warlock = cloth, etc.).

**"Any" wildcard (Chiming Void Curio)** — no Classes div, no slot word in Use text. Detected by absence of both patterns.

### New table: `guild_identity.tier_token_attrs`

**Migration 0085**

| Column | Type | Notes |
|---|---|---|
| `token_item_id` | INTEGER PK FK → wow_items | One row per token |
| `target_slot` | VARCHAR(20) | `chest / head / shoulder / hands / legs / any` |
| `armor_type` | VARCHAR(20) | `cloth / leather / mail / plate / any` |
| `eligible_class_ids` | INTEGER[] | Parsed from tooltip Classes div |
| `is_auto_detected` | BOOLEAN DEFAULT TRUE | False if row was manually created |
| `is_manual_override` | BOOLEAN DEFAULT FALSE | Set to TRUE when an admin edits the row; processor skips these |
| `override_notes` | TEXT | Admin free-text — why this was corrected |
| `last_processed` | TIMESTAMPTZ | When the processor last touched this row |

### Post-processor: `process_tier_tokens()`

New function in `item_source_sync.py` (or a new `tier_token_processor.py`):

1. Find all `wow_items` where `slot_type = 'other'` AND `wowhead_tooltip_html` contains `"Synthesize a soulbound set"` OR `"trade this for powerful class set armor"` — these are tier tokens.
2. For each token:
   - Parse `target_slot` from Use effect text (or `'any'` if absent).
   - Parse `eligible_class_ids` from Classes div (or empty = all classes if absent).
   - Derive `armor_type` from class IDs via the `guild_identity.classes` table (each class has a known armor type); use `'any'` if no class restriction.
   - Upsert into `tier_token_attrs`. **Skip rows where `is_manual_override = TRUE`** — never clobber manual edits.
3. Run junk flagging (Phase 1D.4 criteria) — update `item_sources.is_suspected_junk`.
4. Return a summary dict: `{tokens_processed, tokens_skipped_override, junk_flagged}`.

### New view: `v_tier_piece_sources`

```sql
CREATE OR REPLACE VIEW guild_identity.v_tier_piece_sources AS
SELECT
    tp.id           AS tier_piece_id,
    tp.name         AS tier_piece_name,
    tp.slot_type,
    tk.id           AS token_item_id,
    tk.name         AS token_name,
    tk.blizzard_item_id AS token_blizzard_id,
    is2.source_name     AS boss_name,
    is2.source_instance AS instance_name,
    is2.blizzard_encounter_id,
    is2.blizzard_instance_id,
    is2.quality_tracks
FROM guild_identity.wow_items tp
JOIN guild_identity.tier_token_attrs tta
    ON (tta.target_slot = tp.slot_type OR tta.target_slot = 'any')
   AND (tp.armor_type = tta.armor_type   OR tta.armor_type = 'any')
JOIN guild_identity.wow_items tk
    ON tk.id = tta.token_item_id
JOIN guild_identity.item_sources is2
    ON is2.item_id = tk.id
   AND NOT is2.is_suspected_junk
WHERE tp.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs');
```

### Gear plan service changes

`get_plan_detail()` in `gear_plan_service.py`:
- Detect if the desired item for a slot is a tier piece (tooltip contains set bonus markup, OR a future `is_tier_piece BOOLEAN` flag on `wow_items` if we want to make it explicit).
- If tier piece: query `v_tier_piece_sources` filtered by `tier_piece_id` to get boss/instance/tracks.
- If not tier piece: existing `item_sources` lookup unchanged.
- The source block returned to the UI is the same shape either way — boss name, instance name, quality tracks — so no UI changes are needed beyond the data being correct.

### Files changed

- `alembic/versions/0084_item_sources_junk_flag.py` (Phase 1D.4)
- `alembic/versions/0085_tier_token_attrs.py`
- `src/sv_common/guild_sync/item_source_sync.py` (or new `tier_token_processor.py`) — `process_tier_tokens()`
- `src/sv_common/db/models.py` — `TierTokenAttrs` ORM model
- `src/guild_portal/services/gear_plan_service.py` — tier piece source lookup via view
- `src/guild_portal/api/bis_routes.py` — new endpoint `POST /api/v1/admin/bis/process-tier-tokens`
- `src/guild_portal/templates/admin/gear_plan.html` — Process Tier Tokens button (Phase 1D.6)
- `src/guild_portal/templates/admin/reference_tables.html` — Tier Tokens section (Phase 1D.6)
- `tests/unit/test_tier_token_processor.py` — unit tests for tooltip parsing + junk detection

**Done when:** A tier piece in the gear plan shows the correct boss and instance (via its token's source), not a stale direct-source row. The processor can be triggered from the BIS admin page. `tier_token_attrs` rows are visible and editable in Reference Tables.

---

## Phase 1D.6: BIS Admin Page Restructure

**Purpose:** The current BIS admin controls bar is a single flat row of 8+ mixed buttons with no logical order. This phase reorganises the top of the page into clearly labelled workflow steps (top-to-bottom execution order) and surfaces the new Tier Token processor from Phase 1D.5. The Item Sources collapsible gains a junk data toggle.

### New controls layout — five workflow steps

Replace the single `.gp-controls` bar with five labelled step groups. Each group has a heading, one-line instruction, and its action controls.

---

**Step 1 — Sync Loot Tables**
> *Pull boss/item data from the Blizzard Journal API. Run once per season after new content launches, or after a content patch adds new encounters.*

→ `Sync Loot Tables` button (GL only)

---

**Step 2 — Enrich Items**
> *Fetch Wowhead tooltips for any items that were stubbed without slot or tooltip data (slot_type = 'other'). Required before Step 3.*

→ `Enrich Items` button (already exists — surfaced here explicitly)

---

**Step 3 — Process Tier Tokens**
> *Parse enriched token tooltips to detect slot and eligible classes. Populates the tier token translation table and flags suspected junk sources. Safe to re-run.*

→ `Process Tier Tokens` button (new — calls `POST /api/v1/admin/bis/process-tier-tokens`)
→ Last-run line: "Last run: [timestamp] — [N] tokens detected, [N] junk rows flagged, [N] overrides skipped"

---

**Step 4 — Sync BIS Lists**
> *Scrape u.gg and Wowhead BIS recommendations for all specs. Discover URLs first if this is a fresh season. Re-sync Errors retries only failed targets.*

→ Website dropdown + Plan Type dropdown
→ `Discover URLs` · `Sync Source` · `Sync All` · `Re-sync Errors`

---

**Step 5 — Manual Import**
> *Paste a SimulationCraft profile to set BIS entries for a specific spec manually.*

→ `Import SimC` button

---

### Item Sources collapsible changes

- Add **Show Junk** toggle (checkbox, default off). When on, junk-flagged rows appear with a muted strikethrough style and a "Junk" badge. When off, they are hidden.
- Add a count line above the table: "N sources — N junk hidden" (or "N junk shown") so the admin always knows how many are filtered.

### Files changed

- `src/guild_portal/templates/admin/gear_plan.html` — full controls section rewrite + Show Junk toggle
- `src/guild_portal/templates/admin/reference_tables.html` — new **Tier Tokens** section:
  - Table: Token Name · Slot · Armor Type · Eligible Classes · Auto-detected · Override · Notes
  - Inline-editable rows for `target_slot`, `armor_type`, `override_notes` (same pattern as Guide Sites section)
  - Saving a row sets `is_manual_override = TRUE` automatically
  - Read-only "Last processed" timestamp badge per row
- `src/guild_portal/static/js/gear_plan.js` — Step 3 button handler + last-run status display; Show Junk toggle filter
- `src/guild_portal/static/css/gear_plan.css` — step-group card styles; junk row styles

**Done when:** The BIS admin page top section reads as a clear 5-step workflow. Tier Tokens table is visible and editable in Reference Tables. Item Sources shows/hides junk rows via toggle with a count indicator.

---

## Phase 1E: Roster Aggregation (TODO after 1D)

Admin gear plan page gains a **Roster Needs** section answering: *"Which bosses/dungeons should we prioritize for loot this week?"*

**Design reference:** `prototypes/roster_needs.html` — fully interactive mockup with hardcoded data. Review this before building.

### How "needs" is defined
Same logic as My Characters (`gear_plan_service.py:1046`):
- A slot **needs** an item if `desired_item_id` is set AND the player is not wearing that exact item (`not is_bis`)
- Which quality tracks are needed comes from `upgrade_tracks` — tracks strictly above what they're currently wearing for that item
- If a player needs an item from a boss at Hero but already has it at Champion, they appear in the H column only

### Filters
- **Include Initiates** — default ON; maps to `rank_level === 1` (same as roster page)
- **Include Offspec Characters** — default OFF; uses each player's `secondary_character`; counted as "Playername — Charname" distinct from their main

### Table format
- **Rows (Raid):** Instance (collapsible) → Bosses under each instance
- **Rows (M+):** Flat dungeon list, no boss breakdown
- **Columns (Raid):** V / C / H / M quality tracks — auto-hide any column where no player has needs
- **Columns (M+):** C / H only — no Myth column (M+ Myth track = vault only, reserved for Phase 3)
- **Cell value:** `players | items` — e.g. `3 | 4` = 3 unique players, 4 total slot needs
- **Color scale:** 1–2 = green · 3–5 = gold · 6+ = red (applied to player count)
- Empty cells render blank (no zero)
- Instance rows show rollup aggregates across all their bosses

### Detail drill panel
Click any cell → side panel slides in from the right showing who needs what.
- **By Item view (default):** Each item is a hub card (icon + Wowhead tooltip link + slot label). Player names branch off as spoke rows with class-colored `Playername — Charname` + spec.
- **By Player view:** Each player is a hub card (class-color left border + `Playername — Charname` + spec). Items branch off as spoke rows with icon + Wowhead tooltip link + slot label.
- Toggle between views without closing the panel
- Wowhead tooltip JS fires on item links in the panel
- Panel is drillable at any level: individual boss, entire instance, or dungeon

---

## Phase 1E.1: Backend + Main Table

**Scope:** New admin-only endpoints + the hierarchical table with expand/collapse and filters. No drill panel yet.

New endpoints (admin-only, Officer+):
- `GET /api/v1/admin/gear-needs/raid` — returns needs aggregated by instance → boss → track
- `GET /api/v1/admin/gear-needs/dungeon` — returns needs aggregated by dungeon → track

**Response shape (raid):**
```json
{
  "instances": [
    {
      "key": "lou",
      "name": "Liberation of Undermine",
      "bosses": [
        {
          "key": "vexie",
          "name": "Vexie and the Geargrinders",
          "tracks": {
            "H": { "players": ["shadowtrog", "zulvash", "rocket"], "items": 4 },
            "M": { "players": ["mikenator"], "items": 1 }
          }
        }
      ]
    }
  ]
}
```

**Backend logic:**
1. Load all active players with a `main_character_id` set
2. For each player's active gear plan, find slots where `desired_item_id IS NOT NULL`
3. Compare desired vs equipped (same `is_bis` / `upgrade_tracks` logic as `gear_plan_service.py`) to determine if the slot is still needed and at which tracks
4. Join desired item → `item_sources` to find boss/dungeon source
5. Group by (source, track) → sets of player keys + item counts
6. Apply filters: initiates (`rank_level`), offspec (secondary char plans)

**Frontend:**
- Table rendered client-side from endpoint data
- Expand/collapse instances (state in memory, no persistence needed)
- Auto-hide track columns with 0 needs across the entire table
- Filter checkboxes re-fetch or re-filter client-side (TBD based on response size)
- Active chip outline when panel is open on that cell

**Done when:** Both tables render correctly, expand/collapse works, column auto-hide works, filters work, color scale correct.

---

## Phase 1E.2: Detail Drill Panel

**Scope:** The slide-in panel with By Item / By Player toggle and Wowhead tooltips. No new backend endpoints — uses data already returned by 1E.1 endpoints (extended to include player display names and item details).

**Panel behaviour:**
- Slides in from right (CSS transition), main table remains visible
- Click any cell at any level (boss, instance, dungeon) to open
- Clicked cell gets a gold outline (`active-chip` class)
- Close button (✕) clears the outline and closes panel
- Panel re-renders on filter changes if already open

**By Item view:**
- Group needs by `item_id`, render one hub card per item
- Hub card: item icon (Wowhead CDN) + item name as Wowhead link + slot label
- Spoke rows: `Playername — Charname` (class-colored) + spec + class

**By Player view:**
- Group needs by player, render one hub card per player
- Hub card: `Playername — Charname` (class-colored) + spec, left border tinted to class color
- Spoke rows: item icon + item name as Wowhead link + slot label

**Wowhead tooltip integration:**
- Include `<script>const whTooltips={colorLinks:true,iconizeLinks:false};</script>` before `wow.zamimg.com/widgets/power.js`
- Call `$WowheadPower.refreshLinks()` after each panel render

**Done when:** Panel opens/closes correctly, both views render, Wowhead tooltips fire on item links, filter changes update an open panel.

---

## Phase 1E.3: Auto-Setup for New Guild Members

**Scope:** When a new character is first discovered as `in_guild=TRUE` during Blizzard/equipment sync, automatically create a default gear plan for them so they appear in Roster Needs immediately.

**Hook location:** `src/sv_common/guild_sync/equipment_sync.py` — after a new character row is upserted with `in_guild=TRUE` and `player_characters` link exists, call `get_or_create_plan(pool, player_id, character_id)` then `populate_from_bis(pool, player_id, character_id, source_id=wowhead_source_id)`.

**Existing art:** The admin "Fill BIS" button at `bis_routes.py:823` already does this for all characters in bulk. 1E.3 adds the same call inline during the per-character sync loop so new members don't need a manual admin trigger.

**Done when:** A newly-linked in-guild character gets a default Wowhead Overall BIS plan created automatically on the next equipment sync run.

---

## Full File Inventory

### New files (all already created)
| File | Phase |
|------|-------|
| `alembic/versions/0066_gear_plan.py` | 1A |
| `alembic/versions/0067_hero_talents.py` | 1B |
| `alembic/versions/0068–0077_*.py` | 1B/1C fixes |
| `alembic/versions/0079_member_gear_plan_nav.py` | 1D |
| `src/sv_common/guild_sync/quality_track.py` | 1A |
| `src/sv_common/guild_sync/equipment_sync.py` | 1A |
| `src/sv_common/guild_sync/bis_sync.py` | 1B |
| `src/sv_common/guild_sync/simc_parser.py` | 1B |
| `src/sv_common/guild_sync/item_source_sync.py` | 1C |
| `src/guild_portal/services/item_service.py` | 1A |
| `src/guild_portal/services/gear_plan_service.py` | 1D |
| `src/guild_portal/api/bis_routes.py` | 1B |
| `src/guild_portal/api/gear_plan_routes.py` | 1D |
| `src/guild_portal/pages/gear_plan_pages.py` | 1D |
| `src/guild_portal/templates/admin/gear_plan.html` | 1B |
| `src/guild_portal/templates/member/gear_plan.html` | 1D |
| `src/guild_portal/static/css/gear_plan.css` | 1D |
| `src/guild_portal/static/js/gear_plan.js` | 1D |
| `tests/unit/test_item_source_sync.py` | 1C |
| `tests/unit/test_gear_plan_service.py` | 1D |

### Modified files
| File | Change |
|------|--------|
| `src/sv_common/db/models.py` | 10 new model classes + `last_equipment_sync` on WowCharacter |
| `src/sv_common/guild_sync/blizzard_client.py` | `get_character_equipment()` + 4 Journal API methods; `bonus_list` int fix |
| `src/sv_common/guild_sync/scheduler.py` | Equipment sync step in `run_blizzard_sync()` |
| `src/sv_common/guild_sync/item_source_sync.py` | `_RAID_TRACKS` now includes V |
| `src/guild_portal/app.py` | Include bis_routes + gear_plan routers |
| `src/guild_portal/pages/admin_pages.py` | `gear_plan` screen entry + nav item |
| `src/guild_portal/templates/base.html` | Gear Plan nav link for logged-in members |

---

## Known Gotchas

- **TRUNCATE CASCADE danger**: `guild_ranks → players → gear_plans`; `bis_list_sources → bis_list_entries`; always `pg_dump` before destructive ops on dev
- **bis_list_sources guide_site_id**: Must be set after any DB restore — drives `slug_separator` for URL building. u.gg needs guide_sites row with `slug_sep='_'`; Wowhead/IV need `slug_sep='-'`
- **bis_scrape_targets constraint**: Was `(source_id, spec_id, hero_talent_id, content_type)` → changed to `(source_id, spec_id, url)` in migration 0071 (but 0071 used wrong old constraint name). Migration 0077 fixes the orphaned old constraint.
- **Wowhead targets `hero_talent_id=NULL`**: One target per spec, not per HT. Migration 0076 set all existing rows to NULL and fixed discover_targets to skip the HT loop for wowhead origin.
- **Icy Veins**: Out of scope for v1 — pages are fully JS-rendered. Sources exist in DB but show as "Coming Soon" in matrix.
- **Devourer DH spec**: 3rd DH spec added in migration 0073/0074. May 404 on u.gg if Midnight spec not yet published there — expected.
- **Migration conflict (0066 duplicate)**: Hotfix `0066_raid_boss_counts` from main and feature `0066_gear_plan` both claimed revision="0066". Resolved by renaming hotfix to `0078_raid_boss_counts.py` with `down_revision="0077"` and `CREATE TABLE IF NOT EXISTS` (prod already had the table).
- **Alembic drift on test/prod**: After merge, test DB had 0067–0071 DDL applied but alembic stuck at "0066"; prod was at old "0066" (raid_boss_counts). Fixed by stamping alembic directly: `UPDATE patt.alembic_version SET version_num = 'XXXX'` then re-deploying.
- **Wowhead `slotbak` removed**: Wowhead nether tooltip API silently dropped `slotbak` field. `item_service.py` now parses slot from tooltip HTML via `_slot_from_tooltip()`. Existing `slot_type='other'` rows fixed by re-running Sync Loot Tables.
- **u.gg rate limiting (Hillsboro OR prod IP)**: migration 0077 clears all `bis_scrape_targets` on deploy. Bulk fresh re-sync on prod triggered 403s from u.gg for ~69 healer/tank targets. Use "Re-sync Errors" button after rate limit clears. Dev IP (Falkenstein) was not affected.
- **Scheduler skipped on dev**: `GuildSyncScheduler` requires `audit_channel_id` to be set in `discord_config`. Dev typically has no audit channel configured → scheduler is `None`. The `sync-equipment` endpoint handles this by creating a short-lived `BlizzardClient` from env vars directly.
- **bonus_list is list[int] not list[dict]**: Blizzard equipment API returns `bonus_list` as plain integers. `blizzard_client.py` previously had `[b.get("id",0) for b in ...]` which crashed on any character with bonused items. Fixed to `item.get("bonus_list") or []`.
- **Static file caching**: gear_plan.css and gear_plan.js use `?v=N` query strings for cache-busting. Increment N in `gear_plan.html` whenever JS or CSS changes are deployed. Current version: `?v=7`.
