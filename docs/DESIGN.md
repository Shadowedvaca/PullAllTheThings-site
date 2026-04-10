# Design Language

> Dark fantasy / WoW tavern aesthetic. Used across all PATT web pages.

---

## Colors

| Role | Value | Usage |
|------|-------|-------|
| Background (deep) | `#0a0a0b` | Page background |
| Background (surface) | `#141416` | Body background |
| Card / Panel | `#1a1a1d`, `#1e1e22` | Cards, sidebars |
| Primary Accent | `#d4a84b` | Headers, borders, highlights (gold) |
| Text primary | `#e8e8e8` | Body text |
| Text secondary | `#888` | Labels, meta |
| Border subtle | `#2a2a2e`, `#3a3a3e` | Dividers, card borders |

## Role Colors

| Role | Color | Hex |
|------|-------|-----|
| Tank | Blue | `#60a5fa` |
| Healer | Green | `#4ade80` |
| Melee DPS | Red | `#f87171` |
| Ranged DPS | Amber | `#fbbf24` |

## Status Colors

| State | Color | Hex |
|-------|-------|-----|
| Success | Green | `#4ade80` |
| Warning | Amber | `#fbbf24` |
| Danger | Red | `#f87171` |

## Typography

| Use | Font |
|-----|------|
| Headers / display | Cinzel |
| Body text | Source Sans Pro |
| Code / data | JetBrains Mono |

## CSS Custom Properties

All colors and spacing use CSS custom properties defined in `src/guild_portal/static/css/main.css`.
The accent color (`--color-accent`, `--color-gold`) is overridden at runtime by both base templates
using the `accent_color_hex` value from `sv_common.config_cache` — this is how per-guild branding works.

## Admin Layout Pattern

All admin pages extend `base_admin.html` (NOT `base.html`). It provides:
- Shared `site-header` at top (guild name, Home/Roster/Crafting/Admin links, char badge, rank badge, Log Out)
- App-shell layout: `admin-body` is column flex `height: 100vh; overflow: hidden`; header is fixed; `admin-main` row fills remaining height; sidebar and content each scroll independently
- Collapsible left sidebar (state saved to localStorage); active link set by `current_screen == item.screen_key`
