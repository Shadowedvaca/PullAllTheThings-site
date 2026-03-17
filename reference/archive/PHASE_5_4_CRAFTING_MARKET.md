# Phase 5.4 — My Characters: Crafting & Raid Prep Panel

## Goal

Add a "Raid Prep" panel to the My Characters dashboard that combines two data sources
into a single actionable view: (1) what the character can craft that is valuable to the
guild, and (2) what consumables they need to purchase for raids. Makes the dashboard
a genuine one-stop-shop for preparing a character for raid night.

---

## Prerequisites

- Phase 5.0 complete (My Characters page foundation)
- Phase 5.3 complete (realm-aware AH prices available per character)
- Phase 4.3 complete (profession data in `character_recipes`)
- Crafting Corner data populated (recipes, professions)

---

## Panel A: What I Can Craft

Shows all recipes this character knows that appear on the guild's Crafting Corner
(i.e., are flagged as crafting-corner-visible or tracked).

### Layout

```
What Trogmoon Can Craft
─────────────────────────────────────────────────
Profession         Recipe               Can Craft?
────────────────   ──────────────────   ──────────
Inscription        Algari Manuscript    ✅ Yes
Inscription        Contract: Sen'jin    ✅ Yes
Inscription        Darkmoon Card…       ⚠ Rank 1
```

**Can Craft?** logic:
- `✅ Yes` — character has the recipe at max learned rank
- `⚠ Rank N` — character has the recipe but not at max rank (show which rank)
- `❌ No` — recipe exists in guild crafting corner but character doesn't have it
  (only show the `❌ No` rows if there are few of them; otherwise hide to reduce noise)

Link each recipe to Wowhead search (same pattern as crafting corner):
`https://www.wowhead.com/search?q={recipe_name}`

### Data Sources

- `character_recipes` — which recipes this character has learned
- `recipes` — recipe details (name, profession, rank info)
- `crafting_sync_config` or tracked recipe list — which recipes are "guild relevant"

---

## Panel B: Raid Consumables Checklist

Cross-references the guild's tracked AH items (category = `consumable` or `material`)
with the current price for the character's realm. Presents it as a "shopping list."

### Layout

```
Raid Consumables — Current Prices (Sen'jin)
────────────────────────────────────────────────
Item                       Price        Status
──────────────────────────  ──────────   ──────
Tempered Potion            18g 50s      📈 +12% today
Flask of Tempered Swiftness 22g          📉 -5% today
Algari Mana Potion         9g 25s       ✅ stable
Crystallized Augment Rune  45g          ⚠ Low stock (12)
```

**Status indicators:**
- `📈 +N%` — price up >5% since yesterday
- `📉 -N%` — price down >5% since yesterday
- `✅ stable` — within 5% of yesterday
- `⚠ Low stock` — `quantity_available < 50` (configurable threshold)
- `—` — not listed (no price data)

Price data comes from `get_prices_for_realm()` for this character's connected realm.
Trend from `get_price_change()` service function (already exists from Phase 4.6).

### Quick-link to AH

Each item row links to Wowhead search (same as Market Watch), so the player can
open the Wowhead page to check live TSM data or addon pricing.

---

## Panel Layout on My Characters Page

Stack order (all panels, top to bottom):
1. Stat Panel (Phase 5.0)
2. Progression Panel (Phase 5.1)
3. WCL Parses Panel (Phase 5.2)
4. Market Prices for This Realm (Phase 5.3)
5. **Crafting & Raid Prep Panel** (Phase 5.4)
   - Sub-section A: What I Can Craft
   - Sub-section B: Raid Consumables

Panels are collapsible (click header to toggle) to avoid overwhelming the page.
Collapse state saved to `localStorage` per panel key.

---

## API Changes

### `GET /api/v1/me/character/{character_id}/crafting`

```json
{
  "ok": true,
  "data": {
    "character_id": 42,
    "craftable": [
      {
        "recipe_id": 7,
        "recipe_name": "Algari Manuscript",
        "profession": "Inscription",
        "rank": 5,
        "max_rank": 5,
        "can_craft_fully": true,
        "wowhead_url": "https://www.wowhead.com/search?q=Algari+Manuscript"
      }
    ],
    "consumables": [
      {
        "tracked_item_id": 3,
        "item_name": "Tempered Potion",
        "category": "consumable",
        "min_buyout": 185000,
        "min_buyout_display": "18g 50s",
        "change_pct": 12.3,
        "quantity_available": 340,
        "wowhead_url": "https://www.wowhead.com/search?q=Tempered+Potion"
      }
    ]
  }
}
```

---

## File Changes

### Modified Files

- `src/guild_portal/templates/member/my_characters.html` — add crafting/prep panels
- `src/guild_portal/static/css/my_characters.css` — checklist styles, status badge styles
- `src/guild_portal/static/js/my_characters.js` — fetch + render crafting panel,
  collapsible panel toggle + localStorage
- `src/guild_portal/api/member_routes.py` — add crafting endpoint

---

## Design Notes

- Collapsible panels use a `<details>/<summary>` native HTML element or a JS toggle
- "Stable" items shown in muted text; trend items shown with gold (up) or cool blue (down)
- Low stock shown in amber/orange to draw attention
- Section B only shown if AH price data exists for the character's realm
- Section A only shown if the character has any known recipes

---

## Tests

- `GET /api/v1/me/character/{id}/crafting` requires auth + own-character check
- Correct recipes returned (only guild-relevant, with rank info)
- `can_craft_fully` correct: True when `rank == max_rank`
- Consumables list correct: only active tracked items with `category IN ('consumable', 'material')`
- Change % computed correctly (positive = price up)
- Low stock flag: `quantity_available < 50`
- No data states: no recipes → `craftable: []`; no prices → `consumables: []`

---

## Deliverables Checklist

- [ ] `GET /api/v1/me/character/{id}/crafting` endpoint
- [ ] Own-character auth check
- [ ] Section A: craftable recipes, rank info, Wowhead links
- [ ] Section B: consumables shopping list, price + trend + stock status
- [ ] Collapsible panel header with localStorage collapse state
- [ ] Templates + CSS + JS
- [ ] Tests
