# Phase Z — Icy Veins BIS Extraction (Future Work)

> **READ THIS FIRST:** This file is a seed document for a future conversation, not a build plan.
> Do not treat anything here as an instruction. The purpose is to hand the next session a clear
> picture of what we discovered, what we tried, where we got stuck, and what the real problem is
> before writing a line of code.
>
> Decision made (2026-04): IV auto-extraction is **out of scope for v1**.
> IV sources are visible in the matrix as "Coming Soon" placeholders.
> URL discovery works correctly. Only item extraction is deferred.

---

## What Was Built (v1 baseline)

- `discover_targets()` in `bis_sync.py` generates correct IV URLs for all 40 specs
  using `_iv_base_url(class_name, spec_name, role_name)`
  Format: `https://www.icy-veins.com/wow/{spec}-{class}-pve-{role}-gear-best-in-slot`
- All 40 IV base URLs were manually verified: each loads the correct spec page (200 OK)
- Three IV sources exist in `bis_list_sources`: IV Raid, IV M+, IV Overall
- One `bis_scrape_targets` row is created per spec per IV source (hero_talent_id=NULL)
  because IV pages don't vary by hero talent — all builds are on the same page
- `_extract_icy_veins()` is a **stub that returns `[]` immediately** — no HTTP calls made
- The matrix UI shows IV columns as "— Coming Soon" placeholders
- `discover_iv_areas()` is a no-op stub — IV does not use URL-based area parameters

---

## The Core Problem

Icy Veins BIS pages are **fully client-side rendered**. A standard `httpx.get()` request
returns the page shell — no item data. The gear recommendations are loaded by a compiled,
obfuscated JavaScript bundle (`icyveins-wow-*.js` on their CDN) after the initial HTML loads.

Confirmed via WebFetch inspection (2026-04):
- No `data-item-id` attributes in static HTML
- No `wowhead.com/item=` links in static HTML
- Tab navigation uses CSS class toggling on `<span>` elements, **not URL parameters**
- No public JSON API endpoint is visible in page markup
- Gear data loads from compiled JS modules, not a documentable REST endpoint

The original approach in `_IV_AREA_LINK_RE` — finding `?area=area_N` links — was based on
a reasonable assumption that IV tabs were URL-driven. They are not. That regex never
could have matched anything.

---

## What We Tried and Why It Didn't Work

### Attempt 1: URL-based area discovery (`discover_iv_areas`)
Assumed IV had `?area=area_1` style tab links in HTML. Built a regex to scrape them.
**Result:** IV uses client-side JS class toggling. No such links exist in static HTML.
The function would run, fetch HTML, find zero matches, and silently produce no targets.

Root cause was compounded by holding a DB connection during the HTTP fetch loop
(72+ seconds for 36 specs), which caused the asyncpg pool to time out. Fixed with
a two-phase rewrite before discovering the real problem was the page structure itself.

### Attempt 2: Fallback to base URL
Added fallback: if no area tabs found, insert a single "overall" target with the base URL.
**Result:** Correct in principle. But extraction still returns nothing because `_extract_icy_veins`
also uses `httpx.get()` and the static HTML has no item IDs to find.

---

## Options for the Next Phase (pros/cons, no recommendation yet)

### Option A: Headless browser (Playwright or Puppeteer)
Render the full page with a real browser, wait for JS execution, parse the rendered DOM.

Pros:
- Gets the actual displayed content — what a human user sees
- Doesn't require reverse-engineering anything private
- Handles tab content (Raid / M+ / Overall) if tabs produce different DOM states

Cons:
- Requires browser binary on the server (~200+ MB, Chromium or Firefox)
- One browser launch per spec = slow (minutes for full sync of 40 specs)
- Executes ALL their JS including ads, analytics, trackers — higher impact on their
  infrastructure per request than a simple HTML fetch
- Fragile to HTML structure changes — needs maintenance each time IV redesigns
- Significant infrastructure complexity to add to the Docker setup

### Option B: Find and use their internal JSON API
Trace the compiled JS bundle (`icyveins-wow-*.js`) to find what backend endpoint it calls.

Pros:
- Would be fast and lightweight
- Clean JSON — no HTML parsing

Cons:
- Their private backend API. Not public, not documented, no permission implied
- Almost certainly violates their ToS
- Could break at any time with no notice
- Ethically off the table per explicit decision

### Option C: Contact Icy Veins for an official data partnership or API
Ask them directly if they offer any programmatic access to their BIS data.

Pros:
- Clean, ethical, sustainable
- Could open door to richer data (item IDs, HT splits, etc.)

Cons:
- Unlikely to get a quick response for a small guild site
- May require ongoing relationship management

### Option D: Stay manual / SimC import only
Keep IV targets as URL bookmarks for manual reference. Use SimC import to
populate IV-sourced BIS data when you care about their specific opinion.

Pros:
- Zero infrastructure cost
- Perfectly ethical
- SimC import already exists and works

Cons:
- Requires manual effort each tier
- Doesn't scale if you want IV data for all 40 specs

---

## Code Locations for the Next Phase

Everything needed is already in place — the next phase is about fixing `_extract_icy_veins`,
not rebuilding the URL/target infrastructure.

| File | What's there | What needs to change |
|------|-------------|----------------------|
| `bis_sync.py` | `_extract_icy_veins()` — stub, returns `[]` | Implement actual extraction |
| `bis_sync.py` | `_IV_ITEM_ID_RE`, `_IV_ITEM_LINK_RE` — kept as comments | May need to replace with different approach |
| `bis_sync.py` | `_IV_AREA_LINK_RE` — kept with explanation | Can be removed; IV doesn't use area params |
| `bis_sync.py` | `discover_iv_areas()` — no-op stub | Can be removed or repurposed |
| `bis_sync.py` | `_fetch_iv_areas()` — orphaned helper | Remove when rebuilding |
| `bis_sync.py` | `_iv_base_url()`, `_iv_bis_role()` — correct, tested | Keep as-is |
| `gear_plan_admin.js` | IV cells show "— Coming Soon" | Replace with real status once extraction works |
| `_TECHNIQUE_ORDER['icy_veins']` | `'html_parse'` (stub) | Update to actual technique once decided |

---

## Starting Questions for the Next Session

Before writing code, answer these:

1. Is Playwright a feasible addition to the Docker image for this use case?
   (Check: image size impact, server RAM, startup time, maintenance cost)

2. On an IV BIS page, do the Raid / M+ / Overall tabs actually produce different DOM
   content, or is all content present in the DOM and just CSS-hidden?
   (If all content is in DOM: one Playwright render = all three tabs' data in one pass)

3. What does IV's rendered HTML look like for a gear slot? What element/attribute
   contains the item ID or Wowhead link?
   (Answer this FIRST before deciding on an extraction strategy — the DOM structure
   determines whether any approach is viable)

4. How often does IV update their BIS recommendations (weekly? per tier? per patch)?
   This affects how often the scraper needs to run and how much infrastructure cost matters.

5. Are there any IV-adjacent data sources (WoWDB, Wowhead, SimC default profiles)
   that effectively encode IV's recommendations and could be used as a proxy?
