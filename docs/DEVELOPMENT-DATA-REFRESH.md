# Production-shaped Development refresh

This procedure supports seasonal diagnostics where Blizzard identifiers and their
relationships are distributed across landing, enrichment, configuration,
character equipment, gear plans, progression, and historical content tables.
It is not part of ordinary deployment.

Required controls:

1. Obtain explicit authorization to replace Development.
2. Create and verify a full Development rollback archive before stopping the app.
3. Preserve Development-only service configuration and one existing administrator
   credential on the Development host; never print those values or copy them into
   repository artifacts.
4. Stream a fresh custom-format Production archive directly into the stopped,
   newly-created Development database. Do not write the unsanitized archive to a
   workstation or commit it anywhere.
5. Before restarting the Development app, run
   `scripts/sanitize_development_clone.sql`, restore Development service config,
   and restore only the preserved administrator credential to its matching account.
6. Verify all non-review accounts are disabled and direct identifiers, provider
   tokens, free-form member text, auth sessions, and social submissions are gone.
7. Deploy the seasonal branch so Alembic upgrades the sanitized clone, then run
   the normal seasonal source pipeline: landing API fill, enrichment/classification,
   item-source/legacy-dungeon/crafted links as applicable, BIS refresh, and health
   checks. Roster reset is a separate operation and is not implied.
8. Capture before/after table counts and distinct Blizzard-ID coverage. Report
   provider errors, new IDs, stale IDs, unclassified rows, and recommendation gaps.

The sanitizer deliberately retains stable internal keys and character equipment,
gear-plan, progression, item, source, recipe, raid, dungeon, and landing/enrichment
relationships. Names and login links are removed so those joins remain useful for
season diagnostics without retaining member identity.
