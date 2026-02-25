# Phase 2.8: Crafting Corner

## Overview

This phase adds a guild-wide crafting directory to the PATT platform. The Crafting Corner
lets members browse every recipe known across all guild characters, see who can craft each
item, and request guild orders — all from a single page on the website with Discord
integration for notifications.

**What this phase produces:**
1. Blizzard API extension: `get_character_professions()` method on `BlizzardClient`
2. Database tables for professions, recipes, and character-recipe associations
3. A crafting-specific sync job with adaptive cadence (daily early in a season, weekly later)
4. Admin "force refresh" button for on-demand profession sync
5. A public `crafting-corner.html` page with profession/expansion filters and full-text search
6. Recipe drill-down showing crafters grouped by guild rank with Wowhead links
7. "Guild Order" button that posts to a `#crafters-corner` Discord channel
8. Per-player opt-in for crafting request @mentions (off by default)
9. User settings toggle on the website for crafting notification preference

**What this phase does NOT do:**
- Track crafting order fulfillment or status
- Integrate with the in-game Work Orders system
- Show reagent costs or material lists (Wowhead handles that)
- Require identity/player activation — this is character-based with optional player data

## Prerequisites

- Phase 2.7 complete (clean 3NF data model with `wow_characters`, `players`, `player_characters`)
- Blizzard API credentials configured and working (existing `BlizzardClient`)
- Discord bot running with channel posting capability
- Existing scheduler infrastructure (`GuildSyncScheduler`, APScheduler)

---

## Task 1: Blizzard API — Character Professions Endpoint

Extend `src/sv_common/guild_sync/blizzard_client.py` with a new method.

### Endpoint

```
GET /profile/wow/character/{realmSlug}/{characterName}/professions
Namespace: profile-us
```

### Response Structure

```json
{
  "primaries": [
    {
      "profession": {
        "name": "Blacksmithing",
        "id": 164
      },
      "tiers": [
        {
          "skill_points": 100,
          "max_skill_points": 100,
          "tier": {
            "name": "Khaz Algar Blacksmithing",
            "id": 2872
          },
          "known_recipes": [
            {
              "name": "Everforged Breastplate",
              "id": 453287
            }
          ]
        },
        {
          "skill_points": 175,
          "max_skill_points": 175,
          "tier": {
            "name": "Dragon Isles Blacksmithing",
            "id": 2751
          },
          "known_recipes": [...]
        }
      ]
    }
  ],
  "secondaries": [
    {
      "profession": {
        "name": "Cooking",
        "id": 185
      },
      "tiers": [...]
    }
  ]
}
```

### Key Details

- Recipe `id` is a spell ID — maps directly to Wowhead: `https://www.wowhead.com/spell={id}`
- `tiers` are expansion-specific skill tiers (e.g., "Khaz Algar Blacksmithing", "Classic Blacksmithing")
- The tier `name` contains the expansion name which we use for the expansion filter
- `known_recipes` only includes recipes the character has actually learned
- Gathering professions (Mining, Herbalism, Skinning) have tiers but no `known_recipes` — skip these
- The response includes both `primaries` (2 max) and `secondaries` (Cooking, Fishing, Archaeology)

### New Dataclass

```python
@dataclass
class CharacterProfessionData:
    """Professions data from the character professions endpoint."""
    character_name: str
    realm_slug: str
    professions: list[dict]  # Raw profession+tier+recipe structure
```

### New Method

```python
async def get_character_professions(
    self, realm_slug: str, character_name: str
) -> Optional[CharacterProfessionData]:
    """
    Fetch profession data including known recipes for a character.

    Endpoint: /profile/wow/character/{realmSlug}/{characterName}/professions
    Returns: CharacterProfessionData or None if character not found / no professions
    """
    name_lower = character_name.lower()
    name_encoded = quote(name_lower, safe='')

    path = f"/profile/wow/character/{realm_slug}/{name_encoded}/professions"
    data = await self._api_get(path)

    if not data:
        return None

    professions = []
    for section in ("primaries", "secondaries"):
        for entry in data.get(section, []):
            prof = entry.get("profession", {})
            tiers = entry.get("tiers", [])

            # Skip professions with no recipe tiers (gathering profs)
            recipe_tiers = [t for t in tiers if t.get("known_recipes")]
            if not recipe_tiers:
                continue

            professions.append({
                "profession_name": prof.get("name"),
                "profession_id": prof.get("id"),
                "tiers": [
                    {
                        "tier_name": t["tier"]["name"],
                        "tier_id": t["tier"]["id"],
                        "skill_points": t.get("skill_points", 0),
                        "max_skill_points": t.get("max_skill_points", 0),
                        "known_recipes": [
                            {"name": r["name"], "id": r["id"]}
                            for r in t.get("known_recipes", [])
                        ],
                    }
                    for t in recipe_tiers
                ],
            })

    if not professions:
        return None

    return CharacterProfessionData(
        character_name=character_name,
        realm_slug=realm_slug,
        professions=professions,
    )
```

---

## Task 2: Database Schema

All tables in the `guild_identity` schema. These are reference + junction tables — no
changes to existing tables except adding one preference column.

### New Tables

```sql
-- Professions reference (Alchemy, Blacksmithing, etc.)
-- Seeded from Blizzard API data during first sync
CREATE TABLE guild_identity.professions (
    id SERIAL PRIMARY KEY,
    blizzard_id INTEGER NOT NULL UNIQUE,     -- Blizzard's profession ID (164 = Blacksmithing)
    name VARCHAR(50) NOT NULL UNIQUE,        -- "Blacksmithing", "Alchemy", etc.
    is_primary BOOLEAN DEFAULT TRUE,         -- FALSE for Cooking, Fishing, Archaeology
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Expansion tiers for each profession (e.g., "Khaz Algar Blacksmithing")
-- Seeded/updated during sync from tier data in API response
CREATE TABLE guild_identity.profession_tiers (
    id SERIAL PRIMARY KEY,
    profession_id INTEGER NOT NULL REFERENCES guild_identity.professions(id) ON DELETE CASCADE,
    blizzard_tier_id INTEGER NOT NULL UNIQUE, -- Blizzard's skill tier ID
    name VARCHAR(100) NOT NULL,               -- "Khaz Algar Blacksmithing"
    expansion_name VARCHAR(50),               -- Derived: "Khaz Algar", "Dragon Isles", "Classic", etc.
    sort_order INTEGER DEFAULT 0,             -- Higher = newer expansion (for default filter)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(profession_id, blizzard_tier_id)
);

-- Recipe reference (every unique recipe we've seen)
CREATE TABLE guild_identity.recipes (
    id SERIAL PRIMARY KEY,
    blizzard_spell_id INTEGER NOT NULL UNIQUE, -- The spell/recipe ID → Wowhead URL
    name VARCHAR(200) NOT NULL,
    profession_id INTEGER NOT NULL REFERENCES guild_identity.professions(id) ON DELETE CASCADE,
    tier_id INTEGER NOT NULL REFERENCES guild_identity.profession_tiers(id) ON DELETE CASCADE,
    wowhead_url VARCHAR(300) GENERATED ALWAYS AS (
        'https://www.wowhead.com/spell=' || blizzard_spell_id
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_recipes_profession ON guild_identity.recipes(profession_id);
CREATE INDEX idx_recipes_tier ON guild_identity.recipes(tier_id);
CREATE INDEX idx_recipes_name_lower ON guild_identity.recipes(LOWER(name));

-- Junction: which characters know which recipes
CREATE TABLE guild_identity.character_recipes (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    recipe_id INTEGER NOT NULL REFERENCES guild_identity.recipes(id) ON DELETE CASCADE,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, recipe_id)
);

CREATE INDEX idx_char_recipes_character ON guild_identity.character_recipes(character_id);
CREATE INDEX idx_char_recipes_recipe ON guild_identity.character_recipes(recipe_id);

-- Crafting sync configuration (single row, like discord_config)
CREATE TABLE guild_identity.crafting_sync_config (
    id SERIAL PRIMARY KEY,
    current_cadence VARCHAR(10) NOT NULL DEFAULT 'weekly',  -- 'daily' or 'weekly'
    cadence_override_until TIMESTAMPTZ,                      -- If set, use daily until this date
    expansion_name VARCHAR(50),                              -- e.g., "The War Within", "Midnight"
    season_number INTEGER,                                   -- e.g., 1, 2, 3
    season_start_date TIMESTAMPTZ,                           -- Set when a new season starts
    is_first_season BOOLEAN DEFAULT FALSE,                   -- First season of expansion = 4 weeks daily
    last_sync_at TIMESTAMPTZ,
    next_sync_at TIMESTAMPTZ,
    last_sync_duration_seconds FLOAT,
    last_sync_characters_processed INTEGER,
    last_sync_recipes_found INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- Display name is always derived: "{expansion_name} Season {season_number}"
-- e.g., "The War Within Season 2", "Midnight Season 1"
-- This is computed in application code, never stored as a single field.
```

### Modification to Existing Table

Add crafting notification preference to `guild_identity.players`:

```sql
ALTER TABLE guild_identity.players
    ADD COLUMN crafting_notifications_enabled BOOLEAN DEFAULT FALSE;
```

This is the opt-in flag. When TRUE and the player has a `discord_user_id`, they get @mentioned
in the `#crafters-corner` channel when someone requests an item one of their characters can craft.

### Environment Variables

```bash
# New for Phase 2.8
PATT_CRAFTERS_CORNER_CHANNEL_ID=<discord_channel_id>  # The #crafters-corner channel
```

---

## Task 3: Crafting Sync Job

### Sync Logic

Add to `src/sv_common/guild_sync/crafting_sync.py`:

```python
"""
Crafting profession sync — fetches known recipes for all guild characters.

Adaptive cadence:
- First season of expansion: daily for 4 weeks from season_start_date
- Subsequent seasons: daily for 2 weeks from season_start_date
- After the daily window: weekly
- Manual override: admin can force a refresh at any time

The sync:
1. Fetches profession data for every non-removed character in wow_characters
2. Upserts professions, tiers, and recipes into reference tables
3. Updates the character_recipes junction table
4. Logs sync stats to crafting_sync_config
"""
```

Key implementation details:

- Batch character profession requests (same pattern as `sync_full_roster` — batches of 10 with 0.5s delay)
- Upsert professions and tiers on the fly from API response data (no separate seed step needed)
- For recipes: upsert into `recipes` table, then upsert `character_recipes` junction
- On each sync, also remove `character_recipes` rows for recipes a character no longer knows (recipe unlearned or character left guild)
- Characters with `removed_at IS NOT NULL` are skipped entirely
- Log to `guild_identity.sync_log` with source `'crafting_sync'`

### Cadence Logic

```python
def compute_sync_cadence(config: CraftingSyncConfig) -> tuple[str, int]:
    """
    Determine if we should sync daily or weekly based on season timing.

    Returns: (cadence, daily_days_remaining)
        cadence: 'daily' or 'weekly'
        daily_days_remaining: days left in the daily window (0 if weekly)
    """
    now = datetime.now(timezone.utc)

    # Admin override takes priority
    if config.cadence_override_until and now < config.cadence_override_until:
        remaining = (config.cadence_override_until - now).days
        return 'daily', max(remaining, 0)

    if not config.season_start_date:
        return 'weekly', 0

    days_since_season = (now - config.season_start_date).days
    daily_window = 28 if config.is_first_season else 14

    if days_since_season <= daily_window:
        return 'daily', daily_window - days_since_season

    return 'weekly', 0


def get_season_display_name(config: CraftingSyncConfig) -> str:
    """Build display name from expansion_name and season_number fields."""
    if config.expansion_name and config.season_number:
        return f"{config.expansion_name} Season {config.season_number}"
    return "No season configured"
```

### Scheduler Integration

Add the crafting sync job to `GuildSyncScheduler.start()`:

```python
# Crafting sync: runs on adaptive cadence
self.scheduler.add_job(
    self.run_crafting_sync,
    CronTrigger(hour=3, minute=0),  # Check daily at 3 AM
    id="crafting_sync",
    name="Crafting Professions Sync",
    misfire_grace_time=3600,
)
```

The job runs the CronTrigger daily but checks the cadence config internally:
- If cadence is `'daily'`: always run
- If cadence is `'weekly'`: only run if 7+ days since last sync

### Admin Force Refresh

Add a new API endpoint:

```python
@guild_sync_router.post("/crafting/trigger")
async def trigger_crafting_sync(
    scheduler=Depends(get_sync_scheduler),
):
    """Manually trigger a crafting professions sync (admin only)."""
    import asyncio
    asyncio.create_task(scheduler.run_crafting_sync(force=True))
    return {"ok": True, "status": "crafting_sync_triggered"}
```

Add a button to the admin dashboard that calls this endpoint.

---

## Task 4: Data Access Layer

Create `src/sv_common/guild_sync/crafting_service.py`:

This service provides all the queries the frontend needs.

### Core Queries

```python
async def get_profession_list(pool: asyncpg.Pool) -> list[dict]:
    """Return all professions that have at least one known recipe, sorted alphabetically."""

async def get_expansion_list(pool: asyncpg.Pool, profession_id: int) -> list[dict]:
    """Return all tiers for a profession, sorted by sort_order DESC (newest first)."""

async def get_recipes_for_filter(
    pool: asyncpg.Pool,
    profession_id: int,
    tier_id: int,
) -> list[dict]:
    """
    Return all recipes for a profession+tier combo, sorted alphabetically.
    Each recipe includes: id, name, blizzard_spell_id, wowhead_url, crafter_count.
    """

async def get_recipe_crafters(pool: asyncpg.Pool, recipe_id: int) -> dict:
    """
    Return all characters that know a recipe, grouped by guild rank.

    Returns:
    {
        "recipe": {"name": ..., "wowhead_url": ...},
        "rank_groups": [
            {
                "rank_name": "Guild Leader",
                "rank_level": 0,
                "crafters": [
                    {
                        "character_name": "Trogmoon",
                        "character_class": "Druid",
                        "realm_slug": "senjin",
                        "guild_rank_name": "Guild Leader",
                        "player_discord_id": "123456789",  -- NULL if no player link
                        "player_discord_username": "Trog",  -- NULL if no player link
                        "crafting_notifications_enabled": True,  -- from player prefs
                    }
                ]
            },
            {"rank_name": "Officer", "rank_level": 1, "crafters": [...]},
            {"rank_name": "Veteran", "rank_level": 2, "crafters": [...]},
            {"rank_name": "Member", "rank_level": 3, "crafters": [...]},
            {"rank_name": "Initiate", "rank_level": 4, "crafters": [...]},
        ]
    }

    The query joins:
    character_recipes → wow_characters (for name, class, rank)
    wow_characters → player_characters → players (for discord + notification pref)
    players → discord_users (for discord_id, username)

    Characters without a player link still appear — they just have NULL discord fields.
    """

async def search_recipes(pool: asyncpg.Pool, query: str) -> list[dict]:
    """
    Full-text search across all recipes regardless of profession/expansion.
    Returns: [{id, name, wowhead_url, profession_name, tier_name, crafter_count}]
    Uses ILIKE with wildcards for simplicity. Limit to 100 results.
    """

async def get_sync_status(pool: asyncpg.Pool) -> dict:
    """
    Return sync status for display on the crafting corner page.

    Returns: {
        "season_name": "The War Within Season 2",
        "last_sync_at": "2026-02-24T03:00:00Z",
        "next_sync_at": "2026-02-25T03:00:00Z",
        "current_cadence": "daily",
        "daily_days_remaining": 12,  -- 0 if weekly
    }
    """
```

---

## Task 5: API Routes

Add to the existing guild_sync_router or create a new crafting router.

```python
crafting_router = APIRouter(prefix="/api/crafting", tags=["Crafting Corner"])

GET  /api/crafting/professions              → get_profession_list
GET  /api/crafting/expansions/{prof_id}     → get_expansion_list
GET  /api/crafting/recipes/{prof_id}/{tier_id} → get_recipes_for_filter
GET  /api/crafting/recipe/{recipe_id}/crafters → get_recipe_crafters
GET  /api/crafting/search?q={query}         → search_recipes
GET  /api/crafting/sync-status              → get_sync_status
POST /api/crafting/guild-order              → post_guild_order (auth required)
POST /api/crafting/preferences              → update_crafting_preferences (auth required)
GET  /api/crafting/preferences              → get_crafting_preferences (auth required)
```

### Guild Order Endpoint

```python
class GuildOrderRequest(BaseModel):
    recipe_id: int
    message: str = ""  # Optional note from the requester

@crafting_router.post("/guild-order")
async def post_guild_order(
    request: Request,
    order: GuildOrderRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """
    Post a guild crafting order to the #crafters-corner Discord channel.

    Requirements:
    - User must be logged in (JWT auth)
    - User must have discord_user_id linked on their player record
    - Recipe must exist in the database

    The bot posts an embed to #crafters-corner with:
    - Recipe name + Wowhead link
    - Who requested it (Discord @mention)
    - Optional message
    - List of @mentions for opted-in crafters who know this recipe

    No DMs. Everything happens in the channel, visible to all.
    """
```

---

## Task 6: Discord Bot Integration

### Guild Order Embed

When someone requests a guild order, the bot posts a rich embed to `#crafters-corner`:

```python
embed = discord.Embed(
    title=f"🔨 Guild Order: {recipe_name}",
    url=wowhead_url,
    description=f"Requested by <@{requester_discord_id}>",
    color=0xd4a84b,  # PATT gold
)

if message:
    embed.add_field(name="Note", value=message, inline=False)

# List who can craft it (just character names, not full drill)
crafter_names = ", ".join(c["character_name"] for c in crafters)
embed.add_field(name="Known Crafters", value=crafter_names, inline=False)

embed.set_footer(text="View recipe on Wowhead ↑ • Crafting Corner on pullallthething.com")

# Build @mention string for opted-in players
opted_in_mentions = " ".join(
    f"<@{c['player_discord_id']}>"
    for c in crafters
    if c.get("crafting_notifications_enabled") and c.get("player_discord_id")
)

# Send embed + mentions (mentions outside embed so Discord pings work)
content = opted_in_mentions if opted_in_mentions else None
await channel.send(content=content, embed=embed)
```

---

## Task 7: Frontend — crafting-corner.html

### Page Layout

Server-rendered Jinja2 template following the existing dark tavern aesthetic.

```
┌─────────────────────────────────────────────────────────────────┐
│  ⚒️ Crafting Corner                                            │
│  The War Within Season 2                                        │
│  Last refreshed: Feb 24, 2026 3:00 AM                          │
│  Next refresh: Feb 25, 2026 3:00 AM                            │
│  Refresh frequency: Daily (12 days remaining) ← or "Weekly"   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Profession: [Alchemy ▼]    Expansion: [Khaz Algar ▼]          │
│                                                                 │
│  🔍 [Search all recipes...                              ]      │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Recipe List (alphabetical)                    ### crafters     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Algari Healing Potion                            (3)    │   │
│  │ Ascension Elixir                                 (1)    │   │
│  │ Cauldron of the Pooka                            (2)    │   │
│  │ ...                                                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌── Drill-Down Panel (appears on recipe click) ───────────┐   │
│  │  Algari Healing Potion          [🔗 Wowhead]            │   │
│  │                                                          │   │
│  │  Guild Leader / Officer                                  │   │
│  │    Trogmoon (Druid) • Shodoomalt (Priest)               │   │
│  │                                                          │   │
│  │  Veteran                                                 │   │
│  │    Wylandcraft (Shaman)                                  │   │
│  │                                                          │   │
│  │  Member                                                  │   │
│  │    Craftyboi (Mage) • Potionlady (Warlock)               │   │
│  │                                                          │   │
│  │  Initiate                                                │   │
│  │    Newguy (Paladin)                                      │   │
│  │                                                          │   │
│  │  [📢 Request Guild Order]                                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Behavior

1. **Page load:** Fetch profession list → default to first alphabetically. Fetch expansion list for that profession → default to newest (highest `sort_order`). Load recipes for that combo.
2. **Profession dropdown change:** Fetch new expansion list, default to newest, load recipes.
3. **Expansion dropdown change:** Load recipes for current profession + new expansion.
4. **Recipe click:** Fetch crafters for that recipe, show drill-down panel below the list.
5. **Search box:** On Enter or 3+ characters, call `/api/crafting/search?q=...`. Results replace the recipe list with results from ALL professions/expansions. Each result shows profession + expansion name. Clearing search restores the filtered view.
6. **Guild Order button:** Only visible if user is logged in with Discord linked. Clicking opens a small modal with an optional message field, then POSTs to `/api/crafting/guild-order`.
7. **Sync status:** Displayed in the header area. Shows season name (from expansion_name + season_number), last/next refresh timestamps, and current refresh frequency. If daily, shows days remaining: "Refresh frequency: Daily (12 days remaining)". If weekly, just shows "Refresh frequency: Weekly". This updates every page load from the `/api/crafting/sync-status` endpoint.

### Rank Grouping in Drill-Down

Group crafters into four tiers matching guild rank levels:

| Display Group | Guild Rank Levels |
|---|---|
| Guild Leader / Officer | 0 (Guild Leader) + 1 (Officer) |
| Veteran | 2 |
| Member | 3 |
| Initiate | 4 |

Officers and Guild Leader are combined into one row because functionally they're the same
tier from a "who should I ask" perspective.

### JavaScript (Vanilla JS)

All interaction is vanilla JS fetch calls to the API endpoints. No framework.

```javascript
// Pattern: load data, render to DOM
async function loadRecipes(professionId, tierId) {
    const resp = await fetch(`/api/crafting/recipes/${professionId}/${tierId}`);
    const data = await resp.json();
    renderRecipeList(data.data);
}
```

### Crafting Preferences

If the logged-in user navigates to the Crafting Corner, show a small toggle at the top:

```
🔔 Notify me when someone requests an item I can craft: [OFF / ON]
```

This toggles `players.crafting_notifications_enabled` via the preferences API.
Only visible to logged-in users with a Discord link.

---

## Task 8: Expansion Name Derivation

The tier names from Blizzard follow a pattern: `"{Expansion} {Profession}"`.
Examples: "Khaz Algar Blacksmithing", "Dragon Isles Alchemy", "Classic Cooking"

Derive the expansion name by stripping the profession name from the tier name:

```python
EXPANSION_SORT_ORDER = {
    "Khaz Algar": 90,
    "Dragon Isles": 80,
    "Shadowlands": 70,
    "Kul Tiran": 65,   # BfA Alliance-side naming
    "Zandalari": 65,   # BfA Horde-side naming
    "Legion": 60,
    "Draenor": 50,
    "Pandaria": 40,
    "Cataclysm": 30,
    "Northrend": 20,
    "Outland": 10,
    "Classic": 0,
}

def derive_expansion_name(tier_name: str, profession_name: str) -> tuple[str, int]:
    """Extract expansion name and sort order from a tier name."""
    expansion = tier_name.replace(profession_name, "").strip()
    sort_order = EXPANSION_SORT_ORDER.get(expansion, -1)
    return expansion, sort_order
```

---

## Task 9: Alembic Migration

Create `alembic/versions/0008_crafting_corner.py`:

1. Create `guild_identity.professions` table
2. Create `guild_identity.profession_tiers` table
3. Create `guild_identity.recipes` table (with generated `wowhead_url` column)
4. Create `guild_identity.character_recipes` junction table
5. Create `guild_identity.crafting_sync_config` table with single seed row
6. Add `crafting_notifications_enabled` column to `guild_identity.players`

---

## Task 10: Tests

### Unit Tests

- `test_crafting_sync.py`: cadence computation logic, expansion name derivation
- `test_crafting_service.py`: recipe queries (with mock DB), search, crafter grouping
- `test_blizzard_professions.py`: parsing of professions API response, gathering prof filtering

### Integration Tests (DB-dependent)

- `test_crafting_api.py`: full API endpoint tests (profession list, recipes, search, guild order)
- `test_crafting_db.py`: upsert logic for professions, tiers, recipes, character_recipes

### Key Test Cases

- Gathering professions (Mining, Herbalism, Skinning) are excluded from recipe data
- Characters with `removed_at IS NOT NULL` are excluded from sync and crafter lists
- Guild order fails gracefully if user not logged in or no Discord link
- Search returns results across all professions/expansions
- Cadence logic: first season daily for 28 days, other seasons daily for 14 days, then weekly
- Cadence returns correct daily_days_remaining countdown
- Season display name computed correctly from expansion_name + season_number
- Force refresh works regardless of cadence
- Recipe with zero crafters shows empty rank groups (recipe was known but all crafters left)
- Duplicate recipe names across professions are handled (same spell ID = same recipe)

---

## Task 11: Update Admin Dashboard

Add to the existing admin page:

1. "Crafting Sync" section showing:
   - Last sync timestamp
   - Current cadence (daily/weekly)
   - If daily: "X days remaining at daily frequency"
   - Characters processed / recipes found in last sync
   - "Force Refresh Now" button → calls `POST /api/guild-sync/crafting/trigger`
2. Season management:
   - "Expansion Name" text input (e.g., "The War Within", "Midnight")
   - "Season Number" integer input (e.g., 1, 2, 3)
   - "Season Start Date" date picker → updates `crafting_sync_config`
   - "First Season of Expansion" checkbox
   - Displays computed name: "{Expansion} Season {Number}"

---

## Acceptance Criteria

- [ ] `BlizzardClient.get_character_professions()` fetches and parses profession data
- [ ] Database tables created via Alembic migration (professions, tiers, recipes, character_recipes, config)
- [ ] Crafting sync job runs on adaptive cadence (daily/weekly based on season timing)
- [ ] Admin can force-refresh crafting data via button
- [ ] Admin can set expansion name, season number, season start date, and first-season flag
- [ ] Season displays as "{Expansion} Season {Number}" everywhere
- [ ] Crafting Corner page loads with default profession (first alpha) and expansion (newest)
- [ ] Profession and expansion dropdowns filter the recipe list correctly
- [ ] Only one profession and one expansion visible at a time
- [ ] Search box searches across all professions/expansions
- [ ] Recipe click shows drill-down with crafters grouped by rank tier
- [ ] Wowhead link on each recipe works correctly (spell ID mapping)
- [ ] Guild Order button only visible to logged-in users with Discord link
- [ ] Guild Order posts embed to #crafters-corner with @mentions for opted-in crafters
- [ ] Crafting notification toggle works and persists preference
- [ ] Sync status displayed on page (season name, last/next refresh, frequency with daily countdown)
- [ ] All tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Database migration runs cleanly: `alembic upgrade head`
- [ ] Page renders correctly in dark tavern theme
- [ ] Discord bot posts guild orders successfully
- [ ] Commit: `git commit -m "phase-2.8: crafting corner"`
- [ ] Update CLAUDE.md "Current Build Status" section
- [ ] Update INDEX.md to reference this phase file
