# Prod Data Baseline — 2026-04-13

Captured before Phase A of the Gear Plan Schema Overhaul.  
Dev backup: `reference/archive/dev-backup-2026-04-13.sql` (26k lines, pg_dump of same tables from dev).

## Files

| File | Rows | Notes |
|------|------|-------|
| `wow_items.csv` | 9171 | All items; `wowhead_tooltip_html` excluded (fetched from landing) |
| `item_sources.csv` | 8432 | Joined to `wow_items` to include `blizzard_item_id` + `item_name` |
| `bis_list_entries.csv` | 5524 | Raw IDs; join to `bis_list_sources` + `specializations` for names |
| `bis_scrape_targets.csv` | 480 | External BIS URLs (Wowhead, Archon, Icy Veins) |
| `item_recipe_links.csv` | 222 | Joined to include `blizzard_item_id` + `item_name` |
| `trinket_tier_ratings.csv` | 0 | **Empty on prod** — Wowhead trinket scraper never completed |
| `hero_talents.csv` | 80 | All hero talent rows |
| `bis_list_sources.csv` | 9 | All 9 active BIS sources |
| `specializations.csv` | 40 | All specialization rows |

## Key Findings (inform Phase A + B design)

### wow_items
- **`quality_track` is NULL for ALL 9171 rows on prod.** The column exists but the population
  pipeline (`flag_catalyst_items`) never ran successfully on prod or was never called for
  non-catalyst items. Phase B enrichment sproc must derive quality_track from scratch.
- `ring_2` has 9 rows and `trinket_2` has 8 rows — these are likely aliased duplicates that
  should be normalized to `ring_1` / `trinket_1` during enrichment.
- `slot_type` uses `other` for some items (612 rows) — enrichment sproc needs to handle these.

### item_sources
- **No `catalyst` instance_type on prod** — only `dungeon`, `raid`, `world_boss`. The catalyst
  pipeline (`enrich_catalyst_tier_items`) writes items to `wow_items.quality_track='C'` but
  apparently doesn't insert corresponding source rows with `instance_type='catalyst'`.
- `dungeon` is the dominant type (7985 rows), with 84 junk rows flagged.
- `raid` has 323 rows, `world_boss` has 124 rows — all non-junk.
- Table uses `item_id` (FK to `wow_items.id`), not `blizzard_item_id` directly.

### bis_list_entries
- 5524 entries across 9 active sources (u.gg Raid/M+/Overall, Wowhead Raid/M+/Overall,
  Icy Veins Raid/M+/Overall).
- Uses `item_id` FK to `wow_items` (via `bis_list_entries.item_id`).

### item_recipe_links
- 222 crafted item → recipe relationships.
- Uses `item_id` FK to `wow_items.id`.

### trinket_tier_ratings
- **0 rows on prod.** This table was created in migration 0100 (prod-v0.19.0) but the
  Wowhead trinket scraper was never triggered on prod. Phase B enrichment sproc for trinket
  ratings will start from scratch.

### bis_list_sources (reference)
| Name | Origin | Content type |
|------|--------|-------------|
| u.gg Raid | archon | raid |
| u.gg M+ | archon | mythic_plus |
| u.gg Overall | archon | overall |
| Wowhead Raid | wowhead | raid |
| Wowhead M+ | wowhead | mythic_plus |
| Wowhead Overall | wowhead | overall |
| Icy Veins Raid | icy_veins | raid |
| Icy Veins M+ | icy_veins | mythic_plus |
| Icy Veins Overall | icy_veins | overall |
