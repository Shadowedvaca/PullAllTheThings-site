# Gear Plan Phase 4 — u.gg Trinket Popularity Scraping

> **Status:** Planning
> **Depends on:** Phase 1F (trinket_tier_ratings, Steps 1–4 complete)
> **Branch:** new feature branch off `main` after Phase 1F merges
> **Follows:** Phase 1F (Wowhead/IV tier ratings), Phase 2A–2C (quality tracks)

---

## Motivation

Phase 1F added S/A/B/C/D tier ratings from Wowhead (and Icy Veins via Phase Z). u.gg operates on a fundamentally different model: **popularity percentage** — what fraction of top-performing players of a given spec are using each trinket. This is a complementary signal, not a competing one. A trinket that's S-tier on Wowhead but only 3% on u.gg is probably theorycrafted-BIS-but-rare-in-practice. A trinket that's 34% popular but only B-tier is likely a comfort pick with a high skill ceiling.

Displaying both signals side-by-side gives players a more complete picture of their trinket decisions.

---

## What u.gg Provides vs. Wowhead

| | Wowhead / Icy Veins | u.gg |
|---|---|---|
| Data type | Editorial tier rating (S/A/B/C/D) | Observed popularity % among top players |
| Per hero talent? | No (spec-level) | No (spec-level) |
| Per content type? | Not on trinket page | Yes — separate Raid and M+ pages |
| Source of truth | Human curators | Aggregate parse data |
| Update frequency | Patch-by-patch | Rolling (week-over-week) |

---

## Data Source — How u.gg Trinket Pages Work

u.gg's trinket pages are server-rendered React. All data is pre-computed server-side and embedded in `window.__SSR_DATA__` — the same pattern as our existing `_extract_archon()` function uses for gear pages. **No JavaScript execution required to scrape.**

**Target URLs** (per spec):
- Raid: `https://u.gg/wow/{spec}/{class}/trinkets/raid`
- M+: `https://u.gg/wow/{spec}/{class}/trinkets`

The slug separator is `_` for u.gg (same as the existing gear pages). URL example for Balance Druid:
- `https://u.gg/wow/balance/druid/trinkets/raid`
- `https://u.gg/wow/balance/druid/trinkets`

**SSR data path** (confirmed by live page inspection):
```
window.__SSR_DATA__ = {
  "<stats2 url>": {
    "data": {
      "trinket_combos": [
        { "perc": "24.85", "count": 1234, "item_id": 249346, "item_level": 658, "quality": 4 },
        ...
      ],
      "trinket_combos2": [ ... ]   // paired combo data — not used for ranking
    }
  }
}
```

`trinket_combos` contains individual trinket popularity entries, each with:
- `item_id` — Blizzard item ID (integer)
- `perc` — popularity as a string percentage (e.g. `"24.85"`)
- `count` — raw occurrence count
- `item_level` — the ilvl bracket of that popularity sample
- `quality` — Blizzard quality tier (2=uncommon, 3=rare, 4=epic)

`trinket_combos2` contains pair-combination data and is **not used** — we only care about individual trinket rankings.

**Important:** Do NOT use the `stats2.u.gg` endpoint directly. The SSR approach is version-safe because it uses whatever URL the current page embeds (which updates with each expansion patch). Direct `stats2.u.gg/wow/builds/v29/...` calls have a hardcoded version number that can go stale — the same reason `_extract_archon()` was written to reject the stats2 fallback.

---

## Data Model — Why a Separate Table

u.gg data is a different shape from Wowhead tier ratings:
- Wowhead: one letter grade per item per spec → stored in `trinket_tier_ratings.tier VARCHAR(2)`
- u.gg: one decimal percentage per item per spec per content type → no natural fit in `tier`

Rather than adding nullable columns to `trinket_tier_ratings` (which would create confusing mixed-type rows), u.gg popularity lives in its own table. The API and UI layer join both tables and present them as complementary columns.

---

## Schema

### New table: `guild_identity.ugg_trinket_popularity`

```sql
CREATE TABLE guild_identity.ugg_trinket_popularity (
    id              SERIAL PRIMARY KEY,

    source_id       INTEGER NOT NULL
                    REFERENCES guild_identity.bis_list_sources(id)
                    ON DELETE RESTRICT,           -- must clear before retiring a source
    spec_id         INTEGER NOT NULL
                    REFERENCES guild_identity.specializations(id)
                    ON DELETE RESTRICT,
    item_id         INTEGER NOT NULL
                    REFERENCES guild_identity.wow_items(id)
                    ON DELETE RESTRICT,

    content_type    VARCHAR(20) NOT NULL
                    CHECK (content_type IN ('raid', 'mythic_plus')),
    popularity_pct  NUMERIC(5,2) NOT NULL,        -- e.g. 24.85
    rank            INTEGER NOT NULL,             -- 1 = most popular for that spec+content_type
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source_id, spec_id, item_id, content_type)
);

CREATE INDEX idx_ugg_popularity_spec_content
    ON guild_identity.ugg_trinket_popularity (spec_id, content_type);
```

No `hero_talent_id` column — u.gg trinket pages are spec-level, not hero-talent-level. All rows implicitly apply to all hero talents of that spec.

`source_id` will reference the existing u.gg Raid source (`origin='archon'`, content_type='raid') and u.gg M+ source (`origin='archon'`, content_type='mythic_plus'). These already exist in `bis_list_sources`.

---

## Scraping Approach

### New scrape targets

Trinket popularity scrapes are stored in `bis_scrape_targets` — the same table used for BIS entries. The difference is the URL pattern and the extraction technique.

New `preferred_technique` value: `'ugg_trinket'` — identifies this target as a trinket popularity scrape, not a BIS extraction. The existing `sync_target()` dispatcher branches on technique.

New targets are generated by a new `discover_ugg_trinket_targets(pool)` function, callable from the admin "Discover Targets" button. It iterates all active specs and generates two rows per spec:
- Raid: `https://u.gg/wow/{spec}/{class}/trinkets/raid`
- M+: `https://u.gg/wow/{spec}/{class}/trinkets`

Uses the same slug separator logic as `_build_url()` for origin='archon' (underscore from guide_sites).

### New extractor: `_extract_ugg_trinkets(url)`

```python
@dataclass
class ExtractedUggTrinket:
    blizzard_item_id: int
    popularity_pct: float   # e.g. 24.85
    rank: int               # 1 = most popular

async def _extract_ugg_trinkets(url: str) -> list[ExtractedUggTrinket]:
    """Fetch a u.gg trinket page and extract per-trinket popularity.

    Parses window.__SSR_DATA__ using raw_decode (same technique as _extract_archon).
    Navigates to trinket_combos, extracts item_id + perc, sorts by perc desc,
    assigns rank 1..N.
    """
```

Parsing steps:
1. HTTP GET the trinket URL (same headers as existing u.gg fetches)
2. Locate `window.__SSR_DATA__` in the HTML and `raw_decode` from `{`
3. Iterate top-level keys (each is a stats2 URL); navigate `data["trinket_combos"]`
4. For each entry: extract `int(entry["item_id"])` and `float(entry["perc"])`
5. Deduplicate by `item_id` — keep highest `perc` if the same item appears at multiple ilvl brackets
6. Sort descending by `popularity_pct`, assign rank starting at 1
7. Return `list[ExtractedUggTrinket]`

### Upsert: `_upsert_ugg_trinket_popularity(pool, source_id, spec_id, content_type, trinkets)`

```python
async def _upsert_ugg_trinket_popularity(
    pool: asyncpg.Pool,
    source_id: int,
    spec_id: int,
    content_type: str,
    trinkets: list[ExtractedUggTrinket],
) -> int:
    """Resolve blizzard_item_ids to item.id, upsert popularity rows.

    Items not in wow_items are inserted as stubs (name='', icon_url='')
    and enriched by the existing Enrich Items pipeline.
    Returns count of rows upserted.
    """
```

Logic: resolve `blizzard_item_id → item.id` via `wow_items` (insert stub if missing, same pattern as `_upsert_bis_entries`), then bulk upsert with `ON CONFLICT (source_id, spec_id, item_id, content_type) DO UPDATE SET popularity_pct=EXCLUDED.popularity_pct, rank=EXCLUDED.rank, scraped_at=EXCLUDED.scraped_at`.

### Integration point

`sync_target(pool, target_id)` already dispatches on `preferred_technique`. Add a branch:
```python
elif technique == "ugg_trinket":
    trinkets = await _extract_ugg_trinkets(url)
    upserted = await _upsert_ugg_trinket_popularity(pool, source_id, spec_id, content_type, trinkets)
    await _log_scrape(pool, target_id, "ugg_trinket", "success", upserted)
    return {"items_found": upserted}
```

Trinket scrapes run in parallel with BIS scrapes when "Sync BIS Lists" (Step 4) is triggered — no separate admin button needed.

---

## New API Response Fields

### `GET /api/v1/me/gear-plan/{character_id}/trinket-ratings?slot=trinket_1`

The existing (Phase 1F Step 5) trinket-ratings endpoint is extended to include u.gg popularity data alongside Wowhead tier ratings. Each item in the response gets a `ugg` object:

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
            "name": "Shard of Violent Cognition",
            "icon_url": "...",
            "source_ratings": [{"source_id": 3, "source_origin": "wowhead", "tier": "S"}],
            "ugg": {
              "raid":  {"popularity_pct": 34.12, "rank": 1},
              "mythic_plus": {"popularity_pct": 18.40, "rank": 3}
            },
            "content_types": ["raid_boss"],
            "is_equipped": true,
            "is_bis": false,
            "is_available_this_season": true
          }
        ]
      }
    ],
    "unranked_items": [
      {
        "blizzard_item_id": 999888,
        "name": "Some Popular Trinket",
        "ugg": {
          "raid": {"popularity_pct": 5.21, "rank": 8}
        },
        "source_ratings": []
      }
    ],
    "equipped_is_unranked": false
  }
}
```

`unranked_items` — items that appear in `ugg_trinket_popularity` for this spec but have **no** Wowhead/IV tier rating. These are popular choices that the editorial sources haven't rated (e.g., new patch items, off-meta picks). Included so the UI can surface them.

---

## UI Changes

### Trinket Rankings drawer section — u.gg column

The Phase 1F (Step 11) Trinket Rankings section gains a **u.gg Popularity** column to the right of each item row. Displayed as `34.1% (#1)` or `18.4% (#3)`. The number in parentheses is the rank among items in that content type.

For items with both Wowhead tier and u.gg popularity, the row shows both signals:

```
── S ─────────────────────────────────────────────────────────
[icon] Shard of Violent Cognition  Raid  34.1% (#1)  [EQUIPPED]
[icon] Treacherous Transmitter     Raid  28.7% (#2)  [BIS]
```

For items that have u.gg popularity but no Wowhead tier, they appear in an **Untiered (Popular)** section below the tier groups:

```
── Untiered (Popular) ─────────────────────────────────────────
[icon] Some New Trinket            Raid  12.4% (#5)
```

This section is collapsed by default; expand icon shows the count. This prevents flooding the drawer when many items lack editorial tiers at the start of a new patch cycle.

### Content type tab interaction

The content type filter tabs (All / Raid / M+) control which u.gg column is shown:
- **All**: shows Raid % and M+% as two separate mini-columns (compact: `R 34%  M+ 18%`)
- **Raid**: shows only Raid %, sorted by Raid rank
- **M+**: shows only M+%, sorted by M+ rank

Sorting of the u.gg section follows the active content type tab's rank order.

### Paperdoll — no change for Phase 4

The paperdoll trinket slot already shows the Wowhead tier badge (Phase 1F Step 9). u.gg popularity is not surfaced on the paperdoll — it's drawer-level detail. Revisit if players find it useful there.

### Slot table (Option C) — no change for Phase 4

Same rationale — u.gg % is a deep-dive signal, not a quick-read signal. The slot table keeps just the tier badge for now.

---

## Admin UI Changes

### Trinket Ratings tab (Admin → Gear Plan)

The existing (Phase 1F Step 3) Trinket Ratings status tab gains a second matrix for u.gg popularity data, below the Wowhead/IV matrix:

```
u.gg Trinket Popularity

             Raid        M+
Balance    23 items    21 items   Last sync: 2h ago
Shadow      0 items     0 items   [Sync]
...
```

Color coding: green cell ≥ 15 items, yellow 1–14 items, grey = no data.

Each row has an inline **Sync** button that fires the single-target sync for both Raid and M+ targets for that spec (same `resyncSingleTarget()` pattern as the existing BIS per-row sync).

---

## Implementation Steps

### Backend

| Step | Scope | Size | Status |
|------|-------|------|--------|
| BE-1 | Migration — `ugg_trinket_popularity` table + index | Tiny | ⬜ |
| BE-2 | `bis_sync.py` — `ExtractedUggTrinket` dataclass + `_extract_ugg_trinkets()` + `_upsert_ugg_trinket_popularity()` | Small | ⬜ |
| BE-3 | `bis_sync.py` — `discover_ugg_trinket_targets()` + wire into `sync_target()` dispatcher | Small | ⬜ |
| BE-4 | `bis_routes.py` — extend `GET /trinket-ratings` to LEFT JOIN `ugg_trinket_popularity` and populate `ugg` field + `unranked_items` | Small | ⬜ |

BE-1 through BE-4 can be built in a single session — they are all backend Python/SQL with no frontend dependencies.

### Frontend — Admin

| Step | Scope | Size | Status |
|------|-------|------|--------|
| FE-A1 | `gear_plan.html` admin tab — add u.gg popularity matrix below Wowhead matrix; per-row Sync button wired to existing `resyncSingleTarget()` | Small | ⬜ |

FE-A1 is a standalone session — only depends on BE-4 (the data must exist in the API response for the matrix counts to show).

### Frontend — Member UI

| Step | Scope | Size | Status |
|------|-------|------|--------|
| FE-M1 | `my_characters.js` — parse `ugg` field from trinket-ratings API response; render `u.gg Popularity` column in drawer list rows; add Untiered (Popular) collapsible section | Medium | ⬜ |
| FE-M2 | Content type tab interaction — show correct u.gg column per active tab; sort untiered section by active tab's rank | Small | ⬜ |
| FE-M3 | CSS — u.gg % display styling; Untiered section collapse/expand; compact dual-column (`R% / M+%`) in All tab | Small | ⬜ |

FE-M1 through FE-M3 are the member-facing work and can be built together in one session once the API (BE-4) is complete.

---

## Proof-of-Concept Step (Do First)

Before writing the scraper, confirm the exact SSR data path on live pages:

```python
import httpx, json

url = "https://u.gg/wow/balance/druid/trinkets/raid"
r = httpx.get(url, headers={"User-Agent": "..."})
idx = r.text.find("window.__SSR_DATA__")
obj_start = r.text.find("{", idx)
data, _ = json.JSONDecoder().raw_decode(r.text, obj_start)

# Print the data keys to find the right path:
for k, v in data.items():
    inner = v.get("data", {})
    print(k[:80])
    print("  keys:", list(inner.keys())[:10])
    combos = inner.get("trinket_combos", [])
    print("  trinket_combos count:", len(combos))
    if combos:
        print("  first entry:", combos[0])
```

Run this against both URLs before implementing BE-2. Confirm:
1. `trinket_combos` is the right key (not `trinket_combos2` or nested differently)
2. Each entry has `item_id` and `perc` at the top level
3. `item_id` values are Blizzard item IDs (can cross-reference against a known trinket ID)
4. Whether the same item appears multiple times (multiple ilvl brackets) — if yes, dedup logic in `_extract_ugg_trinkets()` keeps the highest `perc`

Document the actual path in the BE-2 implementation notes section.

---

## Key Files

| File | Change |
|------|--------|
| `alembic/versions/NNNN_ugg_trinket_popularity.py` | New migration (BE-1) |
| `src/sv_common/guild_sync/bis_sync.py` | `_extract_ugg_trinkets()`, `_upsert_ugg_trinket_popularity()`, `discover_ugg_trinket_targets()`, `sync_target()` dispatcher extension (BE-2, BE-3) |
| `src/guild_portal/api/bis_routes.py` | Extend `GET /trinket-ratings` (BE-4) |
| `src/guild_portal/templates/admin/gear_plan.html` | u.gg matrix + per-row Sync button (FE-A1) |
| `src/guild_portal/static/js/my_characters.js` | u.gg column rendering, Untiered section (FE-M1, FE-M2) |
| `src/guild_portal/static/css/my_characters.css` | u.gg % styles (FE-M3) |

---

## Edge Cases and Notes

### Items in u.gg but not in wow_items
When `_upsert_ugg_trinket_popularity` resolves a `blizzard_item_id` not found in `wow_items`, insert a stub row with `name=''` and `icon_url=''`. Running Enrich Items fills name + icon. Items with empty names are still stored — they appear in the drawer with a placeholder name until enriched.

### Items in u.gg but not in trinket_tier_ratings
These appear in `unranked_items` in the API response. The frontend renders them in the collapsible "Untiered (Popular)" section. This is expected at the start of a new patch when u.gg data updates faster than editorial sources.

### u.gg rate limiting
The existing u.gg BIS scraping has hit 403s on the prod IP for bulk syncs (~69 healer/tank BIS targets). Trinket popularity scrapes are two targets per spec (Raid + M+). For a full guild roster with 30+ specs, that's ~60 requests. Space requests with the existing inter-request delay (`asyncio.sleep(1.0)`) and use the same retry-on-error pattern as BIS targets.

### No hero talent split
u.gg trinket pages don't vary by hero talent. All popularity rows use spec-level only. The `ugg` field in the API response is always spec-level; if a player has a specific hero talent active, the popularity data is still the same (no filtering needed).

### Popularity % interpretation for the UI
Do not attempt to map popularity % to S/A/B/C/D tier buckets (e.g., ">20% = S"). This mapping would be arbitrary and misleading — a spec where two trinkets split 48% and 47% of the top-player field would both deserve "S" while a spec with one dominant 60% pick and everything else below 10% has a different meaning. Show the raw % and rank. Players understand "34.1% (#1)" better than a derived letter grade.

---

## Open Questions

1. **`trinket_combos` vs `trinket_combos2`** — do both need to be parsed, or does `trinket_combos` contain all individual trinket data? Answer this during the proof-of-concept step.

2. **Ilvl deduplication strategy** — if item 249346 appears at multiple ilvl brackets (e.g., 639 and 658), should we keep the highest-ilvl entry's `perc`, or aggregate counts across brackets? Tentative plan: keep highest-ilvl entry's `perc`, since the top-end bracket represents players who have farmed the item to its maximum and chose to equip it.

3. **Spec coverage** — some u.gg pages return no data for specs with small sample sizes (e.g., a new spec or extremely niche choice). Handle gracefully: if `trinket_combos` is empty or missing, log warning and mark the scrape target as `status='no_data'` rather than `'error'`.

4. **Sync frequency** — u.gg popularity data updates weekly or faster. Currently BIS syncs are manual (admin-triggered). Should trinket popularity syncs be scheduled separately? Defer until Phase 4 ships — evaluate based on how often the data changes in practice.
