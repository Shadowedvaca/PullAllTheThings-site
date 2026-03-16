# Phase 5.2 — My Characters: WCL Parse Panel

## Goal

Add a Parses panel to the My Characters dashboard showing Warcraft Logs performance
percentiles for the selected character. Uses parse data already collected by the WCL
sync (Phase 4.5). Gives members a personal view of their logs performance without
navigating away to WCL.

---

## Prerequisites

- Phase 5.0 complete (My Characters page)
- Phase 4.5 complete (`character_parses` in DB, WCL sync running)
- WCL API credentials configured

---

## Panel: WCL Performance

Title: **Warcraft Logs — Recent Parses**

Display the character's parses for the current raid tier, grouped by boss, showing
the highest percentile parse per boss per difficulty.

### Layout

```
[Difficulty Tab: Heroic | Normal | Mythic]

Boss Name                  Parse %    Rank    Best Parse
────────────────────────────────────────────────────────
Ulgrax the Devourer        94         94th    ███████████░ 94%
The Bloodbound Horror      87         87th    ████████░░░░ 87%
Sikran, Captain of…        72         72nd    ███████░░░░░ 72%
...
```

Parse color tiers (WCL standard):
- 0–24: gray
- 25–49: green
- 50–74: blue
- 75–94: purple
- 95–98: orange (epic)
- 99: gold (legendary)
- 100: pink (artifact — rare)

Each row is a link to the full WCL report if `report_id` is available in `character_parses`.

### Aggregate Summary

Above the table, show:
- **Best parse this tier:** `97th percentile (Mythic, Boss Name)`
- **Average (all bosses, Heroic):** `82nd percentile`

### No Data State

If no parse rows exist for this character + current tier:
```
No Warcraft Logs data found for this character.
[View on Warcraft Logs ↗]
```
Link goes to `https://www.warcraftlogs.com/character/us/{realm_slug}/{character_name}`.

---

## API Changes

### `GET /api/v1/me/character/{character_id}/parses`

```json
{
  "ok": true,
  "data": {
    "character_id": 42,
    "tier_name": "Nerub-ar Palace",
    "parses": [
      {
        "boss_name": "Ulgrax the Devourer",
        "difficulty": "heroic",
        "percentile": 94,
        "rank_world": null,
        "report_id": "abc123",
        "recorded_at": "2026-03-14T22:00:00Z"
      }
    ],
    "summary": {
      "best_percentile": 97,
      "best_boss": "The Bloodbound Horror",
      "best_difficulty": "mythic",
      "heroic_average": 82
    }
  }
}
```

Authorization: member may only fetch their own characters.

---

## Data Notes

- `character_parses` may have multiple rows per boss (one per sync run) — use the
  most recent `recorded_at` row per `(boss_name, difficulty)` combination.
- If `raid_reports` table has the associated `report_id`, include it for linking.
- WCL sync currently stores global rank — surface if available, don't break if null.

---

## File Changes

### Modified Files

- `src/guild_portal/templates/member/my_characters.html` — add parses panel section
- `src/guild_portal/static/css/my_characters.css` — parse tier colors, progress bar, tab styles
- `src/guild_portal/static/js/my_characters.js` — fetch + render parses panel, tab switching
- `src/guild_portal/api/member_routes.py` — add parses endpoint

---

## Design Notes

- Parse progress bars: thin, colored left-to-right fill matching WCL percentile tiers
- Difficulty tabs: Normal / Heroic / Mythic, only show tabs for difficulties with data
- Table rows are muted for gray-tier parses; gold glow for 99+
- Link to WCL always present (external link icon), even when data exists
- Panel should degrade gracefully if WCL is not configured — show "WCL not configured"
  banner with link to `/admin/warcraft-logs` (for officers) or no banner for regular members

---

## Tests

- `GET /api/v1/me/character/{id}/parses` requires auth + own-character check
- Correct most-recent parse per `(boss_name, difficulty)` returned
- Percentile color tier function correct at boundaries (0, 25, 50, 75, 95, 99, 100)
- Summary fields (best, average) calculated correctly
- No data → `parses: []`, `summary: null`
- Multiple rows for same boss → only most recent returned

---

## Deliverables Checklist

- [ ] `GET /api/v1/me/character/{id}/parses` endpoint
- [ ] Own-character authorization check
- [ ] Parse table in template: boss, percentile, color tier, progress bar
- [ ] Difficulty tab switching (JS)
- [ ] Summary bar (best parse, heroic average)
- [ ] WCL link always present
- [ ] No data / WCL not configured states
- [ ] Tests
