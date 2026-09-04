# Pull All The Things Unreleased

## Highlights

- Activate Midnight Season 2 as the sole active season, effective 2026-08-18.

## Fixes/Changes

- Seed Blizzard M+ season 18, raids Tidebound Grotto (1317) and Venomous Abyss (1320), the eight Season 2 M+ journal instances, class tier sets 2055–2067, and Season 2 drop/crafted item-level bands.
- Expose `tier_set_ids` through the RaidSeason ORM and admin season API.
- Make season creation and activation deactivate the previous season atomically; a partial unique index also enforces one active row in PostgreSQL.
- Allow admins to enter raid, dungeon, and tier-set IDs before those sources have been synchronized, and to edit season start dates.
- Update Midnight Season 2 M+ labels so Hero begins at +6. Remove the inaccurate claim that a persisted Site Config SimC bonus-ID override exists; the verified built-in mapping and empirical fallback remain in use.

## Validation

- Focused unit tests cover complete season configuration, active-season rollover, and M+ threshold labels.
- Focused season/source tests: 71 passed against isolated PostgreSQL 16. Full unit + integration + regression: 2,150 passed, 34 skipped; one Windows/WSL path-translation-only failure in the backup-script test (the same test is CI-authoritative on Linux). Playwright Chromium: 2 passed.
- Fresh Alembic upgrade to 0183, seeded-row assertions, one-revision downgrade/re-upgrade, and `current --check-heads` passed on isolated PostgreSQL 16.
- Release, production-readiness configuration, deployment-control, compile, and changed-file critical Ruff checks passed.

## Deployment/Migrations

- Alembic 0183 performs an idempotent upsert of Midnight Season 2, deactivates prior seasons without deleting them, and installs the single-active-season index.
- Before production promotion, confirm old-event attendance processing is complete. The 2026-09-03 read-only inventory found 0 unprocessed past attendance events but 54 old events without signup snapshots; those histories remain attached to Season 1.
- After deployment, run the normal Blizzard item-source, item-set, enrichment/classification, and BIS refresh sequence for the new IDs. Roster reset remains a separate explicit operation.

## Rollback

- A one-revision downgrade removes the active-row index, deactivates Midnight Season 2, and reactivates the latest prior season without deleting either season or related history. Re-upgrade reconciles and reactivates the S2 row.

## Known Limitations

- Season 2 SimC bonus IDs were not added because no authoritative mapping was available. Display-string detection, the existing verified map, exact-item matching, and empirical bonus learning continue to provide fallback coverage.
- Manual visual validation of the admin season editor is Release-timed per issue #60.
