# Phase G — Spec Guide Links

## Goal

Add a **Spec Guide Links** panel to the My Characters page that renders one badge button
per row in a new `common.guide_sites` configuration table. The panel shows a spec dropdown
(defaulting to the character's current spec) and a badge button for every enabled guide
site. Everything — URL patterns, role slug words, badge colors, enabled state, display
order — is configurable from the Reference Data admin page. Adding or removing a guide
site is a data change, not a code change.

---

## Background / Design Rationale

Three authoritative WoW guide sites exist today. Their URLs are derived from class name,
spec name, and role. No public APIs exist; URLs are stable and pattern-based.

| Site | URL template |
|------|-------------|
| **Wowhead** | `https://www.wowhead.com/guide/classes/{class}/{spec}/overview-pve-{role}` |
| **Icy Veins** | `https://www.icy-veins.com/wow/{spec}-{class}-pve-{role}-guide` |
| **u.gg** | `https://u.gg/wow/{spec}/{class}/talents` |

The critical difference that drove the config approach: Wowhead uses `healer` in its URL;
Icy Veins uses `healing`. Rather than hardcode that divergence in Python, store it in
`role_healer_slug` per site row. If any site changes its URL structure, it's an admin
data fix — not a deployment.

The UI is fully data-driven: the badge count, labels, and colors all come from the DB.
Disabling a row hides that badge everywhere. Adding a fourth site (e.g., Bloodmallet for
tanks) is an INSERT, not a feature request.

Badge colors are brand-adjacent styled text — no third-party logos, no copyright exposure.
Every badge opens the external site in a new tab. The platform drives traffic *to* these
communities.

---

## Prerequisites

- Phase 5.0 complete (My Characters page + stat panel)
- `guild_identity.specializations` seeded with all specs + `default_role_id` FKs
- Reference Data admin page exists (`/admin/reference-tables`)

---

## Database — Migration 0051

### New Table: `common.guide_sites`

```sql
CREATE TABLE common.guide_sites (
    id                 SERIAL      PRIMARY KEY,
    name               VARCHAR(50) NOT NULL,
    badge_label        VARCHAR(50) NOT NULL,
    url_template       TEXT        NOT NULL,
    role_dps_slug      VARCHAR(20) NOT NULL DEFAULT 'dps',
    role_tank_slug     VARCHAR(20) NOT NULL DEFAULT 'tank',
    role_healer_slug   VARCHAR(20) NOT NULL DEFAULT 'healer',
    badge_bg_color     CHAR(7)     NOT NULL DEFAULT '#333333',
    badge_text_color   CHAR(7)     NOT NULL DEFAULT '#ffffff',
    badge_border_color CHAR(7)     NOT NULL DEFAULT '#555555',
    enabled            BOOLEAN     NOT NULL DEFAULT TRUE,
    sort_order         INTEGER     NOT NULL DEFAULT 0
);
```

### Seed Data (3 rows)

| id | name | badge_label | url_template | role_healer_slug | badge_bg | badge_text | badge_border | sort |
|----|------|-------------|--------------|-----------------|----------|------------|--------------|------|
| 1 | Wowhead | Wowhead | `https://www.wowhead.com/guide/classes/{class}/{spec}/overview-pve-{role}` | healer | #8b1a1a | #ffd280 | #cc4444 | 1 |
| 2 | Icy Veins | Icy Veins | `https://www.icy-veins.com/wow/{spec}-{class}-pve-{role}-guide` | healing | #0d3a5c | #7ed4f7 | #2a7aaa | 2 |
| 3 | u.gg | u.gg | `https://u.gg/wow/{spec}/{class}/talents` | healer | #3d2000 | #f59c3c | #a05a00 | 3 |

Note: u.gg's template has no `{role}` placeholder so the role slug columns are irrelevant
for that row — they're stored for consistency and ignored at render time.

### New ORM model: `GuideSite` in `sv_common.db.models`

```python
class GuideSite(Base):
    """External guide site config row — drives badge rendering on My Characters."""

    __tablename__ = "guide_sites"
    __table_args__ = {"schema": "common"}

    id                 : Mapped[int]          = mapped_column(Integer, primary_key=True)
    name               : Mapped[str]          = mapped_column(String(50), nullable=False)
    badge_label        : Mapped[str]          = mapped_column(String(50), nullable=False)
    url_template       : Mapped[str]          = mapped_column(Text, nullable=False)
    role_dps_slug      : Mapped[str]          = mapped_column(String(20), nullable=False)
    role_tank_slug     : Mapped[str]          = mapped_column(String(20), nullable=False)
    role_healer_slug   : Mapped[str]          = mapped_column(String(20), nullable=False)
    badge_bg_color     : Mapped[str]          = mapped_column(String(7), nullable=False)
    badge_text_color   : Mapped[str]          = mapped_column(String(7), nullable=False)
    badge_border_color : Mapped[Optional[str]] = mapped_column(String(7))
    enabled            : Mapped[bool]         = mapped_column(Boolean, nullable=False)
    sort_order         : Mapped[int]          = mapped_column(Integer, nullable=False)
```

---

## Sub-phases

### G.1 — DB Migration + ORM + Pure URL Builder

**Deliverables:**
- Alembic migration 0051 (`common.guide_sites` + seed)
- `GuideSite` ORM model in `sv_common.db.models`
- `src/sv_common/guide_links.py` — pure URL builder, zero DB access

```python
"""Pure URL builder for external WoW guide sites.

All functions are stateless and take only plain values — no DB, no async.
The service layer (guild_portal.services.guide_links_service) handles loading
site config and calling these functions.
"""


def _slug(name: str) -> str:
    """Convert a display name to a lowercase hyphenated URL slug."""
    return name.lower().replace(" ", "-")


def _resolve_role_slug(
    role_name: str,
    dps_slug: str,
    tank_slug: str,
    healer_slug: str,
) -> str:
    """Pick the correct site-specific role slug from the DB role name."""
    r = role_name.lower()
    if "tank" in r:
        return tank_slug
    if "heal" in r:
        return healer_slug
    return dps_slug  # covers Melee DPS, Ranged DPS, Support


def build_link_for_site(
    url_template: str,
    class_name: str,
    spec_name: str,
    role_name: str,
    role_dps_slug: str,
    role_tank_slug: str,
    role_healer_slug: str,
) -> str:
    """Return the URL for one guide site given spec and role metadata.

    Template placeholders: {class}, {spec}, {role}. Sites without {role}
    (e.g. u.gg) are unaffected — str.replace on a missing placeholder is a no-op.
    """
    cls  = _slug(class_name)
    spec = _slug(spec_name)
    role = _resolve_role_slug(role_name, role_dps_slug, role_tank_slug, role_healer_slug)
    return (
        url_template
        .replace("{class}", cls)
        .replace("{spec}", spec)
        .replace("{role}", role)
    )
```

**Tests:** `tests/unit/test_guide_links.py`

```
test_balance_druid_wowhead          → correct Wowhead URL with "dps"
test_balance_druid_icyveins         → correct Icy Veins URL with "dps"
test_balance_druid_ugg              → correct u.gg URL (no role placeholder)
test_holy_paladin_wowhead_healer    → "healer" in URL (Wowhead row)
test_holy_paladin_icyveins_healing  → "healing" in URL (Icy Veins row)
test_blood_dk_tank                  → "tank" slug on both sites
test_augmentation_evoker_support    → "Support" role → falls through to dps_slug
test_death_knight_class_slug        → "death-knight" in URL
test_demon_hunter_class_slug        → "demon-hunter" in URL
test_ugg_no_role_placeholder        → role slug absent from u.gg URL
```

---

### G.2 — Guide Links Service

**New file:** `src/guild_portal/services/guide_links_service.py`

Loads `GuideSite` rows via SQLAlchemy session, caches in process (5-min TTL),
builds per-spec link lists, and exposes a cache invalidator for the admin save path.

```python
"""Service layer for guide site config.

Caches the guide_sites rows for 5 minutes. On admin save, call invalidate_cache()
so the next request picks up the new config.
"""

import time
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sv_common.db.models import GuideSite
from sv_common.guide_links import build_link_for_site

_TTL = 300.0  # seconds
_cache: list[dict] | None = None
_cache_at: float = 0.0


def invalidate_cache() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


async def get_enabled_sites(db: AsyncSession) -> list[dict]:
    """Return enabled guide site configs, ordered by sort_order. Cached for 5 min."""
    global _cache, _cache_at
    if _cache is not None and (time.monotonic() - _cache_at) < _TTL:
        return _cache
    result = await db.execute(
        select(GuideSite)
        .where(GuideSite.enabled == True)
        .order_by(GuideSite.sort_order, GuideSite.id)
    )
    rows = result.scalars().all()
    _cache = [
        {
            "id":                 s.id,
            "badge_label":        s.badge_label,
            "url_template":       s.url_template,
            "role_dps_slug":      s.role_dps_slug,
            "role_tank_slug":     s.role_tank_slug,
            "role_healer_slug":   s.role_healer_slug,
            "badge_bg_color":     s.badge_bg_color,
            "badge_text_color":   s.badge_text_color,
            "badge_border_color": s.badge_border_color or s.badge_bg_color,
        }
        for s in rows
    ]
    _cache_at = time.monotonic()
    return _cache


def build_links_for_spec(
    sites: list[dict],
    class_name: str,
    spec_name: str,
    role_name: str,
) -> list[dict]:
    """Build a badge-ready link list for one spec across all enabled sites.

    Returns a list ordered by site sort_order. Each entry contains everything
    the JS needs to render a badge: label, colors, and resolved URL.
    """
    return [
        {
            "site_id":            s["id"],
            "badge_label":        s["badge_label"],
            "badge_bg_color":     s["badge_bg_color"],
            "badge_text_color":   s["badge_text_color"],
            "badge_border_color": s["badge_border_color"],
            "url": build_link_for_site(
                url_template     = s["url_template"],
                class_name       = class_name,
                spec_name        = spec_name,
                role_name        = role_name,
                role_dps_slug    = s["role_dps_slug"],
                role_tank_slug   = s["role_tank_slug"],
                role_healer_slug = s["role_healer_slug"],
            ),
        }
        for s in sites
    ]
```

---

### G.3 — Character API Enhancement

**Modified:** `src/guild_portal/api/member_routes.py`

`GET /api/v1/me/characters` response gains two new fields per character:

```json
{
  "guide_links": [
    {
      "site_id": 1,
      "badge_label": "Wowhead",
      "badge_bg_color": "#8b1a1a",
      "badge_text_color": "#ffd280",
      "badge_border_color": "#cc4444",
      "url": "https://www.wowhead.com/guide/classes/druid/balance/overview-pve-dps"
    },
    {
      "site_id": 2,
      "badge_label": "Icy Veins",
      "badge_bg_color": "#0d3a5c",
      "badge_text_color": "#7ed4f7",
      "badge_border_color": "#2a7aaa",
      "url": "https://www.icy-veins.com/wow/balance-druid-pve-dps-guide"
    },
    {
      "site_id": 3,
      "badge_label": "u.gg",
      "badge_bg_color": "#3d2000",
      "badge_text_color": "#f59c3c",
      "badge_border_color": "#a05a00",
      "url": "https://u.gg/wow/balance/druid/talents"
    }
  ],
  "class_specs": [
    {
      "name": "Balance",
      "role": "Ranged DPS",
      "guide_links": [ ... same structure as above ... ]
    },
    {
      "name": "Feral",
      "role": "Melee DPS",
      "guide_links": [ ... ]
    },
    {
      "name": "Guardian",
      "role": "Tank",
      "guide_links": [ ... ]
    },
    {
      "name": "Restoration",
      "role": "Healer",
      "guide_links": [ ... ]
    }
  ]
}
```

- `guide_links` — links for the character's **current spec** (`active_spec`). Used to
  pre-populate the badges on first render.
- `class_specs` — all specs for the character's class, sorted alphabetically. Powers the
  dropdown; each entry carries its own `guide_links` list so the JS never needs to
  reconstruct URLs client-side.
- If `active_spec` or `wow_class` is `None`, both fields are `null`.
- If `guide_sites` table is empty or all rows disabled, both fields are `[]`.

**Query additions required:**

```python
# Already loaded: wow_class, active_spec
# Add for class_specs:
selectinload(WowCharacter.wow_class)
    .selectinload(WowClass.specializations)
    .selectinload(Specialization.default_role)
```

The `guide_sites` are loaded once per request via `get_enabled_sites(db)` and passed into
`_build_char_dict` (or resolved before the loop and threaded in). The cache means only the
first request after a cold start (or post-save invalidation) hits the DB.

**Tests:**

```
test_characters_guide_links_present          → 3 items in guide_links for seeded sites
test_characters_class_specs_present          → 4 items for Druid, sorted alphabetically
test_characters_guide_links_correct_urls     → URLs match expected patterns
test_characters_no_active_spec_returns_null  → guide_links: null, class_specs: null
test_characters_disabled_site_excluded       → disabled row absent from guide_links
```

---

### G.4 — Reference Data Admin UI

**Modified files:**
- `src/guild_portal/pages/admin_pages.py` — load guide sites; add PATCH endpoint
- `src/guild_portal/templates/admin/reference_tables.html` — new Guide Sites section

#### Admin page route change

Add to `admin_reference_tables`:

```python
from sv_common.db.models import GuideSite

guide_sites_result = await db.execute(
    select(GuideSite).order_by(GuideSite.sort_order, GuideSite.id)
)
guide_sites = list(guide_sites_result.scalars().all())
ctx["guide_sites"] = guide_sites
```

#### New PATCH endpoint

```
PATCH /api/v1/admin/guide-sites/{site_id}
```

Accepts any subset of editable fields (all optional in patch body):
`badge_label`, `url_template`, `role_dps_slug`, `role_tank_slug`, `role_healer_slug`,
`badge_bg_color`, `badge_text_color`, `badge_border_color`, `enabled`, `sort_order`.

On success: calls `guide_links_service.invalidate_cache()` so the next member request
picks up the updated config. Returns `{"ok": true, "data": {updated row}}`.

Auth: Officer+ (same as other reference table endpoints).

#### Template section

New `rt-section` block in `reference_tables.html` titled **"Guide Sites"**.
Table columns:

| Col | Input type | Width |
|-----|-----------|-------|
| Sort | `number` `.rt-editable--number` | narrow |
| Name (display) | read-only `<td>` | |
| Badge label | `text` | |
| URL template | `text` (wider) | max 500px |
| DPS slug | `text` small | |
| Tank slug | `text` small | |
| Healer slug | `text` small | |
| Bg color | `text` (hex) + color swatch | |
| Text color | `text` (hex) + color swatch | |
| Border color | `text` (hex) + color swatch | |
| Enabled | `<select>` true/false | |
| Preview | live badge preview | |
| Save | `.rt-save-btn` | |

**Live preview:** A small `<span>` styled inline with the row's current bg/text/border
colors, showing the badge label. Updates immediately on any field input change in that
row (via a small per-row `oninput` handler) so the admin sees the badge before saving.

The `data-type="guide-sites"` attribute on each row lets the existing `saveRow()` JS
function route `PATCH /api/v1/admin/guide-sites/{id}` automatically — no new JS needed.

After a successful save, call the existing `showToast('Saved!')` path. The server-side
cache is invalidated; next member request loads fresh config.

---

### G.5 — My Characters: Spec Guide Links Panel

#### HTML — `src/guild_portal/templates/member/my_characters.html`

Insert **before** `#mc-progression`:

```html
{# Spec Guide Links panel — populated by JS, no fetch required #}
<div id="mc-guides" class="mc-guides" hidden></div>
```

#### JS — `src/guild_portal/static/js/my_characters.js`

Add `renderGuidesPanel(char)`. Call it in `selectCharacter` right after `renderPanel(char)`
before the first `await fetch` call — it is synchronous, no loading state needed.

```
selectCharacter(charId):
  renderSelectorMeta(char)
  renderPanel(char)           ← stat card (existing)
  renderGuidesPanel(char)     ← NEW — synchronous, from char data
  showStaleNotice(char)
  [progression fetch …]
  [parses fetch …]
  [market fetch …]
  [crafting fetch …]
```

`renderGuidesPanel` logic:

1. If `char.class_specs` is null or empty → hide `#mc-guides` and return.
2. Build one `.mc-prog-card` so `makeCardsCollapsible` works.
3. Spec `<select>`: one `<option>` per entry in `class_specs`, sorted by the API order
   (already alphabetical). Default selection = option whose `name === char.spec_name`,
   fall back to index 0.
4. Badge container: iterate `spec.guide_links` and emit one `<a>` per item, styled with
   inline `background-color`, `color`, `border-color` from the site config.
5. Store `char.class_specs` in a module-level map keyed by `charId` so the dropdown
   `change` handler can look up links without re-fetching.
6. Dropdown `onchange`: find the selected spec in the stored `class_specs`, replace the
   badge container's inner HTML with newly rendered badges.
7. `makeCardsCollapsible(panel, 'mc-guides')` after HTML injection.
8. `panel.hidden = false`.

Panel HTML output example:

```html
<div class="mc-prog-card">
  <div class="mc-prog-card__title">Spec Guide Links</div>
  <div class="mc-prog-card__body">
    <div class="mc-guides-controls">
      <label class="mc-guides-label" for="mc-guides-spec">Spec</label>
      <select id="mc-guides-spec" class="mc-guides-select">
        <option value="Balance">Balance</option>
        <option value="Feral">Feral</option>
        <option value="Guardian">Guardian</option>
        <option value="Restoration">Restoration</option>
      </select>
    </div>
    <div id="mc-guides-badges" class="mc-guides-badges">
      <a href="https://www.wowhead.com/…" target="_blank" rel="noopener noreferrer"
         class="mc-guide-badge"
         style="background:#8b1a1a;color:#ffd280;border-color:#cc4444">
        Wowhead
      </a>
      <a href="https://www.icy-veins.com/…" target="_blank" rel="noopener noreferrer"
         class="mc-guide-badge"
         style="background:#0d3a5c;color:#7ed4f7;border-color:#2a7aaa">
        Icy Veins
      </a>
      <a href="https://u.gg/…" target="_blank" rel="noopener noreferrer"
         class="mc-guide-badge"
         style="background:#3d2000;color:#f59c3c;border-color:#a05a00">
        u.gg
      </a>
    </div>
  </div>
</div>
```

Badge colors come from inline `style` attributes — no hardcoded CSS classes per site.
This is what makes the table-driven approach work: add a row, configure colors in admin,
badges appear automatically.

#### CSS — `src/guild_portal/static/css/my_characters.css`

```css
/* Spec Guide Links panel */
.mc-guides-controls {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-md);
}

.mc-guides-label {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.mc-guides-select {
  background: var(--color-bg-dark);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  border-radius: 4px;
  padding: 0.25rem 0.5rem;
  font-size: 0.9rem;
}

.mc-guides-badges {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.mc-guide-badge {
  display: inline-block;
  padding: 0.45em 1.1em;
  border-radius: 6px;
  font-size: 0.9rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-decoration: none;
  border: 1px solid transparent;
  transition: filter 0.15s ease, transform 0.1s ease;
}
.mc-guide-badge:hover {
  filter: brightness(1.2);
  transform: translateY(-1px);
}
.mc-guide-badge:active {
  transform: translateY(0);
}
```

---

## File Changes

### New Files

| File | Purpose |
|------|---------|
| `src/sv_common/guide_links.py` | Pure URL builder — no DB, fully unit-testable |
| `src/guild_portal/services/guide_links_service.py` | DB loader + 5-min cache + `build_links_for_spec` |
| `alembic/versions/0051_guide_sites.py` | Migration: create + seed `common.guide_sites` |
| `tests/unit/test_guide_links.py` | Unit tests for pure builder (10 tests) |

### Modified Files

| File | Change |
|------|--------|
| `src/sv_common/db/models.py` | Add `GuideSite` ORM model |
| `src/guild_portal/api/member_routes.py` | Add eager loads; `_build_char_dict` includes `guide_links` + `class_specs` |
| `src/guild_portal/pages/admin_pages.py` | Load guide sites in reference tables route; add `PATCH /guide-sites/{id}` |
| `src/guild_portal/templates/admin/reference_tables.html` | New Guide Sites section with live preview |
| `src/guild_portal/templates/member/my_characters.html` | Add `#mc-guides` div before `#mc-progression` |
| `src/guild_portal/static/js/my_characters.js` | Add `renderGuidesPanel`, call in `selectCharacter`, dropdown handler |
| `src/guild_portal/static/css/my_characters.css` | Badge + controls styles |

---

## Design Notes

- **No hardcoded site names anywhere in the application code.** The badge label, URL
  pattern, role slugs, and colors are all data. `sv_common/guide_links.py` knows nothing
  about "Wowhead" — it only knows about `{class}`, `{spec}`, `{role}` placeholders.
- **Badge colors via inline `style`**, not CSS classes. This is what makes adding a new
  site require zero front-end code.
- **5-min service cache** protects DB from hammering on a busy characters endpoint.
  Admin save explicitly invalidates it so changes are visible within seconds.
- **`Specialization.wowhead_slug`** field already exists in the schema. Phase G ignores
  it — we derive the spec URL segment from `spec_name`. A later phase could populate that
  column for any edge-case spec names and the `build_link_for_site` function can be
  extended to prefer it.
- The panel is **synchronous** — no spinner, no empty state needed. Data arrives with the
  initial characters fetch. If no guide sites are enabled, the panel simply stays hidden.
- Collapse state saved to `localStorage` via the existing `makeCardsCollapsible` helper,
  key prefix `'mc-guides'`.

---

## Tests

### Unit (`tests/unit/test_guide_links.py`)

10 tests covering the pure builder: all three site patterns, healer vs healing, tank,
support/dps fallthrough, multi-word class/spec slugs, template with no `{role}`.

### Route / Integration

```
test_characters_guide_links_list_length_matches_enabled_sites
test_characters_guide_links_correct_urls_for_known_spec
test_characters_class_specs_all_druid_specs_present
test_characters_class_specs_sorted_alphabetically
test_characters_no_active_spec_guide_links_null
test_characters_disabled_site_not_in_guide_links
test_patch_guide_site_updates_row_and_invalidates_cache
test_patch_guide_site_requires_officer_rank
```

---

## Deliverables Checklist

### G.1 — DB + Pure Builder
- [ ] Migration 0051: `common.guide_sites` table + 3 seed rows
- [ ] `GuideSite` ORM model in `sv_common.db.models`
- [ ] `src/sv_common/guide_links.py` with `build_link_for_site` + helpers
- [ ] `tests/unit/test_guide_links.py` — all 10 tests pass

### G.2 — Service Layer
- [ ] `src/guild_portal/services/guide_links_service.py`
- [ ] `get_enabled_sites(db)` with 5-min cache
- [ ] `build_links_for_spec(sites, class_name, spec_name, role_name)` returns list
- [ ] `invalidate_cache()` function

### G.3 — Character API
- [ ] Eager load `wow_class.specializations.default_role` in characters query
- [ ] `_build_char_dict` includes `guide_links` (list) and `class_specs` (list)
- [ ] Both fields `null` when `active_spec` or `wow_class` is None
- [ ] Route tests pass

### G.4 — Reference Data Admin
- [ ] `admin_reference_tables` route loads `guide_sites`
- [ ] `PATCH /api/v1/admin/guide-sites/{id}` endpoint (Officer+)
- [ ] Endpoint calls `invalidate_cache()` on success
- [ ] Guide Sites section in `reference_tables.html`
- [ ] Live badge preview updates on field input
- [ ] Existing `saveRow()` JS handles `data-type="guide-sites"` with no changes
- [ ] Admin route tests pass

### G.5 — My Characters Panel
- [ ] `#mc-guides` div in template before `#mc-progression`
- [ ] `renderGuidesPanel(char)` function in `my_characters.js`
- [ ] `selectCharacter` calls `renderGuidesPanel` synchronously
- [ ] Spec dropdown defaults to `char.spec_name`
- [ ] Dropdown `change` re-renders badge container from stored `class_specs`
- [ ] `makeCardsCollapsible(panel, 'mc-guides')` called after render
- [ ] Panel hidden when `class_specs` is null or empty
- [ ] Badges use inline `style` colors from site config
- [ ] All badges `target="_blank" rel="noopener noreferrer"`
- [ ] CSS styles added to `my_characters.css`
- [ ] All tests pass

---

## What This Phase Does NOT Do

- No hero spec (`?hero=`) query param on u.gg links — base URL is sufficient
- No sub-page links on Wowhead (Talents, BiS, Rotation) — overview link only for now
- Does not add CREATE/DELETE for guide sites in the admin UI — rows are seeded by
  migration; an admin can disable rows but the initial three are managed via migration
- Does not populate `Specialization.wowhead_slug`
- Does not touch the `/guide` page, public pages, or bot
