# Pull All The Things Unreleased

## Highlights

- Activate Midnight Season 2 as the sole active season, effective 2026-08-18.
- Add the later-released Sporefall raid (Blizzard journal instance 1305) to
  Midnight Season 1 without changing the active season.
- Remove Champion from the Midnight Season 2 crafted item-level map because
  Blizzard does not provide a Champion-crest crafted track.
- Make active-season tier-token publication an automatic part of Enrich &
  Classify, using Blizzard-derived token data instead of requiring Wowhead
  tooltips or a removed Gear Plan button.
- Display tier-token names from the dedicated Blizzard-derived token catalog;
  non-equippable tokens are intentionally absent from `enrichment.items`.
- Make Icy Veins Catalyst recommendations actionable by centering Gear Plan rows
  on the farmable base item and showing the resulting tier item as a Catalyst action.
- Keep direct tier goals and Catalyst acquisition routes distinct, including a
  clear "base equipped, ready to catalyze" state instead of a false BIS star.

## Fixes/Changes

- Seed Blizzard M+ season 18, raids Tidebound Grotto (1317) and Venomous Abyss (1320), the eight Season 2 M+ journal instances, class tier sets 2055–2067, and Season 2 drop/crafted item-level bands.
- Expose `tier_set_ids` through the RaidSeason ORM and admin season API.
- Make season creation and activation deactivate the previous season atomically; a partial unique index also enforces one active row in PostgreSQL.
- Allow admins to enter raid, dungeon, and tier-set IDs before those sources have been synchronized, and to edit season start dates.
- Update Midnight Season 2 M+ labels so Hero begins at +6. Remove the inaccurate claim that a persisted Site Config SimC bonus-ID override exists; the verified built-in mapping and empirical fallback remain in use.
- Parse Icy Veins' redesigned Best-in-Slot card grids, ignoring nested gems,
  enchants, embellishments, Shirt, and Tabard cards. Explicit `original-item`
  attributes retain their provider result; cards explicitly worded as Catalyst
  routes resolve to the one active-season class tier result for that slot. Apply
  an exact Protection Warrior hands-route correction where Icy Veins omits both
  markers for Bonds of the Hash'ura (251214) to Jade Warlord tier hands (271457).
- Scope Gear Plan recommendations and item sources to the active season, and
  season-align tier-token raid sources so Season 1 tier and raid locations do
  not leak into Season 2 recommendations.
- Select weapon layout from the configured guide source and clear stale unlocked
  opposite-hand goals during Fill BIS, restoring Protection Warrior shields and
  the matching one-hand drill-down.
- Treat bare Blizzard "Heroic"/"Mythic" labels as acquisition difficulty, not
  upgrade tracks, and include equipped bonus IDs in Wowhead tooltip links.
- Expose the Gear Plan specialization instead of leaving stale hidden spec
  state, and refill unlocked guide goals when the specialization changes.
- When Blizzard omits an equipped item's upgrade track, retain useful track
  recommendations whose active-season ceiling exceeds the equipped item level.
- Display a selected Catalyst goal as the converted tier result while retaining
  its farmable base route in the label and Wowhead tooltip. A generic tier result
  no longer proves or receives the BIS state for a specific Catalyst route.

## Validation

- Focused unit tests cover complete season configuration, active-season rollover, and M+ threshold labels.
- Make loot-table synchronization idempotent with a pre-populated landing schema;
  existing journal encounters are refreshed instead of aborting their item-source pass.
- Focused season/source tests: 71 passed against isolated PostgreSQL 16. Full unit + integration + regression: 2,150 passed, 34 skipped; one Windows/WSL path-translation-only failure in the backup-script test (the same test is CI-authoritative on Linux). Playwright Chromium: 2 passed.
- Fresh Alembic upgrade to 0183, seeded-row assertions, one-revision downgrade/re-upgrade, and `current --check-heads` passed on isolated PostgreSQL 16.
- Release, production-readiness configuration, deployment-control, compile, and changed-file critical Ruff checks passed.
- Focused card-parser, Catalyst persistence, and Gear Plan rendering tests cover
  the explicit 268229 base to 271456 tier-result relationship and 16-slot output.
- Focused Icy Veins parser/insertion tests cover the missing-metadata Protection
  Warrior glove route and its spec isolation: 105 passed.
- Focused Gear Plan service and JavaScript contract tests cover unknown-track
  upgrade advice, explicit spec selection, and Catalyst base/result display.

## Deployment/Migrations

- Alembic 0183 performs an idempotent upsert of Midnight Season 2, deactivates prior seasons without deleting them, and installs the single-active-season index.
- Alembic 0187 adds the BiS recommendation type and explicit Catalyst tier-result
  item, with constraints that prevent incomplete or inferred relationships, and
  exposes base/result metadata through `viz.bis_recommendations`.
- Alembic 0188 persists the selected Catalyst route on player goals and rebuilds
  `viz.tier_piece_sources` with active-season alignment.
- Before production promotion, confirm old-event attendance processing is complete. The 2026-09-03 read-only inventory found 0 unprocessed past attendance events but 54 old events without signup snapshots; those histories remain attached to Season 1.
- After deployment, run the normal Blizzard item-source, item-set, enrichment/classification, and BIS refresh sequence for the new IDs. Roster reset remains a separate explicit operation.

## Rollback

- A one-revision downgrade removes the active-row index, deactivates Midnight Season 2, and reactivates the latest prior season without deleting either season or related history. Re-upgrade reconciles and reactivates the S2 row.

## Known Limitations

- Season 2 SimC bonus IDs were not added because no authoritative mapping was available. Display-string detection, the existing verified map, exact-item matching, and empirical bonus learning continue to provide fallback coverage.
- Manual visual validation of the admin season editor is Release-timed per issue #60.
