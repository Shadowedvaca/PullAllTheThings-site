# Phase Y — WoW Collections: Progress, Wishlists & Farm Route Planner

> **Status:** DRAFT / Pre-planning
> **Priority:** TBD — not yet scheduled
> **Last updated:** 2026-03-17
> **Depends on:** Phase 5.0 (My Characters page) complete

This document lays out the vision and sub-phase breakdown for adding WoW collection
tracking to the platform: mounts, pets, toys, transmog appearances, achievements,
hunter pets, and The War Within housing decor. The goal is a **Collections** panel on
the My Characters page with progress tracking, per-character wishlists, and a farm
route planner that groups missing items by content location.

---

## The Vision

Players spend enormous time collecting things in WoW. The core loop we want to support:

1. **"What am I missing?"** — See collection progress across all categories, browsable
   by expansion and source type.
2. **"I want that."** — Star items into a personal wishlist.
3. **"How do I get all of this efficiently?"** — The route planner groups wishlist items
   by instance or zone, showing which single lockout gives the most items, and produces
   a prioritized farm list the player can work through.

We are not trying to replace Wowhead or WarcraftMounts.com. We serve a summary inside
the platform and link out to those sites for detailed farming guides and lore. The
integration value is having it tied to *this character's* actual collection state and
*this player's* wishlist — not a generic list.

---

## Collection Categories

| Category | Scope | Blizzard API | Notes |
|---|---|---|---|
| Mounts | Account-wide | ✅ `/profile/user/wow/collections/mounts` | ~1,100+ mounts in game |
| Battle Pets | Account-wide | ✅ `/profile/user/wow/collections/pets` | ~1,700+ pets; also has quality/level |
| Toys | Account-wide | ✅ `/profile/user/wow/collections/toys` | ~600+ toys |
| Heirlooms | Account-wide | ✅ `/profile/user/wow/collections/heirlooms` | Lower priority for farm planning |
| Achievements | Char + Account | ✅ `/character/{realm}/{name}/achievements` | Thousands; filter to collectible ones |
| Transmog Appearances | Character | ✅ `/character/{realm}/{name}/appearance` | Appearance ID lists, not names |
| Hunter Pets | Character | ✅ `/character/{realm}/{name}/hunter-pets` | Stable slots; 50+ exotic families |
| Housing Decor | Account-wide | ⚠️ `/profile/user/wow/collections/` | New in TWW; endpoint availability TBD |

> **Account-wide vs character-specific:** Mounts, pets, toys, and housing are shared
> across the account. Transmog and hunter pets are per-character. Achievements are
> mostly account-wide but some are character-specific. The sync layer must record which
> `bnet_account` owns the account-wide data and which `wow_character` owns the rest.

---

## Data Sources

### Primary: Blizzard Game Data + Profile APIs

Already integrated in `sv_common.guild_sync.blizzard_client`. What we need to add:

**Game data (full catalog — client credentials, no OAuth):**
- `/data/wow/mount/index` + `/data/wow/mount/{id}` — all mounts with source info
- `/data/wow/pet/index` + `/data/wow/pet/{id}` — all pets
- `/data/wow/toy/index` + `/data/wow/toy/{id}` — all toys
- `/data/wow/journal/instance/index` → links dungeons/raids to journal entries,
  which cross-reference to mount/item drops
- Achievement data: `/data/wow/achievement-category/index` tree

Catalog data is stable enough to cache heavily — sync weekly or on-demand from admin.

**Profile data (per account — requires OAuth `wow.profile` scope):**
- `/profile/user/wow/collections/mounts`
- `/profile/user/wow/collections/pets`
- `/profile/user/wow/collections/toys`
- `/profile/user/wow/collections/heirlooms`
- `/profile/wow/character/{realm}/{name}/achievements`
- `/profile/wow/character/{realm}/{name}/appearance` (transmog sets)
- `/profile/wow/character/{realm}/{name}/hunter-pets`

OAuth tokens are already stored per player via `bnet_character_sync`. The same refresh
flow used for character sync applies here.

### Secondary: External Reference Links (no API needed)

These sites don't offer public APIs we can consume, but they are authoritative sources
for detailed farming guides. The integration is outbound links only — we show a summary
and a "Full Guide →" link.

| Site | What we link to |
|---|---|
| **Wowhead** | Per-item/mount/achievement detail pages (`wowhead.com/mount=N`) |
| **WarcraftMounts.com** | Mount farming guides + drop location maps |
| **WoWDB** | Alternative item detail reference |

Since Blizzard's game data API includes `source_type` and a `source` sub-object
(often with journal instance IDs), we can generate Wowhead links directly from the
API data we already have — no scraping needed.

### Possible: WoW Addon Export

For users willing to install a small addon, we could read their collection state from
a SavedVariables file instead of relying solely on the Blizzard API (which can lag
behind actual game state by hours). This would be an optional enhancement, not a
requirement for the base phase.

---

## Sub-Phases

### Phase Y.1 — Game Data Catalog Sync

**Goal:** Populate the platform's DB with the full list of what exists — all mounts,
pets, and toys, with their source info. This is the reference catalog that all
"missing" calculations compare against.

**New tables:**

```sql
-- Reference catalog synced from Blizzard game data API
CREATE TABLE guild_identity.collection_items (
    id              SERIAL PRIMARY KEY,
    category        VARCHAR(20) NOT NULL,   -- 'mount','pet','toy','achievement'
    blizzard_id     INTEGER NOT NULL,
    name            VARCHAR(255) NOT NULL,
    icon_url        VARCHAR(500),
    source_type     VARCHAR(50),            -- 'drop','vendor','quest','achievement','tcg','promotion','craft'
    source_name     VARCHAR(255),           -- e.g. "Onyxia" or "Reins of the Azure Drake"
    journal_instance_id INTEGER,            -- links to dungeon/raid; used for route grouping
    expansion_id    INTEGER,                -- 0=Classic, 8=TWW, etc.
    faction         VARCHAR(20),            -- 'alliance','horde','neutral'
    is_tradeable    BOOLEAN DEFAULT FALSE,
    wowhead_url     VARCHAR(500),
    notes           TEXT,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (category, blizzard_id)
);

CREATE INDEX idx_collection_items_category  ON guild_identity.collection_items (category);
CREATE INDEX idx_collection_items_instance  ON guild_identity.collection_items (journal_instance_id);
CREATE INDEX idx_collection_items_expansion ON guild_identity.collection_items (expansion_id);
```

**New service:** `sv_common.guild_sync.collection_catalog_sync`
- `sync_mounts(pool, blizzard_client)` — fetch index, upsert all items
- `sync_pets(pool, blizzard_client)`
- `sync_toys(pool, blizzard_client)`
- Scheduler job: `run_collection_catalog_sync()` — weekly, Sunday 2 AM UTC
- Admin: `POST /api/v1/admin/collection-catalog/sync` — on-demand trigger

**Tests:** Unit test upsert logic with mocked Blizzard responses. No network calls in
unit tests.

---

### Phase Y.2 — Character/Account Collection Sync

**Goal:** Record what each player has actually collected. This is the OAuth-gated sync
that runs against a player's Battle.net account.

**New tables:**

```sql
-- What a player has collected (account-wide categories keyed to bnet_account)
CREATE TABLE guild_identity.player_collections (
    id              SERIAL PRIMARY KEY,
    bnet_account_id INTEGER NOT NULL REFERENCES guild_identity.battlenet_accounts(id) ON DELETE CASCADE,
    category        VARCHAR(20) NOT NULL,
    blizzard_id     INTEGER NOT NULL,       -- references collection_items.blizzard_id
    collected_at    TIMESTAMPTZ,            -- if Blizzard provides it; otherwise NULL
    quality         SMALLINT,               -- for pets: 1=poor…4=epic
    level           SMALLINT,               -- for pets
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bnet_account_id, category, blizzard_id)
);

-- Character-specific collections (transmog, hunter pets)
CREATE TABLE guild_identity.character_collections (
    id              SERIAL PRIMARY KEY,
    character_id    INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    category        VARCHAR(20) NOT NULL,   -- 'transmog','hunter_pet','achievement'
    blizzard_id     INTEGER NOT NULL,
    extra_data      JSONB,                  -- hunter pet: name, level, slot; achievement: completed_at
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, category, blizzard_id)
);
```

**New service:** `sv_common.guild_sync.collection_sync`
- `sync_player_collections(pool, player_id, access_token)` — fetches account-wide
  collections (mounts, pets, toys) and upserts into `player_collections`
- `sync_character_collections(pool, character_id, realm_slug, char_name, access_token)`
  — fetches character-specific collections (transmog, hunter pets, achievements)
- `get_valid_access_token(pool, player_id)` — reuse existing pattern from
  `bnet_character_sync`

Sync is triggered:
1. After OAuth link (same callback that triggers character sync)
2. On the daily `run_bnet_character_refresh()` scheduler job (add collection sync there)
3. On-demand via the Collections panel "Refresh" button

---

### Phase Y.3 — Collections Panel (My Characters)

**Goal:** Add a "Collections" panel to the My Characters page, alongside the existing
Progression, M+, WCL, and Market panels.

**Panel layout:**
```
[Collections]
  Progress summary bar: Mounts 348/1050 | Pets 412/1743 | Toys 88/610 | ...

  Category tabs: Mounts | Pets | Toys | Transmog | Achievements | Hunter Pets | Housing

  [Filters: Expansion ▾] [Source ▾] [Show: All / Missing / Collected]

  Grid of items (icon + name + source) — missing items dimmed; collected items gold tick
  Each item has: ★ Wishlist button | → Wowhead link
```

**API endpoint:**
```
GET /api/v1/me/character/{id}/collections?category=mounts&expansion=8&show=missing
→ {
    ok: true,
    summary: { mounts: {collected: 348, total: 1050}, pets: {...}, ... },
    items: [
        { id: 1, name: "...", icon_url: "...", source_type: "drop", source_name: "Onyxia",
          journal_instance_id: 77, expansion_id: 1, collected: false, in_wishlist: false,
          wowhead_url: "..." },
        ...
    ]
  }
```

Collections are account-wide for mounts/pets/toys — the character is used only to
determine which account to look up. The panel makes this clear with a note:
"Collection data is account-wide — same across all your characters."

Transmog and hunter pets display the character's specific state.

---

### Phase Y.4 — Wishlist

**Goal:** Let players star items they want and manage a personal wishlist.

**New table:**

```sql
CREATE TABLE guild_identity.collection_wishlist (
    id              SERIAL PRIMARY KEY,
    player_id       INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    category        VARCHAR(20) NOT NULL,
    blizzard_id     INTEGER NOT NULL,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes           VARCHAR(500),
    UNIQUE (player_id, category, blizzard_id)
);
```

**API endpoints:**
```
POST   /api/v1/me/wishlist          { category, blizzard_id }   → add item
DELETE /api/v1/me/wishlist/{id}                                  → remove item
GET    /api/v1/me/wishlist?category=mounts                       → list wishlist
```

The wishlist is player-scoped (not per-character), because most collections are
account-wide. The UX is a toggle star on each item card in the Collections panel.
A dedicated `/wishlist` tab (or sub-section within the Collections panel) shows
only starred items with their route grouping.

---

### Phase Y.5 — Farm Route Planner

**Goal:** Group wishlist items by source content and produce a prioritized farm list.

**The algorithm:**
1. Fetch all wishlist items for the player with their `journal_instance_id` and
   `source_type`.
2. Group by `journal_instance_id` (for dungeon/raid drops) or by `source_type`
   (for vendor, quest, achievement, world drop, etc.).
3. Score each group by item count — most items per lockout at the top.
4. Within each group, note the specific bosses/sources for each item so the player
   knows whether they need a full clear or can target specific bosses.
5. Items with no `journal_instance_id` (world drops, vendor, TCG, promotion) get
   grouped separately by source_type with links to their Wowhead pages.

**Output format (rendered in the panel):**

```
Your Farm Route (12 wishlist items across 5 sources)

  ★★★  Onyxia's Lair (3 items)
       • Reins of the Onyxian Drake — Onyxia (10% drop)
       • Pristine Black Diamond — Trash drop
       • Formula: Enchant Chest — Rare trash
       → Wowhead Guide

  ★★   Karazhan (2 items)
       • Fiery Warhorse's Reins — Attumen the Huntsman (1%)
       • Goredome — Netherspite
       → Wowhead Guide

  ...

  World / Other (2 items — no lockout)
       • Spectral Tiger — Black Market AH / TCG (Wowhead link)
       • Tyrael's Charger — Blizzard Shop (link)
```

**Stretch: TomTom Strings**

For outdoor world drops or non-instanced sources, we could generate a TomTom-compatible
waypoint string (e.g., `/way 52.4 37.8 Huolon`) from stored coordinate data. This
requires augmenting `collection_items` with optional `map_id`, `coord_x`, `coord_y`
columns. This is optional for the initial build — only worth doing if we can reliably
populate coordinates from the Blizzard API or an open dataset.

**API endpoint:**
```
GET /api/v1/me/wishlist/route
→ {
    ok: true,
    groups: [
        { type: "instance", journal_instance_id: 77, name: "Onyxia's Lair",
          expansion_id: 1, item_count: 3, wowhead_url: "...",
          items: [ { name: "...", source_name: "Onyxia", drop_chance: "10%" }, ... ] },
        ...
    ],
    ungrouped: [ ... ]
  }
```

---

## DB Migration Plan

| Migration | Contents |
|---|---|
| 0051 | `collection_items` catalog table + indexes |
| 0052 | `player_collections` + `character_collections` + indexes |
| 0053 | `collection_wishlist` |

---

## Admin Additions

- **Admin → Reference Tables** (existing page): add "Collection Catalog" tab showing
  item counts by category + last synced timestamp + "Sync Now" button.
- No dedicated admin page needed; catalog is managed via sync, not manual entry.

---

## Open Questions / Design Decisions

**1. Collection scope mismatch**
The Collections panel lives on the My Characters page, which is character-centric.
But mounts/pets/toys are account-wide. We display account-wide data regardless of which
character is selected, with a clear note. Transmog and hunter pets remain
character-specific. Decide early to avoid confusing the UI.

**2. Catalog completeness**
The Blizzard Game Data API doesn't always include source info for every item —
some mounts have `source_type: null`. We can populate gaps manually over time
or leave them as "Unknown source" with a Wowhead link. Decide on the policy.

**3. Achievement scope**
Achievements number in the thousands. Most are not about collecting anything.
For this phase, scope achievements to: mount-reward achievements, pet-reward
achievements, and title-reward achievements. Full achievement tracking is its own
project.

**4. Housing decor availability**
The War Within housing launched mid-2025. Blizzard's API coverage may be incomplete.
Treat housing decor as a stretch goal for Y.1/Y.2 — add it only if the API endpoints
are reliable and return useful source data.

**5. Transmog complexity**
Transmog appearances are tracked at the appearance ID level, not item level.
The Blizzard API returns `appearance_ids` the character has unlocked. Mapping
appearance IDs back to their source items (for "how to farm this look") requires
joining against the item/appearance game data endpoints. This is significantly
more complex than mounts/pets. Consider scoping transmog to "show what you have"
without route planning in the first pass.

**6. Hunter pets**
Hunter pets are in the stable, not a collection in the "go farm it" sense — though
rare or exotic pets (like Time-Lost Proto-Drake tameable spirit beasts) are absolutely
collectibles. The Blizzard API returns a character's stable list with pet names and
creature IDs. For the initial pass, show the stable contents. Route planning for rare
spawns (Loque'nahak etc.) is a stretch goal.

---

## What Already Exists (Reusable)

| Asset | Where | Why it helps |
|---|---|---|
| Battle.net OAuth + token storage | `sv_common.guild_sync.bnet_character_sync` | Token refresh already works |
| Blizzard API client | `sv_common.guild_sync.blizzard_client` | Add collection endpoints here |
| `battlenet_accounts` table | existing | FK for account-wide collection rows |
| My Characters panel system | `my_characters.html` + `my_characters.js` | Collections becomes a new panel tab |
| `member_routes.py` | `src/guild_portal/api/member_routes.py` | Add collection + wishlist + route endpoints |

---

## What This Phase Does NOT Do

- No guild-wide collection leaderboards (interesting, but out of scope)
- No sharing or comparing wishlists between players
- No in-game addon integration (GuildSync could be extended later, but not here)
- No crafted-item transmog sourcing (too complex for initial pass)
- No full achievement tree tracking (only achievement rewards that unlock collectibles)
- No pricing integration for TCG/AH-obtainable items (interesting but separate concern)
