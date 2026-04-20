# Gear Plan — Method.gg BIS Integration

> **Status:** Planning  
> **Branch:** (new feature branch, branched from main after gear-plan-schema-overhaul merges)  
> **Scope:** Add Method.gg as a fourth BIS source alongside u.gg, Wowhead, and Icy Veins.

---

## Why Method.gg

Method.gg guides are written by top-end raiders and updated per patch. They differ from u.gg
(simulation-weighted BiS) and Wowhead (community aggregation) in that they reflect curated,
human-authored, raid-context recommendations. They have no hero talent variants — one list
per spec — which makes them simpler to ingest than u.gg but still valuable as a third opinion.

**Observed page structure (Balance Druid, April 2026):**
- Server-rendered HTML, no headless browser required
- Three tables per gearing page: **Overall BIS**, **Raid**, **Mythic+**
- Each row: Slot | Item (wowhead link with item ID + bonus IDs) | Source
- No hero talent tabs — single unified recommendation per spec
- No auth, no CAPTCHA, no obvious bot detection

---

## Target Architecture Fit

Method.gg slots cleanly into the existing BIS pipeline:

```
method.gg HTML
      │ fetch (requests + BeautifulSoup)
      ▼
landing.bis_scrape_raw          ← same table as u.gg / Wowhead raw HTML
      │ rebuild_bis_from_landing()
      ▼
enrichment.bis_entries          ← source_id = method row in bis_list_sources
      │ viz.bis_recommendations
      ▼
gear_plan UI (slot drawer BIS tab)
```

The only new code is:
1. A `_extract_method(url, content_type)` function in `bis_sync.py`
2. A `_parse_method_html(html, content_type)` pure function
3. Seed rows in a new migration for `bis_list_sources` and `common.guide_sites`
4. URL template logic in `discover_targets()` for the method.gg slug pattern

---

## URL Pattern

Method.gg guide URLs follow a consistent pattern:

```
https://www.method.gg/guides/{spec}-{class}/gearing
```

Examples:
- `https://www.method.gg/guides/balance-druid/gearing`
- `https://www.method.gg/guides/frost-mage/gearing`
- `https://www.method.gg/guides/protection-warrior/gearing`

The slug is `{spec-slug}-{class-slug}` (always hyphen-separated), e.g.:
- Balance Druid → `balance-druid`
- Frost Death Knight → `frost-death-knight`
- Arms Warrior → `arms-warrior`

The `gearing` segment is constant across all specs.

**slug_separator:** `-` (hyphens only — ignore `guide_sites.slug_separator` for this source,
same as Wowhead which hard-codes hyphens regardless of that field).

---

## Page Structure

Each gearing page has three `<table>` elements in document order:

| Table index | content_type  | Label in UI |
|-------------|---------------|-------------|
| 0           | `overall`     | Overall BIS |
| 1           | `raid`        | Raid BIS    |
| 2           | `mythic_plus` | Mythic+ BIS |

Each table has `<thead><tr><th>` columns: **Slot**, **Item**, **Source**.

Each `<tbody><tr>` contains:
- `td[0]` — plain text slot name (e.g. "Head", "Back", "Trinket 1")
- `td[1]` — `<a href="https://www.wowhead.com/[beta/]item=NNNNNN/...?bonus=X:Y">Item Name</a>`
- `td[2]` — plain text source (e.g. "Alleria Windrunner", "Tier", "Pit of Saron")

Item IDs and bonus IDs are embedded in the Wowhead href. The page may use `/beta/item=` or
`/item=` depending on whether Method has updated for a live patch.

---

## Slot Name Mapping

Method.gg uses natural English slot names. Map them to internal slot keys:

| Method label   | Internal slot   |
|----------------|-----------------|
| Head           | `head`          |
| Neck           | `neck`          |
| Shoulders      | `shoulder`      |
| Back           | `back`          |
| Chest          | `chest`         |
| Wrists         | `wrist`         |
| Hands          | `hands`         |
| Waist          | `waist`         |
| Legs           | `legs`          |
| Feet           | `feet`          |
| Ring 1         | `ring_1`        |
| Ring 2         | `ring_2`        |
| Trinket 1      | `trinket_1`     |
| Trinket 2      | `trinket_2`     |
| Main Hand      | `main_hand`     |
| Off Hand       | `off_hand`      |

If Method uses positional ring/trinket labels ("Ring 1"/"Ring 2"), map directly. If they use
a single "Ring" slot repeated twice, use document order (_1 first, _2 second) — same pattern
as Wowhead.

---

## Implementation Plan

### Step 1 — Migration: seed `guide_sites` and `bis_list_sources`

New migration (next available number after 0109):

```sql
-- guide_sites row for method.gg
INSERT INTO common.guide_sites (name, base_url, slug_separator)
VALUES ('Method', 'https://www.method.gg', '-')
ON CONFLICT DO NOTHING;

-- Three bis_list_sources rows (one per content_type)
INSERT INTO config.bis_list_sources
    (name, short_label, origin, content_type, is_default, is_active, sort_order, guide_site_id)
VALUES
    ('Method Overall', 'Method', 'method', 'overall',     FALSE, TRUE,  30,
     (SELECT id FROM common.guide_sites WHERE name = 'Method')),
    ('Method Raid',    'Method', 'method', 'raid',        FALSE, TRUE,  31,
     (SELECT id FROM common.guide_sites WHERE name = 'Method')),
    ('Method M+',      'Method', 'method', 'mythic_plus', FALSE, TRUE,  32,
     (SELECT id FROM common.guide_sites WHERE name = 'Method'))
ON CONFLICT DO NOTHING;
```

`origin = 'method'` is the new discriminator string — used in `bis_sync.py` dispatch logic,
the same way `'ugg'`/`'wowhead'`/`'icy_veins'` are used today.

`trinket_ratings_by_content_type` defaults to FALSE — Method does not publish tier lists.

### Step 2 — `discover_targets()` in `bis_sync.py`

Method targets are generated the same way as Wowhead: one per spec, `hero_talent_id=NULL`
(no hero talent variants). Add a branch in `discover_targets()`:

```python
elif source.origin == "method":
    for spec in specs:
        class_slug = spec.class_slug.lower().replace(" ", "-")
        spec_slug  = spec.spec_slug.lower().replace(" ", "-")
        url = f"https://www.method.gg/guides/{spec_slug}-{class_slug}/gearing"
        targets.append(BisTarget(
            source_id=source.id,
            spec_id=spec.id,
            hero_talent_id=None,
            content_type=source.content_type,
            url=url,
            preferred_technique="html_parse_method",
        ))
```

`preferred_technique` should be a new literal: `"html_parse_method"`. Add it to the CHECK
constraint in the migration (or use the existing unconstrained VARCHAR if the column has no
CHECK).

### Step 3 — `_extract_method()` in `bis_sync.py`

```python
async def _extract_method(url: str, content_type: str) -> tuple[list[SimcSlot], str]:
    """Fetch and parse a method.gg gearing page."""
    async with httpx.AsyncClient(headers=_HEADERS, timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    html = resp.text
    slots = _parse_method_html(html, content_type)
    return slots, html
```

Follow the existing pattern: return `(slots, raw_html)` so the raw HTML is stored in
`landing.bis_scrape_raw` before enrichment, exactly like `_extract_ugg` and `_extract_wowhead`.

### Step 4 — `_parse_method_html()` in `bis_sync.py`

Pure function; no network calls. Takes HTML string and `content_type`, returns `list[SimcSlot]`.

```python
def _parse_method_html(html: str, content_type: str) -> list[SimcSlot]:
    from bs4 import BeautifulSoup
    import re

    TABLE_INDEX = {"overall": 0, "raid": 1, "mythic_plus": 2}
    SLOT_MAP = {
        "head": "head", "neck": "neck", "shoulders": "shoulder",
        "back": "back", "chest": "chest", "wrists": "wrist",
        "hands": "hands", "waist": "waist", "legs": "legs",
        "feet": "feet", "ring 1": "ring_1", "ring 2": "ring_2",
        "ring": None,        # handled positionally below
        "trinket 1": "trinket_1", "trinket 2": "trinket_2",
        "trinket": None,     # handled positionally below
        "main hand": "main_hand", "main-hand": "main_hand",
        "off hand": "off_hand",  "off-hand": "off_hand",
    }

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    idx = TABLE_INDEX.get(content_type, 0)
    if idx >= len(tables):
        return []

    table = tables[idx]
    results: list[SimcSlot] = []
    ring_count = trinket_count = 0

    for row in table.find_all("tr")[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        raw_slot = cells[0].get_text(strip=True).lower()
        link = cells[1].find("a", href=True)
        if not link:
            continue

        # Extract item ID and bonus IDs from Wowhead URL
        m = re.search(r"item=(\d+)", link["href"])
        if not m:
            continue
        item_id = int(m.group(1))

        bonus_ids: list[int] = []
        bm = re.search(r"bonus=([0-9:]+)", link["href"])
        if bm:
            bonus_ids = [int(b) for b in bm.group(1).split(":") if b]

        # Resolve slot key
        slot_key = SLOT_MAP.get(raw_slot)
        if slot_key is None:
            if "ring" in raw_slot:
                ring_count += 1
                slot_key = "ring_1" if ring_count == 1 else "ring_2"
            elif "trinket" in raw_slot:
                trinket_count += 1
                slot_key = "trinket_1" if trinket_count == 1 else "trinket_2"
            else:
                continue  # unknown slot, skip

        quality_track = _quality_track_from_bonus_ids(bonus_ids)

        results.append(SimcSlot(
            slot=slot_key,
            blizzard_item_id=item_id,
            bonus_ids=bonus_ids,
            enchant_id=None,
            gem_ids=[],
            quality_track=quality_track,
        ))

    return results
```

`_quality_track_from_bonus_ids()` already exists in `bis_sync.py` — reuse it directly.

### Step 5 — Wire into `sync_target()` dispatch

In `sync_target()`, add a branch alongside the existing `ugg`, `wowhead`, `icy_veins` cases:

```python
elif technique == "html_parse_method":
    slots, raw_html = await _extract_method(target.url, target.content_type)
    # store raw HTML in landing.bis_scrape_raw (same as wowhead path)
```

### Step 6 — Wire into `rebuild_bis_from_landing()`

In the enrichment rebuild loop, add a dispatch case for `origin == "method"`:

```python
elif source.origin == "method":
    slots = _parse_method_html(raw_row["raw_html"], raw_row["content_type"])
```

This mirrors the existing `wowhead` branch which re-parses stored HTML using the pure function.

### Step 7 — Admin UI: Gear Plan dashboard

No structural changes required — the existing BIS sync matrix (`GET /api/v1/admin/bis/matrix`)
will automatically include Method rows once targets are discovered. The Sync / Re-sync buttons
work against `bis_scrape_targets` regardless of source.

The only UI addition worth considering: a **Method** column in the sync status matrix, which
will appear automatically from the matrix query since it groups by `source_id`.

---

## Migration Summary

One migration needed:
- INSERT into `common.guide_sites` (Method row)
- INSERT into `config.bis_list_sources` (3 rows: overall / raid / mythic_plus)
- If `preferred_technique` has a CHECK constraint, ALTER it to include `'html_parse_method'`

No new tables. No schema changes to `bis_scrape_targets`, `landing.bis_scrape_raw`, or
`enrichment.bis_entries` — all existing columns cover Method's data model.

---

## Limitations and Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Method guide URL doesn't exist for a spec (no guide written yet) | Low | `resp.raise_for_status()` → 404 → target status = `failed`; safe |
| Table order changes (Overall/Raid/M+ swap) | Medium | Add a section heading check before table selection |
| Method publishes only one unified table (not three) | Low | Fall back to index 0 for all content_types; log warning |
| No hero talent distinction | By design | One target per spec, `hero_talent_id=NULL`; all HT plans share it |
| Bonus IDs out of date between patch releases | Low | Same risk as Wowhead; bonus IDs are cosmetic for ilvl track, core item ID is stable |
| Method changes slug pattern | Low | Manual URL override in `bis_scrape_targets` handles one-offs |

---

## Testing

Add to `tests/unit/test_bis_sync.py` (or a new `test_bis_sync_method.py`):

1. `test_parse_method_html_overall` — fixture HTML with 3 tables, assert Overall slots
2. `test_parse_method_html_raid` — same fixture, content_type='raid', assert different slots
3. `test_parse_method_html_mplus` — content_type='mythic_plus'
4. `test_parse_method_html_positional_rings` — page uses single "Ring" label twice, assert ring_1 / ring_2
5. `test_parse_method_html_positional_trinkets` — same for trinkets
6. `test_parse_method_html_missing_table` — only 1 table present, content_type='raid' → returns []
7. `test_parse_method_html_bonus_quality_track` — bonus IDs containing a track ID resolve correctly

Use a local HTML fixture file (`tests/fixtures/method_balance_druid_gearing.html`) rather than
mocking the network — paste a real snapshot of the page at the time of implementation.

---

## Estimated Effort

| Task | Estimate |
|------|----------|
| Migration | 30 min |
| `_extract_method` + `_parse_method_html` | 2 hr |
| `discover_targets` branch | 30 min |
| `sync_target` + `rebuild_bis_from_landing` wiring | 1 hr |
| Unit tests + fixture | 1.5 hr |
| Dev deploy + manual verification | 30 min |
| **Total** | **~6 hr** |

---

## Open Questions

1. **Section heading guard** — should `_parse_method_html` verify a heading like "Overall Best
   Gear" exists above `tables[0]` before trusting index order, or is index-by-order sufficient?
   Index order is simpler and safe as long as Method's page template is consistent.

2. **Trinket ratings** — Method does not publish a tier list for trinkets. The
   `trinket_ratings_by_content_type` flag stays FALSE; no changes to `rebuild_trinket_ratings_from_landing`.
   If Method ever adds a tier list section, revisit.

3. **Wowhead `/beta/` vs `/ptr/` vs live** — the beta subdomain appears in current page links
   (April 2026 = Midnight launch window). After live launch the links will likely drop `/beta/`.
   The item ID regex `item=(\d+)` is agnostic to subdomain so no code change is needed; item
   IDs should remain stable across beta→live.

4. **Rate limiting** — no bot detection was observed, but Method.gg is a smaller site. Keep
   the same `asyncio.sleep(1–2s)` courtesy delay between requests that the u.gg scraper uses.
   Do not bulk-scrape all specs in a tight loop.
