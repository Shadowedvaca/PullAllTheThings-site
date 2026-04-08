# My Characters — Full UI Redesign Plan
# (Incorporates Gear Plan; replaces /my-characters + /gear-plan)

> **Status:** UI-1G complete (Professions + Market panels, icon fixes, recipe table redesign); UI-1H next  
> **Branch strategy:** All work on `feature/gear-plan-phase-1d`  
> **Temp URL during dev:** `/my-characters-new` (delete old pages, rename at end)  
> **Last updated:** 2026-04-08

---

## 1. Design Vision

Replace the two existing pages (`/my-characters` and `/gear-plan`) with a single, unified
character page. The new page has a WoW character-sheet feel: paperdoll on the left and right
columns, a persistent header describing who you are looking at, and a swappable center panel
that shows the detail for whichever section the user clicks into.

**Core principles:**
- No emojis. Real WoW icons for everything (class, spec, race, role, professions).
- Persistent top section (character identity + guide links) — never replaced by a drill-in.
- Center panel swaps: six summary cards by default; clicking one replaces center with detail.
- Paperdoll always flanks the center. It is visible in every state.
- Visual communication over text labels — coloured borders, icons, and status marks carry
  meaning that would otherwise require words.

---

## 2. Layout Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│  CHARACTER SELECTOR  [dropdown]  [Refresh / BNet badge]                    │
├────────────────────────────────────────────────────────────────────────────┤
│  PERSISTENT HEADER (never replaced)                                        │
│  ─────────────────────────────────────────────────────────────────────── │
│  Trogmoon          Sen'jin          [BNet ✓]                               │
│  [class icon] Balance Druid  [spec icon]  Night Elf  [role icon] Ranged   │
│               [MAIN badge]                                                  │
│  ─────────────────────────────────────────────────────────────────────── │
│  GUIDES: [Spec ▼]  [Guide Type ▼]  [Go →]                                 │
├──────────────┬─────────────────────────────────────────┬──────────────────┤
│  LEFT PAPERDOLL   │   CENTER PANEL (swappable)         │  RIGHT PAPERDOLL │
│  ─────────── │   ─────────────────────────────────    │  ─────────────   │
│  [Head   ]   │   Default: 6 summary cards:             │  [Hands   ]      │
│  [Neck   ]   │   ┌──────┐ ┌──────┐ ┌──────┐           │  [Waist   ]      │
│  [Shoulder]  │   │ Gear │ │ M+   │ │ Raid │           │  [Legs    ]      │
│  [Back   ]   │   │ 489  │ │ 2450 │ │ 8/8H │           │  [Feet    ]      │
│  [Chest  ]   │   └──────┘ └──────┘ └──────┘           │  [Ring 1  ]      │
│  [Shirt* ]   │   ┌──────┐ ┌──────┐ ┌──────┐           │  [Ring 2  ]      │
│  [Tabard*]   │   │Parse │ │ Prof │ │Market│           │  [Trinket1]      │
│  [Wrist  ]   │   │  72% │ │ 2/3  │ │      │           │  [Trinket2]      │
│              │   └──────┘ └──────┘ └──────┘           │                  │
│  [MainHand]  │                                         │  [OffHand ]      │
└──────────────┴─────────────────────────────────────────┴──────────────────┘
```

**When a summary card is clicked, center becomes:**
```
┌──────────────────────────────────────────┐
│  [← Back]  Gear Plan                     │
│  ─────────────────────────────────────   │
│  [Hero Talent ▼] [BIS Source ▼]          │
│  [Sync Gear] [Fill BIS] [Import] [Export]│
│  [Reset Plan]                            │
│  ─────────────────────────────────────   │
│  Slot table (Option C — see §5)          │
└──────────────────────────────────────────┘
```

The paperdoll columns stay in place during all detail views.

---

## 3. Paperdoll Slot Card Design

Each card holds two sub-boxes side by side. Interior side = upgrade box, exterior side =
equipped box. "Interior" means toward the center column (Option B).

```
LEFT COLUMN card (upgrade left, equipped right):
┌─────────────────────────────────────────────┐
│  [upgrade box]     │    [equipped box]       │
│  [goal icon]       │    [equipped icon]      │
│  [V][C][H] pills   │    276                  │
└─────────────────────────────────────────────┘

RIGHT COLUMN card (equipped left, upgrade right — row-reverse):
┌─────────────────────────────────────────────┐
│  [equipped box]    │    [upgrade box]        │
│  [equipped icon]   │    [goal icon]          │
│  276               │    [V][C][H] pills      │
└─────────────────────────────────────────────┘
```

**Upgrade box states:**

| State | Visual |
|-------|--------|
| Not BIS | Goal item icon (quality-colored border = min track needed for upgrade); Wowhead hover; upgrade track pills below |
| BIS, not Mythic | Gold star-checkmark icon; upgrade pills below (e.g., [M] only) |
| BIS at Mythic | Single green checkmark; no pills needed |
| No goal set | Faint dashed outline; no pills |
| Inactive (shirt/tabard) | Both boxes dimmed; no content |

**Equipped box:**
- Item icon, quality border (V=green / C=blue / H=purple / M=orange / unknown=faint white)
- ilvl as a small number below the icon
- Wowhead hover tooltip on icon

**Slot label:** Displayed as a small CAPS row above both boxes (full width of card).

---

## 4. WoW Icon Strategy

All game icons served from Wowhead CDN:
`https://wow.zamimg.com/images/wow/icons/medium/{slug}.jpg`

**Class icons** — hardcoded slug map in JS (36 entries, one per class):
```js
const CLASS_ICONS = {
  'Death Knight': 'classicon_deathknight',
  'Druid':        'classicon_druid',
  // etc.
};
```

**Spec icons** — Blizzard media API returns `media.assets[].value` (icon URL) when we fetch
spec data. OR use hardcoded Wowhead slug map per spec (72 entries). Hardcoded preferred — no
extra API call.

**Race icons** — Wowhead has `race_nightelf_male` / `race_nightelf_female` style slugs. Store
a `gender` flag (M/F/X) on `wow_characters` or derive from Blizzard API `character-profile`
gender field (already in the equipment endpoint response). Map to slug.

**Role icons** — Four icons: tank, healer, melee DPS, ranged DPS. Wowhead slugs:
`icon_role_tank`, `icon_role_healer`, `icon_role_dps` (or use the in-game LFD role icons).
Role derived from spec: existing `specializations.role` column already stores this.

**Profession icons** — Wowhead slugs follow the pattern `trade_{profession}`:
`trade_alchemy`, `trade_blacksmithing`, `trade_cooking`, etc. Map by profession name.

---

## 5. Gear Detail — Option C Table

Full-width table replacing center panel when Gear card is clicked.

```
┌──────────┬───────────────────────────────┬───────────────────────────┬──────────────────┬──────────────┐
│  Slot    │  Equipped                     │  Goal                     │  Source          │  Upgrades    │
├──────────┼───────────────────────────────┼───────────────────────────┼──────────────────┼──────────────┤
│  Head    │  [icon] Branches... 276 [H]   │  ✓ BIS                    │  —               │  [M]         │
│  Neck    │  [icon] Chain... 263 [C]      │  [icon] Pendant of Night  │  Fyrakk • Mythic │  [H] [M]     │
│  Hands   │  [icon] Arbortenders 276 [H]  │  ✓ BIS                    │  —               │  [M]         │
│  Wrist   │  [icon] Aetherlume 272 Crafted│  [icon] Better Wrist      │  The Stonevault  │  [H] [M]     │
└──────────┴───────────────────────────────┴───────────────────────────┴──────────────────┴──────────────┘
```

- Wowhead hover on all item name links and icons
- Rows with is_bis + Mythic get a green ✓ row highlight
- Rows with needs_upgrade get a subtle red-left-border
- Action bar above table: [Hero Talent ▼] [BIS Source ▼] [Sync Gear] [Fill BIS] [Import SimC] [Export SimC] [Reset]

---

## 6. Data Sources Per Panel

| Panel | Summary Data | Detail Data | Existing API? |
|-------|-------------|-------------|---------------|
| Gear | avg ilvl (avg of `character_equipment.item_level` for 14 active slots) | gear_plan_service `get_plan_detail` | Yes — extend `/me/characters` to include avg_ilvl |
| M+ | `raiderio_profiles.overall_score` + `raiderio_url` | `raiderio_profiles` full data: per-dungeon scores, best keys | Yes — `/me/character/{id}/progression` |
| Raid | `raiderio_profiles.raid_progression` condensed summary | `character_raid_progress`: per-boss by difficulty | Yes — same endpoint |
| Parses | `character_report_parses` avg percentile (current zone) | Per-encounter grid: best %, total kills, avg % — grouped Raid/M+/Overall | New endpoint needed |
| Professions | `character_recipes` JOIN `professions`: name + icon slug | Full recipe list (existing crafting panel) | Yes — `/me/character/{id}/crafting` |
| Market | Top 5 tracked items + current realm price | Full AH price table | Yes — `/me/character/{id}/market` |

---

## 7. What Is NOT in This Redesign

- Admin pages — untouched
- Public roster page — untouched
- Gear plan admin (`/admin/gear-plan`) — untouched
- BIS sync/scrape pipeline — untouched
- WCL sync pipeline — untouched
- Raider.IO sync pipeline — untouched

---

## 8. Phase Breakdown

### Phase UI-1A — Foundation: new page shell + character header

**Purpose:** Stand up the new page at `/my-characters-new`. Get the persistent header
rendering correctly with WoW icons.

**Scope:**
- Migration 0080: add `race VARCHAR(40)` to `guild_identity.wow_characters`;
  update `bnet_character_sync.py` to populate it from the `playable-race.name` field
  in the `/character-profile` endpoint response.
- New route `/my-characters-new` in `gear_plan_pages.py` (or a dedicated page file).
  Shares auth/session logic with the existing My Characters route. Old `/my-characters`
  route stays untouched.
- New template `templates/member/my_characters_new.html` (extends `base.html`).
  Sections: selector bar, persistent header (rows 1–2 + guides row), center panel
  placeholder, left paperdoll placeholder, right paperdoll placeholder.
- New CSS `static/css/my_characters_new.css` — new design language.
  Three-column grid layout (columns defined as CSS Grid not flexbox).
  No styles shared with `my_characters.css` or `gear_plan.css` — clean slate.
- New JS `static/js/my_characters_new.js` — character selector + header render only.
  Reads from `/api/v1/me/characters` (existing endpoint — already returns realm_display,
  class_name, spec_name, is_main, is_offspec, raiderio_url, wcl_url, bnet_linked).
- Icon render helpers: `classIcon(className)`, `specIcon(specName)`, `raceIcon(race, gender)`,
  `roleIcon(role)` — hardcoded slug maps, render `<img>` from Wowhead CDN.
- Realm name: use `realm_display` from existing API (already human-readable).
- BNet badge: reuse existing logic from `/me/characters` (bnet_linked flag).
- Main/Off badges: from `is_main` / `is_offspec` on character object.
- Guide section: spec dropdown + guide type dropdown + Go button — compact flex row.
  Reuse `guide_links` data already on character object.

**API changes:** None — all data already in `/api/v1/me/characters`.

**Migration:** 0080 — `race` column on `wow_characters`.

**Files created/changed:**
- `alembic/versions/0080_wow_characters_race.py` (new)
- `src/sv_common/guild_sync/bnet_character_sync.py` (add race field)
- `src/guild_portal/pages/gear_plan_pages.py` (add `/my-characters-new` route)
- `src/guild_portal/templates/member/my_characters_new.html` (new)
- `src/guild_portal/static/css/my_characters_new.css` (new)
- `src/guild_portal/static/js/my_characters_new.js` (new)

**Done when:** `/my-characters-new` loads, character selector works, header shows name /
realm / BNet badge / class icon / spec icon / race text / role icon / Main badge — all for
Trogmoon. Old pages still work. Deploy to dev, verify.

---

### Phase UI-1B — Summary cards + center panel switching

**Purpose:** Six summary cards in the default center panel. Clicking any card replaces center
with a placeholder detail panel. Wire up real data for all six summaries.

**Scope:**
- Extend `/api/v1/me/characters` response (or add new `GET /api/v1/me/character/{id}/summary`
  endpoint) to return:
  ```json
  {
    "avg_ilvl": 271,
    "mplus_score": 2450,
    "mplus_color": "#a335ee",
    "raid_summary": "8/8 Heroic",
    "avg_parse": 72,
    "profession_count": 2,
    "profession_total": 3
  }
  ```
  - `avg_ilvl`: average of `character_equipment.item_level` for active 14 slots (exclude
    shirt/tabard). Simple SQL avg.
  - `mplus_score`: from `raiderio_profiles.overall_score`.
  - `mplus_color`: from `raiderio_profiles.score_color`.
  - `raid_summary`: from `raiderio_profiles.raid_progression` JSONB — find the current tier,
    return highest difficulty clear as "X/Y Heroic" or "X/Y Mythic".
  - `avg_parse`: avg percentile from `character_report_parses` for current zone (same
    logic as roster page avg_parse).
  - `profession_count` / `profession_total`: count of character's known professions from
    `character_recipes` JOIN `professions`.
- 6 summary card components in the new JS. Each card: icon (SVG or Wowhead CDN), title,
  big stat value, subtitle. Click → call `setDetailPanel(panelName)`.
- `setDetailPanel(name)` replaces center panel innerHTML with a placeholder ("Loading…" or
  skeleton) and calls the appropriate render function (stubbed in this phase — all just
  show "[Panel Name] detail — coming soon").
- Back button (`← Overview`) in detail panel header resets to summary cards.
- CSS for the 6-card grid (2 rows × 3 cols, or 3 rows × 2 cols on narrow viewports).

**API changes:** New `GET /api/v1/me/character/{id}/summary` endpoint in
`gear_plan_routes.py` (or `member_routes.py`). Single fast query.

**Files created/changed:**
- `src/guild_portal/api/gear_plan_routes.py` or `member_routes.py` (new summary endpoint)
- `src/guild_portal/templates/member/my_characters_new.html` (center panel structure)
- `src/guild_portal/static/css/my_characters_new.css` (card grid styles)
- `src/guild_portal/static/js/my_characters_new.js` (6 card renders + panel switching)

**Done when:** All 6 summary cards show real data for Trogmoon. Clicking any card shows
the placeholder detail view. Back button returns to cards. Deploy to dev, verify.

---

### Phase UI-1C — Paperdoll redesign ✓ COMPLETE

**Purpose:** Replace the current gear_plan slot cards with the new two-box design (upgrade
box + equipped box). Wire into the existing gear_plan_service — no service changes.

Edit: We need to preserve the blizzard look so Put main hand and off hand at the bottom middle.

**Scope:**
- New slot card render function `buildSlotCard(slotKey, slotData)` in
  `my_characters_new.js` (replaces `buildSlotCard` logic from `gear_plan.js`).
- Card structure: slot label row (full width) + content row (upgrade box | equipped box).
  Option B positioning: upgrade box on interior side, equipped box on exterior side.
  Right-column cards use `flex-direction: row-reverse` so equipped is still "outer."
- **Upgrade box states** (see §3 above for full spec):
  - Not BIS: goal icon (quality-colored, Wowhead href) + upgrade track pills
  - BIS not Mythic: star-check SVG icon (gold, inline SVG or icon font) + remaining pills
  - BIS at Mythic: single green check SVG; no pills
  - No goal: dashed empty box (40×40px)
  - Inactive: dimmed dual-placeholder
- **Equipped box:** icon with quality border + ilvl below. Wowhead href on icon link.
- Gear plan detail panel (triggered by Gear summary card) renders the paperdoll using
  `renderGearPaperdoll()` which calls the existing `/api/v1/me/gear-plan/{id}` endpoint.
- Config controls compact bar above paperdoll: [Hero Talent ▼] [BIS Source ▼] — use
  existing plan config API (`PATCH /api/v1/me/gear-plan/{id}/config`).
- Action buttons compact row: Sync Gear, Fill BIS, Import SimC, Export SimC, Reset Plan.
  Reuse existing gear plan API endpoints (no changes needed).
- SimC import modal — reuse existing modal markup, just move it into the new template.
- Slot drawer — keep the existing drawer mechanism, adapting CSS to the new template context.

**API changes:** None — reuses all existing gear plan API endpoints.

**Files created/changed:**
- `src/guild_portal/static/css/my_characters_new.css` (paperdoll + new card styles)
- `src/guild_portal/static/js/my_characters_new.js` (slot card render + gear plan wiring)
- `src/guild_portal/templates/member/my_characters_new.html` (paperdoll DOM structure,
  SimC modal, drawer placeholder)

**Done when:** Gear detail panel shows the paperdoll with new two-box cards. Trogmoon's
items show quality borders, ilvls, upgrade states. Sync Gear / Fill BIS / SimC import work.
Drawer opens on card click. Deploy to dev, verify all slot states visible.

**As shipped (deviations from spec):**
- Shirt and Tabard removed entirely (not dimmed — gone)
- Both Main Hand and Off Hand in left column (bottom), separated from body slots by a faint rule
- Right column: Hands → Trinket 2 only (8 slots, no weapon)
- `GP_LEFT_BODY_SLOTS` + `GP_LEFT_WEAPON_SLOTS` split for clean render with separator

---

### Phase UI-1D — Gear detail: Option C table

**Purpose:** The Gear detail panel shows both the paperdoll (for visual overview) AND the
Option C full-width table (for readable per-slot details) below it. They show the same data.

**Scope:**
- `renderGearTable(slots)` function in JS — builds the 16-row table from the same
  `get_plan_detail` API response already used for the paperdoll.
- Columns: Slot | Equipped (icon + name + ilvl + track badge) | Goal (icon + name, or ✓ BIS)
  | Source (boss • instance) | Upgrades (track pills, or ✓ if Mythic BIS)
- All item name links wrapped in `<a href="https://www.wowhead.com/item=N">` for tooltips.
- Rows: green-left-border for BIS rows; red-left-border for needs_upgrade rows.
- Table scrolls independently if tall; paperdoll stays above it.
- "No plan yet" empty state with a "Set up gear plan" call to action.

**API changes:** None — same `get_plan_detail` endpoint.

**Files created/changed:**
- `src/guild_portal/static/css/my_characters_new.css` (table styles)
- `src/guild_portal/static/js/my_characters_new.js` (renderGearTable function)

**Done when:** Gear detail shows paperdoll on top and table below. Table has correct data,
Wowhead tooltips fire, BIS/upgrade row coloring correct. Deploy to dev, verify.

**As shipped + post-ship fixes:**
- Paperdoll slot click routes into center panel (above "Gear Plan" heading) instead of the bottom drawer. Gold-bordered slot detail section shows equipped, goal, BIS grid, drop source; same slot click or × closes it.
- Three `gear_plan_service.py` bugs fixed: (1) wrong paired slot updated — `_normalize_paired_slot` now records `canonical_slot` per slot; frontend uses it for all writes; (2) empty Hero Talent + BIS Source dropdowns — API returns snake_case (`bis_sources`, `hero_talents`, `track_colors`), JS now reads those keys correctly; (3) duplicate paired slots after Reset Plan — removed BIS fallback for `desired_bid` in slot-building loop.

---

### Phase UI-1E — Raid and M+ detail panels ✓ COMPLETE

**Purpose:** Clicking the Raid or M+ summary card shows meaningful detail.

**As shipped:**
- Raid panel: difficulty tabs (Normal/Heroic/Mythic) with killed/total counts; tab defaults
  to highest difficulty with any kills; per-boss list with ✓ (green) / ✗ (muted) rows.
  Filtered to active season via `current_raid_ids` from `patt.raid_seasons`.
- M+ panel: `_mplusScoreTier()` color-coded overall score (large, Cinzel font) + season name
  + per-dungeon table (Dungeon / Best Key [+level + ⏱ if timed] / Score); zero-run dungeons
  dimmed. Filtered to active season via `blizzard_mplus_season_id` from `patt.raid_seasons`.
- `GET /api/v1/me/character/{id}/progression` extended: added `raid_bosses` list (per-boss
  detail with difficulty/boss_name/killed bool) and `mythic_plus.dungeons` list
  (dungeon_name/best_level/best_timed/best_score). `_progressionCache` prevents re-fetches.
- RIO / WCL / Armory links moved from detail panels to the **persistent guides bar** — always
  visible regardless of active tab. Separated from guide badges by a subtle border. Update
  automatically when character selector changes.

**Files changed:**
- `src/guild_portal/api/member_routes.py` (progression endpoint extended)
- `src/guild_portal/static/js/my_characters_new.js` (_renderRaidDetail, _renderMplusDetail,
  _progressionCache, ext-links in _renderHeader)
- `src/guild_portal/static/css/my_characters_new.css` (.mcn-prog-panel, .mcn-diff-tab,
  .mcn-boss-row, .mcn-mplus-*, .mcn-char-ext-link)
- `src/guild_portal/templates/member/my_characters_new.html` (mcn-char-ext-links div)

---

### Phase UI-1F — Parses detail panel ✓ COMPLETE

**Purpose:** Clicking the Parses summary card shows a WCL-based per-encounter breakdown.

**Scope:**
- New endpoint `GET /api/v1/me/character/{id}/parses-detail` in `member_routes.py`:
  ```sql
  SELECT encounter_name, zone_id, difficulty,
         MAX(percentile)   AS best_pct,
         COUNT(*)          AS total_kills,
         AVG(percentile)   AS avg_pct,
         MAX(amount)       AS best_dps
    FROM guild_identity.character_report_parses
   WHERE character_id = $1
   GROUP BY encounter_name, zone_id, difficulty
   ORDER BY zone_id, difficulty, encounter_name
  ```
  Returns data grouped into three buckets: raid (zone_id = known raid zones), mythic_plus
  (difficulty = 'mythic_plus' if WCL returns it), overall (all rows for avg computation).
- UI: three tabs — Raid | M+ | Overall.
  Each tab: table with columns Boss | Best % (color-coded) | Kills | Avg % | Best DPS.
  "No data" state for M+ tab (expected for most players).
- Overall tab: one row per encounter (highest difficulty if duplicated), same columns.
  Overall avg parse = same value shown on Roster page.
- WCL profile link at top of panel.
- Parse percentile coloring reuse: same tier thresholds as existing `parsePercentileTier`.

**API changes:** One new read-only endpoint. No schema changes.

**Files created/changed:**
- `src/guild_portal/api/member_routes.py` (new `/parses-detail` endpoint)
- `src/guild_portal/static/css/my_characters_new.css`
- `src/guild_portal/static/js/my_characters_new.js` (renderParsesDetail + tabs)

**Done when:** Parses detail shows per-boss table for Trogmoon with correct color coding.
Tabs work (Raid shows data; M+ shows "no data" gracefully). Deploy to dev, verify.

**As shipped:**
- Raid tab includes a Difficulty column (Normal/Heroic/Mythic) rather than difficulty sub-tabs, keeping the table flat and scannable.
- M+ tab shows a clean "No M+ parse data available" empty state (WCL does not publish M+ parses).
- Overall tab: one row per encounter name, highest difficulty kept.
- WCL Profile link rendered inline in the panel heading.
- Parse coloring uses same tier thresholds as the existing `parsePercentileTier` in `my_characters.js`: ≥100 pink, ≥99 gold, ≥95 orange, ≥75 purple, ≥50 blue, ≥25 green, else gray.
- Results cached in `_parsesCache` per character_id (same pattern as `_progressionCache`).

---

### Phase UI-1G — Professions + Market detail panels ✓ COMPLETE

**Purpose:** Fill in the two remaining summary cards.

**As shipped + post-ship fixes:**
- Professions panel: grid of known professions with Wowhead icons + recipe counts; uses existing `/crafting` endpoint. Profession icon slugs hardcoded in `PROFESSION_ICONS` map (13 professions). Fixed slugs: `trade_engraving` for Enchanting, `inv_misc_food_15` for Cooking (`trade_enchanting` / `trade_cooking` / `ability_cooking` all 404 on Wowhead CDN).
- Recipe table redesigned: 4 columns — Profession / Expansion / Recipe / WH link badge. Filter bar has Profession dropdown + Expansion dropdown (populated from actual data) + search field; all controls dark-themed via `--color-bg-card` (was `--color-surface` which is undefined → browser default light grey). 15-row pagination with Prev/Next.
- Market panel: full AH price table with category badges, gold formatting, realm-specific footnote + last-updated timestamp in upper-right of panel heading (derived from `MAX(snapshot_at)` across returned rows; displayed in user's local time via `toLocaleTimeString()`). Market API now returns `last_updated` ISO timestamp. Tab count updates dynamically after data loads.
- `/character/{id}/summary` endpoint extended: now returns `profession_names: list[str]` in addition to `profession_count`.
- `goldStr` and `escHtml` helpers added to `my_characters_new.js`.
- `_craftingCache` and `_marketCache` added (same pattern as progression/parses caches).

**Files changed:**
- `src/guild_portal/api/member_routes.py` (summary endpoint: profession_names)
- `src/guild_portal/static/css/my_characters_new.css` (prof + market styles)
- `src/guild_portal/static/js/my_characters_new.js` (fetch/render functions + router)

---

### Phase UI-1H — Cleanup + migration to canonical URLs

**Purpose:** Remove the old pages. The new page becomes `/my-characters`. `/gear-plan`
becomes a redirect.

**Scope:**
- Rename route `/my-characters-new` → `/my-characters` in `gear_plan_pages.py`.
- Rename template `my_characters_new.html` → `my_characters.html` (after deleting old one).
- Rename CSS/JS: `my_characters_new.css` → `my_characters.css`, same for JS
  (delete old `my_characters.css` / `my_characters.js`).
- `/gear-plan` route: return HTTP 302 redirect to `/my-characters`.
- Remove Gear Plan nav link from `base.html`.
- `screen_permissions`: migration 0081 — check if `my_gear_plan` screen permission row
  should be merged into `my_characters` or retired.
- Old template `my_characters.html` (pre-redesign): delete.
- Old gear_plan template and standalone JS: `gear_plan.html` → delete (or repurpose as
  redirect page). `gear_plan.js` and `gear_plan.css` → delete after confirming no other
  references. CSS that is still needed (slot card styles, drawer styles) should already
  have been migrated into `my_characters_new.css` in earlier phases.
- Smoke test: all links in nav, all character-related flows, no broken CSS/JS references.
- Update `CLAUDE.md` current phase section.
- Update `gear-plan-1-feature.md` to reflect Phase 1D → complete, 1E superseded by UI redesign.

**Migration:** 0081 — screen permissions cleanup.

**Files changed:**
- `src/guild_portal/pages/gear_plan_pages.py`
- `src/guild_portal/templates/base.html` (remove Gear Plan nav link)
- `src/guild_portal/templates/member/my_characters_new.html` → rename
- `src/guild_portal/static/css/my_characters_new.css` → rename + delete old
- `src/guild_portal/static/js/my_characters_new.js` → rename + delete old
- `src/guild_portal/templates/member/my_characters.html` (old) → delete
- `src/guild_portal/templates/member/gear_plan.html` → delete
- `src/guild_portal/static/css/gear_plan.css` → delete
- `src/guild_portal/static/js/gear_plan.js` → delete
- `alembic/versions/0081_screen_permissions_cleanup.py` (new)
- `CLAUDE.md` (phase status update)

**Done when:** Single `/my-characters` page. `/gear-plan` redirects cleanly. No 404s. No
orphaned CSS/JS. Old My Characters page and Gear Plan page are gone. Prod-ready.

**Post-deploy prod action required:**
After tagging and deploying to prod, trigger a WCL sync from **Admin → Warcraft Logs**.
This re-queries all stored reports and uses `difficulty = EXCLUDED.difficulty` in the
upsert to correct every row that was previously hardcoded to `3` (Normal). Until this
runs, the Parses panel "By Difficulty" section may still show all rows as Normal on prod.
The Chimaerus Normal vs Heroic split should be visible and correctly labelled after the sync.

---

## 9. Phase Dependency Map

```
UI-1A (foundation + header)
  └─ UI-1B (summary cards + panel switching)
       ├─ UI-1C (paperdoll redesign)      ← can parallel with UI-1D
       │    └─ UI-1D (gear table)
       ├─ UI-1E (raid + M+ detail)        ← can parallel with UI-1C
       ├─ UI-1F (parses detail)           ← can parallel with UI-1C
       └─ UI-1G (professions + market)    ← can parallel with UI-1C
            └─ UI-1H (cleanup + rename)   ← last, needs all above
```

UI-1C, UI-1E, UI-1F, UI-1G have no inter-dependencies and can be done in any order after
UI-1B is complete.

---

## 10. Known Risks + Open Questions

| Risk | Mitigation |
|------|-----------|
| Per-dungeon M+ data not stored in `raiderio_profiles` | Ship summary view (overall score only) in UI-1E; per-dungeon is a follow-up |
| Race field population requires a character re-sync | `bnet_character_sync.py` picks it up on next scheduled sync; manual re-sync available on Profile page |
| WoW icon CDN slugs for new Midnight specs/races may not exist yet | Fall back to a generic class/spec icon; add a `data-icon-slug` attribute so it's easy to patch |
| `gear_plan.js` is referenced from `gear_plan.html` AND potentially the admin gear-plan page | Verify admin gear-plan template before deleting `gear_plan.js` — may need to keep a slimmed version for admin use |
| Center panel is narrow on small viewports with paperdoll flanking | Set a min-width on the overall three-column layout; accept horizontal scroll on small screens (this is a PC-focused tool) |
| Professions expansion level data | Check `character_recipes` schema at start of UI-1G — drop level display if field absent |

---

## 11. File Inventory (end state)

**New files (survive into prod):**
- `alembic/versions/0080_wow_characters_race.py`
- `alembic/versions/0081_screen_permissions_cleanup.py`
- `src/guild_portal/pages/gear_plan_pages.py` (modified — new routes, old removed)
- `src/guild_portal/templates/member/my_characters.html` (renamed from `_new`)
- `src/guild_portal/static/css/my_characters.css` (renamed from `_new` — replaces old)
- `src/guild_portal/static/js/my_characters.js` (renamed from `_new` — replaces old)
- `src/guild_portal/api/member_routes.py` (modified — new summary + parses-detail endpoints)
- `src/guild_portal/api/gear_plan_routes.py` (modified — summary endpoint if added here)
- `src/sv_common/guild_sync/bnet_character_sync.py` (modified — populate race field)

**Deleted files:**
- `src/guild_portal/templates/member/my_characters.html` (pre-redesign version)
- `src/guild_portal/templates/member/gear_plan.html`
- `src/guild_portal/static/css/gear_plan.css`
- `src/guild_portal/static/js/gear_plan.js`
- `src/guild_portal/static/css/my_characters.css` (pre-redesign version)
- `src/guild_portal/static/js/my_characters.js` (pre-redesign version)

**Unchanged gear plan backend (no changes in any phase):**
- `src/guild_portal/services/gear_plan_service.py`
- `src/guild_portal/api/bis_routes.py`
- `src/sv_common/guild_sync/equipment_sync.py`
- `src/sv_common/guild_sync/quality_track.py`
- All migrations 0066–0079
