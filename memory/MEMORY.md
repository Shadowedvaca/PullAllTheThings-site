# PATT Platform — Session Memory

## Project State
- Phase 0 complete
- Phase 1 complete: identity services, admin/guild API, full test suite
- Phase 2 complete: auth package, discord package, auth API, tests (28/28 pass)
- Phase 2.5A complete: guild_identity schema, Blizzard API client, db_sync, migration, sync_logger
- Phase 2.5B complete: discord_sync, identity_engine, integrity_checker, reporter, scheduler, API routes
- Phase 2.5C complete: WoW addon (wow_addon/PATTSync/), companion app (companion_app/)
- Phase 2.5D complete: 133 unit tests pass (24 skipped/DB-only); integration tests in tests/integration/test_guild_*.py need live DB
- Phase 3 complete: campaign_service, vote_service, campaign_routes (admin/vote/public), background status checker, unit+integration tests (163/187 pass, 24 skip DB-only)
- Phase 4 complete: page routes (auth, vote, admin, public), Jinja2 templates, cookie auth, JS files, integration tests (test_page_rendering.py)
- Phase 5 complete: member_availability + mito_quotes + mito_titles tables (migration 0003); legacy HTML moved to src/patt/static/legacy/ and served by FastAPI at original URLs; new guild API endpoints (roster-data, roster-submit, availability, mito CRUD); migration script scripts/migrate_sheets.py; tests 192/216 pass; data migrated from Sheets on server
- Phase 6 complete: contest agent Discord updates; migration 0004 (agent_enabled, agent_chattiness on campaigns); contest_agent.py service; channels.py Discord posting module; admin form updated; 36 unit tests + integration tests; tests 228/252 pass (24 skip DB-only)
- Phase 7 complete: regression suite (tests/regression/test_full_platform.py); art vote setup script (scripts/setup_art_vote.py); 500.html error page; 404.html enhanced; SecurityHeadersMiddleware + login rate limiting in app.py; secure cookie flag (production); CSS animations for score bars + result rows; docs/OPERATIONS.md; CLAUDE.md updated
- CI/CD: GitHub Actions at .github/workflows/deploy.yml — auto-deploys on push to main
- Phase 2.6 built but NOT activated: onboarding_sessions table (migration 0005), preferred_role column (migration 0006), conversation.py, provisioner.py, deadline_checker.py, commands.py — on_member_join not wired, slash commands not registered

## Current Phase: 2.7 — Data Model Migration (Clean 3NF Rebuild)
- See reference/PHASE_2_7_DATA_MODEL_MIGRATION.md for full instructions
- Eliminates: common.guild_members, common.characters, guild_identity.identity_links
- Creates: reference tables (roles, classes, specializations), player_characters bridge
- Renames: persons → players, discord_members → discord_users
- Adds to players: discord_user_id, website_user_id, guild_rank_id, main/offspec fields
- Repoints all FKs from guild_members → players
- Updates all models, services, routes, templates, tests
- Alembic migration: 0007

## Key Data State (pre-2.7)
- guild_identity.persons: EMPTY (identity engine never run on existing data)
- guild_identity.identity_links: EMPTY
- guild_identity.wow_characters: ~320 rows from Blizzard API syncs
- guild_identity.discord_members: populated from Discord bot syncs
- common.guild_members: ~40 rows (from Google Sheets migration — will be migrated to players)
- common.characters: character data from Sheets migration (will be dropped)
- Migrations 0001-0006 deployed on server

## Architecture Notes
- Server: Hetzner 5.78.114.224, Nginx → uvicorn :8100
- Domain: pullallthething.com (SSL via certbot)
- DB: PostgreSQL 16, schemas: common, patt, guild_identity
- App: Python 3.11+, FastAPI, SQLAlchemy 2.0, discord.py 2.x
- Repo: Shadowedvaca/PullAllTheThings-site
- Deploy: systemd patt.service, auto-deploy via GitHub Actions
- Completed phase docs archived to reference/archive/
