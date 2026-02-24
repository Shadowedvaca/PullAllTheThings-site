# PATT Platform — Session Memory

> This file captures the running state of the project for context continuity.
> Updated at the end of each work session.

---

## Project State (as of Phase 2.8 start)

### Completed
- **Phases 0–7:** Full platform built and deployed (auth, campaigns, voting, contest agent, web UI)
- **Phase 2.5A–D:** Guild identity system (Blizzard API integration, Discord member sync, PATTSync addon + companion app, integrity checker)
- **Phase 2.6:** Onboarding system code complete but NOT activated (on_member_join not wired up)
- **Phase 2.7:** Data model migration — 3NF rebuild complete
  - Migration 0007 applied: 43 players created, 195 player_characters linked, reference tables seeded
  - `GuildMember` and `Character` models eliminated from all application code
  - `Player`, `DiscordUser`, `PlayerCharacter`, `Role`, `WowClass`, `Specialization` models live
  - All deps, routes, services, templates, tests updated for Player model
  - 202 unit tests pass

### Current Phase: 2.8 — Scheduling, Availability & Attendance Foundation
- Replace boolean availability with time-window + weighted scheduling
- Add `scheduling_weight` to guild_ranks
- Add `timezone` and `auto_invite_events` to players
- Create `player_availability`, `raid_seasons`, `raid_events`, `raid_attendance` tables
- Drop old `member_availability` table (data is garbage, 133 orphaned rows)
- Admin page for reference table management (ranks, roles, seasons)
- Attendance tables are schema-only in this phase — feature implementation is a fast follower

### Key Data State
- `guild_identity.players`: 43 rows (migrated from guild_members)
- `guild_identity.player_characters`: 195 rows
- `guild_identity.wow_characters`: ~320 rows (from Blizzard API syncs)
- `guild_identity.discord_users`: Discord server members (from bot syncs)
- `guild_identity.roles`: 4 rows (Tank, Healer, Melee DPS, Ranged DPS)
- `guild_identity.classes`: 13 rows (all WoW classes)
- `guild_identity.specializations`: ~39 rows (all WoW specs)
- `patt.member_availability`: 133 rows with NULL player_id — TO BE DROPPED in Phase 2.8
- All players have `main_character_id`, `main_spec_id`, `offspec_*` as NULL (set on first login)

### Dormant Code (not wired up, uses old schema names)
These modules still reference `persons`, `discord_members`, `identity_links` from pre-2.7:
- `src/sv_common/guild_sync/identity_engine.py`
- `src/sv_common/guild_sync/integrity_checker.py`
- `src/sv_common/guild_sync/discord_sync.py`
- `src/sv_common/guild_sync/db_sync.py`
- `src/sv_common/guild_sync/onboarding/*.py`

Will be updated when these features are activated.

---

## Architecture Notes

- **Server:** Hetzner VPS at 5.78.114.224
- **Domain:** pullallthething.com (Nginx → FastAPI on port 8100)
- **Database:** PostgreSQL 16 — patt_db (schemas: common, patt, guild_identity)
- **Migrations:** Alembic — 0001 through 0007 deployed
- **CD:** GitHub Actions auto-deploy on push to main (SSH key = DEPLOY_SSH_KEY)

## Key File Locations
- Phase plans: `reference/PHASE_2_8_SCHEDULING_AND_ATTENDANCE.md` (current)
- Testing guide: `reference/TESTING.md`
- App entry: `src/patt/app.py` (factory: `create_app()`)
- Config: `src/patt/config.py` (pydantic-settings, reads .env)
- DB dependency: `src/patt/deps.py` — `get_db()`, `get_current_player()`, `require_rank(level)`
- Models: `src/sv_common/db/models.py` (all ORM models — common, patt, guild_identity schemas)
- Engine/session: `src/sv_common/db/engine.py`
- Seed data: `src/sv_common/db/seed.py` + `data/seed/ranks.json`
- Identity services: `src/sv_common/identity/ranks.py`, `members.py` (Player CRUD), `characters.py`
- Auth services: `src/sv_common/auth/passwords.py`, `jwt.py`, `invite_codes.py`
- Discord services: `src/sv_common/discord/bot.py`, `role_sync.py`, `dm.py`
- Guild sync package: `src/sv_common/guild_sync/` (dormant — see note above)
- Admin API: `src/patt/api/admin_routes.py` (Officer+ protected)
- Auth API: `src/patt/api/auth_routes.py` (register, login, me)
- Guild API: `src/patt/api/guild_routes.py`
- Campaign API: `src/patt/api/campaign_routes.py`
- Health route: `src/patt/api/health.py`
- Alembic migrations: `alembic/versions/` (0001–0007)
- Tests: `tests/unit/`, `tests/integration/`

## Archived Documentation
Completed phase instructions moved to `reference/archive/`:
- PHASE-0.md through PHASE-7.md
- PHASE_2_5A.md through PHASE_2_5D.md
- PHASE_2_7_DATA_MODEL_MIGRATION.md
- ADMIN-SETUP-GUIDE.md (legacy Google Sheets system)
