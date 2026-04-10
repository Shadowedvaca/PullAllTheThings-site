# Gear Plan — Controls Revisited

> **Phase:** 1D polish  
> **Branch:** `feature/gear-plan-phase-1d` (or a new `fix/gear-plan-controls` branch off main)  
> **Scope:** Front-end only. No migrations, no new API endpoints. One small API change (add `has_hero_talent_variants` to source list response).

---

## What Changed and Why

The original gear controls panel had too many moving parts exposed to the user: a Hero Talent dropdown, a Source dropdown, four labeled buttons (Sync Gear, Fill BIS, SimC, Reset Plan), and a SimC modal. This was designed while the feature was being built and reflects implementation thinking, not user thinking.

Decisions made in design review:

- Hero Talent and Source are separate concerns but can live in one clean row
- The Source dropdown already combines provider + content type — keep that, just make it legible
- Hero Talent dropdown should only appear when the selected source actually has HT-specific data
- Sync Gear is redundant with the top-right Refresh button — remove it
- Reset Plan has no genuine use case distinct from "pick defaults and Fill BIS" — remove it entirely, no replacement
- SimC Import/Export is advanced/untested — commented out for now (see `gear-plan-4-simc.md`)
- Fill BIS becomes the single action on a one-row control bar, following the same pattern as the Guide tab

---

## New Controls Layout

Replace the current `mcn-gear-controls` block with a single row:

```
BIS List: [Guide + Content dropdown ▾]   [Hero Talent dropdown ▾ — only when relevant]   [Fill BIS button]
```

This mirrors the Guide tab UX: one row, dropdowns left, action right. No secondary buttons.

---

## Change 1 — BIS Grid Header (slot detail drawer)

**Current:** Single `<thead>` row with flat labels — "u.gg Raid", "u.gg M+", "u.gg Overall", "Wowhead Overall", etc.

**New:** Two `<thead>` rows. Row 1 groups columns by provider using `colspan`. Row 2 shows the content type label for each column.

```
┌──────────────┬──────────────────────┬───────────────┬───────────────┐
│              │       u.gg           │   Wowhead     │   Icy Veins   │
│    Item      ├───────┬───────┬──────┼───────────────┼───────┬───────┤
│              │ Raid  │  M+   │ All  │     All       │ Raid  │  M+   │
├──────────────┼───────┼───────┼──────┼───────────────┼───────┼───────┤
```

**Implementation notes:**
- `_gpRenderBisGrid()` in `my_characters.js` builds the table. Currently builds `hdrCells` as a flat list.
- Group source rows by `origin` field (already present on BIS entries as `source_name` or derivable from `short_label`). The API response for slot BIS data should include `origin` (e.g., `archon`, `wowhead`, `icy_veins`) per row so the JS can group without hard-coding provider names.
- If only one active source exists for a provider, that provider's colspan is 1 and the content type label fills both rows (no colspan needed, use `rowspan=2` on the provider header cell).
- CSS: provider header cells use a slightly muted style to de-emphasize them relative to the content-type row, which is the actionable label.

---

## Change 2 — Source Dropdown (BIS List selector)

**Current:** Label "Source", flat `<select>` with entries like "u.gg Raid", "u.gg M+", "u.gg Overall", "Wowhead Overall".

**New:**
- Label: **"BIS List"** (or just no label if the row is tight — one label for the whole row is enough)
- Use `<optgroup>` to group by provider:
  ```html
  <optgroup label="u.gg">
    <option value="10">Raid</option>
    <option value="11">M+</option>
    <option value="12">All ★</option>   <!-- ★ marks the is_default row -->
  </optgroup>
  <optgroup label="Wowhead">
    <option value="13">All ★</option>
  </optgroup>
  <optgroup label="Icy Veins">
    <option value="16">Raid</option>
    <option value="17">M+</option>
    <option value="18">All</option>
  </optgroup>
  ```
- The `★` marker (or a `·` dot, or `(default)` text — pick whatever reads cleanly) appears next to options where `is_default = TRUE` in `bis_list_sources`.
- The API already returns `is_default` per source row — no backend change needed for this.
- Provider name for optgroup label is derivable from `origin`: `archon` → "u.gg", `wowhead` → "Wowhead", `icy_veins` → "Icy Veins". Map in JS, no backend change.

---

## Change 3 — Hero Talent Dropdown (conditional)

**Current:** Always rendered, same width as Source dropdown.

**New:** Only rendered when the selected source has at least one `bis_list_entries` row with a non-null `hero_talent_id`.

**API change needed:** Add `has_hero_talent_variants: bool` to each source object in the `bis_sources` array returned by `GET /api/v1/me/gear-plan/{charId}`. Computed in `gear_plan_service.py` by checking whether any `bis_list_entries` rows for that source have `hero_talent_id IS NOT NULL`.

```python
# In get_plan_detail() — add to source_list query or as a second query:
ht_source_ids = await conn.fetchval(
    """
    SELECT array_agg(DISTINCT source_id)
      FROM guild_identity.bis_list_entries
     WHERE hero_talent_id IS NOT NULL
    """
)
# Then annotate each source row:
# source["has_hero_talent_variants"] = source["id"] in (ht_source_ids or [])
```

**JS change:** When rendering the controls row, check `source.has_hero_talent_variants` for the currently selected source. If false, don't render the HT `<select>` at all. When the source dropdown changes, re-evaluate and show/hide the HT dropdown accordingly.

This is data-driven: when a new scraper comes online with or without HT variants, the UI adapts automatically without code changes.

---

## Change 4 — Remove Sync Gear Button

The top-right Refresh button already calls `POST /api/v1/me/bnet-sync`, which syncs all BNet data including equipment. The "Sync Gear" button (`POST /api/v1/me/gear-plan/{charId}/sync-equipment`) is a narrower, character-only version of the same thing. From a user's perspective they look identical.

**Action:** Remove the Sync Gear button (`mcn-gp-btn-sync`) and its event listener. The `_gpOnSyncGear` function and the `/sync-equipment` endpoint can stay in place (they're not harmful), but nothing should call them from the UI.

---

## Change 5 — Remove Reset Plan Button

The Reset Plan button called `DELETE /api/v1/me/gear-plan/{charId}`, which deleted the entire plan record including locked slots. The only scenario where this is useful — "I want to start completely over from scratch" — is handled by picking the default source options and hitting Fill BIS. Locked slots that the user intentionally set should not be wiped without explicit per-slot action anyway.

**Action:** Remove the Reset Plan button (`mcn-gp-btn-reset`) and its event listener entirely. No replacement UI. The `_gpOnDeletePlan` function and the DELETE endpoint can stay in place for potential admin use, but they are removed from the member UI.

---

## Change 6 — Comment Out SimC

Hide the SimC Export button, SimC Import button, and SimC modal (`mcn-simc-modal`) from view. Use HTML comments or `hidden` attribute — do not delete the code. The backend (`simc_parser.py`, the import/export API endpoints, `simc_profile` on `gear_plans`) stays fully intact.

See `reference/gear-plan-4-simc.md` for the plan to re-introduce this as a tested feature.

---

## Change 7 — Manual Item Lookup (Item ID → Name/URL)

The slot detail drawer has a manual "Your Goal" input: `type="number"`, placeholder "Item ID", with a Fetch button. Nobody knows a Blizzard item ID. The input needs to accept something a normal player would actually have.

### Injection safety (already fine)
The current field is protected at three independent layers:
1. JS `parseInt(value, 10)` — `NaN` causes early exit, nothing is sent
2. FastAPI path param `blizzard_item_id: int` — 422s any non-integer before the handler runs
3. asyncpg parameterized query — value never touches SQL as a string

No injection risk. The integer constraint means no SQL injection or XSS payload can survive the pipeline. No backend changes needed for security.

### The UX fix — accept three input formats

Make the input smarter without adding a new endpoint. Update `mcnGpFetchAndSet` to detect which format the user typed before calling the API:

**Format 1 — Plain integer (current behavior)**
```
212456
```
Pass directly as the item ID.

**Format 2 — Wowhead URL (paste from browser)**
```
https://www.wowhead.com/item=212456/tidebound-cuirass
https://www.wowhead.com/item=212456
```
Wowhead item URLs always contain `/item=NNNNN`. Extract with a regex: `/[?&/]item[=/](\d+)/i`. This is a pure client-side parse — no API call, no backend change.

**Format 3 — Item name (type a name)**
```
Tidebound Cuirass
```
A non-numeric string that doesn't match the URL pattern → treat as a name search. Call a new lightweight endpoint: `GET /api/v1/items/search?q={name}` that queries `wow_items.name ILIKE '%query%'` and returns up to 10 matches. Show results as a small inline dropdown below the input; clicking a result populates the field and calls `mcnGpSetDesiredItem` directly.

### Name search endpoint

New route on the existing `items_router`:

```
GET /api/v1/items/search?q=tidebound
```

Returns:
```json
{
  "ok": true,
  "data": [
    { "blizzard_item_id": 212456, "name": "Tidebound Cuirass", "icon_url": "...", "slot_type": "chest" }
  ]
}
```

Query: `SELECT blizzard_item_id, name, icon_url, slot_type FROM guild_identity.wow_items WHERE name ILIKE $1 ORDER BY name LIMIT 10` with `%{q}%`. Requires auth (same `get_current_player` dependency as the existing items endpoint). Minimum 2 characters before triggering.

This searches only items we've already synced into `wow_items` — no external API call. If an item isn't in our DB yet, the user can still fall back to pasting a Wowhead URL, which will trigger the existing `get_or_fetch_item()` Wowhead fetch.

### Updated placeholder text

Change placeholder from `"Item ID"` to `"Name, ID, or Wowhead link"` so users know all three formats are accepted.

---

## Files Touched

| File | Change |
|------|--------|
| `src/guild_portal/services/gear_plan_service.py` | Add `has_hero_talent_variants` to source list in `get_plan_detail()` |
| `src/guild_portal/api/gear_plan_routes.py` | Add `GET /api/v1/items/search?q=` endpoint on `items_router` |
| `src/guild_portal/static/js/my_characters.js` | `_gpRenderCenterPanel()`: new one-row controls; `_gpRenderBisGrid()`: two-row grouped header; remove Sync/Reset buttons and SimC wiring; update `mcnGpFetchAndSet` to handle URL/name formats; add inline search result dropdown |
| `src/guild_portal/templates/member/my_characters.html` | Comment out SimC modal block |
| `src/guild_portal/static/css/main.css` | Minor: optgroup styling, two-row header styles for `mcn-bis-grid`, inline search result dropdown styles |

No migrations.

---

## Open Questions Before Build

- **Content type label for Wowhead Overall**: display as "All" (matching u.gg Overall) or "Overall"? Decide on one consistent term across all providers.
- **HT dropdown hide vs. disable**: when switching to a non-HT source, should the HT picker vanish (cleaner) or stay visible but greyed out (reminds user it exists for some sources)? Prefer vanish.
- **Default marker symbol**: `★`, `·`, `(default)`, or a small CSS badge? Something that reads at small font sizes.
