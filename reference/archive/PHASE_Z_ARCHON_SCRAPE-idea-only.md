# IDEA ONLY — Archon.gg as a BIS Source

> **Status:** Idea only. Not planned for any current phase.
> Do not implement without a dedicated planning session.

---

## What This Is

[archon.gg](https://archon.gg) is a WoW BIS analysis site distinct from u.gg.

| | u.gg (current) | archon.gg (this idea) |
|---|---|---|
| Data source | Player usage / popularity | Simulation-based |
| Methodology | "What are players running" | "What does the math say is best" |
| Hero talent split | Aggregated across builds | Per-hero-talent recommendations |
| Update cadence | Near-real-time from player data | Manual sim updates |

Archon would be a valuable second opinion alongside u.gg and Wowhead — it represents the theorycrafting community's recommendations.

---

## Technical Considerations

### Page Rendering
archon.gg pages are likely fully JS-rendered (similar to Icy Veins).
A plain httpx fetch will not return item data — headless browser or
internal API discovery would be required.

This is the same blocker as Icy Veins extraction (see `PHASE_Z_ICY_VEINS_SCRAPE-idea-only.md`).

### Possible Approaches
1. **Find their internal API** — inspect network requests on an archon.gg BIS page;
   they may have a JSON endpoint that serves the same data the page renders.
2. **Headless browser** (Playwright/Puppeteer) — spin up a browser, wait for JS to load,
   extract the rendered DOM. High infra cost, fragile to page changes.
3. **Manual import via SimC** — if archon.gg publishes SimC profiles, use the existing
   SimC import endpoint. No scraping needed.

### Source Setup
If implemented, would add three new `bis_list_sources` rows:
- `Archon Raid` (origin='archon_gg', content_type='raid')
- `Archon M+` (origin='archon_gg', content_type='mythic_plus')
- `Archon Overall` (origin='archon_gg', content_type='overall')

Note: origin must differ from 'archon' (which is now the u.gg extractor identifier).

---

## My Characters Link

When this feature is built, add an archon.gg link to the My Characters
spec guide links panel (alongside Wowhead and Icy Veins), similar to how
u.gg gear links are surfaced (see below).

---

## u.gg Gear Link on My Characters (Related)

`common.guide_sites` already has a u.gg entry (id=3) pointing to the
**talents** page (`/wow/{spec}/{class}/talents`).

To link the **gear** page from My Characters:
- Either update the existing entry to point to the gear URL, or
- Add a second guide_sites entry specifically for gear.

The gear URL format is: `https://u.gg/wow/{spec}/{class}/gear`
where spec and class use underscore separators (e.g. `death_knight`).

This is a small migration + guide_links.py update, independent of
the archon.gg scraping work.
