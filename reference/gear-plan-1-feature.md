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
| **1C** Item source mapping | ✅ COMPLETE | item_source_sync.py, Journal API, admin Item Sources section, migration 0077 bugfix |
| **1D** Personal gear plan | ⬜ NEXT | gear_plan_service.py, gear_plan_routes.py, /gear-plan member page |
| **1E** Roster aggregation | ⬜ TODO | Roster needs computation, admin grids |

**Active branch:** `feature/gear-plan-feature`
**Last migration:** 0077
**Last prod tag:** `prod-v0.10.0` (Gear Plan not yet tagged to prod)
**Test count:** 1211 pass (54 BIS + 18 item source unit tests; 2 pre-existing bnet template failures)

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

Synced via `item_source_sync.py` → Blizzard Journal API. Raid bosses → C/H/M, dungeons → C/H.

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

## Phase 1D: Personal Gear Plan (NEXT)

### New files needed
| File | Purpose |
|------|---------|
| `src/guild_portal/services/gear_plan_service.py` | Plan CRUD + upgrade computation + BIS population |
| `src/guild_portal/api/gear_plan_routes.py` | Member + admin gear plan API endpoints |
| `src/guild_portal/pages/gear_plan_pages.py` | Page route for `/gear-plan` |
| `src/guild_portal/templates/member/gear_plan.html` | Personal gear plan UI |
| `src/guild_portal/static/css/gear_plan.css` | Gear plan styles |
| `src/guild_portal/static/js/gear_plan.js` | Personal plan client interactions |

### API endpoints for 1D
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/me/gear-plan/{character_id}` | Full plan: equipped + desired + BIS options + upgrade tracks per slot |
| POST | `/api/v1/me/gear-plan/{character_id}` | Create plan (optionally from BIS source + hero talent) |
| PUT | `/api/v1/me/gear-plan/{character_id}/slot/{slot}` | Update desired item for a slot |
| POST | `/api/v1/me/gear-plan/{character_id}/populate` | Re-populate unlocked slots from a BIS source |
| DELETE | `/api/v1/me/gear-plan/{character_id}` | Delete plan |
| POST | `/api/v1/me/gear-plan/{character_id}/import-simc` | Paste SimC → populate slots |
| GET | `/api/v1/me/gear-plan/{character_id}/export-simc` | Download `.simc` file |
| GET | `/api/v1/items/{blizzard_item_id}` | Fetch/cache item (Wowhead tooltip) |

### UI: `/gear-plan` (member page)
- Character selector + spec/hero talent display + Sync Gear button
- BIS source selector dropdown + hero talent filter
- 16 slot rows (Head → Off Hand, WoW order)

Each slot row:
- Icon + name + ilvl + quality badge (V/C/H/M pill, colored)
- Desired item name + upgrade track badges (needed tracks highlighted)
- Click → expand drawer

Slot drawer:
- Currently Equipped: icon, name, ilvl, quality track, enchant, gems
- BIS Recommendations: one row per source showing that source's recommendation + drop location
- User's Selection: selected desired item + lock toggle
- Manual Lookup: Wowhead item ID input + Fetch
- Source info: where selected item drops, available quality tracks, which are upgrades

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
| `src/sv_common/guild_sync/quality_track.py` | 1A |
| `src/sv_common/guild_sync/equipment_sync.py` | 1A |
| `src/sv_common/guild_sync/bis_sync.py` | 1B |
| `src/sv_common/guild_sync/simc_parser.py` | 1B |
| `src/sv_common/guild_sync/item_source_sync.py` | 1C |
| `src/guild_portal/services/item_service.py` | 1A |
| `src/guild_portal/api/bis_routes.py` | 1B |
| `src/guild_portal/templates/admin/gear_plan.html` | 1B |
| `src/guild_portal/static/js/gear_plan_admin.js` | 1B |
| `tests/unit/test_item_source_sync.py` | 1C |

### Modified files
| File | Change |
|------|--------|
| `src/sv_common/db/models.py` | 10 new model classes + `last_equipment_sync` on WowCharacter |
| `src/sv_common/guild_sync/blizzard_client.py` | `get_character_equipment()` + 4 Journal API methods |
| `src/sv_common/guild_sync/scheduler.py` | Equipment sync step in `run_blizzard_sync()` |
| `src/guild_portal/app.py` | Include bis_routes router |
| `src/guild_portal/pages/admin_pages.py` | `gear_plan` screen entry + nav item |

---

## Known Gotchas

- **TRUNCATE CASCADE danger**: `guild_ranks → players → gear_plans`; `bis_list_sources → bis_list_entries`; always `pg_dump` before destructive ops on dev
- **bis_list_sources guide_site_id**: Must be set after any DB restore — drives `slug_separator` for URL building. u.gg needs guide_sites row with `slug_sep='_'`; Wowhead/IV need `slug_sep='-'`
- **bis_scrape_targets constraint**: Was `(source_id, spec_id, hero_talent_id, content_type)` → changed to `(source_id, spec_id, url)` in migration 0071 (but 0071 used wrong old constraint name). Migration 0077 fixes the orphaned old constraint.
- **Wowhead targets `hero_talent_id=NULL`**: One target per spec, not per HT. Migration 0076 set all existing rows to NULL and fixed discover_targets to skip the HT loop for wowhead origin.
- **Icy Veins**: Out of scope for v1 — pages are fully JS-rendered. Sources exist in DB but show as "Coming Soon" in matrix.
- **Devourer DH spec**: 3rd DH spec added in migration 0073/0074. May 404 on u.gg if Midnight spec not yet published there — expected.
