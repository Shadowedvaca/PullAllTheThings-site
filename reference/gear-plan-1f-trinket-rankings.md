# Gear Plan Phase 1F — Trinket Rankings

> **Status:** In Progress — Steps 1–8 complete and verified on `feature/gear-plan-phase-1f` (dev deployed)  
> **Depends on:** Phase 1A–1E (complete), Phase 1C item_sources data  
> **Follows:** Current prod work (tier/catalyst hotfixes, Blizzard API Explorer)  
> **Precedes:** Phase 2 (Enchants, Gems, Crafting)

---

## Motivation

Trinkets are the highest-variance gear decisions in the game. Unlike armor slots where "highest ilvl BIS item wins," trinkets have complex interactions: stat sticks, on-use effects, ICD procs, and tuning that changes patch-to-patch. The BIS drawer already shows what a player *wants* — but it gives no signal about whether their current trinkets are competitively tuned or just whatever dropped.

This phase adds:

1. **Tier ratings** (S/A/B/C/D) per trinket per spec, scraped from Wowhead's curated trinket tier list — and later Icy Veins as Phase Z ships
2. **At-a-glance tier display** on the paperdoll and in the slot table — no drilling required for the quick read
3. **Trinket Rankings drawer section** — when you click a trinket slot, shows the full ranked list with upgrade callouts
4. **Equipped / BIS badges** across all item lists — at a glance, you know which items in any list you're already wearing or have targeted

---

## New Data: What We're Storing vs. What We Derive

**Stored** — the only genuinely new data this feature introduces:
- `tier` rating (S/A/B/C/D) per item, per spec, per source

**Derived at query time** from data we already have:
- **Content type** (Raid / M+ / Crafted) — from `item_sources` joined on `item_id`. We do not rely on Wowhead's `display-options` attribute for this. Their classification is a useful cross-check, not the source of truth.
- **Is equipped** — from `character_equipment` for this character + slot
- **Is BIS** — from `gear_plan_slots.desired_item_id` for this plan + slot
- **Available this season** — from `item_sources` + current `raid_season` instance IDs (same logic as existing `get_available_items()`)

---

## Schema

### New table: `guild_identity.trinket_tier_ratings`

```sql
CREATE TABLE guild_identity.trinket_tier_ratings (
    id              SERIAL PRIMARY KEY,

    source_id       INTEGER NOT NULL
                    REFERENCES guild_identity.bis_list_sources(id)
                    ON DELETE RESTRICT,           -- loud failure; never silent cascade
    spec_id         INTEGER NOT NULL
                    REFERENCES guild_identity.specializations(id)
                    ON DELETE RESTRICT,
    hero_talent_id  INTEGER
                    REFERENCES guild_identity.hero_talents(id)
                    ON DELETE SET NULL,           -- safe; NULL means "applies to all HTs"
    item_id         INTEGER NOT NULL
                    REFERENCES guild_identity.wow_items(id)
                    ON DELETE RESTRICT,

    tier            VARCHAR(2) NOT NULL
                    CHECK (tier IN ('S', 'A', 'B', 'C', 'D')),
    sort_order      INTEGER NOT NULL DEFAULT 0,   -- position within the tier group

    UNIQUE (source_id, spec_id, hero_talent_id, item_id)
);

CREATE INDEX idx_trinket_ratings_spec_source
    ON guild_identity.trinket_tier_ratings (spec_id, source_id);
```

### FK safety rationale

All critical FKs use `ON DELETE RESTRICT`. This means:

- Deleting a `bis_list_sources` row will **fail** if trinket ratings reference it — you must clear `trinket_tier_ratings` for that source first
- Deleting a `wow_items` row will **fail** if trinket ratings reference it — same
- `hero_talent_id` uses `SET NULL` because losing the HT link degrades gracefully to a spec-level rating

This is an intentional tradeoff: a failed delete is a loud, easy-to-diagnose problem. A silent cascade on a hallucinated query is a corrupted dataset with no obvious symptom. See `docs/BACKUPS.md` — "Recovering from a Bad Delete" for the recovery path.

### No `content_type` column

Content type is not stored in `trinket_tier_ratings`. It is joined at query time:

```sql
SELECT
    ttr.tier,
    ttr.sort_order,
    ttr.source_id,
    wi.name,
    wi.icon_url,
    wi.blizzard_item_id,
    array_agg(DISTINCT src.source_type) FILTER (WHERE src.source_type IS NOT NULL)
        AS content_types
FROM guild_identity.trinket_tier_ratings ttr
JOIN guild_identity.wow_items wi ON wi.id = ttr.item_id
LEFT JOIN guild_identity.item_sources src ON src.item_id = ttr.item_id
WHERE ttr.spec_id = :spec_id
  AND (ttr.hero_talent_id = :ht_id OR ttr.hero_talent_id IS NULL)
  AND ttr.source_id = ANY(:source_ids)
GROUP BY ttr.id, wi.id
ORDER BY ttr.sort_order
```

`source_type` values from `item_sources`: `raid_boss` → "Raid", `dungeon` → "M+". A crafted trinket is identified via `item_recipe_links` (JOIN on `item_id`). Delves are not currently a `source_type` in `item_sources` — the "Delves" tab in the drawer UI will be gated behind this data existing. Items with no `item_sources` entry show no content type chip.

---

## Scraping Extension

### Wowhead trinket tier blocks — confirmed parseable

The Wowhead BIS guide page (`wowhead.com/guide/classes/druid/balance/bis-gear`) contains a trinket tier list in the same page we already fetch for BIS entries. Verified via proof-of-concept fetch. No new scrape targets needed — zero new HTTP calls per spec.

Wowhead markup structure (from the live page):

```
[tier-list=rows grid]
  [tier]
    [tier-label bg=q5]S[/tier-label]
    [tier-content]
      [icon-badge=249346 quality=4 display-options=raid ...]
      [icon-badge=249809 quality=4 display-options=dungeon ...]
    [/tier-content]
  [/tier]
  [tier]
    [tier-label bg=q4]A[/tier-label]
    ...
  [/tier]
[/tier-list]
```

Item IDs are in the `icon-badge=ITEM_ID` attribute. `WH.Gatherer.addData()` has full item metadata for each ID, same as BIS entries.

We parse Wowhead's `display-options` attribute **only** to understand what they think the content type is — useful as a sanity check during development. It is not stored. Content type is resolved from our `item_sources` data at query time.

### New dataclass

```python
@dataclass
class ExtractedTrinketRating:
    blizzard_item_id: int
    item_name: str
    tier: str         # 'S', 'A', 'B', 'C', 'D'
    sort_order: int   # position within tier, counting from 0 within each tier group
```

### Parser logic

In `bis_sync.py`, extend `_extract_wowhead()` to also call `_extract_trinket_tiers()`:

```python
TIER_LIST_BLOCK_RE = re.compile(
    r'\[tier-list[^\]]*\](.*?)\[/tier-list\]', re.DOTALL
)
TIER_BLOCK_RE = re.compile(
    r'\[tier\](.*?)\[/tier\]', re.DOTALL
)
TIER_LABEL_RE = re.compile(r'\[tier-label[^\]]*\]([SABCDF])\[/tier-label\]')
ICON_BADGE_RE = re.compile(r'\[icon-badge=(\d+)[^\]]*?\]')

def _extract_trinket_tiers(raw_html: str) -> list[ExtractedTrinketRating]:
    ratings = []
    for tier_list_match in TIER_LIST_BLOCK_RE.finditer(raw_html):
        block = tier_list_match.group(1)
        for tier_match in TIER_BLOCK_RE.finditer(block):
            tier_block = tier_match.group(1)
            label_match = TIER_LABEL_RE.search(tier_block)
            if not label_match:
                continue
            tier_letter = label_match.group(1)
            for pos, badge_match in enumerate(ICON_BADGE_RE.finditer(tier_block)):
                item_id = int(badge_match.group(1))
                ratings.append(ExtractedTrinketRating(
                    blizzard_item_id=item_id,
                    item_name="",   # resolved from WH.Gatherer or item cache
                    tier=tier_letter,
                    sort_order=pos,
                ))
    return ratings
```

Item names are resolved from `WH.Gatherer.addData()` keyed by item ID, same as the BIS entry extractor. If not in gatherer, fall back to the item cache (`wow_items` lookup by `blizzard_item_id`).

### Upsert

New function `_upsert_trinket_ratings(pool, source_id, spec_id, hero_talent_id, ratings)` parallel to `_upsert_bis_entries()`. Uses `ON CONFLICT (source_id, spec_id, hero_talent_id, item_id) DO UPDATE SET tier=EXCLUDED.tier, sort_order=EXCLUDED.sort_order`.

### Hero talent note

Wowhead BIS guide pages are **spec-level** — they do not vary by hero talent. All ratings scraped from Wowhead are inserted with `hero_talent_id=NULL`, meaning "applies to all HTs for this spec." The column exists for future sources (e.g., a hypothetical per-HT IV page) that might be more granular.

### Sync trigger

Trinket tier extraction fires automatically as part of the existing "Sync BIS Lists" step (Step 4 in the admin gear plan pipeline). No new button needed. The existing `bis_scrape_log` captures success/error per target.

---

## Source Logo Assets

Each source in `bis_list_sources` has an `origin` field (`'wowhead'`, `'archon'`, `'icy_veins'`). We map `origin` → icon path in the frontend.

**Plan:** Download favicons/icons from each source site at a legible size (16–20px) and host in `static/img/sources/`:

| File | Source URL to pull from |
|------|------------------------|
| `static/img/sources/wowhead.png` | Wowhead favicon / brand icon |
| `static/img/sources/icy-veins.png` | Icy Veins favicon |
| `static/img/sources/archon.png` | Archon/u.gg favicon |

Frontend mapping (in `my_characters.js`):

```javascript
const SOURCE_ICONS = {
  wowhead:   '/static/img/sources/wowhead.png',
  icy_veins: '/static/img/sources/icy-veins.png',
  archon:    '/static/img/sources/archon.png',
};
```

These are fetched and committed to the repo as static assets — no runtime CDN dependency.

---

## Multi-Source Badge Design

When displaying a tier rating in any context, the badge shows the source logo(s) alongside the letter. This makes disagreements between sources immediately readable without needing extra columns or tooltips.

### Cases

**Single source:**
```
[WH icon] S
```

**Two sources agree:**
```
[WH icon][IV icon] S
```
Both logos, one letter. Compact — signals consensus at a glance.

**Two sources disagree:**
```
[WH icon] S   [IV icon] A
```
Two separate badge pairs. The player can see the split instantly.

### Implementation

The API returns one rating entry per `(item_id, source_id, tier)`. The frontend groups by `item_id` and renders:

```javascript
function renderTierBadge(ratings) {
  // ratings = [{source_origin: 'wowhead', tier: 'S'}, ...]
  const grouped = groupBy(ratings, r => r.tier);
  if (Object.keys(grouped).length === 1) {
    // All sources agree — show all logos + one letter
    const tier = Object.keys(grouped)[0];
    const icons = ratings.map(r => `<img src="${SOURCE_ICONS[r.source_origin]}" class="source-icon">`).join('');
    return `<span class="tier-badge tier-${tier}">${icons} ${tier}</span>`;
  }
  // Sources disagree — show separate badges
  return ratings.map(r =>
    `<span class="tier-badge tier-${r.tier}">
       <img src="${SOURCE_ICONS[r.source_origin]}" class="source-icon"> ${r.tier}
     </span>`
  ).join('');
}
```

Badge color map: `S` → gold (#d4a84b), `A` → green (#4ade80), `B` → blue (#60a5fa), `C` → grey (#9ca3af), `D` → muted red (#f87171). Sizes: icon 14px, letter text 11px bold, badge height ~20px.

---

## UI Changes

### 1. Paperdoll — trinket slot tier overlay

The paperdoll already renders item name + ilvl for each slot. For `trinket_1` and `trinket_2` slots only, a small tier badge is rendered **above the item name** within the slot cell.

```
┌────────────────────────────────────────┐
│  [WH icon] S                           │  ← tier badge (new, trinkets only)
│  [icon]  Shard of Violent Cognition    │
│          658 Hero                      │
└────────────────────────────────────────┘
```

If no rating exists for the equipped trinket (item not in `trinket_tier_ratings` for this spec): no badge shown, no placeholder.

If equipped trinket is unranked and any ranked upgrade exists: show a subtle "↑ ranked upgrades available" pill instead — clickable, opens the trinket drawer section.

### 2. Gear plan slot table — BIS trinket tier

In the Option C slot table, for trinket rows, show the tier badge for the desired item (from `gear_plan_slots.desired_item_id`) in the same row as the item name:

```
Trinket 1   [WH icon] S  Shard of Violent Cognition   Hero   [BIS] [EQUIPPED]
Trinket 2   [WH icon] A  Treacherous Transmitter       Hero   [BIS]
```

The tier badge uses the same `renderTierBadge()` function.

### 3. Trinket Rankings drawer section

When you click a trinket slot, the slot drawer gains a third collapsible section below "BIS" and "Available from Content": **Trinket Rankings**.

```
▼ Trinket Rankings

  [All]  [Raid]  [M+]  [Crafted]         Source: [WH icon]  [IV icon when available]

  ── S ──────────────────────────────────────────────────────────
  [icon]  Shard of Violent Cognition      Raid     [EQUIPPED]
  [icon]  Treacherous Transmitter         Raid     [BIS]

  ── A ──────────────────────────────────────────────────────────
  [icon]  Mad Queen's Mandate             Raid     ↑ available from current content
  [icon]  Signet of the Priory            M+

  ── B ──────────────────────────────────────────────────────────
  [icon]  ...

  ── Unranked ────────────────────────────────────────────────────
  Your equipped trinket in this slot has no tier rating.
  Getting even an A-tier trinket would be a meaningful upgrade.
```

**Content type filter tabs** — filter the list to show only items from that source type (based on `item_sources` JOIN). "All" = no filter. Active tab highlighted in gold.

**Source switcher** — logo icons in top-right of the section. Clicking a logo icon toggles that source's ratings. When multiple sources are active and disagree on an item, the multi-source badge logic applies.

**EQUIPPED badge** — gold pill. Appears when `character_equipment` for this character + slot has this `blizzard_item_id`.

**BIS badge** — teal pill. Appears when `gear_plan_slots.desired_item_id` for this plan + slot resolves to this item.

**"↑ available from current content"** — shown when the item appears in `item_sources` for the current season's instance IDs (same filter as the existing Available from Content section).

**Unranked notice** — only shown when the currently equipped item in this slot has no entry in `trinket_tier_ratings` for this spec. Omitted otherwise.

### 4. Equipped / BIS badges on all item lists

Cross-cutting. Applies to: BIS drawer section, Available from Content section, and Trinket Rankings section.

Each item row gains two optional pill tags rendered to the right of the item name:

| Badge | Color | Condition |
|-------|-------|-----------|
| EQUIPPED | Gold | `character_equipment[slot].blizzard_item_id == item.blizzard_item_id` |
| BIS | Teal | `gear_plan_slots[slot].desired_item_id == item.id` |

A single item can show both (you have it equipped AND you've set it as your target). The same `renderItemBadges(isEquipped, isBis)` function is called from all three list renderers.

API: Both `is_equipped: bool` and `is_bis: bool` are added to each item object returned by:
- `GET /api/v1/me/gear-plan/{character_id}/available-items?slot=`
- The trinket ratings endpoint (new — see below)
- The existing BIS slot query response

---

## API Changes

### New endpoint

```
GET /api/v1/me/gear-plan/{character_id}/trinket-ratings?slot=trinket_1
```

Returns the tier list for this character's spec, for a given trinket slot. Response:

```json
{
  "ok": true,
  "data": {
    "spec_id": 102,
    "slot": "trinket_1",
    "tiers": [
      {
        "tier": "S",
        "items": [
          {
            "blizzard_item_id": 249346,
            "item_id": 1234,
            "name": "Shard of Violent Cognition",
            "icon_url": "...",
            "sort_order": 0,
            "source_ratings": [
              {"source_id": 3, "source_origin": "wowhead", "tier": "S"}
            ],
            "content_types": ["raid_boss"],
            "is_equipped": true,
            "is_bis": false,
            "is_available_this_season": true
          }
        ]
      }
    ],
    "equipped_is_unranked": false
  }
}
```

`equipped_is_unranked: true` drives the "no tier rating — even a D-tier would help" notice.

### Extensions to existing endpoints

**`GET /api/v1/me/character/{character_id}/equipment`** (paperdoll data):  
Add `tier_badge` to trinket slot entries — same shape as `source_ratings` above. `null` if no rating for this spec.

**`GET /api/v1/me/gear-plan/{character_id}/available-items?slot=`**:  
Add `tier`, `source_ratings`, `is_equipped`, `is_bis` to each item for trinket slots.

**Existing BIS slot query** (used by the BIS section of the drawer):  
Add `is_equipped`, `is_bis` to each item returned.

---

## Admin UI

New **"Trinket Ratings"** tab in `/admin/gear-plan` (after the BIS matrix tabs).

Layout: matrix of spec rows × source columns. Each cell shows:
- Count of ratings for that spec+source ("23 ratings")
- Last-scraped timestamp
- Color coding: green = populated, grey = empty/no data

No separate sync button — ratings populate as part of existing "Sync BIS Lists" (Step 4). The tab is read-only status display only.

---

## Recovery Considerations

Before deleting a `bis_list_sources` row (e.g., retiring a source):
```sql
DELETE FROM guild_identity.trinket_tier_ratings WHERE source_id = :id;
-- then delete the source row
```

Before deleting a `wow_items` row:
```sql
DELETE FROM guild_identity.trinket_tier_ratings WHERE item_id = :id;
-- also check: bis_list_entries, gear_plan_slots, character_equipment
```

If ratings data is accidentally wiped for a source: re-run "Sync BIS Lists" from Admin → Gear Plan. The scraper is idempotent — all rows are upserted on conflict. Full repopulation takes the same time as a normal BIS sync.

See `docs/BACKUPS.md` — "Recovering from a Bad Delete" for the full procedure when a restore is needed.

---

## Implementation Order

| Step | Scope | Size | Status |
|------|-------|------|--------|
| 1 | Migration — `trinket_tier_ratings` table + index | Tiny | ✅ migration 0100 |
| 2 | `bis_sync.py` — `_extract_trinket_tiers()` + `_upsert_trinket_ratings()` | Small | ✅ verified — Balance Druid data confirmed correct in dev |
| 3 | Admin gear plan — Trinket Ratings status tab + per-row Sync button with inline result | Small | ✅ |
| 4 | Static assets — source icon SVGs in `static/img/sources/` | Tiny | ✅ (SVG placeholders; replace w/ real favicons when available) |
| 5 | API — new `GET /trinket-ratings` endpoint | Small | ✅ `get_trinket_ratings()` in `gear_plan_service.py`; route in `gear_plan_routes.py` |
| 6 | API — extend equipment endpoint with `tier_badge` for trinket slots | Small | ✅ `get_plan_detail()` attaches `tier_badge` (source_ratings) to equipped trinket items |
| 7 | API — extend `available-items` + BIS query with `is_equipped`, `is_bis` | Small | ✅ All item groups get `is_equipped`/`is_bis`; trinket slots also get `source_ratings`; BIS recs stamped |
| 8 | JS — `renderTierBadge()` + `renderItemBadges()` utilities | Small | ✅ `GP_SOURCE_ICONS`, `_gpRenderTierBadge()`, `_gpRenderItemBadges()`, `_gpTrinketCache`; CSS v2.1.0 |
| 9 | UI — paperdoll trinket slot tier overlay + unranked upgrade pill | Small | ✅ |
| 10 | UI — slot table tier badge on trinket rows | Small | ✅ |
| 11 | UI — Trinket Rankings drawer section (tabs, list, source switcher) | Medium | ✅ |
| 12 | UI — EQUIPPED / BIS badges across all three list sections | Medium | ✅ |
| **Total** | | **Medium — 1-2 dev sessions** | |

Steps 1–4 are backend setup. Steps 5–8 are API + shared JS utilities. Steps 9–12 are the visible UI work. Each step is independently deployable behind the existing gear plan feature gate (GL only for the admin side; character-owned gating for the member side).

---

## Implementation Notes (from Steps 1–4)

### Wowhead closing tag normalisation
Wowhead's raw HTML escapes BBCode closing tags as `[\/tag]` (backslash before slash). The regex approach (`\\?` to make the backslash optional) failed silently in practice. Fix: `_extract_trinket_tiers()` pre-normalises with `raw_html.replace("[\\/", "[/")` before applying regexes. Patterns use plain `[/tag]` form.

### All three Wowhead sources return identical trinket data
Wowhead has one trinket tier list per spec page — not separate per content-type. All three source rows (Raid/M+/Overall) produce identical `trinket_tier_ratings` rows. Expected. The `source_id` distinction becomes meaningful when Icy Veins data lands.

### Items with empty names
Some scraped items land with empty `item_name` — not in `WH.Gatherer.addData()` at scrape time. Stubs are in `wow_items` with the correct `blizzard_item_id`. Running Enrich Items fills the names.

### Admin per-row Sync button (added to Step 3 scope)
Each Scrape Targets row has an inline Sync button: fires `POST /api/v1/admin/bis/sync/target/{id}`, shows spinner on the button, then updates the row's status/items cells in place and displays `X BIS · Y trinkets` inline. Function: `resyncSingleTarget()` in `gear_plan_admin.js` (v1.2.0).

---

## Implementation Notes (from Steps 5–8)

### `get_available_items()` char_row query extended
Added `gp.spec_id, gp.hero_talent_id` to the existing char_row query so trinket rating lookups don't need a separate plan fetch. Both fields land in `avail_spec_id` / `avail_ht_id` and are used by Query 2b.

### Query 2b — trinket ratings pre-fetch inside the conn block
Runs only for `trinket_1` / `trinket_2` slots. Fetches all `trinket_tier_ratings` rows for the spec and deduplicates by `(blizzard_item_id, source_origin, tier)` in Python (not SQL). Result stored in `trinket_ratings_by_bid`, which is then stamped onto every item at return time. Non-trinket slots pay zero query cost.

### Source-rating deduplication by (origin, tier)
All 3 Wowhead sources produce identical rows. Rather than DISTINCT ON in SQL (ordering dependency), dedup happens with a Python `seen` set keyed on `(item_id, source_origin, tier)`. This pattern is used consistently in all three places: `get_available_items()`, `get_plan_detail()`, and `get_trinket_ratings()`.

### `tier_badge` placement in plan detail
Added inside the conn block, immediately before the "Build bid → equipment data lookup" comment. Uses `_trinket_bids_pd` / `_tb_rows` / `_tb_map` / `_tb_seen` naming (prefixed with `_` and `_pd` suffix) to avoid shadowing the broader scope variables.

### `is_equipped` / `is_bis` on BIS recs
Stamped in the per-slot loop in `get_plan_detail()`, right after the `target_ilvl` assignment. Uses `equipped_bid` (already in scope) and `desired_bid` (also already in scope for that slot). No extra queries.

### JS cache buster
`my_characters.js` → v2.7.0, `my_characters.css` → v2.1.0.

### `_gpTrinketCache`
Declared alongside `_gpAvailCache` for the upcoming Trinket Rankings drawer section (step 11). Shape: `"charId:slot"` → `{status:'loading'|'done'|'error', data:{...}}`.

---

## Implementation Notes (from Steps 9–12)

### Paperdoll tier badge (Step 9)
Added inline to the `.mcn-slot-card__ilvl` div — appended after the ilvl number for `trinket_1`/`trinket_2` slots when `eq.tier_badge` has entries. Uses the existing `.gp-tier-badge` CSS with a scoped `.mcn-slot-card__ilvl .gp-tier-badge` override to shrink the badge to fit the compact card. No unranked pill in the paperdoll card — card is already clickable to open the drawer where the Trinket Rankings section gives full detail.

### Gear table tier badge (Step 10)
Added `_gpRenderTierBadge(eq.tier_badge)` to the `.mcn-gt__meta` div in the Equipped cell for trinket rows. Sits inline alongside the ilvl number and track pill.

### Trinket Rankings drawer section (Step 11)
Added below BIS Recommendations and Available from Content, before Excluded Items. Structure:
- `<details class="mcn-avail-section" open>` with DOM id `mcn-trinket-ratings-body-{slot}`
- Async loaded by `_gpLoadTrinketRatings(charId, dbSlot)` — no-op if already loading/loaded
- `_gpTrinketFilter` dict (`dbSlot → 'all'|'raid_boss'|'dungeon'|'crafted'`) drives filter state
- `mcnGpSetTrinketFilter(dbSlot, filter)` — window global for onclick attrs; updates filter + re-renders body in-place
- Tier groups rendered with a horizontal rule divider (`gp-trinket-tier-rule`) and a full-size tier badge as the group header

### EQUIPPED / BIS badges (Step 12)
- **BIS grid**: `is_equipped`/`is_bis` now stored in `itemMap` (taken from first rec per item). Badges rendered as `_gpRenderItemBadges(item.is_equipped, item.is_bis)` inline after the item name.
- **Available from Content**: `_gpRenderItemBadges(item.is_equipped, item.is_bis)` added inline after item name in `_gpRenderAvailTable`.
- **Trinket Rankings**: badges rendered per item inside `_gpRenderTrinketRankings`.

### JS/CSS versions
`my_characters.js` → v2.8.0, `my_characters.css` → v2.2.0.

---

## Phase Z Compatibility

When Icy Veins scraping ships (Phase Z), IV trinket ratings slot in cleanly:
- IV source rows already exist in `bis_list_sources` with `origin='icy_veins'`
- IV trinket ratings are inserted with the IV `source_id`
- Multi-source badge logic handles Wowhead + IV disagreements automatically
- No schema changes required

---

## Key Files

| File | Change |
|------|--------|
| `alembic/versions/0100_trinket_tier_ratings.py` | Migration (complete) |
| `src/sv_common/guild_sync/bis_sync.py` | `_extract_trinket_tiers()`, `_upsert_trinket_ratings()`, extend `_extract_wowhead()` (complete) |
| `src/guild_portal/api/bis_routes.py` | `GET /admin/bis/trinket-ratings-status` (complete) |
| `src/guild_portal/api/gear_plan_routes.py` | `GET /trinket-ratings` endpoint (complete — step 5) |
| `src/guild_portal/services/gear_plan_service.py` | `get_trinket_ratings()`, extended `get_plan_detail()` + `get_available_items()` (complete — steps 5–7) |
| `src/guild_portal/templates/admin/gear_plan.html` | Trinket Ratings status tab (complete — step 3) |
| `src/guild_portal/templates/member/my_characters.html` | Paperdoll trinket badges, drawer section (steps 9–11, next) |
| `src/guild_portal/static/js/my_characters.js` | `_gpRenderTierBadge()`, `_gpRenderItemBadges()`, drawer renderer (steps 8 done; 9–12 next) |
| `src/guild_portal/static/css/my_characters.css` | Tier badge + EQUIPPED/BIS pill styles (complete — step 8) |
| `src/guild_portal/static/img/sources/` | `wowhead.svg`, `icy-veins.svg`, `archon.svg` (complete — step 4) |

---

## Open Questions

1. **Tier F?** — Some Wowhead tier lists include an F tier for actively harmful or trap choices. The `CHECK` constraint currently allows `'F'`. If Wowhead doesn't use it for trinkets in practice, it's a no-op. If it does, the display logic should handle it (muted red, below D in sort order).

2. **Multi-slot trinket handling** — When both trinket slots are the same item (e.g., duplicate crafted trinkets), how does the EQUIPPED badge behave? Most players won't have this. For now: mark EQUIPPED if `blizzard_item_id` appears in *either* trinket slot's equipment row. ✅ Implemented this way.

3. **Archon trinket tier data** — u.gg provides ranked item popularity data (count of top players using each trinket) but not explicit S/A/B/C/D tier labels. Their data could be used to derive a popularity-based tier proxy, but that's a different data shape than Wowhead's curated editorial tiers. Defer to a later pass if desired.
