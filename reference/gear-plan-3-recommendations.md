# Weekly Gearing Recommendations — Implementation Plan

## Document Purpose

This plan covers the design and phased implementation of the **Weekly Gearing Recommendations** feature (Phase 2A–2D) for the Pull All The Things guild platform. It is a direct continuation of the Gear Plan work (Phases 1A–1E). The feature gives each player a one-screen answer to "what content should I do this week to upgrade my gear toward BIS?"

---

## 1. Feature Overview

### What It Does

Compute a personalized priority list of in-game activities for each player based on:

1. **Gap analysis** — compare the player's `character_equipment` against their `gear_plan_slots` to find which slots need upgrades and at what quality track.
2. **Drop source lookup** — for each needed item, find which activities have it in their loot pool and whether it's available this week.
3. **Pool source ranking** — for activities with no specific item targeting, surface them when the player can benefit from the gear level they award.
4. **Vault tracking** — show progress toward unlocking Great Vault slots and what's needed to unlock the next tier.

**Output format:** A list of activities, each showing: what the activity is, how many times to do it this week, what gear level it awards, and what specific items to look for. One screen, immediately actionable.

### Three Activity Types

1. **`specific`** — Has a defined loot pool of named items. "Do LFR Wing 2 for a chance at [Nerubian Handguards, Ner'zhul's Band, ...]." Admin configures both the activity and its item list. Typically: LFR wings, World Boss, specific M+ dungeons for a particular trinket.

2. **`pool`** — Awards gear at a quality level, no specific items tracked. "Do Bountiful Delves — first 2 give Champion gear, next 2 give Veteran gear." Admin configures the activity, reward tiers, and optionally a list of which slots can drop. Supports diminishing returns (multiple reward tiers per week). Typically: Delves, crafting content, seasonal events, world quests, the "Prey" system.

3. **`vault`** — Special computed type. Not a runnable activity itself. Shows the player how many M+/raid/PvP runs they've done this week and how many more are needed to unlock the next vault slot tier. Configured once per season (vault thresholds change by season).

### Player Experience

On `/my-characters`, after selecting a character with an active gear plan, a collapsible **"This Week"** panel appears. Sections:

1. **Specific Drops** — activities with loot pools containing items the player needs for BIS or slot upgrades. Format: "LFR Wing 2 → chance at Hands (need Hero, have Champion), Ring 1 (need Hero, empty)."
2. **Pool Sources** — activities worth doing at the player's current item level, with reward tier breakdown.
3. **Great Vault** — progress toward each vault slot tier across M+/Raid/PvP.

Degrades gracefully: if no gear plan exists → notice. If no equipment data → notice. If no item sources data (Phase 1C not yet run) → specific drops section hidden with a notice; pool sources and vault still work.

---

## 2. Schema Additions

All new tables in `guild_identity`. Migration: **0072**.

### 2.1 `guild_identity.weekly_activity_catalog`

One row per configurable activity per season. The activity is just a name plus reward and cadence metadata — no code changes needed to add new content each season.

```sql
CREATE TABLE guild_identity.weekly_activity_catalog (
    id              SERIAL PRIMARY KEY,
    season_id       INTEGER NOT NULL
                    REFERENCES patt.raid_seasons(id) ON DELETE CASCADE,

    -- Identity
    name            VARCHAR(200) NOT NULL,   -- "Bountiful Delves", "LFR Wing 2: The Nerubian Empire"
    short_label     VARCHAR(50),             -- "Delves", "LFR W2" — optional abbreviated name
    activity_type   VARCHAR(20) NOT NULL
                    CHECK (activity_type IN ('specific', 'pool', 'vault')),

    -- Cadence
    reset_cadence   VARCHAR(20) NOT NULL DEFAULT 'weekly'
                    CHECK (reset_cadence IN ('daily', 'weekly', 'biweekly', 'monthly', 'never')),
    -- 'never' = no lockout, can be done unlimited times; award still applies up to max_completions
    max_completions_per_reset   INTEGER,
    -- NULL = unlimited. Expected range 1–10 in practice. No hard limit enforced.

    -- Reward tiers (JSONB array, ordered by completion count)
    -- Each tier: {"from": 1, "to": 2, "quality_track": "C", "item_level": 619, "label": "Champion gear"}
    -- "from"/"to" are inclusive completion counts within a reset.
    -- First 2 completions = tier 1, next 2 = tier 2, etc.
    -- For vault type: tiers define vault slot unlock thresholds instead.
    -- For simple activities with one flat reward, just one tier with from=1, to=max_completions.
    reward_tiers    JSONB NOT NULL DEFAULT '[]',

    -- Vault-type config (only used when activity_type = 'vault')
    -- vault_contributions: which activity_types count toward this vault
    -- e.g. ["mythic_plus_vault_contrib", "raid_vault_contrib", "pvp_vault_contrib"]
    -- Left as JSONB to remain flexible across seasons.
    vault_config    JSONB,

    -- Availability
    is_weekly_rotating      BOOLEAN NOT NULL DEFAULT FALSE,
    -- TRUE = only available some weeks (world boss rotation, Timewalking, etc.)
    is_available_this_week  BOOLEAN NOT NULL DEFAULT TRUE,
    -- Mike flips this each weekly reset for rotating content

    -- Display
    display_order   INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,           -- Admin-only notes, not shown to players
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (season_id, name)
);
```

**`reward_tiers` examples** (item levels are illustrative — fill in real values for the current expansion season):

```json
// Simple flat reward (LFR wing — 1 run per week)
[{"from": 1, "to": 1, "quality_track": "V", "item_level": 0, "label": "Veteran gear"}]

// Diminishing returns (e.g. Delves — first 2 = Champ, next 2 = Vet)
[
  {"from": 1, "to": 2, "quality_track": "C", "item_level": 0, "label": "Champion gear"},
  {"from": 3, "to": 4, "quality_track": "V", "item_level": 0, "label": "Veteran gear"}
]

// Vault slot unlock thresholds (activity_type = 'vault')
// These are not "rewards per completion" — they're "how many runs to unlock each vault slot"
// Stored here for admin configurability since Blizzard changes them each season.
// vault_config handles this instead of reward_tiers for the vault type.
```

**`vault_config` example (for vault activity_type) — thresholds change each season, Mike configures:**

```json
{
  "mythic_plus": {"thresholds": [1, 4, 8], "label": "M+ Vault"},
  "raid":        {"thresholds": [2, 4, 6], "label": "Raid Vault"},
  "pvp":         {"thresholds": [3, 6, 9], "label": "PvP Vault"}
}
```

---

### 2.2 `guild_identity.weekly_activity_loot`

The "chance at A, B, C, D" list for each activity. Used for both `specific` type (named items with Blizzard IDs) and `pool` type (slot labels only, no specific items). Optional for pool — if empty, the activity card just shows the reward tier without a "what to look for" list.

```sql
CREATE TABLE guild_identity.weekly_activity_loot (
    id              SERIAL PRIMARY KEY,
    activity_id     INTEGER NOT NULL
                    REFERENCES guild_identity.weekly_activity_catalog(id) ON DELETE CASCADE,

    -- What can drop
    slot            VARCHAR(30) NOT NULL,    -- "head", "hands", "ring_1", etc. (canonical slot key)
    item_name       VARCHAR(200),            -- NULL for pool sources where the item isn't known
    blizzard_item_id INTEGER,               -- NULL for pool sources
    item_id         INTEGER                  -- FK → wow_items, NULL until item is cached
                    REFERENCES guild_identity.wow_items(id) ON DELETE SET NULL,
    quality_tracks  TEXT[] NOT NULL DEFAULT '{}',
    -- Which tracks this item can drop at from this activity.
    -- For LFR: {'V'}. For Normal+: {'C','H','M'}. For pool sources: match the reward_tiers tracks.

    display_order   INTEGER NOT NULL DEFAULT 0,

    UNIQUE (activity_id, slot, blizzard_item_id)
    -- Allow multiple items per slot (the player might see a helm or a different helm)
    -- NULL blizzard_item_id = "any item in this slot" (pool source row)
    -- UNIQUE constraint: activity + slot + item (NULL treated as distinct by Postgres UNIQUE)
);
```

**For specific activities (LFR wing, World Boss):** one row per item in the loot table. Admin fills these in. Later, `sync-from-item-sources` can auto-populate from `item_sources` rows matching the encounter.

**For pool activities:** one row per slot that *can* drop, with `item_name=NULL` and `blizzard_item_id=NULL`. Tells the player "Delves can give you a head, chest, legs, ring, or trinket this week." Admin configures which slots are in the pool. If Mike doesn't add any rows, the loot list just doesn't appear — the activity card still shows.

**Member UI rendering:**
```
LFR Wing 2: The Nerubian Empire  [1× this week]  [DO IT]
  Veteran gear
  Look for:
    Nerubian Broodkeeper's Handguards  (Hands)  ← you need Hero, have Champion
    Ner'zhul's Ritual Band             (Ring 1) ← you need Hero, slot is empty
    Whispers of the Harbinger          (Neck)   ← not in your plan
```

Items in the player's gear plan are surfaced first, with the track delta shown. Items not in their plan are listed below (still useful to know what can drop).

---

### 2.3 `guild_identity.weekly_recommendation_cache`

Pre-computed per-character recommendations. Invalidated when equipment changes. Swept hourly by scheduler.

```sql
CREATE TABLE guild_identity.weekly_recommendation_cache (
    id              SERIAL PRIMARY KEY,
    character_id    INTEGER NOT NULL UNIQUE
                    REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    season_id       INTEGER NOT NULL
                    REFERENCES patt.raid_seasons(id) ON DELETE CASCADE,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    equipment_hash  VARCHAR(64),
    -- SHA-256 of sorted (slot:blizzard_item_id:quality_track) — skip recompute if unchanged
    recommendations JSONB NOT NULL DEFAULT '[]',
    vault_analysis  JSONB,
    is_stale        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_weekly_rec_cache_stale
    ON guild_identity.weekly_recommendation_cache(is_stale)
    WHERE is_stale = TRUE;
```

---

## 3. New Modules

### 3.1 `src/sv_common/guild_sync/recommendation_engine.py` (new)

Pure DB reads, no HTTP. Public API:

```python
async def compute_recommendations(pool, character_id, season_id, force=False) -> dict
async def mark_equipment_stale(pool, character_id) -> None
async def sweep_stale_caches(pool) -> int
```

### 3.2 `src/guild_portal/api/recommendation_routes.py` (new)

Member + admin endpoints (see Section 4).

### 3.3 `src/guild_portal/services/recommendation_service.py` (new)

Auth gating (is this character owned by the requesting player?) + response shaping.

### 3.4 `src/sv_common/guild_sync/equipment_sync.py` (modify)

Add `await mark_equipment_stale(pool, character_id)` after each equipment upsert.

### 3.5 `src/sv_common/guild_sync/scheduler.py` (modify)

Add `sweep_stale_recommendation_caches()` to the hourly job. Non-fatal.

### 3.6 `src/guild_portal/pages/gear_plan_pages.py` (modify)

Add Tab 4 route to `/admin/gear-plan`.

---

## 4. API Endpoints

### 4.1 Member

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/me/character/{id}/recommendations` | Player | Cached recommendations. Recomputes if stale. |
| `POST` | `/api/v1/me/character/{id}/recommendations/refresh` | Player | Force recompute. Rate-limited 1/min. |

**Response shape** (item levels and names are illustrative — real values come from the season's admin-configured catalog):

```json
{
  "character_id": 123,
  "computed_at": "2026-04-04T14:00:00Z",
  "is_stale": false,
  "has_gear_plan": true,
  "has_equipment_data": true,
  "has_item_sources": true,
  "sections": {
    "specific_drops": [
      {
        "priority": 1,
        "activity_id": 7,
        "activity_name": "LFR Wing 2",
        "activity_type": "specific",
        "max_completions": 1,
        "reset_cadence": "weekly",
        "reward_tiers": [{"from": 1, "to": 1, "quality_track": "V", "item_level": 0, "label": "Veteran gear"}],
        "is_available_this_week": true,
        "loot_items": [
          {
            "slot": "hands",
            "item_name": "Some Hands Item",
            "blizzard_item_id": 0,
            "quality_tracks": ["V"],
            "in_gear_plan": true,
            "desired_track": "H",
            "have_track": "C",
            "upgrade_magnitude": 2
          }
        ]
      }
    ],
    "pool_sources": [
      {
        "activity_id": 12,
        "activity_name": "Delves",
        "activity_type": "pool",
        "max_completions": 4,
        "reset_cadence": "weekly",
        "reward_tiers": [
          {"from": 1, "to": 2, "quality_track": "C", "item_level": 0, "label": "Champion gear"},
          {"from": 3, "to": 4, "quality_track": "V", "item_level": 0, "label": "Veteran gear"}
        ],
        "is_available_this_week": true,
        "slots_below_threshold": 3,
        "loot_items": [
          {"slot": "head", "item_name": null, "quality_tracks": ["C", "V"]},
          {"slot": "chest", "item_name": null, "quality_tracks": ["C", "V"]}
        ]
      }
    ],
    "vault": {
      "note": "Showing season totals — weekly tracking available after Phase 2C",
      "mythic_plus": {"current": 5, "slots_unlocked": 2, "runs_to_next_slot": 3, "thresholds": [1, 4, 8]},
      "raid":        {"current": 4, "slots_unlocked": 2, "runs_to_next_slot": 2, "thresholds": [2, 4, 6]},
      "pvp":         {"current": 0, "slots_unlocked": 0, "runs_to_next_slot": 3, "thresholds": [3, 6, 9]}
    }
  }
}
```

### 4.2 Admin (Officer+ read, GL-only write)

Under `/api/v1/admin/activities`:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `?season_id={id}` | List all activities for a season. |
| `POST` | `/` | Create activity (GL-only). |
| `PUT` | `/{id}` | Update activity. |
| `DELETE` | `/{id}` | Soft-delete (set `is_active=FALSE`). GL-only. |
| `POST` | `/{id}/toggle-available` | Flip `is_available_this_week`. Weekly maintenance. |
| `GET` | `/{id}/loot` | List loot entries. |
| `POST` | `/{id}/loot` | Add item to loot pool. |
| `DELETE` | `/{id}/loot/{loot_id}` | Remove loot entry. |
| `POST` | `/{id}/loot/sync-from-item-sources` | Auto-populate from `item_sources` by encounter ID. Requires Phase 1C. |
| `POST` | `/sweep-recommendations` | Manual trigger of `sweep_stale_caches()`. |

---

## 5. Admin UI

Extend `/admin/gear-plan` with **Tab 4: Weekly Activities**.

### Section A: This-Week Toggles

Card grid — one card per `is_weekly_rotating=TRUE` activity. Each card: activity name, type badge, big `is_available_this_week` toggle. Mike flips at weekly reset. Rotating content (World Boss, Timewalking weeks) lives here.

### Section B: Activity Catalog Table

Columns: Name | Type | Available | Cadence | Max/Reset | Reward Tiers | Order | Active | Actions

Actions per row (GL-only): Edit (inline expand), Delete, Manage Loot.

**Edit form fields:**
- Name (text)
- Type (specific / pool / vault)
- Reset cadence (dropdown)
- Max completions per reset (number input — label: "Times per reset for reward")
- Reward tiers (dynamic table: from → to → quality track → item level → label; add/remove rows)
- Is rotating (checkbox)
- Notes (textarea)

### Section C: Loot Pool Modal

Title: "Loot Pool — {activity name}"

For `specific` type: list shows item name, slot, quality tracks. "Add Item" form: Blizzard item ID + Fetch (resolves via item cache API). "Sync from Item Sources" button (when `blizzard_encounter_id` is set and Phase 1C data exists).

For `pool` type: list shows slot only (no item name). "Add Slot" form: slot picker (head/chest/legs/...). Purpose: tell players which slots can drop so they know what to look for.

For `vault` type: no loot pool. Section replaced with vault threshold configuration (see Section D).

### Section D: Vault Configuration

Only shown for the vault-type activity row. Inline-editable JSON or a structured form for `vault_config`: M+ thresholds, Raid thresholds, PvP thresholds. Mike updates once per season.

---

## 6. Member UI

### Panel: "This Week" on `/my-characters`

**Placement:** After `#mc-crafting`. New div `#mc-weekly-recs`.

**Section 1 — Do These (Specific Drops)**

Activity cards:

```
┌──────────────────────────────────────────────────────────────┐
│  [SPECIFIC]  LFR Wing 2: The Nerubian Empire   [1×/week]    │
│  Veteran gear                                                │
│  Look for:                                                   │
│  ★ Nerubian Broodkeeper's Handguards  Hands  C→H needed     │
│  ★ Ner'zhul's Ritual Band             Ring1  empty slot      │
│    Whispers of the Harbinger          Neck   (not in plan)  │
└──────────────────────────────────────────────────────────────┘
```

- ★ = item is in the player's gear plan (an upgrade toward BIS)
- Items in plan surfaced first, sorted by upgrade_magnitude desc
- Items not in plan shown below in muted style (still useful context)
- Unavailable this week: omit entirely

**Section 2 — Pool Sources**

```
┌──────────────────────────────────────────────────────────────┐
│  [POOL]  Bountiful Delves               [up to 4×/week]     │
│  Run 1–2: Champion gear (619 ilvl)                          │
│  Run 3–4: Veteran gear (606 ilvl)                           │
│  Can drop: Head • Chest • Legs • Ring • Trinket              │
│  3 of your slots are below 619 ilvl                         │
└──────────────────────────────────────────────────────────────┘
```

Only shown when the player has slots below the activity's highest reward tier item level, OR when crests from the activity can upgrade BIS gear they already have.

**Section 3 — Great Vault**

```
  M+ Vault          Raid Vault        PvP Vault
  5 runs done       4 kills done      0 wins done
  ──────────────    ──────────────    ──────────────
  Slot 1: ✓         Slot 1: ✓         Slot 1: need 3
  Slot 2: ✓         Slot 2: ✓         Slot 2: need 6
  Slot 3: need 3    Slot 3: need 2    Slot 3: need 9
  
  ⓘ Showing season totals (weekly tracking in Phase 2C)
```

---

## 7. Recommendation Engine Logic

### 7.1 Gap Analysis

```python
TRACK_ORDER = {"V": 0, "C": 1, "H": 2, "M": 3}

def compute_gap(slot, equipped, desired_item_id, available_track) -> GapResult | None:
    current_order = TRACK_ORDER[equipped.quality_track or "V"] if equipped else -1
    desired_order = TRACK_ORDER[available_track]
    
    if equipped is None:
        return GapResult(slot, desired_item_id, upgrade_magnitude=desired_order + 1, is_empty=True)
    if equipped.blizzard_item_id == desired_item_id:
        if desired_order > current_order:
            return GapResult(slot, desired_item_id, desired_order - current_order)
    else:
        if desired_order >= current_order:
            return GapResult(slot, desired_item_id, desired_order - current_order)
    return None
```

### 7.2 Activity Matching

For `specific` activities:
1. Find `weekly_activity_loot` rows where `item_id` (or `blizzard_item_id`) matches a gap item
2. Filter to activities that are `is_active=TRUE AND is_available_this_week=TRUE`
3. Check that the loot row's `quality_tracks` can provide the needed track or better

For `pool` activities:
1. Check if any gap slot is in the activity's loot pool (by `slot` column)
2. Check if the highest reward tier `item_level` > player's equipped item level for that slot
3. OR check if the activity's `reward_crest_type` can upgrade already-owned BIS gear

### 7.3 Priority Scoring

Lower score surfaces first:

```
score = base + track_bonus

base:
  in gear plan (is BIS):        0
  in gear plan (alt pick):     10
  not in gear plan:            20

track_bonus:
  upgrade_magnitude 3+:  -15
  upgrade_magnitude 2:   -10
  upgrade_magnitude 1:    -5
  upgrade_magnitude 0:     0  (filtered out)
```

### 7.4 Vault Computation

```python
def compute_vault(m_plus_runs, raid_kills, pvp_wins, vault_config):
    result = {}
    for key, runs in [("mythic_plus", m_plus_runs), ("raid", raid_kills), ("pvp", pvp_wins)]:
        t = vault_config[key]["thresholds"]
        slots_unlocked = sum(1 for thr in t if runs >= thr)
        next_thr = next((thr for thr in t if runs < thr), None)
        result[key] = {
            "current": runs,
            "slots_unlocked": slots_unlocked,
            "runs_to_next_slot": (next_thr - runs) if next_thr else 0,
            "thresholds": t,
        }
    return result
```

Run counts from `character_mythic_plus` (seasonal) and `character_raid_progress`. Phase 2A shows seasonal totals. Phase 2C uses weekly completion tracking for accurate numbers.

### 7.5 Caching

- `equipment_sync.py` calls `mark_equipment_stale()` after each equipment upsert
- API endpoint recomputes synchronously if `is_stale=TRUE` or `computed_at` > 4 hours old
- `sweep_stale_caches()` runs hourly in scheduler to pre-warm
- `equipment_hash` prevents recompute when sync found no changes

---

## 8. Dependency Map

```
Phase 1A (COMPLETE) ─── equipment_sync.py (character_equipment populated) ──┐
Phase 1B (COMPLETE) ─── bis_list_entries, gear_plan_slots ─────────────────┐ │
                                                                             │ │
Phase 1C (NEXT) ──────── item_source_sync.py ─── item_sources populated ────┤ │
Phase 1D (PLANNED) ───── gear plan member UI ─── players create plans ──────┤ │
                                                                             │ │
Phase 2A ─────────────── Schema (0072) + recommendation_engine.py ──────────┴─┘
                          Activity catalog admin API
                          Tab 4 on /admin/gear-plan
                          Scheduler: sweep_stale_caches() hourly
                          equipment_sync.py: mark_equipment_stale() hook

Phase 2B ─────────────── Member API endpoints
                          #mc-weekly-recs panel on /my-characters
                          Degrades gracefully: no 1C = no specific drops
                          Degrades gracefully: no 1D = no gear plan notice

Phase 2C (future) ──────  weekly_activity_completions table
                          Completion tracking UI (checkboxes per activity)
                          Accurate weekly vault counts (not seasonal)

Phase 2D (future) ──────  Roster aggregation admin view
                          "This week N players need LFR Wing 2"
```

**Recommended sequencing:** Ship Phase 2A (engine + admin catalog) before 1C/1D — admin can configure activities and caches will pre-warm when gear plans exist. Deploy Phase 2B member UI only after 1D ships so the panel isn't empty for everyone.

---

## 9. Phase Breakdown

### Phase 2A — Schema + Engine + Admin Catalog

- Migration `0072_weekly_recommendations.py` — 3 tables only; **no seed data** (expansion-specific)
- Activity catalog is populated entirely via admin UI at season start — migration does not hardcode any expansion's content
- `recommendation_engine.py` — gap analysis, activity matching, priority scoring, vault compute, cache management
- `recommendation_service.py` — auth + response shaping
- Admin CRUD API: `/api/v1/admin/activities` + loot endpoints
- Tab 4 on `/admin/gear-plan`: catalog table, this-week toggles, edit forms, loot modal, vault config section
- `equipment_sync.py` and `scheduler.py` modifications
- ORM models: `WeeklyActivityCatalog`, `WeeklyActivityLoot`, `WeeklyRecommendationCache`
- Tests: gap analysis (all edge cases), priority sort, pool source filtering, vault compute, cache invalidation. Target 40+ tests.

### Phase 2B — Member UI Panel

- Member API: `GET + POST /api/v1/me/character/{id}/recommendations`
- `#mc-weekly-recs` panel in `my_characters.html`
- `my_characters.js` — load + render functions per section
- CSS — activity cards, reward tier display, vault progress columns
- Graceful degradation for all missing-data states
- Tests: API auth gating, response shape

### Phase 2C — Weekly Completion Tracking

- `guild_identity.weekly_activity_completions` (character_id, activity_id, week_start_date, completed_at, completed_count)
- `POST /api/v1/me/character/{id}/recommendations/complete`
- Checkbox UI per activity in member panel
- Vault progress from weekly completion data (accurate)
- Admin: roster completion view per activity per week

### Phase 2D — Roster Aggregation

- Admin view: per-activity count of how many guild members have it as a top recommendation
- Optional weekly Discord report

---

## 10. Open Questions

### Q1 — "Prey" System (RESOLVED)

"Prey" is a current expansion system: accept a prey target, complete objectives, kill the target, receive a quest reward chest. It is a `pool`-type weekly activity — no different from Delves or Timewalking in terms of data model. Mike configures it via the admin UI at season start with the correct item levels for that season's reward tiers. No schema changes needed.

### Q2 — Blizzard Vault API

**There is no direct Great Vault API endpoint.** Progress must be computed from:
- `character_mythic_plus` — M+ run counts (seasonal total; accurate weekly count requires Phase 2C tracking)
- `character_raid_progress` — raid kill counts
- PvP win data (if the platform syncs PvP data — currently unclear)

Phase 2A vault section shows seasonal totals with a disclaimer. Phase 2C makes it accurate with weekly tracking. If PvP data is not currently synced, the PvP vault column can be omitted until it is.

### Q3 — LFR Loot Quality Tracks

LFR wings drop Veteran-track gear (lower ilvl, `quality_track='V'`). Normal/Heroic/Mythic bosses drop Champion/Hero/Mythic. When `sync-from-item-sources` is built (Phase 1C), it must distinguish LFR journal entries from Normal+ entries and set `quality_tracks={'V'}` on LFR loot rows. Do not copy Normal boss loot to an LFR activity.

### Q4 — Weekly Completion Tracking in Phase 2A/2B Scope?

Phase 2C scoped separately to not block 2A/2B. If Mike wants check-off UI in the first pass, it can be added to 2A with the `weekly_activity_completions` table. Flag the decision before starting 2A.

### Q5 — Pool Source Loot Slots Optional or Required?

For pool activities (Delves, Prey, etc.), the loot slot rows in `weekly_activity_loot` are optional. If Mike doesn't add any, the activity card just shows reward tiers without a "can drop" list. This is acceptable — the activity is still actionable. No schema enforcement needed; it's a data quality choice.

---

## Key Files for Implementation

| File | Status | Notes |
|------|--------|-------|
| `alembic/versions/0072_weekly_recommendations.py` | New | 3 tables + seed data |
| `src/sv_common/guild_sync/recommendation_engine.py` | New | Core computation |
| `src/guild_portal/api/recommendation_routes.py` | New | Member + admin API |
| `src/guild_portal/services/recommendation_service.py` | New | Auth + response shaping |
| `src/sv_common/guild_sync/equipment_sync.py` | Modify | `mark_equipment_stale()` after upsert |
| `src/sv_common/guild_sync/scheduler.py` | Modify | `sweep_stale_caches()` hourly |
| `src/guild_portal/pages/gear_plan_pages.py` | Modify | Tab 4 route |
| `src/guild_portal/templates/admin/gear_plan.html` | Modify | Tab 4 HTML |
| `src/guild_portal/templates/member/my_characters.html` | Modify | `#mc-weekly-recs` panel |
| `src/guild_portal/static/js/my_characters.js` | Modify | Panel loading + render |
