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
| **1D** Personal gear plan | 🔄 IN PROGRESS | Member page `/gear-plan` built and deployed to dev. See Phase 1D section. |
| **1E** Roster aggregation | ⬜ TODO | Roster needs computation, admin grids |

**Active branch:** `feature/gear-plan-phase-1d`
**Last migration:** 0079
**Last prod tag:** `prod-v0.11.2`

---

## Quality Track System

| Track | Letter | Color | Sources |
|-------|--------|-------|---------|
| Veteran | V | Green (#1eff00) | Raid Finder |
| Champion | C | Blue (#0070dd) | Normal Raid, M+ 0–5 |
| Hero | H | Purple (#a335ee) | Heroic Raid, M+ 6+ |
| Mythic | M | Orange (#ff8000) | Mythic Raid only |

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

### Open issues / next steps (as of 2026-04-07)

**Icon quality colour (quality_track = null)**
- Blizzard API `name_description.display_string` regex `^(Veteran|Champion|Hero|Mythic)\s+\d+/\d+$` may not match Midnight expansion format
- TWW S2 bonus ID map (`quality_track.py._DEFAULT_SIMC_BONUS_IDS`) is season-specific; Midnight uses different IDs
- Result: most equipped items have `quality_track = null` → no coloured icon border
- **Need:** Confirm what display_string Blizzard returns for Midnight track items, or provide Midnight bonus IDs

**Crafted item quality track**
- Crafted items (bonus_id 1808, may differ for Midnight) have `quality_track = null` because neither detection method matches
- User wants: crafted items treated as H or M based on crest tier used to craft them
- Two ilvl ranges (H-crest = ilvl range, M-crest = ilvl range), breakpoint varies by patch
- **Need:** Decide mechanism for breakpoint — options: (a) configurable via `site_config.crafted_m_ilvl_threshold` (migration 0080, admin sets per season), (b) derive from highest H/M non-crafted items in roster
- **Current state:** is_crafted detected from bonus_id 1808, shows "Crafted" badge, participates in upgrade logic; quality_track still null so upgrade tracks empty

**Slot drawer — BIS Recommendations redesign**
- Replace current list-style recommendations with a grid:
  - Columns: one per (provider × list-type) combination, e.g. "u.gg Raid", "u.gg M+", "Wowhead"
  - Rows: each unique item that appears in any BIS list for this slot, sorted by how many lists contain it (most-recommended first)
  - Cell: ✓ (gold) if that provider/list has this item, — (greyed) if not
  - "Use" button on each row
- New data needed from service: `get_plan_detail` already returns `bis_recommendations` as flat list; needs to be restructured to support the grid (unique items × sources matrix)

**Hover tooltip** ✅ RESOLVED
- Added `whTooltips` config + Wowhead `power.js` to member gear plan template
- Item names in slot cards, drawer equipped/desired, and BIS grid wrapped in `<a href="https://www.wowhead.com/item=N">` links
- `$WowheadPower.refreshLinks()` called after paperdoll render and drawer open

**Ring/Trinket BIS sort** ✅ RESOLVED
- `_normalize_paired_slot` now swaps `desired_by_slot` and `bis_by_slot` in the alphabetical fallback case, not just `equipped_by_slot`
- JS `renderBisGrid` pins `desired_blizzard_item_id` to top row

**Slot card hover state**
- Cards don't clearly communicate "click to open drawer" — consider adding a subtle indicator
- Hover cursor is `pointer` which is correct, but visual feedback is minimal

**Upgrade track pills — crafted items**
- Crafted items with no quality_track → no upgrade pills → no guidance to get higher crest tier
- Blocked by quality_track detection fix above

---

## Phase 1E: Roster Aggregation (TODO after 1D)

Admin gear plan page gains "Roster Needs" section:
- **Raid grid:** Instance header, boss rows, quality track columns (C/H/M). Cell = count of players needing a drop. Click → popup with player names.
- **M+ grid:** Dungeon rows, quality track columns (C/H). Same cell pattern.
- Color scale: 0=grey, 1–2=green, 3–5=gold, 6+=red
- Filter: active raid season, include/exclude specific ranks

New endpoints:
- `GET /api/v1/guild/gear-needs/raid`
- `GET /api/v1/guild/gear-needs/dungeon`

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
