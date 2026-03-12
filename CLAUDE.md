# PATT Guild Platform — CLAUDE.md

> **Read this file first.** This is the master context for the Pull All The Things guild platform.
> It is updated at the end of every build phase. If you are starting a new phase, this file
> tells you everything you need to know about what exists and what has been built so far.

---

## Project Identity

- **Project:** Pull All The Things (PATT) Guild Platform
- **Repo:** `Shadowedvaca/PullAllTheThings-site` (GitHub)
- **Domain:** pullallthethings.com
- **Owner:** Mike (Discord: Trog, Character: Trogmoon, Balance Druid, Sen'jin)
- **Guild:** "Pull All The Things" — a WoW guild focused on casual heroic raiding with a "real-life first" philosophy and zero-toxicity culture
- **Podcast:** "Salt All The Things" — a companion podcast to the guild, co-hosted by Trog and Rocket

---

## What This Is

A web platform for the PATT guild that provides:
- **Guild identity system** — players, characters, ranks, tied to Discord roles and Blizzard API data
- **Authentication** — invite-code registration via Discord DM, password login
- **Voting campaigns** — ranked-choice voting on images, polls, book club picks, etc.
- **Discord integration** — bot for role sync, DMs, contest updates, announcements, crafting orders
- **Admin tools** — campaign management, roster management, rank configuration, crafting sync
- **Blizzard API integration** — guild roster sync, character profiles, item levels, profession/recipe data
- **Crafting Corner** — guild-wide recipe directory with Discord guild order system
- **GuildSync addon** — WoW Lua addon + companion app for guild/officer note sync

The platform is built with **shared common services** that will be reused by other sites (shadowedvaca.com, Salt All The Things site). The common layer handles auth, Discord integration, identity, and notifications.

---

## Architecture

```
Hetzner Server (5.78.114.224)
├── Nginx (reverse proxy)
│   ├── shadowedvaca.com    → /var/www/shadowedvaca.com/ (static, existing)
│   └── pullallthething.com → proxy to PATT app (uvicorn, port 8100)
│
├── PostgreSQL 16
│   ├── common.*         (users, guild_ranks, discord_config, invite_codes, screen_permissions)
│   ├── patt.*           (campaigns, votes, entries, results, contest_agent_log, mito content,
│   │                     player_availability, raid_seasons, raid_events, raid_attendance,
│   │                     recurring_events)
│   └── guild_identity.* (players, wow_characters, discord_users, player_characters,
│                          player_note_aliases, player_action_log, classes, specializations,
│                          roles, audit_issues, sync_log, onboarding_sessions, professions,
│                          profession_tiers, recipes, character_recipes, crafting_sync_config,
│                          discord_channels)
│
├── PATT Application (Python 3.11+ / FastAPI)
│   ├── API routes
│   ├── Admin pages (Jinja2, server-rendered)
│   ├── Public pages (Jinja2, server-rendered)
│   └── Background tasks (role sync, contest agent, Blizzard sync, crafting sync)
│
├── PATT-Bot (discord.py, runs within the app process)
│   ├── Role sync (configurable interval)
│   ├── DM dispatch (registration codes)
│   ├── Contest agent (milestone posts)
│   ├── Campaign announcements
│   ├── Discord member sync
│   ├── Onboarding conversation flow (built, not yet activated)
│   └── Crafting Corner guild order embeds (#crafters-corner channel)
│
├── Common Services (sv_common Python package)
│   ├── sv_common.auth
│   ├── sv_common.discord
│   ├── sv_common.identity
│   ├── sv_common.notify
│   └── sv_common.guild_sync (Blizzard API, identity engine, addon processor, scheduler,
│                              crafting sync, crafting service, matching_rules package,
│                              drift_scanner, raid_booking_service)
│
├── PATTSync WoW Addon (wow_addon/PATTSync/)
│   └── Exports guild roster + notes from in-game
│
└── Companion App (companion_app/)
    └── Watches addon exports, uploads to API
```

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | Python 3.11+ | Matches shadowedvaca.com patterns, strong ecosystem |
| Web Framework | FastAPI | Async, auto API docs, Pydantic validation |
| Templates | Jinja2 | Same as shadowedvaca.com, server-rendered |
| Database | PostgreSQL 16 | Robust, supports schemas for multi-app isolation |
| ORM | SQLAlchemy 2.0 + Alembic | Industry standard, migration support |
| Discord | discord.py 2.x | Mature, async, full bot support |
| Auth | JWT (PyJWT) + bcrypt | Lightweight, stateless |
| Blizzard API | httpx + OAuth2 | Async HTTP, client credentials flow |
| Testing | pytest + pytest-asyncio + httpx | Async-native testing |
| Process Manager | systemd | Native Linux, no extra dependencies |
| Reverse Proxy | Nginx | Already running for shadowedvaca.com |

---

## Design Language

All PATT web pages follow a consistent dark fantasy theme:

- **Background:** Dark (#0a0a0b, #141416)
- **Cards/Panels:** Slightly lighter (#1a1a1d, #1e1e22)
- **Primary Accent:** Gold (#d4a84b) — used for headers, borders, highlights
- **Text:** Light (#e8e8e8 primary, #888 secondary)
- **Role Colors:** Tank (#60a5fa blue), Healer (#4ade80 green), Melee DPS (#f87171 red), Ranged DPS (#fbbf24 amber)
- **Status Colors:** Success (#4ade80), Warning (#fbbf24), Danger (#f87171)
- **Borders:** Subtle (#2a2a2e, #3a3a3e)
- **Fonts:** Cinzel (headers, display), Source Sans Pro (body), JetBrains Mono (code/data)
- **Feel:** WoW-inspired tavern aesthetic — warm, dark, gold accents, stone/metal textures

---

## Directory Structure

This project lives in the existing `Shadowedvaca/PullAllTheThings-site` repo.

```
PullAllTheThings-site/          (repo root)
├── CLAUDE.md                          ← YOU ARE HERE
├── TESTING.md                         ← Testing strategy and conventions
├── INDEX.md                           ← Context files quick reference
├── requirements.txt                   ← Python dependencies
├── alembic.ini                        ← Database migration config
├── .env.example                       ← Template for environment variables
│
├── alembic/                           ← Migration scripts
│   └── versions/
├── src/
│   ├── sv_common/                     ← Shared services package
│   │   ├── __init__.py
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── jwt.py
│   │   │   ├── passwords.py
│   │   │   └── invite_codes.py
│   │   ├── discord/
│   │   │   ├── __init__.py
│   │   │   ├── bot.py
│   │   │   ├── role_sync.py
│   │   │   ├── dm.py
│   │   │   └── channels.py
│   │   ├── identity/
│   │   │   ├── __init__.py
│   │   │   ├── members.py
│   │   │   ├── ranks.py
│   │   │   └── characters.py
│   │   ├── notify/
│   │   │   ├── __init__.py
│   │   │   └── dispatch.py
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py
│   │   │   ├── models.py
│   │   │   └── seed.py
│   │   └── guild_sync/
│   │       ├── __init__.py
│   │       ├── blizzard_client.py
│   │       ├── crafting_sync.py
│   │       ├── crafting_service.py
│   │       ├── discord_sync.py
│   │       ├── addon_processor.py
│   │       ├── identity_engine.py
│   │       ├── integrity_checker.py
│   │       ├── reporter.py
│   │       ├── scheduler.py
│   │       ├── db_sync.py
│   │       ├── sync_logger.py
│   │       ├── api/
│   │       │   ├── routes.py
│   │       │   └── crafting_routes.py
│   │       └── onboarding/
│   │           ├── conversation.py
│   │           ├── provisioner.py
│   │           ├── deadline_checker.py
│   │           └── commands.py
│   │
│   └── guild_portal/                  ← Guild platform application package
│       ├── __init__.py
│       ├── app.py
│       ├── config.py
│       ├── deps.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── auth_routes.py
│       │   ├── campaign_routes.py
│       │   ├── vote_routes.py
│       │   ├── admin_routes.py
│       │   └── guild_routes.py
│       ├── pages/
│       │   ├── __init__.py
│       │   ├── auth_pages.py
│       │   ├── vote_pages.py
│       │   ├── admin_pages.py
│       │   └── public_pages.py
│       ├── templates/
│       │   ├── base.html
│       │   ├── admin/
│       │   ├── vote/
│       │   └── public/
│       │       └── crafting_corner.html
│       ├── static/
│       │   ├── css/
│       │   ├── js/
│       │   └── legacy/
│       ├── services/
│       │   ├── __init__.py
│       │   ├── campaign_service.py
│       │   ├── vote_service.py
│       │   └── contest_agent.py
│       └── bot/
│           ├── __init__.py
│           └── contest_cog.py
│
├── wow_addon/
│   └── GuildSync/
│       ├── GuildSync.toc
│       ├── GuildSync.lua
│       └── README.md
│
├── companion_app/
│   ├── guild_sync_watcher.py
│   ├── requirements.txt
│   └── README.md
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── regression/
│
├── deploy/
│   ├── nginx/
│   ├── systemd/
│   └── setup_postgres.sql
│
├── data/
│   ├── contest_agent_personality.md
│   └── reference/
├── seed/
│   └── ranks.json
│
├── scripts/
│   ├── setup_art_vote.py
│   └── run_dev.py
│
├── docs/
│   ├── DISCORD-BOT-SETUP.md
│   └── OPERATIONS.md
│
├── reference/                         ← Phase plans and context docs
│   ├── INDEX.md
│   ├── PHASE_2_5_OVERVIEW.md
│   ├── PHASE_2_6_ONBOARDING.md
│   ├── PHASE_2_7_DATA_MODEL_MIGRATION.md
│   ├── PHASE_2_8_CRAFTING_CORNER.md
│   └── archive/                       ← Completed phase plans
│
└── memory/
    └── MEMORY.md
```

### Legacy Files

Root-level HTML files (index.html, roster.html, etc.) are legacy GitHub Pages files.
They are served by FastAPI from `src/guild_portal/static/legacy/` at their original URLs.

### Google Drive Images

Campaign entry images are stored in Google Drive and referenced by direct URL:
```
https://drive.google.com/uc?id={FILE_ID}&export=view
```
This renders the image directly without the Drive UI wrapper.
Images for the art vote live at: `J:\Shared drives\Salt All The Things\Marketing\Pull All The Things`

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://patt_user:PASSWORD@localhost:5432/patt_db

# Auth
JWT_SECRET_KEY=generate-a-strong-random-key
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Discord Bot
DISCORD_BOT_TOKEN=your-bot-token-here
DISCORD_GUILD_ID=your-discord-server-id

# Server
APP_ENV=production
APP_PORT=8100
APP_HOST=0.0.0.0

# Blizzard API (Phase 2.5)
BLIZZARD_CLIENT_ID=your-blizzard-client-id
BLIZZARD_CLIENT_SECRET=your-blizzard-client-secret

# Guild sync config (realm/name also configurable via Admin → Site Config)
GUILD_REALM_SLUG=senjin
GUILD_NAME_SLUG=pull-all-the-things

# Companion app API key
GUILD_SYNC_API_KEY=generate-a-strong-random-key

# NOTE: audit_channel_id and crafters_corner_channel_id are configured
# via the Admin UI (Admin → Raid Tools and Admin → Crafting Sync).
# They are stored in common.discord_config, NOT in .env.
```

---

## Database Schema

> Full DDL for all tables lives in **`reference/SCHEMA.md`**. Summary below.

Three PostgreSQL schemas, current through **migration 0033**:

| Schema | Key tables |
|--------|-----------|
| `common` | `guild_ranks`, `users`, `discord_config` (+`bot_token_encrypted`), `invite_codes`, `screen_permissions`, `site_config` (+`blizzard_client_id`, `blizzard_client_secret_encrypted`), `rank_wow_mapping` |
| `guild_identity` | `players` (central entity), `wow_characters`, `discord_users`, `player_characters` (bridge), `player_note_aliases`, `player_action_log`, `roles`, `classes`, `specializations`, `audit_issues`, `sync_log`, `onboarding_sessions`, `professions`, `profession_tiers`, `recipes`, `character_recipes`, `crafting_sync_config`, `discord_channels` |
| `patt` | `campaigns`, `campaign_entries`, `votes`, `campaign_results`, `contest_agent_log`, `guild_quotes`, `guild_quote_titles`, `player_availability`, `raid_seasons`, `raid_events`, `raid_attendance`, `recurring_events` |

**Key design notes:**
- `guild_identity.players` is the central identity entity — 1:1 FK to `discord_users` and `common.users`
- Character ownership via `player_characters` bridge (not a direct FK on `wow_characters`)
- `player_characters` carries `link_source` + `confidence` attribution metadata
- `common.guild_members` and `common.characters` are legacy tables — still in DB but removed from all ORM/code
- All Discord channel IDs stored in `common.discord_config`, configured via Admin UI (no hardcoded IDs)
- `crafting_sync_config` is a single-row table; display name built in code as `"{expansion_name} Season {season_number}"`
- `site_config` is a single-row table loaded at startup into `sv_common.config_cache`; all modules read guild name/color/flags from cache
- `rank_wow_mapping` maps WoW guild rank indices (0–9) to platform rank IDs; replaces hardcoded `RANK_NAME_MAP` in blizzard_client.py

---

## Operations & Deployment

- **Tests:** 418 pass, 69 skip (skips are pre-existing: identity_engine import error, one bot DM gate test); regression suite at `tests/regression/` requires live DB
- **CI/CD:** Three GitHub Actions workflows — each environment has its own trigger:
  - `deploy-dev.yml` — triggers on push to **any branch except main** → deploys to `dev.pullallthethings.com` (port 8102)
  - `deploy-test.yml` — triggers on push to **main** (i.e. PR merge) → deploys to `test.pullallthethings.com` (port 8101)
  - `deploy-prod.yml` — triggers on **version tag** (`v*`) → deploys to `pullallthethings.com` (port 8100)
  - SSH key: `DEPLOY_SSH_KEY` secret in GitHub repo (ed25519 key authorized on server)
  - Deploy steps: git fetch/checkout → docker build → docker up -d → health check
- **Branch strategy:** Feature branches → dev auto-deploys. Merge to main → test auto-deploys. Tag release → prod deploys.
- **Environments:** All three run as Docker containers on Hetzner. Dev/test behind nginx basic auth (username: `admin`). Passwords in server `/etc/nginx/htpasswd/`.

### Known Deploy Quirk — Chrome "GitHub 404" After Restart

If you reload the site in Chrome during or immediately after a deployment and get a GitHub Pages 404:
- This is Chrome serving a stale cached connection from when the repo used GitHub Pages
- **Fix:** Go to `chrome://net-internals/#sockets` → click **Flush socket pools**, then reload
- Not a server or code problem — happens occasionally at night when deploys coincide with Chrome reusing old socket connections

### Local Dev Notes
- Python venv: `.venv/` (created, not committed)
- Run tests: `.venv/Scripts/pytest tests/unit/ -v`
- Run dev server: `python scripts/run_dev.py` (requires .env with DATABASE_URL)
- DB-dependent tests (service + integration) require TEST_DATABASE_URL env var pointing to a running PostgreSQL instance
- Pure unit tests (smoke + pure function tests) pass without a live database
- JWT_SECRET_KEY in .env must be 32+ bytes (PyJWT warns if shorter)

---

## Conventions

### Code Style
- Python: Black formatter, isort for imports, type hints everywhere
- SQL: Lowercase keywords, snake_case for identifiers
- JavaScript: Vanilla JS (no framework), const/let (no var)
- CSS: Custom properties for all colors/spacing, BEM-ish class names
- HTML: Jinja2 templates, semantic HTML5

### Naming
- Database tables: snake_case, plural (players, wow_characters, campaign_entries)
- Python modules: snake_case
- API routes: /api/v1/resource-name (kebab-case)
- Template files: snake_case.html

### Error Handling
- API endpoints return consistent JSON: `{"ok": true, "data": {...}}` or `{"ok": false, "error": "message"}`
- All database operations wrapped in try/except with proper rollback
- User-facing errors are friendly; technical details logged server-side

### Git
- Commit at the end of each phase
- Commit messages: `phase-N: brief description`
- Branch strategy: main only (single developer)

### Testing
- See TESTING.md for full testing strategy
- Every phase includes tests as a deliverable
- Tests must pass before phase is considered complete
- Run: `pytest tests/ -v` from project root

---

## Current Build Status

> **UPDATE THIS SECTION AT THE END OF EVERY PHASE**

### Completed Phases
- Phase 0 through 7: Platform complete and live
- Phase 2.5A–D: Guild identity system (Blizzard API, Discord sync, addon, integrity checker)
- Phase 2.6: Onboarding system (built but NOT activated — on_member_join not wired)
- Phase 2.7: Data Model Migration — Clean 3NF rebuild; `players` table as central entity; reference tables; player_characters bridge
- Phase 2.8: Crafting Corner — profession/recipe DB, `/crafting-corner` public page, adaptive sync cadence, admin sync page
- Phase 2.9: Data Quality Engine — 8-rule registry, targeted mitigations, admin `/admin/data-quality` page
- Phase 3.0A: Matching transparency — link_source/confidence on player_characters, coverage dashboard
- Phase 3.0B: Iterative rule runner — pluggable matching_rules package, progressive discovery, per-rule results UI
- Phase 3.0C: Drift Detection — link_contradicts_note, duplicate_discord, stale_discord_link rules; drift_scanner.py; drift panel on Data Quality page
- Phase 3.0D: Player Manager QoL — player deletion guard, `/admin/users` page, alias chips, `_compute_best_rank` helper
- Phase 3.1: Admin Availability Dashboard — `patt.recurring_events` table, 7-day availability grid, event day config, `GET /admin/availability`
- Phase 3.2: Index Page Revamp — officers, recruiting needs, and weekly schedule all live from DB
- Phase 3.3: Public Roster View — `/roster` page with Full Roster, Composition, and Schedule tabs; Wowhead comp link; legacy redirects
- Phase 3.4: Admin Raid Tools — Raid-Helper API integration, event builder with roster preview, `GET /admin/raid-tools`
- Phase 3.5: Auto-Booking Scheduler — background loop creates next week's Raid-Helper event 10–20 min after raid starts, posts Discord announcement
- Roster Initiate Filtering + Raid Hiatus (migration 0030) — `on_raid_hiatus` flag on players; initiates filtered from comp tab; New Members box; Show Initiates checkbox on roster
- Phase 4.0: Config Extraction & Genericization (migration 0032) — `common.site_config` single-row table, `sv_common.config_cache` in-process cache, `common.rank_wow_mapping`, mito tables renamed to guild_quotes/guild_quote_titles, `/quote` bot command, `/admin/site-config` GL-only page, all hardcoded guild name/color/realm refs removed from code
- Phase 4.1: First-Run Setup Wizard (migration 0033) — 9-step web wizard activated when `setup_complete=FALSE`; encryped credential storage (Fernet/JWT_SECRET_KEY); Discord token/guild verification; Blizzard API verification; rank naming + WoW rank mapping UI; Discord role/channel assignment; admin account bootstrap; guard middleware redirects all routes to `/setup` until complete; setup routes become 404 after completion
- Phase 4.2: Docker Packaging & Environments — `Dockerfile`, `docker-entrypoint.sh`, `docker-compose.yml` (generic), `docker-compose.patt.yml` (PATT 3-env), `Caddyfile` + `Caddyfile.patt`, `.env.template`, `.dockerignore`; updated `setup_postgres.sql` to be Docker-generic; updated GitHub Actions deploy workflow to use Docker

### Current Phase
- **Platform is feature-complete through Phase 4.2.** Next: Phase 4.3 Blizzard API Expansion.

### Recent Changes (2026-03-11, no migration)
- **Phase 4.2 complete**: Docker packaging. `Dockerfile` + `docker-entrypoint.sh` (uses `guild_portal.app:create_app`, `PYTHONPATH=/app/src`). Generic `docker-compose.yml` (app + postgres + caddy). `docker-compose.guild.yml` (3 envs: prod/test/dev, isolated DBs, nginx routing). `Caddyfile` (generic `{$DOMAIN}` routing) + `Caddyfile.guild` (subdomain routing with basic auth on test/dev, username `admin`). `.env.template` for new guild deployments. `.dockerignore` keeps image lean. `deploy/setup_postgres.sql` genericized. GitHub Actions workflow updated to use `docker compose -f docker-compose.guild.yml` against `/opt/guild-portal`. Production migrated from systemd to Docker. Old systemd `patt` service disabled. PATT references scrubbed from all code, comments, templates, and config files (legacy static HTML files excluded).
- **Phase 4.1 complete**: First-Run Setup Wizard. 430 tests pass, 69 skip.
- **Admin nav revamp**: `base_admin.html` now includes the same `site-header` as public pages (guild name, Home/Roster/Crafting/Admin links, character badge, rank badge, Log Out). Sidebar footer removed. Admin layout changed to column flex with app-shell scrolling — header spans full width, sidebar+content row fills remaining height, each scrolls independently.
- **Nginx static path**: `/static/` alias in nginx was hardcoded to `src/patt/static/` — updated to `src/guild_portal/static/` in both live config and `deploy/nginx/pullallthething.com.conf`.
- **Phase 4.0 complete**: genericization, config extraction, migration 0032 all deployed. 418 tests pass, 69 skip.

### Recent Bug Fixes (2026-03-07, no migration)
- **Player Manager character badges**: replaced legacy `M`/`A` letter badges + toggle button with read-only text labels. Gold `Main` / blue `Off` / nothing for alts. If a character is both main and offspec, both badges render via a `.pm-char-badges` flex wrapper. SQL CASE now returns `'main+offspec'` when `main_character_id = offspec_character_id`.
- **Front page recruiting needs**: query now uses `COALESCE(p.main_spec_id, wc.active_spec_id)` (matching roster API logic), excludes initiates (`gr.level > 1`), and filters `on_raid_hiatus IS NOT TRUE`. `preferred_role` does NOT exist on `guild_identity.players` — never add it to SQL against that table.
- **Front page weekly schedule**: was silently empty due to a failed recruiting needs query corrupting the SQLAlchemy session. Fixed as a side effect of the query fix.

### Recent Bug Fixes (2026-03-08, no migration)
- **Discord sync `$4` type error**: asyncpg couldn't infer the type of `$4` (highest_role, which can be NULL) in CASE expressions. Fixed by casting `$4::varchar` in both the UPDATE and INSERT queries in `sync_discord_members()` (`discord_sync.py`).
- **Audit report embed overflow**: `reporter.py` sent all embeds in one `channel.send()` call, hitting Discord's 6000-char per-message limit when many issue types were present. Fixed with char-count-aware batching — flushes to a new message before hitting 5900 chars.
- **Spurious "Discord member not found" warnings**: `reconcile_player_ranks()` tried to fetch/update Discord roles for departed members (`is_present=FALSE`). Fixed by adding `is_present` to the reconcile query and skipping Discord role correction for departed users.
- **Fully-departed player purge**: added `purge_fully_departed_players()` to `discord_sync.py` — deletes player, discord_user, and website account when a player has no linked characters AND is not present in Discord. Runs immediately (no waiting period) after every Blizzard and Discord sync. Posts a "🗑️ Departed Players Removed" embed to the audit channel listing names.

### What Exists
- sv_common.identity package: ranks, players, characters CRUD (`src/sv_common/identity/`)
- sv_common.auth package: passwords (bcrypt), JWT (PyJWT), invite codes (`src/sv_common/auth/`)
- sv_common.discord package: bot client, role sync (DiscordUser+Player), DM dispatch, channel posting, channel_sync (`src/sv_common/discord/`)
- sv_common.guild_sync package: Blizzard API client, identity engine, integrity checker, Discord sync, addon processor, scheduler, crafting sync + service, rules registry + mitigations engine, matching_rules package, drift_scanner, raid_booking_service
- Public pages: `/` (index), `/roster`, `/crafting-corner`, `/guide`; no login required for any of these
- Crafting Corner: `/crafting-corner`, `/api/crafting/*`, profession/recipe DB tables, adaptive sync cadence
- Admin pages (Officer+ rank required):
  - `/admin/campaigns` — campaign lifecycle (draft→live→closed), ranked-choice voting
  - `/admin/players` — Player Manager (drag-and-drop linking, alias chips, hiatus toggle, invite DM)
  - `/admin/users` — website account management (enable/disable/delete)
  - `/admin/availability` — 7-day availability grid with role breakdown, event day config
  - `/admin/raid-tools` — Raid-Helper config, availability grid, event builder, roster preview
  - `/admin/data-quality` — coverage dashboard, unmatched tables, rule stats, drift detection panel
  - `/admin/crafting-sync` — force refresh, season config, sync stats
  - `/admin/bot-settings` — DM feature toggles
  - `/admin/reference-tables` — view roles, classes, specializations
  - `/admin/audit-log` — sync log viewer
  - `/admin/site-config` — guild identity, branding, and feature flags (Guild Leader only)
- Auto-booking: `raid_booking_service.py` — background loop, books next week's raid 10–20 min after current raid starts
- Settings pages (rank-gated via screen_permissions): Availability, Character Claims, Guide
- Auth API: `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`
- Cookie-based auth: `get_page_player()`, `require_page_rank(level)` in `src/guild_portal/deps.py`
- Admin API: `/api/v1/admin/*` — all routes protected (Officer+ rank required)
- Public API: `/api/v1/guild/ranks`, `/api/v1/guild/roster`
- Discord bot starts as background task during FastAPI lifespan (skipped if no token configured)
- Contest agent: Discord milestone posts, auto-activate/close campaigns
- Onboarding system: conversation.py, provisioner.py, deadline_checker.py, commands.py (dormant — needs activation)
- GuildSync WoW addon (`wow_addon/GuildSync/`) + companion app (`companion_app/guild_sync_watcher.py`) — functional, syncing guild notes via `/guildsync` slash command in WoW
- Screen permissions: DB-driven Settings nav — all screens configurable by rank level via `common.screen_permissions`
- Setup wizard: `/setup` through `/setup/complete` — 9-step first-run wizard; guard middleware redirects all traffic until `setup_complete=TRUE`; setup API at `/api/v1/setup/*`; `sv_common.crypto` for Fernet encryption of bot token + Blizzard secret

### Known Gaps / Dormant Features
- Onboarding flow: code exists (`sv_common.guild_sync.onboarding`), not activated — `on_member_join` not wired
- `guild_identity.identity_engine`: some tests skipped due to import error — pre-existing, non-blocking
