---
name: Item Popularity vs Editorial Grade — separate columns, no conversion
description: Design decision: popularity % and letter grades are separate independent columns, never converted into each other
type: project
---

Popularity % and editorial letter grades are two separate data sources displayed as independent columns in the item lists. There is NO % → letter grade conversion.

**Popularity %** — aggregated from viz.item_popularity (u.gg now, Archon and others planned). Shows what % of players use each item in this slot. Follows Guide Mode (Overall/Raid/M+) filter.

**Letter grade** — editorial ranking from sites like Wowhead (S/A/B/C/D), stored in enrichment.trinket_ratings. Wowhead has no content-type split so it shows for all Guide Mode selections.

**Why:** The plan doc (gear-plan-1.3-guide-ui-overhaul.md) proposed converting % to grades — this was explicitly rejected. Keep the two signals honest and separate.

**How to apply:** Never combine these. Popularity column always shows %, grade column always shows letter. Both columns exist independently in the trinket slot item lists.
