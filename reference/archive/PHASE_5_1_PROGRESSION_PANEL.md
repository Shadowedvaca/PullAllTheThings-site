# Phase 5.1 — My Characters: Progression Panel

## Goal

Add a Progression panel to the My Characters dashboard showing the selected character's
raid progress and Mythic+ score for the current tier/season. Builds on the progression
data already collected by Phase 4.3.

---

## Prerequisites

- Phase 5.0 complete (My Characters page + stat panel)
- Phase 4.3 complete (`character_raid_progress`, `character_mythic_plus` in DB)
- Progression sync running (data present in DB)

---

## Panel: Raid Progression

Displayed below the stat panel, title: **Raid Progression**.

For each raid tier tracked in the current season (query `raid_seasons` for current):

```
[Raid Name]
  Normal      ✅ 8/8  (or 5/8, or 0/8 — or — if no data)
  Heroic      ✅ 8/8
  Mythic      🔥 2/8
```

Data source: `character_raid_progress` — `bosses_killed` / `total_bosses` per `difficulty`.

Display order: Mythic first (most prestigious), then Heroic, then Normal.

If a difficulty has `bosses_killed = 0`, show `0/{total}` in muted text.
If no row exists for that difficulty, show `—`.

Color coding:
- Full clear: accent gold `✅`
- Partial (>0): normal text progress bar or fraction
- Zero kills: muted
- Mythic kills: red-orange highlight (matches `var(--color-dps)` melee red)

### Mythic+ Score Panel

Below the raid section, or as a sub-card:

```
Mythic+ Score
  [Season Name]   [Score]   [Color-coded by Blizzard score tier]
  Best key: +24 (Ara-Kara)
```

Data source: `character_mythic_plus` — `overall_score`, `best_run_level`, `best_run_dungeon`.

If score is 0 or no row: show "No keys this season" in muted text.

Score color tiers (approximate Raider.IO colors):
- 0–499: muted gray
- 500–999: green
- 1000–1499: blue
- 1500–1999: purple
- 2000–2499: orange
- 2500+: pink/gold

---

## API Changes

### Extend `GET /api/v1/me/characters` or add:

### `GET /api/v1/me/character/{character_id}/progression`

```json
{
  "ok": true,
  "data": {
    "character_id": 42,
    "raid_progress": [
      {
        "raid_name": "Nerub-ar Palace",
        "difficulties": {
          "normal":  {"killed": 8, "total": 8},
          "heroic":  {"killed": 8, "total": 8},
          "mythic":  {"killed": 2, "total": 8}
        }
      }
    ],
    "mythic_plus": {
      "season_name": "Season 2",
      "overall_score": 2341,
      "best_run_level": 24,
      "best_run_dungeon": "Ara-Kara, City of Echoes"
    }
  }
}
```

Authorization: member may only fetch their own characters (by `player_id`).

---

## File Changes

### Modified Files

- `src/guild_portal/templates/member/my_characters.html` — add progression panel section
- `src/guild_portal/static/css/my_characters.css` — raid progress styles, M+ score styles
- `src/guild_portal/static/js/my_characters.js` — fetch + render progression panel
- `src/guild_portal/api/member_routes.py` (or equivalent) — add progression endpoint

---

## Design Notes

- Panel appears as a card below the stat panel, same card aesthetic
- Use a progress-bar style for raid bosses (thin gold bar, filled fraction)
- M+ score gets a colored badge matching the tier color
- "No data" states shown cleanly — never show raw `null` to the user
- Difficulty sections stacked vertically inside the raid card

---

## Tests

- `GET /api/v1/me/character/{id}/progression` requires auth and own-character check
- Correct raid kill counts returned from `character_raid_progress`
- Missing difficulty rows → `null` in response (not 500)
- M+ score: no season row → `mythic_plus: null`
- Score color tier boundaries correct (unit test the tier function)

---

## Deliverables Checklist

- [ ] `GET /api/v1/me/character/{id}/progression` endpoint
- [ ] Own-character authorization check
- [ ] Raid progress panel in template: all difficulties, kill counts, color coding
- [ ] Mythic+ score panel: score, tier color, best key
- [ ] Empty/no-data states
- [ ] Tests
