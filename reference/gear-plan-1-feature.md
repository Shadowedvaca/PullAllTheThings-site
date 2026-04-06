# Gear Plan Feature — Implementation Plan

## Context

The guild needs to answer "what should we run this week?" from a loot perspective. Today there's no way to see across the roster what items people need, from which bosses, at which quality tier. This feature gives each player a personal gear plan (what they're wearing vs. what they want per slot), then aggregates needs across the roster to show raid boss / M+ dungeon priority grids.

**Design decisions from Mike:**
- BIS lists support per-spec AND per-hero-talent variants
- MVP includes both personal gear plan + roster aggregation grid
- Upgrade logic: same track and above (non-BIS at Champion → Champion+ of BIS item are needs)
- All three BIS sources (Wowhead, Icy Veins, Archon/u.gg) stay — automate all, admin entry as backstop
- BIS data is **centralized by Mike for the entire network**, not per-guild. BIS lists are universal game data.
- Auto-publish scraped data immediately, admin reviews/corrects after (no draft→approve workflow)

---

## Research Findings

### Blizzard API (already integrated)
- **Equipment endpoint** (`/profile/wow/character/{realm}/{name}/equipment`) already called in `blizzard_client.py:310-328` but only extracts `equipped_item_level`. Raw response has `equipped_items[]` with: item ID, name, level, quality, `name_description.display_string` (e.g. "Champion 4/8"), bonus_list, enchants, gems, slot type.
- **Item endpoint** (`/data/wow/item/{itemId}`) — item details. Not currently used.
- **Journal endpoints** (`/data/wow/journal-encounter/{id}`, `/data/wow/journal-instance/{id}`) — encounter loot tables. Not currently used. Confirmed: encounter endpoint returns item IDs that drop from each boss.
- Auth and rate limiting already handled by `BlizzardClient`.

### Archon / u.gg (MOST STRUCTURED — Primary automated source)
Embeds `window.__SSR_DATA__` JSON with a direct data URL pattern:
```
https://stats2.u.gg/wow/builds/v29/all/{Class}/{Class}_{spec}_itemsTable.json
```

**items_table structure (per slot):**
```json
{
  "items_table": {
    "items": {
      "head": {
        "items": [
          {
            "item_id": 250027,        // Blizzard item ID
            "item_level": 289,
            "quality": 4,             // WoW rarity (4=epic)
            "count": 47,              // # top players using
            "dps": 123019,            // DPS contribution
            "perc": "72.31",          // Popularity %
            "img": "inv_chest_...",   // Icon filename
            "enchant_id": 7987,
            "max_mythic_keys": 17
          }
        ],
        "total_count": 65
      }
    }
  }
}
```

**URL:** `https://u.gg/wow/{spec}/{class}/gear?hero={hero_talent}&role={role}`
- `hero=elunes_chosen`, `keeper_of_the_grove`, etc.
- `role=raid` or `role=mythicdungeon`
- Covers all specs, per hero talent, raid vs M+ — exactly what we need
- ~78 specs × 2 hero talents × 2 roles = ~312 fetches for full coverage

**Slot mapping:** belt→waist, cape→back, gloves→hands, ring1→ring_1, ring2→ring_2, trinket1→trinket_1, trinket2→trinket_2, weapon1→main_hand, weapon2→off_hand. Others match.

**Combo data:** Also provides paired-slot recommendations (ring1+ring2, trinket1+trinket2, weapon1+weapon2).

**Limitation:** Does NOT include boss/dungeon source info. Need Blizzard Journal API for that.

### Wowhead (CURATED — Secondary source)
- BIS guides use markup: `[item=249283 bonus=12806:13335]` in slot-organized tables
- `WH.Gatherer.addData()` calls embed item metadata: `name_enus`, `quality`, `icon`, `slotbak` (numeric slot code), `jsonequip` (stats)
- Tier list markup: `[tier-label bg=q5]S[/tier-label]` with `[icon-badge=ID quality=4]`
- Tooltip API: `nether.wowhead.com/tooltip/item/{id}` → JSON (name, quality, icon, tooltip HTML)
- Parseable but uses proprietary markup that could change. Need URL mapping per spec.
- Has separate pages for overall/raid BIS

### Icy Veins (CURATED — Tertiary source)
- No API. JavaScript-rendered with dynamic content loading.
- Uses CSS class filtering: `.recommended`, `.weekly-key`, `.high-key`
- Item data embedded in `image_block` components with item names and narrative source descriptions
- Has 3 variants: overall (`area=area_1`), raid (`area=area_2`?), M+ (`area=area_3`?)
- Hardest to automate — may need headless browser (Playwright/Puppeteer) or server-side rendering detection
- FAQ schema JSON-LD in page has some item references

### Wowhead Tooltip API (for item metadata)
```
GET https://nether.wowhead.com/tooltip/item/{itemId}?dataEnv=1&locale=0
```
Returns: `name`, `quality`, `icon`, `tooltip` (HTML), binding info. Public, no auth. Fast. Use for all item metadata caching.

---

## Quality Track System

| Track | Letter | Color | Sources |
|-------|--------|-------|---------|
| Veteran | V | Green (#1eff00) | Raid Finder |
| Champion | C | Blue (#0070dd) | Normal Raid, M+ 0-5 |
| Hero | H | Purple (#a335ee) | Heroic Raid, M+ 6+ |
| Mythic | M | Orange (#ff8000) | Mythic Raid only |

Sublevels (1/8, 4/8, etc.) don't matter — only the track letter.

### Upgrade Logic
- **Same item, lower track**: need = strictly higher tracks only (e.g., have BIS at C → need H, M)
- **Different item (not BIS)**: need = same track and above (e.g., have non-BIS at C → need C, H, M of BIS item — same-track BIS is still better due to stats)
- Net: if `equipped_item == desired_item`, need tracks > equipped. If `equipped_item != desired_item`, need tracks >= equipped.

### Quality Track Detection
Parse `name_description.display_string` from Blizzard equipment endpoint: `"Champion 4/8"` → `C`. Regex: `^(Veteran|Champion|Hero|Mythic)\s+\d+/\d+$`. Store raw `bonus_ids` as fallback.

---

## Data Model (10 new tables, migration 0066)

### `guild_identity.wow_items` — Cached item metadata
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| blizzard_item_id | INTEGER UNIQUE NOT NULL | Blizzard's item ID |
| name | VARCHAR(200) NOT NULL | |
| icon_url | VARCHAR(500) | Wowhead CDN |
| slot_type | VARCHAR(20) NOT NULL | head, neck, shoulder, etc. |
| armor_type | VARCHAR(20) | cloth/leather/mail/plate/misc |
| weapon_type | VARCHAR(30) | dagger, staff, etc. (NULL for armor) |
| wowhead_tooltip_html | TEXT | Raw tooltip for rich display |
| fetched_at | TIMESTAMPTZ DEFAULT NOW() | |

### `guild_identity.item_sources` — Where items drop
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| item_id | INTEGER FK→wow_items CASCADE | |
| source_type | VARCHAR(20) CHECK | raid_boss, dungeon, profession, world, pvp, other |
| source_name | VARCHAR(100) NOT NULL | "Ky'veza" or "The Stonevault" |
| source_instance | VARCHAR(100) | "Nerub-ar Palace" or NULL |
| blizzard_encounter_id | INTEGER | For raid bosses |
| blizzard_instance_id | INTEGER | For dungeons/raids |
| quality_tracks | TEXT[] DEFAULT '{}' | Tracks available: {C,H,M} for raid, {C,H} for M+ |
| UNIQUE | (item_id, source_type, source_name) | |

### `guild_identity.hero_talents` — Reference table for hero talent trees
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| spec_id | INTEGER FK→specializations CASCADE | |
| name | VARCHAR(100) NOT NULL | "Elune's Chosen" |
| slug | VARCHAR(50) NOT NULL | "elunes_chosen" (for URL building) |
| UNIQUE | (spec_id, name) | |

### `guild_identity.bis_list_sources` — Named BIS list providers
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| name | VARCHAR(100) UNIQUE | "Archon Raid", "Wowhead Overall", "Icy Veins M+" |
| short_label | VARCHAR(30) | "Archon R" (for badge display) |
| origin | VARCHAR(50) | wowhead, icy_veins, archon, manual |
| content_type | VARCHAR(20) | overall, raid, mythic_plus |
| is_default | BOOLEAN DEFAULT FALSE | Guild default for new players |
| is_active | BOOLEAN DEFAULT TRUE | |
| sort_order | INTEGER DEFAULT 0 | |
| last_synced | TIMESTAMPTZ | When auto-sync last ran |

### `guild_identity.bis_list_entries` — BIS items per spec+hero per slot
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| source_id | INTEGER FK→bis_list_sources CASCADE | |
| spec_id | INTEGER FK→specializations CASCADE | |
| hero_talent_id | INTEGER FK→hero_talents SET NULL | NULL = applies to all hero talent builds |
| slot | VARCHAR(20) NOT NULL | |
| item_id | INTEGER FK→wow_items CASCADE | |
| priority | INTEGER DEFAULT 1 | 1=top pick, 2=alt |
| notes | TEXT | |
| UNIQUE | (source_id, spec_id, hero_talent_id, slot, item_id) | |

### `guild_identity.character_equipment` — Current equipped gear per slot
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| character_id | INTEGER FK→wow_characters CASCADE | |
| slot | VARCHAR(20) NOT NULL | |
| blizzard_item_id | INTEGER NOT NULL | Always stored |
| item_id | INTEGER FK→wow_items SET NULL | Nullable, lazy-populated |
| item_name | VARCHAR(200) | Denormalized |
| item_level | INTEGER NOT NULL | |
| quality_track | VARCHAR(1) | V/C/H/M |
| bonus_ids | INTEGER[] | Raw for re-derivation |
| enchant_id | INTEGER | |
| gem_ids | INTEGER[] | |
| synced_at | TIMESTAMPTZ DEFAULT NOW() | |
| UNIQUE | (character_id, slot) | |

### `guild_identity.gear_plans` — Player's gear plan per character
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| player_id | INTEGER FK→players CASCADE | |
| character_id | INTEGER FK→wow_characters CASCADE | |
| spec_id | INTEGER FK→specializations | |
| hero_talent_id | INTEGER FK→hero_talents SET NULL | |
| bis_source_id | INTEGER FK→bis_list_sources SET NULL | Template source |
| is_active | BOOLEAN DEFAULT TRUE | |
| created_at | TIMESTAMPTZ DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ DEFAULT NOW() | |
| UNIQUE | (player_id, character_id) | One plan per character |

### `guild_identity.gear_plan_slots` — Per-slot item selections
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| plan_id | INTEGER FK→gear_plans CASCADE | |
| slot | VARCHAR(20) NOT NULL | |
| desired_item_id | INTEGER FK→wow_items SET NULL | |
| blizzard_item_id | INTEGER | Denormalized |
| item_name | VARCHAR(200) | Denormalized |
| is_locked | BOOLEAN DEFAULT FALSE | User explicitly confirmed |
| notes | TEXT | |
| UNIQUE | (plan_id, slot) | |

Also add `last_equipment_sync TIMESTAMPTZ` column to `guild_identity.wow_characters`.

### `guild_identity.bis_scrape_targets` — URL map for automated BIS extraction
(See BIS Ingestion section below for full schema)

### `guild_identity.bis_scrape_log` — Extraction attempt history
(See BIS Ingestion section below for full schema)

---

## BIS List Ingestion — Discovery-First Pipeline

BIS data is centralized game data managed by Mike for the whole network. The pipeline is admin-triggered, transparent, and designed to automate as much as possible while letting the admin see exactly what happened and fix edge cases.

### Architecture: 4-Step Pipeline

**Step 1: URL Discovery** — Build the map of all BIS list URLs
- New table: `guild_identity.bis_scrape_targets` (spec_id, hero_talent_id, source_id, url, status, last_fetched)
- Auto-generate URLs using known patterns per source:
  - **Archon:** `u.gg/wow/{spec}/{class}/gear?hero={slug}&role={raid|mythicdungeon}`
  - **Wowhead:** `wowhead.com/guide/classes/{class}/{spec}/bis-gear` (+ `#raid-bis` / `#mythic-plus-bis` anchors)
  - **Icy Veins:** `icy-veins.com/wow/{spec}-{class}-pve-{role}-gear-best-in-slot?area=area_N`
- Admin dashboard shows the full spec × source grid. Auto-discovered URLs marked green. Missing URLs marked red — admin fills manually.
- Seed data: `hero_talents` reference table with all spec → hero talent slugs (~78 specs × 2 each = 156 rows)

**Step 2: Extraction** — Try multiple techniques per URL
For each scrape target, the system tries extraction techniques in priority order:

| Technique | Used For | How It Works |
|-----------|----------|--------------|
| `json_embed` | Archon/u.gg | Parse `window.__SSR_DATA__` or fetch `stats2.u.gg` direct JSON endpoint. Extract `items_table.items` per slot. |
| `wh_gatherer` | Wowhead | Parse `WH.Gatherer.addData()` JS calls for item objects + `[item=ID]` markup for slot assignments. |
| `html_parse` | Icy Veins, fallback | Parse HTML for item references in structured containers (`image_block`, `.bis-table`, etc.). Resolve item names to IDs via Wowhead tooltip search. |
| `manual` | All (backstop) | Admin enters Wowhead item IDs per slot via UI or JSON bulk paste. |

Each extraction attempt is logged in a new table: `guild_identity.bis_scrape_log`:
```
id, target_id FK, technique, status (success/partial/failed),
items_found INTEGER, items_expected INTEGER (16 slots),
error_message TEXT, raw_response_hash VARCHAR(64),
created_at TIMESTAMPTZ
```

**Step 3: Auto-publish + Review Dashboard**
- Extracted items immediately upsert into `bis_list_entries` (auto-publish, no draft state)
- Dashboard shows a matrix: specs (rows) × sources (columns)
  - Cell shows: items found / 16, technique used, last sync time
  - Color: green (16/16), yellow (partial), red (0 or failed), grey (not attempted)
  - Click cell → see extracted items per slot, side-by-side with Wowhead tooltips for verification
- Admin can override any slot directly from this view

**Step 4: Cross-reference**
- Once items are extracted from one source, compare with other sources for the same spec
- Show a cross-reference view: per slot, which sources agree vs. disagree
- Agreement = high confidence (highlight green)
- Disagreement = worth reviewing (highlight yellow with both options shown)
- This helps spot extraction errors: if Archon says item X for head and Wowhead says item Y, either they genuinely differ (valid) or one extraction was wrong (needs fix)

### New Table: `guild_identity.bis_scrape_targets`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| source_id | INTEGER FK→bis_list_sources CASCADE | |
| spec_id | INTEGER FK→specializations CASCADE | |
| hero_talent_id | INTEGER FK→hero_talents SET NULL | |
| content_type | VARCHAR(20) | overall, raid, mythic_plus |
| url | TEXT | Discovered or manually entered URL |
| preferred_technique | VARCHAR(20) | json_embed, wh_gatherer, html_parse, manual |
| status | VARCHAR(20) DEFAULT 'pending' | pending, success, partial, failed |
| items_found | INTEGER DEFAULT 0 | |
| last_fetched | TIMESTAMPTZ | |
| UNIQUE | (source_id, spec_id, hero_talent_id, content_type) | |

### New Table: `guild_identity.bis_scrape_log`
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| target_id | INTEGER FK→bis_scrape_targets CASCADE | |
| technique | VARCHAR(20) NOT NULL | |
| status | VARCHAR(20) NOT NULL | success, partial, failed |
| items_found | INTEGER DEFAULT 0 | |
| error_message | TEXT | |
| created_at | TIMESTAMPTZ DEFAULT NOW() | |

### Backstop: Manual Entry
From the review dashboard, admin can:
- Click any slot and enter a Wowhead item ID directly
- Bulk paste JSON: `[{"slot": "head", "item_id": 212345}, ...]`
- These entries are logged with `technique='manual'` in scrape log
- Manual entries are "locked" — auto-sync won't overwrite them unless admin explicitly re-syncs

### Service: `src/sv_common/guild_sync/bis_sync.py`
Contains:
- `discover_targets(pool)` — auto-generate scrape target URLs for all specs
- `sync_source(pool, source_name, spec_ids=None)` — run extraction for a source, optionally filtered to specific specs
- `sync_all(pool)` — full sync across all sources
- `_extract_archon(url)` → list of (slot, item_id, priority) tuples
- `_extract_wowhead(url)` → list of (slot, item_id, priority) tuples
- `_extract_icy_veins(url)` → list of (slot, item_id, priority) tuples
- `_resolve_item(blizzard_item_id)` → fetch/cache item metadata via Wowhead tooltip API
- `cross_reference(pool, spec_id, hero_talent_id)` → cross-source comparison result

---

## SimulationCraft (SimC) Integration

SimulationCraft is a well-established open-source WoW theorycrafting tool. Its text-based profile format (`.simc`) is a universal artifact in the community: the in-game **Simulationcraft addon** exports your current gear as a SimC profile, and every major BIS site (Archon/u.gg, Wowhead, Icy Veins) has an "Export SimC" or "Copy SimC" button on their gear pages. Raidbots accepts SimC profiles as input for DPS sims.

This means SimC is a natural import/export bridge for gear plans with no reverse-engineering needed — it's a documented, stable format maintained by the community.

### SimC Profile Format

```
druid="Trogmoon"
spec=balance
level=80
race=night_elf
region=us
server=senjin

head=dreambinder_loom_of_the_great_cycle,id=208616,bonus_id=4800:1517:8767,enchant_id=7936
neck=fateweaved_needle,id=212449,bonus_id=4800:1520
shoulder=mantle_of_volcanic_grief,id=212065,bonus_id=4800:1520
...
```

Per gear line: `slot=item_name,id=BLIZZARD_ITEM_ID,bonus_id=N:N:N,enchant_id=N,gem_id=N:N`

- `id` — Blizzard item ID (the primary key we use everywhere)
- `bonus_id` — colon-separated list encoding quality track, crafted mods, sockets, season affixes. The quality track IDs are a stable per-season mapping documented in the community (e.g., `1517` = Champion, `1520` = Hero in TWW S1). These are stored as a small config lookup; `quality_track.py` handles both the Blizzard `name_description.display_string` path and the SimC bonus_id path.
- `enchant_id`, `gem_id` — passthrough for display in gear plan

### Use Cases

**1. Player imports a BIS SimC profile → sets their gear goal list**

A player finds the "Copy SimC" button on Archon/u.gg for their spec, pastes the text into their gear plan page. The parser extracts item IDs per slot and populates `gear_plan_slots.desired_item_id`. This is a much faster workflow than building the plan slot-by-slot from the UI.

**2. Player exports their gear plan → SimC file for Raidbots**

The gear plan's desired items are emitted as a valid SimC profile. The player downloads/copies it and pastes it into Raidbots to sim their target gear before they've obtained it. Quality track is encoded using the appropriate bonus_ids for the player's chosen upgrade tier.

**3. Guild leader imports a SimC BIS profile → sets default gear goals for a spec**

Mike finds an Archon or Wowhead SimC export for Balance Druid (Raid) and uploads it in the admin BIS dashboard. This creates/updates `bis_list_entries` for that spec, exactly like the automated scraper but without needing to run the scraper. Logged in `bis_scrape_log` with `technique='simc'`. When players create a gear plan and choose "use guild default", this BIS source populates their slots automatically.

### SimC as the Canonical In-Memory Format

`SimcProfile` / `SimcSlot` are the single in-memory representation for any gear data moving through the system. Every data path converts through this struct before writing to or reading from the DB:

```
Blizzard API response  ──┐
SimC text (player paste) ─┤──► SimcProfile ──► DB (character_equipment / gear_plan_slots)
Archon JSON scrape    ───┤                          │
Wowhead HTML scrape   ───┘                          │
                                                    ▼
                                            SimcProfile ──► SimC text export
```

This means `simc_parser.py` is both the import parser **and** the canonical data model. All BIS extractors in `bis_sync.py` return `list[SimcSlot]`. All equipment sync results in `equipment_sync.py` are normalized to `SimcSlot` before upsert. This keeps quality track detection, bonus_id handling, and slot normalization in one place.

**No external package needed.** The SimC profile grammar is simple enough (~100 lines of Python) that maintaining our own parser is lower risk than depending on an unmaintained third-party library. The format is stable and well-documented by the SimC project. If Blizzard changes bonus ID semantics (happens each season), we update one config value in `site_config`, not a vendored dependency.

Add `simc_profile TEXT` to `gear_plans` to cache the last-imported SimC text verbatim, enabling exact round-trip and diffing.

### Parser Module: `src/sv_common/guild_sync/simc_parser.py`

| Function | Description |
|----------|-------------|
| `parse_profile(text: str) -> SimcProfile` | Full profile: spec, class, realm, region, gear slots |
| `parse_gear_slots(text: str) -> list[SimcSlot]` | Extract slot→(item_id, bonus_ids, enchant_id, gem_ids) |
| `bonus_ids_to_quality_track(bonus_ids: list[int]) -> str \| None` | Map bonus_id list to V/C/H/M using per-season config |
| `export_gear_plan(plan_slots, char_name, spec, realm, region) -> str` | Generate SimC text from gear_plan_slots rows |

`SimcSlot` dataclass: `slot: str`, `blizzard_item_id: int`, `bonus_ids: list[int]`, `enchant_id: int | None`, `gem_ids: list[int]`, `quality_track: str | None`

Quality track bonus IDs are season-specific. Store them in `site_config` as `simc_track_bonus_ids` (JSON: `{"C": [1516, 1517], "H": [1520, 1521], "M": [1524, 1525]}`). Admin can update when a new season launches. If a bonus_id doesn't match any track, fall back to Blizzard API verification for that item.

### API Additions

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/me/gear-plan/{character_id}/import-simc` | Parse pasted SimC text; populate desired items in plan slots |
| GET | `/api/v1/me/gear-plan/{character_id}/export-simc` | Download gear plan goal list as `.simc` file |
| POST | `/api/v1/admin/bis/import-simc` | Import SimC BIS profile as bis_list_entries for a spec (`?spec_id=X&hero_talent_id=Y&source_name=Z`) |

### BIS Pipeline Technique Addition

Add `simc` to the `preferred_technique` CHECK constraint in `bis_scrape_targets`. When a GL uploads a SimC file via the admin import endpoint:
- A `bis_scrape_targets` row is created/updated with `preferred_technique='simc'`, `status='success'`, `items_found=N`
- A `bis_scrape_log` row is appended with `technique='simc'`
- The BIS sync dashboard matrix cell shows the SimC icon and item count, like other techniques

The `bis_sync.py` service gains a `_extract_simc(text)` function that delegates to `simc_parser.parse_gear_slots()`. Manual SimC import is treated as a "locked" BIS source — auto-sync won't overwrite it unless the admin explicitly re-imports.

### UI Additions

**Personal gear plan page:**
- "Import SimC" button → modal with textarea, paste SimC text, "Apply to Plan" populates all slots
- "Export SimC" button → downloads `{char_name}_{spec}_goals.simc`

**Admin BIS dashboard (Tab 2):**
- "Import SimC" button alongside "Sync Source" — opens modal with spec/hero talent picker + SimC textarea
- SimC-imported cells shown with a distinct icon in the matrix

---

## Item Source Mapping

### Blizzard Journal API (primary)
```
GET /data/wow/journal-expansion/{expansionId}  → list of instances
GET /data/wow/journal-instance/{instanceId}    → list of encounters
GET /data/wow/journal-encounter/{encounterId}  → encounter loot (item IDs)
```

**New service:** `src/sv_common/guild_sync/item_source_sync.py`
1. Get current expansion's raid + M+ dungeon instance IDs
2. For each instance, get encounters
3. For each encounter, get item drops → populate `item_sources` table
4. Quality tracks inferred from source: raid boss → {C, H, M}, M+ dungeon → {C, H}
5. Run once per season (admin button), cached indefinitely until next season

**Note:** Journal API doesn't provide quality track per item directly — we infer it from source type (raid bosses drop at all raid difficulties = Champion/Hero/Mythic; M+ dungeons = Champion/Hero).

### Wowhead cross-reference (supplement)
For items not found via Journal API (world drops, crafted, PvP), use Wowhead tooltip data which sometimes includes source hints in the tooltip HTML.

---

## Equipment Sync

Extend `BlizzardClient.get_character_equipment()` to return full per-slot data. New module `equipment_sync.py`:
- Same batch pattern as `progression_sync.py` (10 at a time, 0.5s delay)
- Filters by `last_login_timestamp > last_equipment_sync`
- Parses `name_description.display_string` for quality track (V/C/H/M)
- UPSERTs into `character_equipment` (one row per slot per character)
- Runs as part of 6-hour Blizzard sync cycle
- Also fetchable on-demand (player's "Sync Gear" button)

---

## API Endpoints

### Member (auth required)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/me/gear-plan/{character_id}` | Full gear plan: equipped + desired + all BIS options + upgrades per slot |
| POST | `/api/v1/me/gear-plan/{character_id}` | Create plan (optionally from BIS source + hero talent) |
| PUT | `/api/v1/me/gear-plan/{character_id}/slot/{slot}` | Update desired item for a slot |
| POST | `/api/v1/me/gear-plan/{character_id}/populate` | Re-populate unlocked slots from a BIS source |
| DELETE | `/api/v1/me/gear-plan/{character_id}` | Delete plan |

### Items (public-ish, may require auth)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/items/{blizzard_item_id}` | Fetch/cache item by Blizzard ID (Wowhead tooltip) |
| GET | `/api/v1/items/search?q=name` | Search cached items by name |

### Admin (Officer+ for roster grids, GL-only for BIS management)
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/v1/admin/bis/sources` | BIS source CRUD |
| PUT | `/api/v1/admin/bis/sources/{id}` | Update source (set default, toggle active) |
| GET | `/api/v1/admin/bis/entries?source_id=X&spec_id=Y&hero_talent_id=Z` | Get BIS entries |
| POST | `/api/v1/admin/bis/entries` | Add/update single BIS entry |
| POST | `/api/v1/admin/bis/entries/bulk` | Bulk import JSON |
| DELETE | `/api/v1/admin/bis/entries/{id}` | Remove entry |
| GET | `/api/v1/admin/bis/targets` | Get scrape target matrix (spec × source status) |
| POST | `/api/v1/admin/bis/targets/discover` | Auto-generate scrape target URLs |
| PUT | `/api/v1/admin/bis/targets/{id}` | Manually set/edit a scrape target URL |
| POST | `/api/v1/admin/bis/sync` | Trigger full pipeline (all sources) |
| POST | `/api/v1/admin/bis/sync/{source}` | Trigger sync for one source |
| POST | `/api/v1/admin/bis/sync/target/{id}` | Re-sync a single scrape target |
| GET | `/api/v1/admin/bis/scrape-log?target_id=X` | Get extraction attempt history |
| GET | `/api/v1/admin/bis/cross-reference?spec_id=X&hero_talent_id=Y` | Cross-source comparison |
| POST | `/api/v1/admin/bis/sync-item-sources` | Trigger Journal API item→source mapping |
| GET | `/api/v1/guild/gear-needs/raid` | Roster raid boss need grid |
| GET | `/api/v1/guild/gear-needs/dungeon` | Roster M+ dungeon need grid |

---

## UI

### Personal Gear Plan (`/gear-plan`, member page)
- **Header:** Character selector + spec/hero talent display + "Sync Gear" button
- **BIS source selector:** Dropdown showing all active sources + hero talent filter
- **16 slot rows** stacked vertically (WoW order: Head, Neck, Shoulder, Back, Chest, Wrist, Hands, Waist, Legs, Feet, Ring 1, Ring 2, Trinket 1, Trinket 2, Main Hand, Off Hand)

Each slot row shows:
- Item icon + name + ilvl + quality badge (V/C/H/M letter pill, colored)
- Right side: desired item name + upgrade track badges showing needed tracks
- Click to expand drawer

**Slot drawer (expanded):**
- Currently Equipped section: icon, name, ilvl, quality track, enchant, gems
- BIS Recommendations section: one row per source showing that source's recommendation for this slot
  - "Archon Raid: [icon] Nerubian Handguards — drops from Ky'veza"
  - "Wowhead Overall: [icon] Crafted Gloves — profession crafted"
  - "Icy Veins M+: [icon] Different Item — drops from The Stonevault"
- User's Selection: currently selected desired item, lock toggle
- Manual Lookup: Wowhead item ID input + Fetch button
- Source info: where the selected item drops, available quality tracks, which are upgrades

**Quality badge colors (WoW standard):**
- V = Green (#1eff00)
- C = Blue (#0070dd)
- H = Purple (#a335ee)
- M = Orange (#ff8000)

### Admin Gear Plan (`/admin/gear-plan`)

**Tab 1: Roster Needs**
- **Raid grid:** Instance name header, boss rows, quality track columns (C/H/M — skip V since no one runs Raid Finder). Cell = count of players needing a drop. Click cell → popup with player names.
- **M+ grid:** Dungeon rows, quality track columns (C/H). Same cell pattern.
- Color scale: 0=grey, 1-2=green, 3-5=gold, 6+=red
- Filter: active raid season, include/exclude specific ranks

**Tab 2: BIS Sync Dashboard** (Mike-managed, GL-only access)
- **Matrix view:** specs (rows) × sources (columns, 3 per content type)
  - Cell: items found / 16, technique icon, color (green/yellow/red/grey)
  - Click cell → drill into per-slot items with Wowhead tooltips for spot-checking
- **Controls:**
  - "Discover URLs" button → auto-generates scrape targets for all missing specs
  - "Sync Source" dropdown → trigger extraction for one source across all specs
  - "Sync All" → run full pipeline (all sources, all specs)
  - Per-cell "Re-sync" button
- **Cross-reference panel:** Select a spec → see all sources side-by-side per slot
  - Green highlight = sources agree
  - Yellow = disagreement (shows both options)
- **Manual override:** Click any slot in the drill-down to enter Wowhead item ID directly
- **Scrape log:** Expandable section showing recent extraction attempts, techniques tried, errors
- Bulk JSON import textarea

**Tab 3: Item Sources**
- "Sync Loot Tables" button (triggers Journal API sync)
- Table of known item→source mappings, filterable by instance/dungeon
- Manual source entry for items not found via Journal API

---

## Phasing

### Phase 1 — Core: Equipment Sync + BIS Automation + Gear Plans + Roster Grid
This is the full MVP. All components are needed to close the loop: sync gear → populate BIS → users set plans → admin sees roster needs.

**Sub-phases:**

**1A: Foundation (migration + equipment sync + item cache)** ✅ COMPLETE — 2026-04-04
- Migration 0066: all 10 tables + `last_equipment_sync` column ✅
- `quality_track.py` — track detection from Blizzard response ✅
- `equipment_sync.py` — full slot-by-slot equipment sync ✅
- `item_service.py` — Wowhead tooltip fetch + `wow_items` caching ✅
- Extend `blizzard_client.py` with `get_character_equipment()` + `CharacterEquipmentSlot` dataclass ✅
- Add equipment sync step to `scheduler.py` (Step 8, non-fatal) ✅
- ORM models for all 10 new tables ✅
- `bis_list_sources` seeded with 5 rows (Archon Raid/M+, Wowhead Overall, Icy Veins Raid/M+) ✅
- 28 unit tests (quality track parsing + slot normalisation) — all pass ✅
- Deployed to dev (commit c1499d9, migration confirmed healthy) ✅

**1B: BIS discovery + extraction pipeline** ✅ COMPLETE — 2026-04-04
- Migration 0067: 72 hero talent rows (36 specs × 2) + gear_plan screen permission ✅
- `simc_parser.py` — SimcSlot/SimcProfile dataclasses, parse_profile, parse_gear_slots, export_gear_plan, bonus_ids_to_quality_track; 45 unit tests — all pass ✅
- `bis_sync.py` — discover_targets, sync_source/sync_all/sync_target, extractors for Archon (json_embed), Wowhead (wh_gatherer), Icy Veins (html_parse), SimC; import_simc, cross_reference, get_matrix; slug maps for all 39 specs × 3 source origins ✅
- `bis_routes.py` — admin BIS API (sources, entries, targets, matrix, sync, scrape-log, cross-reference, SimC import) — Officer+ / GL for write ops ✅
- Admin BIS sync dashboard (`/admin/gear-plan`, GL-only) — matrix view, cell drill-down, cross-reference panel, scrape log, SimC import modal ✅
- gear_plan_admin.js — full client-side interactions ✅
- Deployed to dev (commit fd9e4cb, migration confirmed healthy) ✅

**1C: Item source mapping** ✅ COMPLETE — 2026-04-06
- `item_source_sync.py` — `sync_item_sources(pool, client, expansion_id=None)` walks Journal API expansion → instances → encounters → items ✅
- `blizzard_client.py` — 4 new Journal API methods (static-us namespace) ✅
- `bis_routes.py` — `POST /sync-item-sources`, `GET /item-sources`, `DELETE /item-sources/{id}` ✅
- Admin gear plan page — "Item Sources" collapsible section with Sync button + filters + table ✅
- 18 unit tests — all pass ✅
- Deployed to dev (commit 7b843fb) ✅

**1D: Personal gear plan**
- `gear_plan_service.py` — plan CRUD, upgrade computation, BIS population
- `gear_plan_routes.py` — member + admin API endpoints
- `gear_plan.html` + `gear_plan.js` + `gear_plan.css` — personal plan page
- Item lookup endpoint

**1E: Roster aggregation**
- Roster needs computation service
- Admin gear plan page with raid/M+ grids
- `gear_plan_admin.js` — grid interactions

### Phase 2 — Live Raid Mode
- 15-min equipment re-sync during raid event windows (integrate with existing raid_events schedule)
- Real-time roster needs page that auto-refreshes
- "In Raid" indicator on admin grid

### Phase 3 — Polish
- Compact gear plan summary panel on `/my-characters`
- Discord bot `!gearneeds` command
- Gear plan sharing (link a plan for others to view)
- Historical tracking (gear progression over time)

---

## New Files
| File | Purpose |
|------|---------|
| `alembic/versions/0066_gear_plan.py` | Migration: 10 tables + wow_characters column |
| `src/sv_common/guild_sync/quality_track.py` | Quality track detection from Blizzard data |
| `src/sv_common/guild_sync/equipment_sync.py` | Full slot-by-slot equipment sync |
| `src/sv_common/guild_sync/bis_sync.py` | Discovery + extraction pipeline (Archon/Wowhead/IV/SimC parsers) |
| `src/sv_common/guild_sync/simc_parser.py` | SimC profile parse + export utility |
| `src/sv_common/guild_sync/item_source_sync.py` | Blizzard Journal API loot table sync |
| `src/guild_portal/services/item_service.py` | Wowhead tooltip fetch + item caching |
| `src/guild_portal/services/gear_plan_service.py` | Plan CRUD + upgrade computation + BIS population |
| `src/guild_portal/api/gear_plan_routes.py` | All gear plan API endpoints (member + admin + items) |
| `src/guild_portal/pages/gear_plan_pages.py` | Page routes (member gear plan + admin gear plan) |
| `src/guild_portal/templates/member/gear_plan.html` | Personal gear plan |
| `src/guild_portal/templates/admin/gear_plan.html` | Admin: BIS dashboard + roster grids + scrape status |
| `src/guild_portal/static/css/gear_plan.css` | Styles |
| `src/guild_portal/static/js/gear_plan.js` | Client-side gear plan interactions |
| `src/guild_portal/static/js/gear_plan_admin.js` | Admin BIS dashboard + scrape controls + grid interactions |

## Modified Files
| File | Change |
|------|--------|
| `src/sv_common/db/models.py` | 10 new model classes + `last_equipment_sync` on WowCharacter |
| `src/sv_common/guild_sync/blizzard_client.py` | `get_character_equipment()` + Journal API methods |
| `src/sv_common/guild_sync/scheduler.py` | Equipment sync step in `run_blizzard_sync()` |
| `src/guild_portal/app.py` | Include new routers |
| `src/guild_portal/pages/admin_pages.py` | `gear_plan` screen entry + nav item |

---

## Verification

1. **Equipment sync**: Deploy to dev, run Blizzard sync, verify `character_equipment` rows populate with correct slots/items/quality tracks for known characters
2. **BIS automation**: Trigger Archon sync for Balance Druid, verify BIS entries match u.gg page for both hero talents
3. **Item sources**: Trigger Journal sync, verify raid boss → item mappings match Wowhead's loot tables
4. **Personal plan**: Log in as test user, create gear plan, verify equipped items show, populate from BIS source, manually override a slot
5. **Upgrade detection**: Compare a known character's gear to BIS — verify upgrade tracks are correct
6. **Roster grid**: With 2+ gear plans active, verify raid grid shows correct boss need counts
7. **Unit tests**: quality track parsing, upgrade logic, slot mapping, BIS entry dedup
