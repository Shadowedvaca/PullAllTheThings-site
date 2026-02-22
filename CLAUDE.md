# PATT Guild Platform — CLAUDE.md

> **Read this file first.** This is the master context for the Pull All The Things guild platform.
> It is updated at the end of every build phase. If you are starting a new phase, this file
> tells you everything you need to know about what exists and what has been built so far.

---

## Project Identity

- **Project:** Pull All The Things (PATT) Guild Platform
- **Repo:** `Shadowedvaca/PullAllTheThings-site` (GitHub)
- **Domain:** pullallthething.com
- **Owner:** Mike (Discord: Trog, Character: Trogmoon, Balance Druid, Sen'jin)
- **Guild:** "Pull All The Things" — a WoW guild focused on casual heroic raiding with a "real-life first" philosophy and zero-toxicity culture
- **Podcast:** "Salt All The Things" — a companion podcast to the guild, co-hosted by Trog and Rocket

---

## What This Is

A web platform for the PATT guild that provides:
- **Guild identity system** — members, ranks, characters, tied to Discord roles
- **Authentication** — invite-code registration via Discord DM, password login
- **Voting campaigns** — ranked-choice voting on images, polls, book club picks, etc.
- **Discord integration** — bot for role sync, DMs, contest updates, announcements
- **Admin tools** — campaign management, roster management, rank configuration

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
│   ├── common.*   (users, guild_members, ranks, characters, discord_config)
│   └── patt.*     (campaigns, votes, entries, contest_agent_log)
│
├── PATT Application (Python 3.11+ / FastAPI)
│   ├── API routes
│   ├── Admin pages (Jinja2, server-rendered)
│   ├── Public pages (Jinja2, server-rendered)
│   └── Background tasks (role sync, contest agent)
│
├── PATT-Bot (discord.py, runs within the app process)
│   ├── Role sync (configurable interval)
│   ├── DM dispatch (registration codes)
│   ├── Contest agent (milestone posts)
│   └── Campaign announcements
│
└── Common Services (sv_common Python package)
    ├── sv_common.auth
    ├── sv_common.discord
    ├── sv_common.identity
    └── sv_common.notify
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
- **Role Colors:** Tank (#60a5fa blue), Healer (#4ade80 green), DPS (#f87171 red)
- **Status Colors:** Success (#4ade80), Warning (#fbbf24), Danger (#f87171)
- **Borders:** Subtle (#2a2a2e, #3a3a3e)
- **Fonts:** Cinzel (headers, display), Source Sans Pro (body), JetBrains Mono (code/data)
- **Feel:** WoW-inspired tavern aesthetic — warm, dark, gold accents, stone/metal textures

---

## Directory Structure

This project lives in the existing `Shadowedvaca/PullAllTheThings-site` repo.
The repo already contains legacy files from the GitHub Pages era. New platform
code is added alongside them. During Phase 5, legacy files are moved under the
platform's serving structure so everything is served by FastAPI/Nginx.

### Legacy Files (exist in repo root from GitHub Pages era)

These files were built before the platform existed. They talk to Google Apps Script
and are served as static HTML. Phase 5 migrates them into the platform.

```
PullAllTheThings-site/          (repo root)
├── index.html                  ← Guild landing page (will be replaced)
├── roster.html                 ← Roster signup form (calls Google Apps Script)
├── roster-view.html            ← Public roster display
├── raid-admin.html             ← Officer raid admin dashboard
├── mitos-corner.html           ← Mito's quotes/titles management
├── patt-config.json            ← Client-side config (channel IDs, API URL)
├── google-apps-script.js       ← Backend code (deployed in Google Apps Script, copy kept here)
└── (possibly other files)
```

**Do not delete these files during early phases.** They remain functional until
Phase 5 migrates their data and repoints them to the new API. After migration,
they move to `src/patt/static/legacy/` and are served at their original URL paths.

### New Platform Structure

```
PullAllTheThings-site/          (repo root)
├── CLAUDE.md                          ← YOU ARE HERE
├── TESTING.md                         ← Testing strategy and conventions
├── INDEX.md                           ← Context files quick reference
├── requirements.txt                   ← Python dependencies
├── alembic.ini                        ← Database migration config
├── .env.example                       ← Template for environment variables
│
├── # --- Legacy files (untouched until Phase 5) ---
├── index.html
├── roster.html
├── roster-view.html
├── raid-admin.html
├── mitos-corner.html
├── patt-config.json
├── google-apps-script.js
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
│   │   └── db/
│   │       ├── __init__.py
│   │       ├── engine.py
│   │       ├── models.py
│   │       └── seed.py
│   │
│   └── patt/                          ← PATT application package
│       ├── __init__.py
│       ├── app.py
│       ├── config.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── auth_routes.py
│       │   ├── campaign_routes.py
│       │   ├── vote_routes.py
│       │   ├── admin_routes.py
│       │   └── guild_routes.py
│       ├── pages/
│       │   ├── __init__.py
│       │   ├── vote_pages.py
│       │   ├── admin_pages.py
│       │   └── public_pages.py
│       ├── templates/
│       │   ├── base.html
│       │   ├── admin/
│       │   ├── vote/
│       │   └── public/
│       ├── static/
│       │   ├── css/
│       │   ├── js/
│       │   └── legacy/               ← Legacy HTML files moved here in Phase 5
│       ├── services/
│       │   ├── __init__.py
│       │   ├── campaign_service.py
│       │   ├── vote_service.py
│       │   └── contest_agent.py
│       └── bot/
│           ├── __init__.py
│           └── contest_cog.py
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── regression/
│
├── deploy/
│   ├── nginx/
│   │   └── pullallthething.com.conf
│   ├── systemd/
│   │   └── patt.service
│   └── setup_postgres.sql
│
├── data/
│   ├── contest_agent_personality.md
│   └── seed/
│       └── ranks.json
│
├── scripts/
│   ├── migrate_sheets.py
│   └── run_dev.py
│
├── docs/
│   ├── DISCORD-BOT-SETUP.md
│   ├── OPERATIONS.md
│   └── shadowedvaca-conversion-plan.md
│
└── phases/                            ← Phase plans (reference only, not deployed)
    ├── PHASE-0.md
    ├── PHASE-1.md
    ├── ...
    └── PHASE-7.md
```

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

# Google (for Sheets migration and Drive image URLs)
GOOGLE_APPS_SCRIPT_URL=your-existing-script-url

# Server
APP_ENV=production
APP_PORT=8100
APP_HOST=0.0.0.0
```

---

## Database Schema

### common schema

```sql
-- Guild rank levels (admin-configurable)
CREATE TABLE common.guild_ranks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    level INTEGER NOT NULL UNIQUE,  -- numeric ordering, higher = more authority
    discord_role_id VARCHAR(20),     -- maps to Discord server role
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- Default seed: Initiate(1), Member(2), Veteran(3), Officer(4), Guild Leader(5)

-- Registered users (auth records)
CREATE TABLE common.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    phone VARCHAR(20),
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Guild members (identity records, linked to auth)
CREATE TABLE common.guild_members (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES common.users(id) UNIQUE,  -- null until registered
    discord_id VARCHAR(20) UNIQUE,
    discord_username VARCHAR(100) NOT NULL,
    display_name VARCHAR(100),
    rank_id INTEGER REFERENCES common.guild_ranks(id) NOT NULL,
    rank_source VARCHAR(20) DEFAULT 'manual',  -- 'manual' or 'discord_sync'
    registered_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- WoW characters
CREATE TABLE common.characters (
    id SERIAL PRIMARY KEY,
    member_id INTEGER REFERENCES common.guild_members(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,
    realm VARCHAR(50) NOT NULL,
    class VARCHAR(30) NOT NULL,
    spec VARCHAR(30),
    role VARCHAR(20) NOT NULL,  -- tank, healer, melee_dps, ranged_dps
    main_alt VARCHAR(10) DEFAULT 'main',  -- 'main' or 'alt'
    armory_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, realm)
);

-- Discord server configuration
CREATE TABLE common.discord_config (
    id SERIAL PRIMARY KEY,
    guild_discord_id VARCHAR(20) NOT NULL,
    role_sync_interval_hours INTEGER DEFAULT 24,
    default_announcement_channel_id VARCHAR(20),
    last_role_sync_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Invite codes for registration
CREATE TABLE common.invite_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(20) NOT NULL UNIQUE,
    member_id INTEGER REFERENCES common.guild_members(id),  -- who it's for
    created_by INTEGER REFERENCES common.guild_members(id),  -- admin who created it
    used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### patt schema

```sql
-- Voting campaigns
CREATE TABLE patt.campaigns (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    type VARCHAR(20) NOT NULL DEFAULT 'ranked_choice',  -- ranked_choice, approval
    picks_per_voter INTEGER DEFAULT 3,
    min_rank_to_vote INTEGER NOT NULL,       -- guild_ranks.level minimum to cast a vote
    min_rank_to_view INTEGER,                -- null = public, otherwise rank level minimum
    start_at TIMESTAMPTZ NOT NULL,
    duration_hours INTEGER NOT NULL,
    status VARCHAR(20) DEFAULT 'draft',      -- draft, live, closed, archived
    early_close_if_all_voted BOOLEAN DEFAULT TRUE,
    discord_channel_id VARCHAR(20),          -- channel for bot announcements
    created_by INTEGER REFERENCES common.guild_members(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Things being voted on within a campaign
CREATE TABLE patt.campaign_entries (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    image_url TEXT,                           -- Google Drive public share link
    sort_order INTEGER DEFAULT 0,
    associated_member_id INTEGER REFERENCES common.guild_members(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Individual vote records
CREATE TABLE patt.votes (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    member_id INTEGER REFERENCES common.guild_members(id),
    entry_id INTEGER REFERENCES patt.campaign_entries(id),
    rank INTEGER NOT NULL,                   -- 1=first pick, 2=second, 3=third
    voted_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(campaign_id, member_id, rank)     -- one pick per rank per voter
);

-- Cached results (populated on campaign close or on-demand)
CREATE TABLE patt.campaign_results (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    entry_id INTEGER REFERENCES patt.campaign_entries(id),
    first_place_count INTEGER DEFAULT 0,
    second_place_count INTEGER DEFAULT 0,
    third_place_count INTEGER DEFAULT 0,
    weighted_score INTEGER DEFAULT 0,        -- 3*first + 2*second + 1*third
    final_rank INTEGER,
    calculated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Contest agent message log
CREATE TABLE patt.contest_agent_log (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,         -- launch, lead_change, milestone, final_stretch, early_close, results
    message TEXT NOT NULL,
    discord_message_id VARCHAR(20),
    posted_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Guild Ranks (Default Configuration)

| Level | Name | Discord Role | Description |
|-------|------|-------------|-------------|
| 1 | Initiate | (mapped to Discord role) | New members, proving reliability and social fit |
| 2 | Member | (mapped to Discord role) | Regular attendees who engage with the guild |
| 3 | Veteran | (mapped to Discord role) | Key performers, helps others, brings guild together |
| 4 | Officer | (mapped to Discord role) | Guild leadership team |
| 5 | Guild Leader | (mapped to Discord role) | Mike (Trog) |

Ranks drive permissions: voting eligibility, content visibility, raid priority.
Discord is the source of truth — the bot syncs role changes on a configurable interval.

---

## Key Guild Members (Officers)

| Display Name | Discord Username | Character | Class/Spec | Role |
|-------------|-----------------|-----------|------------|------|
| Trog | (Mike) | Trogmoon | Balance Druid | Guild Leader |
| Rocket | | (special chars in name) | Hunter (Engineering) | Officer |
| Mito | | | Paladin (Ret DPS) | Officer |
| Shodoom | | | Paladin (Holy) | Officer |
| Skate | | Skatefarm | Feral Druid | Officer |

---

## Image Hosting Pattern

Images are hosted on Google Drive with public sharing ("Anyone with the link can view").
The URL pattern used in code:
```
https://drive.google.com/uc?id={FILE_ID}&export=view
```
This renders the image directly without the Drive UI wrapper.
Images for the art vote live at: `J:\Shared drives\Salt All The Things\Marketing\Pull All The Things`

---

## Current Build Status

> **UPDATE THIS SECTION AT THE END OF EVERY PHASE**

### Completed Phases
- Phase 0 through 7: Platform complete

### Current Phase
- All phases done. Platform is live and ready.

### What Exists
- sv_common.identity package: ranks, members, characters CRUD (`src/sv_common/identity/`)
- sv_common.auth package: passwords (bcrypt), JWT (PyJWT), invite codes (`src/sv_common/auth/`)
- sv_common.discord package: bot client, role sync, DM dispatch, channel posting (`src/sv_common/discord/`)
- Auth API: `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`
- Auth middleware: `get_current_member()`, `require_rank(level)` deps in `src/patt/deps.py`
- Cookie-based auth for page routes: `get_page_member()`, `require_page_rank(level)` in deps.py
- Admin API: `/api/v1/admin/*` — all routes protected (Officer+ rank required)
- Public API: `/api/v1/guild/ranks`, `/api/v1/guild/roster` (public, no auth required)
- Discord bot starts as background task during FastAPI lifespan (skipped if no token configured)
- Campaign service: `src/patt/services/campaign_service.py` — full lifecycle (draft→live→closed)
- Vote service: `src/patt/services/vote_service.py` — cast votes, validate, calculate results
- Campaign API (admin): `POST/PATCH /api/v1/admin/campaigns`, entries, activate, close, stats
- Campaign API (vote): `POST /api/v1/campaigns/{id}/vote`, `GET /api/v1/campaigns/{id}/my-vote`
- Campaign API (public): `GET /api/v1/campaigns`, `/api/v1/campaigns/{id}`, `/results`, `/results/live`
- Background task: campaign status checker (auto-activate, auto-close, early-close) every 60s
- **Phase 4 Web UI (page routes + templates):**
  - Page routes: `src/patt/pages/` — auth_pages.py, vote_pages.py, admin_pages.py, public_pages.py
  - Templates: `src/patt/templates/` — auth/login.html, auth/register.html, vote/campaign.html,
    vote/_results_panel.html, admin/campaigns.html, admin/campaign_edit.html, admin/roster.html,
    public/index.html, public/404.html, public/403.html
  - JS: `src/patt/static/js/` — vote-interaction.js, countdown.js, admin-forms.js
  - Cookie auth: HTTP-only `patt_token` cookie (30-day), set on login/register, cleared on logout
  - Shared Jinja2 instance: `src/patt/templating.py`
  - Integration tests: `tests/integration/test_page_rendering.py`
- **Phase 5 Legacy Migration:**
  - New DB tables: `common.member_availability`, `patt.mito_quotes`, `patt.mito_titles`
  - Alembic migration: `alembic/versions/0003_phase5_legacy_tables.py`
  - Legacy API: `GET /api/v1/guild/roster-data`, `POST /api/v1/guild/roster-submit`,
    `GET/POST /api/v1/guild/availability`, full Mito CRUD at `/api/v1/guild/mito/*`
  - Legacy HTML moved to `src/patt/static/legacy/` (roster.html, roster-view.html,
    raid-admin.html, mitos-corner.html, patt-config.json) — served at original URL paths by FastAPI
  - Legacy HTML JS updated to call new API instead of Google Apps Script
  - Nginx legacy file block removed — all requests now go through FastAPI
  - Migration script: `scripts/migrate_sheets.py` — run once to import Sheets data
  - Field mapping docs: `docs/MIGRATION-MAP.md`
  - Tests: `tests/integration/test_legacy_api.py`, `tests/unit/test_migration.py`
- **Phase 6 Contest Agent:**
  - DB: `agent_enabled` (bool) + `agent_chattiness` (quiet/normal/hype) on `patt.campaigns`
  - Alembic migration: `alembic/versions/0004_phase6_agent_chattiness.py`
  - Contest agent service: `src/patt/services/contest_agent.py`
    - Pure functions: `detect_milestone()`, `generate_message()`, `get_allowed_events()`
    - Background task: `run_contest_agent()` — checks every 5 minutes
    - Milestone triggers: launch, first_vote, lead_change, 25/50/75%, final_stretch, last_call, all_voted, campaign_closed
    - Chattiness levels control which triggers are active per campaign
    - Deduplication via `contest_agent_log` — events never re-posted
    - Lead change tracking: current leader stored in log message as `leader_id:{id}`
  - Discord channel posting: `src/sv_common/discord/channels.py` — `post_embed_to_channel()`
  - Admin form updated: agent_enabled checkbox + chattiness dropdown in campaign_edit.html
  - Admin pages updated: handles agent_enabled and agent_chattiness form fields
  - Personality reference: `data/contest_agent_personality.md`
  - Tests: `tests/unit/test_contest_agent.py` (36 tests), `tests/integration/test_contest_agent_flow.py`
  - Background task started in `app.py` lifespan alongside campaign_checker
- **Phase 7 Polish & Launch:**
  - End-to-end regression suite: `tests/regression/test_full_platform.py` — covers full auth+campaign+vote+results+agent flow
  - Art vote setup script: `scripts/setup_art_vote.py` — run once with Drive file IDs to configure the campaign
  - Error pages: `src/patt/templates/public/404.html`, `500.html` — styled with PATT theme
  - FastAPI exception handlers for 404 + 500 in `app.py`
  - Security middleware: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy headers (`SecurityHeadersMiddleware` in `app.py`)
  - Rate limiting: login endpoint rate-limited to 10 attempts/60s per IP (in-memory, `app.py`)
  - Secure cookie flag: `httponly=True, secure=True` (production), `samesite="lax"` — in `auth_pages.py`
  - Visual polish: score bars animate on page load (CSS `@keyframes score-bar-grow`), result rows fade-in with stagger, medal emojis (🥇🥈🥉) in results panel
  - Operations guide: `docs/OPERATIONS.md` — how Mike operates the platform independently

### Ready to Launch
- Salt All The Things Profile Pic Contest: run `scripts/setup_art_vote.py` with Drive file IDs to configure
- Mike activates the campaign when ready

### What Exists on the Server
- Nginx running, serving shadowedvaca.com as static files (nginx config at deploy/nginx/)
- PostgreSQL, FastAPI (uvicorn port 8100), systemd patt.service — all running
- All migrations applied through 0004 (agent_enabled, agent_chattiness on campaigns)
- Google Sheets data fully migrated (20 members, 30 chars, 21 Mito quotes, 13 Mito titles)
- Test framework operational — `pytest tests/unit/ -v` passes 228/252 (24 skip when no DB); regression suite at `tests/regression/` requires live DB
- **CI/CD:** GitHub Actions workflow at `.github/workflows/deploy.yml` — auto-deploys on every push to main
  - SSH key: `DEPLOY_SSH_KEY` secret in GitHub repo (ed25519 key authorized on server)
  - Deploy steps: git pull → pip install → alembic upgrade → systemctl restart → health check

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
- Database tables: snake_case, plural (guild_members, campaign_entries)
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
