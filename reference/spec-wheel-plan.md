# Hit's Wheel of Fate — Implementation Plan

## Goal

Add a member-only Wheel of Fortune-style picker that gives every eligible WoW
specialization an equal chance of being selected for either a main or off-spec.

## Decisions

- The active row in `patt.raid_seasons` defines the season.
- Each player has one `patt.spec_wheel_rolls` summary row per season and slot.
  The row stores the first result, latest result, their timestamps, and the
  total number of spins. This preserves at most four visible results per
  player per season: first/latest for main and first/latest for off-spec.
- A repeat spin requires explicit replacement confirmation. The first result
  never changes; the latest result is replaced and the season count increases.
- The server chooses the result with `secrets.choice`. The browser animates to
  the committed result, so each eligible specialization has one equal entry.
- “Open roles” reuses the public roster target logic (2 tanks, 4 healers, and
  14 DPS balanced between melee/ranged).
- Roster filters count only active, non-hiatus players with in-guild mains and
  guild rank level 2 or higher. Initiates never affect either filter.
- The two optional filters intersect:
  - only specs whose role is currently open;
  - only specs not represented by a counted player's main.
- After a spin, a player may assign a linked character of the rolled class to
  the selected main/off-spec slot. Eligible characters are sorted by level
  descending, name ascending, then realm ascending. The prompt is skipped when
  no matching character exists.

## Deliverables

1. Migration 0181 and SQLAlchemy model.
2. Shared roster-needs service to keep public and wheel rules aligned.
3. Member API for state, spinning, and character assignment.
4. Member page, canvas wheel, filters, history, count, confirmation, and
   assignment dialog.
5. Shared hamburger navigation, a bottom-of-page first/latest results table
   containing every current roster player, and first/latest result notes
   beside the My Profile character selectors.
6. Navigation, documentation, and targeted unit tests.
