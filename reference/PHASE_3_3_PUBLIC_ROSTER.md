# Phase 3.3 — Public Roster View

## Goal

New live-data public page at `/roster` replacing the dead static `roster-view.html`. Public visitors
(no login required) can see guild composition, roster table, and a Wowhead composition link.

---

## Route Registration

**File:** `src/patt/pages/public_pages.py`

```python
@router.get("/roster", response_class=HTMLResponse)
async def roster_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Public roster view — no auth required."""
    event_days = await _get_event_days(db)  # reuse from Phase 3.2
    return templates.TemplateResponse(
        "public/roster.html",
        {"request": request, "event_days": event_days}
    )
```

Note: Roster data (players, characters) is loaded client-side via `GET /api/v1/guild/roster`
(already exists and is public). The page route only loads event_days for the Schedule tab.

**In `src/patt/app.py`:** Add redirect `/roster.html` → `/roster` and `/roster-view.html` → `/roster`.

---

## Template: `src/patt/templates/public/roster.html`

Extends `base.html` (public template — NOT base_admin.html).

### Page Structure

```html
<div class="roster-page">
  <!-- Tab navigation -->
  <div class="tab-nav">
    <button class="tab-btn active" data-tab="roster">Full Roster</button>
    <button class="tab-btn" data-tab="composition">Composition</button>
    <button class="tab-btn" data-tab="schedule">Schedule</button>
  </div>

  <!-- Tab 1: Full Roster -->
  <div id="tab-roster" class="tab-content active">...</div>

  <!-- Tab 2: Composition -->
  <div id="tab-composition" class="tab-content">...</div>

  <!-- Tab 3: Schedule -->
  <div id="tab-schedule" class="tab-content">...</div>
</div>
```

---

## Tab 1 — Full Roster

### Controls
- Search box (filters by name, character, class, spec — client-side)
- Checkbox: "Show alts" (default: unchecked — mains only)
- Sort buttons or column header clicks

### Table columns
| Column | Data source | Notes |
|--------|-------------|-------|
| Player | `player.display_name` | |
| Character | `character.character_name` | Class color applied |
| Class | `character.class_name` | With class emoji |
| Spec | `character.spec_name` | |
| Role | `character.role_name` | Colored dot/icon |
| Rank | `player.rank_name` | |
| iLvl | `character.item_level` | Right-aligned |
| Armory | Link | Opens in new tab |

### Class color map (port from legacy roster-view.html lines 700–714)

```javascript
const CLASS_COLORS = {
    "Death Knight": "#C41E3A",
    "Demon Hunter": "#A330C9",
    "Druid": "#FF7C0A",
    "Evoker": "#33937F",
    "Hunter": "#AAD372",
    "Mage": "#3FC7EB",
    "Monk": "#00FF98",
    "Paladin": "#F48CBA",
    "Priest": "#FFFFFF",
    "Rogue": "#FFF468",
    "Shaman": "#0070DD",
    "Warlock": "#8788EE",
    "Warrior": "#C69B3A"
};
```

### Data loading

On page load, fetch from existing public endpoint:
```javascript
const res = await fetch('/api/v1/guild/roster');
const { data } = await res.json();
// data.players array with main character info
```

The existing `/api/v1/guild/roster` endpoint returns:
- `player_id`, `display_name`, `rank_name`, `rank_level`
- `main_character`: `{character_name, realm_slug, class_name, spec_name, role_name, item_level, armory_url}`
- `characters`: array of all characters (for alt view)

### Mains vs Alts toggle logic

```javascript
function renderRoster(showAlts) {
    const rows = [];
    for (const player of players) {
        // Always show main
        rows.push(buildRow(player, player.main_character, 'main'));
        // Optionally show alts
        if (showAlts && player.characters) {
            for (const char of player.characters) {
                if (char.character_id !== player.main_character?.character_id) {
                    rows.push(buildRow(player, char, 'alt'));
                }
            }
        }
    }
    // render rows into table
}
```

---

## Tab 2 — Composition

### Role Distribution Cards

Four cards: Tank, Healer, Melee DPS, Ranged DPS.

**Targets:**
```javascript
const ROLE_TARGETS = { "Tank": 2, "Healer": 4, "Melee DPS": 6, "Ranged DPS": 6 };
```

Each card shows:
- Role name + icon
- `N / Target` count
- Color: green if count >= target, amber if count == target-1, red if count < target-1
- Progress bar

### Class Distribution Grid

Count mains per class, sorted by count desc. Simple grid of `[Class Emoji] ClassName (N)`.

### Wowhead Comp Link Button

**Port the spec code map from legacy `roster-view.html` lines 949–1003 into JavaScript.**

The Wowhead Comp Analyzer URL format:
```
https://www.wowhead.com/raid-comp#{specCode1}{specCode2}...
```

**Spec code map (all 39 specs + Midnight specs):**

```javascript
const SPEC_CODES = {
    // Death Knight
    "Death Knight/Blood": "K1",
    "Death Knight/Frost": "K2",
    "Death Knight/Unholy": "K3",
    // Demon Hunter
    "Demon Hunter/Havoc": "Y1",
    "Demon Hunter/Vengeance": "Y2",
    // Druid
    "Druid/Balance": "D1",
    "Druid/Feral": "D2",
    "Druid/Guardian": "D3",
    "Druid/Restoration": "D4",
    // Evoker
    "Evoker/Devastation": "V1",
    "Evoker/Preservation": "V2",
    "Evoker/Augmentation": "V3",
    // Hunter
    "Hunter/Beast Mastery": "H1",
    "Hunter/Marksmanship": "H2",
    "Hunter/Survival": "H3",
    // Mage
    "Mage/Arcane": "M1",
    "Mage/Fire": "M2",
    "Mage/Frost": "M3",
    // Monk
    "Monk/Brewmaster": "O1",
    "Monk/Mistweaver": "O2",
    "Monk/Windwalker": "O3",
    // Paladin
    "Paladin/Holy": "P1",
    "Paladin/Protection": "P2",
    "Paladin/Retribution": "P3",
    // Priest
    "Priest/Discipline": "R1",
    "Priest/Holy": "R2",
    "Priest/Shadow": "R3",
    // Rogue
    "Rogue/Assassination": "U1",
    "Rogue/Outlaw": "U2",
    "Rogue/Subtlety": "U3",
    // Shaman
    "Shaman/Elemental": "S1",
    "Shaman/Enhancement": "S2",
    "Shaman/Restoration": "S3",
    // Warlock
    "Warlock/Affliction": "W1",
    "Warlock/Demonology": "W2",
    "Warlock/Destruction": "W3",
    // Warrior
    "Warrior/Arms": "A1",
    "Warrior/Fury": "A2",
    "Warrior/Protection": "A3",
    // Midnight specs (future-proof)
    "Death Knight/San'layn": "K4",
};

function buildWowheadUrl(players) {
    const codes = players
        .filter(p => p.main_character?.class_name && p.main_character?.spec_name)
        .map(p => SPEC_CODES[`${p.main_character.class_name}/${p.main_character.spec_name}`])
        .filter(Boolean)
        .join('');
    return `https://www.wowhead.com/raid-comp#${codes}`;
}
```

Button: "🔗 View Comp on Wowhead" — opens URL in new tab.

---

## Tab 3 — Schedule

Simple display. No availability percentages (those are officer-only).

```jinja2
{% if event_days %}
  <div class="schedule-list">
    {% for event in event_days %}
    <div class="schedule-row">
      <span class="schedule-day">{{ day_names[event.day_of_week] }}</span>
      <span class="schedule-label">{{ event.label }}</span>
      <span class="schedule-time">{{ event.default_start_time | format_time }} EST</span>
    </div>
    {% endfor %}
  </div>
{% else %}
  <p>Check our Discord for the current raid schedule.</p>
{% endif %}
```

Uses `event_days` loaded server-side (same query as Phase 3.2 index page).

---

## Static file / route cleanup (`src/patt/app.py`)

Remove or redirect legacy static file serving:

```python
# Add redirects for legacy static files
@app.get("/roster.html")
async def roster_html_redirect():
    return RedirectResponse(url="/roster", status_code=301)

@app.get("/roster-view.html")
async def roster_view_redirect():
    return RedirectResponse(url="/roster", status_code=301)
```

Do NOT delete the actual static files immediately — they may be referenced elsewhere.
Set redirects, verify nothing breaks, clean up static files in a follow-up.

---

## CSS Additions

Add to `src/patt/static/css/main.css` (or a new `roster.css`):

- `.tab-nav` — horizontal pill tabs with gold active state
- `.tab-content` — hidden by default, `display: block` when `.active`
- `.roster-table` — sortable table with class color support
- `.role-card` — role distribution cards with color-coded borders
- `.class-grid` — compact class distribution display
- `.schedule-list` — styled schedule rows (reuse from index page)

---

## Files Modified/Created

| File | Action |
|------|--------|
| `src/patt/templates/public/roster.html` | NEW |
| `src/patt/pages/public_pages.py` | ADD GET /roster route |
| `src/patt/app.py` | ADD 301 redirects for roster.html, roster-view.html |
| `src/patt/static/css/main.css` | ADD roster page styles |

---

## Verification Checklist

- [ ] `/roster` loads without login
- [ ] Tab switching works (JS, no page reload)
- [ ] Full roster table shows mains by default
- [ ] "Show alts" checkbox loads all characters per player
- [ ] Character names are colored by class
- [ ] Composition tab shows 4 role cards with correct counts
- [ ] Role cards color-coded correctly (green/amber/red)
- [ ] Class distribution grid shows all classes with counts
- [ ] "View Comp on Wowhead" button generates valid URL
- [ ] Wowhead URL contains correct spec codes for current mains
- [ ] Schedule tab shows event days (or fallback message)
- [ ] `/roster.html` redirects → `/roster` (301)
- [ ] `/roster-view.html` redirects → `/roster` (301)
- [ ] Armory links open in new tab
