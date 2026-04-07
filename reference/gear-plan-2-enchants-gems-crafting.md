# Gear Plan Phase 2: Enchants, Gems & Demand-Driven Crafting

> **Status:** Planning — to be executed after Gear Plan Phase 1 is complete  
> **Motivation:** Connect gear plans to enchant/gem recommendations, aggregate demand across raid mains, and surface actionable crafting gaps to the guild.

---

## Vision

Gear plans currently track which *items* a player wants. This phase extends that to include **enchants** (per gear slot) and **gems** (per socket). With those selections stored per player, we can:

1. Tell a player "here are your recommended enchants and gems, and here's who in the guild can craft them"
2. Give officers a raid-team demand view: "15 people need Flask of __, 8 need Sapphire Cluster, nobody can craft Crystalline Radiance"
3. Update Crafting Corner to show live demand counts per craftable item
4. Drive the **Market View** from gear plan demand — no more manual tracked_items management for enchants/gems
5. Have the bot post a weekly "crafting gap report" to Discord

The core insight: we already know **who can make what** (`character_recipes`). We just need to connect the output side (`recipes` → crafted item) to the demand side (gear plans).

---

## Prerequisites / Known Gaps

### Gap 1: `recipes` has no output item link

The `recipes` table stores `blizzard_spell_id` and `name` but **not** which item the recipe produces. We need this to enable "who can craft item X?" queries.

**Resolution:** Phase 2A adds `output_item_id` to `recipes` and populates it via a two-step resolution process (see below).

### Gap 2: Enchants are tracked as raw spell IDs in `character_equipment`

`character_equipment.enchant_id` is a raw Blizzard enchantment spell ID — not resolved to an item name or linked to `wow_items`. We know *which enchant* a player currently has but can't display it by name or link it to a recipe.

**Resolution:** Phase 2A resolves enchant spell IDs to `wow_items` using the same two-step process (fuzzy name match + Wowhead fallback). This is a **one-time backfill job** run from the admin panel, not part of the recurring sync.

### Gap 3: No enchant/gem recommendations data

`bis_list_entries` covers gear slots only. We need a parallel data source for enchant and gem recommendations per spec.

**Resolution:** Phase 2B adds new tables + extends BIS scraping.

---

## Enchant/Recipe Name Resolution (Phase 2A Core Logic)

The challenge: we have enchant spell IDs (from Blizzard equipment API) and recipe names (from crafting sync), but no direct mapping between them and the scroll items they correspond to in `wow_items`.

### Step 1: Fuzzy Name Match

Recipe names for enchants and gems almost always contain the output item name:
- Recipe: `"Formula: Enchant Cloak — Graceful Avoidance"` → item: `"Enchant Cloak — Graceful Avoidance"`
- Recipe: `"Design: Sapphire Cluster"` → item: `"Sapphire Cluster"`
- Recipe: `"Design: Inscribed Illimited Diamond"` → item: `"Inscribed Illimited Diamond"`

Strip the `"Formula: "` / `"Design: "` / `"Technique: "` / `"Pattern: "` / `"Schematic: "` prefix from the recipe name and attempt an exact or fuzzy match against `wow_items.name`. This will cover ~90% of cases.

Implementation in `recipe_item_resolver.py` (new utility):
```python
RECIPE_PREFIX_STRIP = re.compile(
    r'^(Formula|Design|Technique|Pattern|Schematic|Recipe|Plans|Manual):\s*', re.IGNORECASE
)

async def resolve_recipe_outputs(pool) -> dict[int, int]:
    """
    Returns {recipe_id: wow_items.id} for all recipes that can be matched.
    Step 1: fuzzy name match against wow_items already in DB.
    Step 2: Wowhead lookup for unmatched recipes.
    """
```

### Step 2: Wowhead Lookup for Unmatched

For recipes that fuzzy matching can't resolve (the ~10% exception cases):
- Query Wowhead's item search API: `https://www.wowhead.com/api/item-search?q={stripped_name}`
- Parse the top result's item ID
- Upsert a stub into `wow_items` if not present
- Store the result

This step is batched and rate-limited (Wowhead is not an official API — be respectful: 1 req/sec, stop after 429).

### Admin Trigger

New button in **Admin → Crafting Sync** (or Admin → Gear Plan): **"Resolve Recipe Outputs"**
- Shows progress: "N of M recipes resolved, K unmatched"
- Unmatched recipes listed for manual review
- Can be re-run any time (idempotent — skips already-resolved recipes)
- Same flow used to resolve enchant spell IDs → item names (Blizzard returns `display_string` like "Enchant Cloak — Graceful Avoidance" in the equipment payload — use that string for the lookup rather than just the raw spell ID)

---

## WoW TWW Reference

### Enchantable Slots (TWW)
| Slot Key | Display Name | Notes |
|----------|-------------|-------|
| `enchant_back` | Cloak | |
| `enchant_chest` | Chest | |
| `enchant_wrist` | Bracers | |
| `enchant_legs` | Legs | Spellthread / Embroidery / Embellishment |
| `enchant_feet` | Boots | |
| `enchant_ring_1` | Ring 1 | Rings can have different enchants |
| `enchant_ring_2` | Ring 2 | |
| `enchant_main_hand` | Weapon | |
| `enchant_off_hand` | Off-Hand | Only if item is enchantable |

Head, shoulder, neck, hands, waist, trinkets, and shirt/tabard are **not enchantable** in TWW.

### Gems (TWW)

Gem sockets in TWW come in colors (prismatic, red, blue, yellow) but most modern sockets are **prismatic** (any gem fits). Recommendations are expressed as an **ordered priority list**, not a flat 1-or-2 choice:

- Some specs: `1× Inscribed Illimited Diamond, fill remaining with Energized Harbinger's Sapphire` (2 types)
- Other specs: `1× Diamond, 1× [specific gem], fill remaining with [budget gem]` (3 types)
- Some specs just say: `fill all with [one gem]` (1 type)

The key per-gem attributes are:
- **Which item** (the gem)
- **Socket color** it applies to (`prismatic`, `red`, `blue`, `yellow` — defaults to `prismatic`)
- **Quantity**: either a specific number (`1`, `2`) OR `NULL` meaning "fill all remaining sockets of this color"
- **Sort order**: the sequence in which to fill — spec guides say "put the expensive one in first, fill the rest with budget"

---

## Phase 2A: Recipe Output Mapping (prerequisite, ~1 migration)

**Goal:** Link `recipes` to their output `wow_items`. Enable "who can craft item X?" queries across the whole system.

### Migration 0068 changes

```sql
-- Link recipes to the item they produce
ALTER TABLE guild_identity.recipes
  ADD COLUMN output_item_id INTEGER REFERENCES guild_identity.wow_items(id) ON DELETE SET NULL;

-- Store resolved enchant item alongside the raw spell ID we already have
ALTER TABLE guild_identity.character_equipment
  ADD COLUMN enchant_item_id INTEGER REFERENCES guild_identity.wow_items(id) ON DELETE SET NULL;
```

### New utility: `recipe_item_resolver.py`

- `resolve_recipe_outputs(pool)` — fuzzy match + Wowhead fallback as described above
- `resolve_equipment_enchants(pool)` — for all `character_equipment` rows with `enchant_id IS NOT NULL` and `enchant_item_id IS NULL`, use the Blizzard `display_string` field (stored or re-fetched) to look up the enchant item via the same fuzzy/Wowhead pipeline
- Both functions return a resolution report: matched, unmatched, skipped

### `crafting_sync.py` — populate going forward

Once the resolution utility exists, extend the crafting sync recipe upsert to:
1. After inserting/updating a recipe, strip the prefix and attempt a name match against existing `wow_items`
2. If matched, set `output_item_id` immediately
3. Unmatched ones are queued for the admin-triggered Wowhead lookup

**Tests:** unit tests for prefix stripping, fuzzy matching logic, and the resolver's match/no-match paths.

---

## Phase 2B: Enchant & Gem Recommendation Tables (~1 migration + scraper extension)

**Goal:** Store per-spec enchant and gem recommendations from BIS sources, parallel to `bis_list_entries`.

### New tables

```sql
-- Enchant recommendations: what enchant item to use in each slot, per spec/hero talent/source
CREATE TABLE guild_identity.bis_enchant_recommendations (
  id              SERIAL PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES guild_identity.bis_list_sources(id) ON DELETE CASCADE,
  spec_id         INTEGER NOT NULL REFERENCES guild_identity.specializations(id) ON DELETE CASCADE,
  hero_talent_id  INTEGER REFERENCES guild_identity.hero_talents(id) ON DELETE SET NULL,
  enchant_slot    VARCHAR(30) NOT NULL,  -- 'enchant_back', 'enchant_chest', etc.
  item_id         INTEGER NOT NULL REFERENCES guild_identity.wow_items(id) ON DELETE CASCADE,
  priority        INTEGER NOT NULL DEFAULT 1,  -- for when sources list 2 options (BiS vs budget)
  notes           TEXT,
  UNIQUE (source_id, spec_id, hero_talent_id, enchant_slot, item_id)
);

-- Gem recommendations: ordered priority list per spec/source
-- quantity NULL = "fill remaining sockets of this color"
CREATE TABLE guild_identity.bis_gem_recommendations (
  id              SERIAL PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES guild_identity.bis_list_sources(id) ON DELETE CASCADE,
  spec_id         INTEGER NOT NULL REFERENCES guild_identity.specializations(id) ON DELETE CASCADE,
  hero_talent_id  INTEGER REFERENCES guild_identity.hero_talents(id) ON DELETE SET NULL,
  sort_order      INTEGER NOT NULL DEFAULT 1,  -- apply gems in this order
  item_id         INTEGER NOT NULL REFERENCES guild_identity.wow_items(id) ON DELETE CASCADE,
  quantity        INTEGER,  -- NULL = fill all remaining sockets of socket_color
  socket_color    VARCHAR(20) NOT NULL DEFAULT 'prismatic',
  notes           TEXT,
  UNIQUE (source_id, spec_id, hero_talent_id, sort_order)
);
```

### BIS scraper extension (`bis_sync.py`)

BIS guide pages (Archon, Icy Veins, Wowhead) all include "Best Enchants" and "Best Gems" sections. Extend each extractor:

- **Archon** (`json_embed`): enchants/gems are in the same JSON blob under separate keys — parse alongside gear slots
- **Icy Veins** (`html_parse`): enchants table under `#enchants` anchor; gems table under `#gems` anchor
- **Wowhead** (`wh_gatherer`): enchants/gems in dedicated guide sections

New extractor dataclasses:
```python
@dataclass
class ExtractedEnchant:
    slot: str           # 'enchant_back', 'enchant_chest', etc.
    blizzard_item_id: int
    item_name: str
    priority: int = 1

@dataclass
class ExtractedGem:
    blizzard_item_id: int
    item_name: str
    sort_order: int = 1     # apply in this order
    quantity: int | None = None   # None = fill remaining
    socket_color: str = 'prismatic'
```

`_upsert_bis_enchants()` and `_upsert_bis_gems()` parallel `_upsert_bis_entries()`.

### Admin UI extension

Add **Enchants** and **Gems** tabs to `/admin/gear-plan`:
- Matrix layout (source × spec × hero talent), same as gear BIS tabs
- Cross-reference panel: do Archon and IV agree on the ring enchant for Arms War + Colossus?
- Sync button triggers enchant/gem extraction alongside gear slot BIS

---

## Phase 2C: Player Gear Plan — Enchants & Gems (~1 migration + UI)

**Goal:** Let players see enchant/gem recommendations within their gear plan and confirm or override selections.

### New tables

```sql
-- Player's enchant selections within a gear plan
CREATE TABLE guild_identity.gear_plan_enchants (
  id              SERIAL PRIMARY KEY,
  plan_id         INTEGER NOT NULL REFERENCES guild_identity.gear_plans(id) ON DELETE CASCADE,
  enchant_slot    VARCHAR(30) NOT NULL,
  item_id         INTEGER REFERENCES guild_identity.wow_items(id) ON DELETE SET NULL,
  blizzard_item_id INTEGER,
  item_name       VARCHAR(200),
  source          VARCHAR(10) NOT NULL DEFAULT 'recommended'
                  CHECK (source IN ('recommended', 'custom', 'skipped')),
  is_confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (plan_id, enchant_slot)
);

-- Player's gem priority list within a gear plan
-- quantity NULL = fill remaining sockets of socket_color
CREATE TABLE guild_identity.gear_plan_gems (
  id              SERIAL PRIMARY KEY,
  plan_id         INTEGER NOT NULL REFERENCES guild_identity.gear_plans(id) ON DELETE CASCADE,
  sort_order      INTEGER NOT NULL DEFAULT 1,
  item_id         INTEGER REFERENCES guild_identity.wow_items(id) ON DELETE SET NULL,
  blizzard_item_id INTEGER,
  item_name       VARCHAR(200),
  quantity        INTEGER,  -- NULL = fill remaining
  socket_color    VARCHAR(20) NOT NULL DEFAULT 'prismatic',
  source          VARCHAR(10) NOT NULL DEFAULT 'recommended'
                  CHECK (source IN ('recommended', 'custom')),
  is_confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (plan_id, sort_order)
);
```

### Auto-populate on plan creation/spec change

When a player's gear plan is saved or spec/hero talent changes:
1. Query `bis_enchant_recommendations` for their spec + hero talent + BIS source
2. Upsert `gear_plan_enchants` — `source='recommended'`, `is_confirmed=FALSE`; skip slots already confirmed by the player
3. Same for `gear_plan_gems` (preserve confirmed rows by sort_order)

### API endpoints (`bis_routes.py` additions)

```
GET  /api/v1/gear-plan/{plan_id}/enchants-gems
     → {
         enchants: [{
           slot, item_id, item_name, icon_url, source, is_confirmed,
           current_enchant_item_id,   -- from character_equipment
           current_enchant_name,
           is_equipped: bool,         -- current == recommended
           crafter_count,
           crafters: [{player_name, character_name, discord_username, recipe_name}]
         }],
         gems: [{
           sort_order, item_id, item_name, icon_url, quantity, socket_color,
           source, is_confirmed,
           crafter_count,
           crafters: [{...}]
         }],
         socket_count: N   -- total sockets across current equipped gear
       }

PATCH /api/v1/gear-plan/{plan_id}/enchants-gems
     body: { enchants: [{slot, item_id?, source, is_confirmed}],
             gems: [{sort_order, item_id?, quantity, socket_color, source, is_confirmed}] }

GET  /api/v1/gear-plan/{plan_id}/crafters/{item_id}
     → [{player_name, character_name, discord_username, recipe_name}]
     (works for both enchant and gem items — same query path)
```

Crafter query (reusable):
```sql
SELECT p.display_name, wc.name AS character_name,
       du.username AS discord_username, r.name AS recipe_name
FROM guild_identity.character_recipes cr
JOIN guild_identity.recipes r ON r.id = cr.recipe_id AND r.output_item_id = :item_id
JOIN guild_identity.wow_characters wc ON wc.id = cr.character_id AND wc.in_guild = TRUE
JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
JOIN guild_identity.players p ON p.id = pc.player_id
LEFT JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
```

---

## Phase 2C UI: Gear Plan Layout

### Overall layout — Blizzard character screen style

The gear plan page uses a **3-column layout** mirroring the WoW in-game character panel:

```
┌─────────────────────────────────────────────────────────────┐
│  Left Gear Column  │   Gem Panel (middle)  │ Right Gear Column │
│                    │                       │                   │
│  [Head]  [Enchant] │  ┌──────────────────┐ │ [Enchant] [Hands] │
│  [Neck]            │  │  Gem Slots        │ │           [Waist] │
│  [Shoulder][Encht] │  │  ○ Prismatic ×3  │ │ [Enchant] [Legs]  │
│  [Back] [Enchant]  │  │  ○ Red ×1        │ │ [Enchant] [Feet]  │
│  [Chest][Enchant]  │  └──────────────────┘ │ [Enchant] [Ring1] │
│  [Wrist][Enchant]  │                       │ [Enchant] [Ring2] │
│                    │                       │           [Trin1] │
│                    │                       │           [Trin2] │
│                    │                       │                   │
│          [Main Hand][Enchant]  [Enchant][Off Hand]            │
└─────────────────────────────────────────────────────────────┘
```

**Slot ordering** (matches WoW character screen and Blizzard armory):

| Left column | Right column |
|------------|-------------|
| Head | Hands |
| Neck | Waist |
| Shoulders | Legs |
| Back | Feet |
| Chest | Ring 1 |
| Wrist | Ring 2 |
| | Trinket 1 |
| | Trinket 2 |

Bottom row: Main Hand, Off Hand (centered, spanning full width)

### Gear slot cells

Each slot in the left/right columns is rendered as:

```
┌──────────────────────────────────────┐
│ [Item Icon] Item Name          [ilvl] │  ← gear slot (existing)
│             Quality track / BIS badge │
└──────────────────────────────────────┘
              ↓ (enchantable slots only)
┌──────────────────────────────────────┐
│ ✦ Enchant: [icon] Enchant Name    ✓  │  ← enchant box, right of item
│            [Crafters: 3]             │
└──────────────────────────────────────┘
```

- Green checkmark (✓) = currently equipped enchant matches recommended
- Amber warning (!) = currently equipped enchant differs from recommended
- Gray dash (—) = no current enchant detected
- "Crafters: N" badge opens the same popover used by Crafting Corner
- Clicking the enchant box opens a compact picker: recommended options from the BIS source + "Custom" + "Skip"

Slots with no enchant (head, neck, shoulder, hands, waist, trinkets) show no enchant box at all.

### Gem panel (middle column)

The gem panel is a standalone card between the two gear columns. It does **not** attach gems to specific item slots — it shows the priority list for gem selection and the socket count from current gear.

```
┌────────────────────────────────────┐
│  Gem Sockets                       │
│  You have 6 open sockets           │
│                                    │
│  Priority order:                   │
│  1. [icon] Inscribed Illimited     │
│            Diamond  ×1 prismatic   │
│            [Crafters: 2]           │
│                                    │
│  2. [icon] Energized               │
│            Harbinger's Sapphire    │
│            fill remaining          │
│            [Crafters: 5]  ✓        │
└────────────────────────────────────┘
```

Display per gem entry:
- Gem icon + name
- `×N` for fixed quantity, or `fill remaining` for NULL quantity
- Socket color pill if not prismatic (e.g., "Red socket")
- Crafter count badge + popover
- Checkmark if the player's current gem IDs include this gem type (from `character_equipment.gem_ids`)

The panel footer shows: **"N of M sockets filled with recommended gems"** based on current equipment vs. the priority list.

Players can reorder the list, change quantities, or swap to a custom gem. Clicking the gem opens the same picker pattern as enchants.

---

## Phase 2D: Raid Demand Aggregation (Officer+)

**Goal:** Give officers a raid-level view of what enchants/gems are needed and where crafting gaps exist.

### API

```
GET /api/v1/admin/gear-plan/raid-demand
    ?season_id=N  (optional, defaults to current)
    → {
        enchants: [{
          enchant_slot, item_id, item_name, icon_url,
          demand_count,    -- # confirmed plans wanting this enchant
          crafter_count,
          crafters: [{...}],
          is_gap: bool     -- crafter_count == 0
        }],
        gems: [{
          item_id, item_name, icon_url,
          demand_count,    -- total socket demand (e.g., 8 plans × avg 2 needed = 16)
          crafter_count,
          crafters: [{...}],
          is_gap: bool
        }],
        covered_players: N,
        total_raid_mains: N,
        as_of: timestamp
      }
```

**Demand scope:** Active mains = `main_character_id IS NOT NULL AND on_raid_hiatus = FALSE`, with an active `gear_plan` with at least one confirmed enchant/gem selection. Confirmed-only to exclude noise from auto-populated-but-never-reviewed plans.

**Gem demand count:** For each gem item, sum `quantity` across plans (treating `NULL` quantity as "remaining sockets" — calculate from that player's socket count minus sockets already assigned to higher-priority gems in their plan).

### Admin UI: Demand tab on `/admin/gear-plan`

Layout:
- Coverage banner: `N / M raid mains have confirmed gear plans` (link to player list)
- **Gaps** (red section): items needed by ≥1 main that zero crafters can make — "Post to Discord" button per item
- **Enchants**: table sorted by demand desc — Item | Slot | Need | Crafters | Who
- **Gems**: same format

---

## Phase 2E: Crafting Corner Integration

### Recipe list changes

Each recipe row in Crafting Corner gains:
- **Demand badge** (amber): `N need this` — populated when `output_item_id` matches active gear plan demand
- **Gap indicator** (red): shown when demand > 0 but crafter count ≤ 1

**"Missing Crafts" section** — prepended panel (collapsible, red border):
> **Missing Crafts (N items)** — needed by raiders but no one in the guild can craft them yet.

- Listed by demand desc
- No "Request Guild Order" button (no one to fill it)
- "Notify Officers" button → posts to officer channel

### API change

`GET /api/crafting/recipes/{profession_id}/{tier_id}` adds two optional fields per recipe:
```json
{ "demand_count": 3, "is_gap": false }
```
Zero if `output_item_id` is NULL or no active demand. This keeps the API change additive / non-breaking.

### Discord bot: Weekly Gap Report

New task in `scheduler.py`, runs Monday 8 AM server time (configurable):

```python
async def post_weekly_crafting_gaps(pool, bot, guild_id, channel_id): ...
```

Message format:
```
📋 Weekly Crafting Gap Report
━━━━━━━━━━━━━━━━━━━━
🔴 Needed by raiders — no crafter in guild:
  • Crystalline Radiance (×8 sockets across 6 mains) — Jewelcrafting
  • Formula: Enchant Cloak — Graceful Avoidance (×5 mains) — Enchanting

✅ Fully covered: Sapphire Cluster, Enchant Chest — Crystalline Potential, ...

Plans active: N/M raid mains. Update yours: https://pullallthethings.com/gear-plan
```

Config: opt-in toggle in Admin → Bot Settings → "Post weekly crafting gap report", channel selector (defaults to Crafters Corner channel).

---

## Phase 2F: Market View Driven by Gear Plans

**Goal:** Replace manual `tracked_items` management with automatic demand signals from gear plans.

### Current state

The Market View panel on `/my-characters` shows AH prices for items in `guild_identity.tracked_items`. This list is managed manually by officers. It works but requires someone to remember to add/remove items each tier.

### New behavior

Once gear plans include enchants and gems:

**My Characters — Market panel:**
- Show AH prices for items in the *player's own active gear plan* (gear slots, enchants, gems)
- Items already equipped at recommended quality: grey / de-emphasized
- Items still needed: highlighted
- No configuration needed — the plan drives the display automatically

**Officer/GL Market dashboard** (new or extend existing):
- Aggregate AH prices for top-demand items across all raid mains' gear plans
- Sorted by total demand desc
- Replaces the need to manually manage `tracked_items` for consumables/crafted gear

**`tracked_items` fate:**
- Keep the table and existing functionality — it still has value for non-gear items (consumables, crafting mats, etc.)
- Just add a second "source" for the Market panel: gear plan items
- Long-term: officers only need to manually track items that gear plans don't cover (e.g., pots, food, optional crafting mats)

### Implementation notes

- The price lookup itself is unchanged — `item_price_history` by `blizzard_item_id + connected_realm_id`
- The gear plan enchants and gems need their `blizzard_item_id` populated (handled in Phase 2C auto-populate)
- API change: `/api/v1/me/market` (or wherever the existing AH price endpoint is) gains an optional `?source=plan` mode that queries `gear_plan_enchants` + `gear_plan_gems` + `gear_plan_slots` instead of `tracked_items`

---

## Implementation Order

| Phase | Migration | Scope | Effort |
|-------|-----------|-------|--------|
| 2A | 0068 | `recipes.output_item_id`, `character_equipment.enchant_item_id`, `recipe_item_resolver.py`, admin trigger | Small–Medium |
| 2B | 0069 | `bis_enchant_recommendations`, `bis_gem_recommendations`, scraper extension, admin tabs | Medium |
| 2C | 0070 | `gear_plan_enchants`, `gear_plan_gems` (w/ quantity), auto-populate, gear-plan UI overhaul | Large |
| 2D | — | Raid demand API + admin Demand tab | Small |
| 2E | — | Crafting Corner badges + gap section + Discord weekly report | Small |
| 2F | — | Market View pulls from gear plan; `tracked_items` supplemental | Small |

Total: 3 migrations. Phase 2C is the biggest investment (UI is a full redesign of the gear plan page). Each phase is independently deployable.

---

## Resolved Design Decisions

1. **Enchant ID resolution:** Fuzzy name match first (strip `Formula:` prefix → match against `wow_items.name`) covers ~90%. Wowhead lookup for the remainder. **Not part of recurring sync** — runs as a one-time admin-triggered job in `recipe_item_resolver.py`. Re-runnable and idempotent.

2. **Gem model:** Priority-ordered list per plan (not 1-or-2 fixed). Each entry has `quantity` (INT or NULL for "fill remaining") and `socket_color`. This handles the full range from "1 expensive gem + fill the rest" to "6 different specific gems."

3. **UI layout:** 3-column — left gear column, middle gem panel, right gear column — mirroring Blizzard's character screen slot ordering. Enchant box appears directly below/attached to each enchantable slot's item box.

4. **Demand scope:** Confirmed selections only (is_confirmed=TRUE) to prevent noise from auto-populated-but-never-reviewed plans. Raid mains only (not alts) for the officer demand view; full guild for Crafting Corner badges.

5. **Market View:** Gear plan becomes the primary demand signal. `tracked_items` kept as supplement for items not in gear plans (consumables, etc.).

6. **Gem socket count source:** Use current equipped gear (`character_equipment.gem_ids` array length) for now. Planned-item socket count (from BIS slot selections) can be added in a future pass once Wowhead tooltip data is reliable enough to determine socket counts per item.
