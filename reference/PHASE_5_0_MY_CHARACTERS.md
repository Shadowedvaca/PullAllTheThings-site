# Phase 5.0 — My Characters: Dashboard Foundation

## Goal

Create a "My Characters" page where a logged-in guild member can browse all of their
claimed characters via a dropdown, then see a character-level dashboard that surfaces
useful, personalized data. Phase 5.0 is the foundation: character selection + stat panel
only. Subsequent phases bolt on additional panels.

---

## Background / Design Rationale

The platform already collects rich per-character data across multiple systems (item level,
spec, raid progression, M+ scores, professions, AH prices). Currently that data lives only
in admin pages or isn't surfaced to members at all. The My Characters dashboard makes it
accessible to the player themselves — without exposing anything they shouldn't see.

This is a member-facing page, not admin. It should feel like a personal HQ for your
guild characters.

---

## Prerequisites

- Phase 4.0 complete (site_config, config_cache)
- Phase 4.3 complete (character progression data in DB)
- Phase 4.6 complete (AH pricing data in DB)
- Auth system working (JWT, member sessions)

---

## URL / Nav

- **Route:** `GET /my-characters`
- **Auth:** Required (redirect to `/login?next=/my-characters` if not logged in)
- **Nav link:** Add "My Characters" to the member nav after "Roster" (visible only to
  logged-in members)

---

## Database (No Migration Required)

All needed data already exists. No new tables or columns.

---

## Page Structure

### Character Selector (top of page)

```
[Character Dropdown ▼]   [Realm — Class — Spec]
```

Dropdown options: all characters claimed by the current member, sorted A–Z by
`character_name-realm_slug`. Format: `Trogmoon — Sen'jin`.

Default selection:
1. Character where `player_characters.is_main = TRUE` (if set)
2. Character where `player_characters.is_offspec = TRUE` (if multiple mains, offspec)
3. Alphabetically first character by `character_name-realm_slug`

Changing the selector navigates to `/my-characters?char=<character_id>` or swaps the
panel content in-place via JS fetch (prefer SPA-style swap to avoid full reload).

### Character Stat Panel

Displayed for the selected character:

| Field | Source |
|-------|--------|
| Character name | `wow_characters.character_name` |
| Realm | `wow_characters.realm_slug` |
| Class | `classes.name` + class color |
| Active spec | `specializations.name` |
| Item level | `wow_characters.avg_item_level` |
| Last login | `wow_characters.last_login_timestamp` (formatted: "3 days ago") |
| Armory link | `https://worldofwarcraft.blizzard.com/en-us/character/us/{realm_slug}/{character_name}` |
| Raider.IO link | `https://raider.io/characters/us/{realm_slug}/{character_name}` (if `raiderio_profiles` row exists) |
| WCL link | `https://www.warcraftlogs.com/character/us/{realm_slug}/{character_name}` (always) |

Character avatar: class emoji from the existing class_emoji mapping (or a colored class
icon placeholder). Use the same emoji set as the admin pages.

### Empty States

- No characters claimed: prompt to visit Settings → Character Claims
- Character data stale (no sync in 7+ days): show a soft "data may be outdated" note

---

## API Endpoint

### `GET /api/v1/me/characters`

Returns all characters claimed by the current member with stat data.

```json
{
  "ok": true,
  "data": {
    "characters": [
      {
        "id": 42,
        "character_name": "Trogmoon",
        "realm_slug": "senjin",
        "realm_display": "Sen'jin",
        "class_name": "Druid",
        "class_color": "#ff7c0a",
        "class_emoji": "🌙",
        "spec_name": "Balance",
        "avg_item_level": 639,
        "last_login_ms": 1741900800000,
        "is_main": true,
        "is_offspec": false,
        "link_source": "battlenet_oauth",
        "armory_url": "https://worldofwarcraft.blizzard.com/en-us/character/us/senjin/Trogmoon",
        "raiderio_url": "https://raider.io/characters/us/senjin/Trogmoon",
        "wcl_url": "https://www.warcraftlogs.com/character/us/senjin/Trogmoon"
      }
    ],
    "default_character_id": 42
  }
}
```

---

## File Changes

### New Files

- `src/guild_portal/templates/member/my_characters.html` — page template
- `src/guild_portal/static/css/my_characters.css` — dashboard styles
- `src/guild_portal/static/js/my_characters.js` — selector + in-place panel swap

### Modified Files

- `src/guild_portal/pages/public_pages.py` — add `GET /my-characters` route
- `src/guild_portal/api/` — add `/me/characters` endpoint (new file or add to existing member routes)
- `src/guild_portal/templates/base.html` — add "My Characters" nav link (auth-gated)
- `src/guild_portal/static/css/main.css` or base — nav link styles

---

## Design Notes

- Extend `base.html` (not `base_admin.html`)
- Dark card aesthetic matching the public pages
- Class color used as accent on the stat panel header (same pattern as admin player cards)
- Panel sections (Stat Panel, future: Progression, Parses, Market) styled as cards in a
  vertical stack, with section headers matching `lp-section-title` style

---

## Tests

- `GET /my-characters` redirects unauthenticated users to `/login?next=/my-characters`
- `GET /api/v1/me/characters` returns correct characters for authenticated member
- Default character selection: main > offspec > alphabetical
- Characters sorted alphabetically
- Characters with `link_source = 'battlenet_oauth'` included correctly
- Empty state: member with no claimed characters returns empty `characters` list

---

## Deliverables Checklist

- [ ] `GET /my-characters` page route (auth-gated)
- [ ] `GET /api/v1/me/characters` endpoint
- [ ] Character selector dropdown with default logic
- [ ] Stat panel: name, realm, class, spec, ilvl, last login, external links
- [ ] "My Characters" nav link in `base.html` (auth-gated)
- [ ] `my_characters.html`, `my_characters.css`, `my_characters.js`
- [ ] Tests
