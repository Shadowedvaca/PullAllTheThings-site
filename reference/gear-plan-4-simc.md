# Gear Plan Phase 4 — SimulationCraft Integration

> **Status:** Deferred. Code exists but is hidden from the UI as of the Phase 1D controls revisit.  
> **Prerequisite:** Mike needs to test SimC end-to-end before this ships to members.  
> **Backend:** Fully implemented. `simc_parser.py`, import/export endpoints, `simc_profile` column on `gear_plans` — all intact and untouched.

---

## What SimulationCraft Is

SimulationCraft (SimC) is the standard tool WoW players use to model character performance. It uses a plain-text profile format that describes a character's gear, talents, and stats. That same format is also used by:

- **Raidbots** (web-based sims — the tool most PATT members would recognize)
- **Archon / u.gg** (they generate SimC profiles for their recommended builds)
- **The in-game SimulationCraft addon** (exports your currently-equipped gear as a SimC string)

The profile looks like this:
```
balance_druid="Trogmoon"
level=80
race=night_elf
...
head=,id=212456,bonus_id=4800/1498/8767/9413
neck=,id=215133,...
```

---

## What the Existing Feature Does

### Import

The user pastes a SimC string (from the in-game addon or a guide site). The backend:
1. Calls `simc_parser.parse_profile()` to extract gear slots
2. Maps bonus IDs to quality tracks (V/C/H/M) via `bonus_ids_to_quality_track()`
3. Calls the same populate logic as Fill BIS — writes to `gear_plan_slots`, skipping locked slots
4. Returns `{populated, skipped_locked, unrecognised}` counts

The use case: a player has a SimC string from Raidbots or their addon and wants to set their gear plan to match exactly what they're wearing (or what a sim recommends), faster than clicking slot by slot.

### Export

Generates a SimC profile string from the player's current `character_equipment` (what they have equipped) and `gear_plan_slots` (what they're targeting). The player can paste this into Raidbots to sim their current gear, or share it with others.

### Storage

`gear_plans.simc_profile TEXT` stores the last-imported SimC text verbatim for reference. Not used for display; just a record of what was imported.

---

## Why It Was Hidden

1. **Untested end-to-end.** The import parser and populate logic were written but never verified against a real SimC string from the in-game addon. We don't know if edge cases (dual-wield, off-hand, missing slots, malformed strings) are handled gracefully.
2. **Redundant with Blizzard sync for most users.** The Blizzard API already gives us exactly what a player has equipped. SimC import only adds value when: (a) a player wants to load a *hypothetical* gear set (e.g., what they're targeting for next tier), or (b) the Blizzard sync is stale and they've just changed gear in-game.
3. **Not clearly explained in the UI.** The Import/Export buttons appeared next to Sync Gear and Fill BIS with no context. Most members wouldn't know what a SimC profile is.
4. **Export value is unclear.** What does a member do with the exported string on this platform? If it just goes to Raidbots, that workflow needs to be clearly explained.

---

## What Needs to Happen Before Re-Introducing It

### 1. End-to-end test the import
- Install the in-game SimulationCraft addon
- Export a real character's gear as a SimC string
- Paste it into the import modal
- Verify all slots populate correctly, including weapons and rings/trinkets (which can be ambiguous — ring 1 vs ring 2)
- Test with a SimC string that has some slots missing — does it handle gracefully or error?
- Test the `skipped_locked` path — lock a slot, then import and confirm it's not overwritten

### 2. Decide on the actual use case we're serving
Two distinct use cases with different UX needs:

**Use case A — "Load what I'm wearing right now"**  
Export from the in-game addon, paste, populate. This overlaps with Blizzard sync. Only useful if the user has just changed gear and hasn't waited for the scheduled sync. May not be worth surfacing at all once we confirm the sync cadence is fast enough.

**Use case B — "Load a hypothetical / theorycraft set"**  
Paste a SimC profile from a guide, a Discord post, or a Raidbots sim result to see what that gear set looks like in the plan. This is genuinely valuable and doesn't overlap with Blizzard sync. This is the case worth building for.

### 3. Design the UX for re-introduction
Recommended placement: a collapsible **"Advanced"** section below the gear table, not buttons in the primary control row.

```
▶ Advanced Options
  ┌────────────────────────────────────────────────────────┐
  │ Import SimC Profile                                    │
  │ Paste a SimC string to load a gear set into your plan. │
  │ Locked slots will not be overwritten.                  │
  │ [textarea]                        [Import]  [Cancel]   │
  ├────────────────────────────────────────────────────────┤
  │ Export SimC Profile                                    │
  │ Copy your current gear plan as a SimC string for use  │
  │ in Raidbots or other sim tools.                        │
  │                                          [Copy]        │
  └────────────────────────────────────────────────────────┘
```

Both actions should be clearly explained with one-line descriptions so members who don't know what SimC is can understand the value proposition or ignore the section entirely.

### 4. Consider a Raidbots link
If we export a SimC profile, we could offer a direct "Sim on Raidbots" button that opens `https://www.raidbots.com/simbot` in a new tab with the profile pre-filled (Raidbots supports URL-based profile passing). This makes the export actually useful to members rather than just generating a text blob.

---

## Files to Revisit When Building

| File | Notes |
|------|-------|
| `src/sv_common/guild_sync/simc_parser.py` | Core parser — needs end-to-end testing |
| `src/guild_portal/api/gear_plan_routes.py` | Import (`POST .../import-simc`) and export (`GET .../export-simc`) endpoints — verify error handling |
| `src/guild_portal/static/js/my_characters.js` | `_gpOnSimcImport`, `_gpShowSimcModal`, SimC modal wiring — currently commented out |
| `src/guild_portal/templates/member/my_characters.html` | SimC modal block — currently commented out |

No migrations needed. The `simc_profile` column on `gear_plans` already exists (migration 0066).

---

## Out of Scope for Phase 4

- Automated SimC profile generation from guide recommendations (Archon publishes SimC profiles — we could pull these, but that's a separate scraper concern)
- Raidbots API integration (they don't have a public write API)
- Per-slot SimC bonus ID mapping updates (this is a maintenance task, not a feature)
