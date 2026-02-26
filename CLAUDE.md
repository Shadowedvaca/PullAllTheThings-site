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
- **PATTSync addon** — WoW Lua addon + companion app for guild/officer note sync

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
│   ├── common.*         (users, guild_ranks, discord_config, invite_codes, member_availability)
│   ├── patt.*           (campaigns, votes, entries, results, contest_agent_log, mito content)
│   └── guild_identity.* (players, wow_characters, discord_users, player_characters,
│                          classes, specializations, roles, audit_issues, sync_log,
│                          onboarding_sessions, professions, profession_tiers,
│                          recipes, character_recipes, crafting_sync_config)
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
│                              crafting sync, crafting service)
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
│   │       ├── crafting_sync.py       ← Phase 2.8: Profession sync with adaptive cadence
│   │       ├── crafting_service.py    ← Phase 2.8: Data access for crafting queries
│   │       ├── discord_sync.py
│   │       ├── addon_processor.py
│   │       ├── identity_engine.py
│   │       ├── integrity_checker.py
│   │       ├── reporter.py
│   │       ├── scheduler.py
│   │       ├── db_sync.py
│   │       ├── sync_logger.py
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   ├── routes.py
│   │       │   └── crafting_routes.py ← Phase 2.8: /api/crafting/* routes
│   │       └── onboarding/
│   │           ├── __init__.py
│   │           ├── conversation.py
│   │           ├── provisioner.py
│   │           ├── deadline_checker.py
│   │           └── commands.py
│   │
│   └── patt/                          ← PATT application package
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
│       │       └── crafting_corner.html  ← Phase 2.8
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
│   └── PATTSync/
│       ├── PATTSync.toc
│       ├── PATTSync.lua
│       └── README.md
│
├── companion_app/
│   ├── patt_sync_watcher.py
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
│   ├── migrate_sheets.py
│   ├── migrate_to_players.py          ← Phase 2.7 data migration
│   ├── setup_art_vote.py
│   └── run_dev.py
│
├── docs/
│   ├── DISCORD-BOT-SETUP.md
│   ├── OPERATIONS.md
│   └── shadowedvaca-conversion-plan.md
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
They are served by FastAPI from `src/patt/static/legacy/` at their original URLs.

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

# Google (for Sheets migration and Drive image URLs)
GOOGLE_APPS_SCRIPT_URL=your-existing-script-url

# Server
APP_ENV=production
APP_PORT=8100
APP_HOST=0.0.0.0

# Blizzard API (Phase 2.5)
BLIZZARD_CLIENT_ID=your-blizzard-client-id
BLIZZARD_CLIENT_SECRET=your-blizzard-client-secret

# Guild sync config (Phase 2.5)
PATT_GUILD_REALM_SLUG=senjin
PATT_GUILD_NAME_SLUG=pull-all-the-things
PATT_AUDIT_CHANNEL_ID=your-discord-audit-channel-id

# Companion app API key (Phase 2.5)
PATT_API_KEY=generate-a-strong-random-key

# Crafting Corner (Phase 2.8)
PATT_CRAFTERS_CORNER_CHANNEL_ID=your-discord-crafters-corner-channel-id
```

---

## Database Schema

> **Phase 2.7 target schema + Phase 2.8 additions.** Clean 3NF design with players as the core entity.
> Reference tables normalize WoW classes, specializations, and combat roles.
> Bridge table tracks character ownership. Direct 1:1 FKs for Discord and website accounts.

### guild_identity schema — Reference Tables

```sql
-- Combat roles (4 values)
CREATE TABLE guild_identity.roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) NOT NULL UNIQUE  -- Tank, Healer, Melee DPS, Ranged DPS
);

-- WoW classes (13 current)
CREATE TABLE guild_identity.classes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(30) NOT NULL UNIQUE,  -- Death Knight, Druid, Evoker, etc.
    color_hex VARCHAR(7)               -- Blizzard class color for UI (#FF7C0A etc.)
);

-- Class + Spec combinations (~39 current, grows when Blizzard adds specs)
-- PK is auto-increment id. UNIQUE on (class_id, name) — 'Frost' appears twice (DK + Mage)
CREATE TABLE guild_identity.specializations (
    id SERIAL PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES guild_identity.classes(id),
    name VARCHAR(50) NOT NULL,
    default_role_id INTEGER NOT NULL REFERENCES guild_identity.roles(id),
    wowhead_slug VARCHAR(50),  -- For comp export URLs: 'balance-druid', 'frost-death-knight'
    UNIQUE(class_id, name)
);
```

### guild_identity schema — External Data (from APIs, never manually edited)

```sql
-- WoW characters from guild roster (Blizzard API + PATTSync addon)
-- Ownership tracked via player_characters bridge, NOT a direct person_id FK
CREATE TABLE guild_identity.wow_characters (
    id SERIAL PRIMARY KEY,
    character_name VARCHAR(50) NOT NULL,
    realm_slug VARCHAR(50) NOT NULL,
    realm_name VARCHAR(100),
    class_id INTEGER REFERENCES guild_identity.classes(id),
    active_spec_id INTEGER REFERENCES guild_identity.specializations(id),
    level INTEGER,
    item_level INTEGER,
    guild_rank_id INTEGER REFERENCES common.guild_ranks(id),
    last_login_timestamp BIGINT,
    guild_note TEXT,
    officer_note TEXT,
    addon_last_seen TIMESTAMPTZ,
    blizzard_last_sync TIMESTAMPTZ,
    addon_last_sync TIMESTAMPTZ,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    removed_at TIMESTAMPTZ,
    UNIQUE(character_name, realm_slug)
);

-- Discord server members tracked by the bot
CREATE TABLE guild_identity.discord_users (
    id SERIAL PRIMARY KEY,
    discord_id VARCHAR(25) NOT NULL UNIQUE,
    username VARCHAR(50) NOT NULL,
    display_name VARCHAR(50),
    highest_guild_role VARCHAR(30),
    all_guild_roles TEXT[],
    joined_server_at TIMESTAMPTZ,
    last_sync TIMESTAMPTZ,
    is_present BOOLEAN DEFAULT TRUE,
    removed_at TIMESTAMPTZ,
    first_seen TIMESTAMPTZ DEFAULT NOW()
);
```

### guild_identity schema — Core Entities

```sql
-- THE PLAYER — the central identity entity
CREATE TABLE guild_identity.players (
    id SERIAL PRIMARY KEY,
    display_name VARCHAR(100) NOT NULL,
    discord_user_id INTEGER UNIQUE REFERENCES guild_identity.discord_users(id),
    website_user_id INTEGER UNIQUE REFERENCES common.users(id),
    guild_rank_id INTEGER REFERENCES common.guild_ranks(id),
    guild_rank_source VARCHAR(20),       -- 'wow_character', 'discord', 'admin_override'
    main_character_id INTEGER REFERENCES guild_identity.wow_characters(id),
    main_spec_id INTEGER REFERENCES guild_identity.specializations(id),
    offspec_character_id INTEGER REFERENCES guild_identity.wow_characters(id),
    offspec_spec_id INTEGER REFERENCES guild_identity.specializations(id),
    crafting_notifications_enabled BOOLEAN DEFAULT FALSE,  -- Phase 2.8: opt-in for @mentions
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Character ownership bridge
CREATE TABLE guild_identity.player_characters (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL UNIQUE REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, character_id)
);
```

### guild_identity schema — Crafting Corner (Phase 2.8)

```sql
-- Profession reference (Alchemy, Blacksmithing, Cooking, etc.)
CREATE TABLE guild_identity.professions (
    id SERIAL PRIMARY KEY,
    blizzard_id INTEGER NOT NULL UNIQUE,
    name VARCHAR(50) NOT NULL UNIQUE,
    is_primary BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Expansion-specific tiers (e.g., "Khaz Algar Blacksmithing")
CREATE TABLE guild_identity.profession_tiers (
    id SERIAL PRIMARY KEY,
    profession_id INTEGER NOT NULL REFERENCES guild_identity.professions(id) ON DELETE CASCADE,
    blizzard_tier_id INTEGER NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    expansion_name VARCHAR(50),
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(profession_id, blizzard_tier_id)
);

-- Recipe reference (spell ID → Wowhead URL)
CREATE TABLE guild_identity.recipes (
    id SERIAL PRIMARY KEY,
    blizzard_spell_id INTEGER NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    profession_id INTEGER NOT NULL REFERENCES guild_identity.professions(id) ON DELETE CASCADE,
    tier_id INTEGER NOT NULL REFERENCES guild_identity.profession_tiers(id) ON DELETE CASCADE,
    wowhead_url VARCHAR(300) GENERATED ALWAYS AS (
        'https://www.wowhead.com/spell=' || blizzard_spell_id
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Character ↔ Recipe junction
CREATE TABLE guild_identity.character_recipes (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    recipe_id INTEGER NOT NULL REFERENCES guild_identity.recipes(id) ON DELETE CASCADE,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(character_id, recipe_id)
);

-- Crafting sync configuration (single row)
CREATE TABLE guild_identity.crafting_sync_config (
    id SERIAL PRIMARY KEY,
    current_cadence VARCHAR(10) NOT NULL DEFAULT 'weekly',
    cadence_override_until TIMESTAMPTZ,
    expansion_name VARCHAR(50),           -- e.g., "The War Within"
    season_number INTEGER,                -- e.g., 1, 2, 3
    season_start_date TIMESTAMPTZ,
    is_first_season BOOLEAN DEFAULT FALSE,
    last_sync_at TIMESTAMPTZ,
    next_sync_at TIMESTAMPTZ,
    last_sync_duration_seconds FLOAT,
    last_sync_characters_processed INTEGER,
    last_sync_recipes_found INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- Display name derived in code: "{expansion_name} Season {season_number}"
```

### guild_identity schema — System Tables

```sql
-- Integrity issues found by automated checks
CREATE TABLE guild_identity.audit_issues (
    id SERIAL PRIMARY KEY,
    issue_type VARCHAR(50) NOT NULL,
    severity VARCHAR(10) DEFAULT 'info',
    wow_character_id INTEGER REFERENCES guild_identity.wow_characters(id),
    discord_member_id INTEGER REFERENCES guild_identity.discord_users(id),
    summary TEXT NOT NULL,
    details JSONB,
    issue_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(50),
    notified_at TIMESTAMPTZ,
    UNIQUE(issue_hash, resolved_at)
);

-- Sync operation logs
CREATE TABLE guild_identity.sync_log (
    id SERIAL PRIMARY KEY,
    source VARCHAR(30) NOT NULL,         -- blizzard_api, addon_upload, discord_sync, crafting_sync
    status VARCHAR(20) NOT NULL,
    characters_found INTEGER,
    characters_updated INTEGER,
    characters_new INTEGER,
    characters_removed INTEGER,
    error_message TEXT,
    duration_seconds FLOAT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Onboarding sessions (Phase 2.6 — built, not yet activated)
CREATE TABLE guild_identity.onboarding_sessions (
    id SERIAL PRIMARY KEY,
    discord_member_id INTEGER NOT NULL REFERENCES guild_identity.discord_users(id) ON DELETE CASCADE,
    discord_id VARCHAR(25) NOT NULL UNIQUE,
    state VARCHAR(30) NOT NULL DEFAULT 'awaiting_dm',
    reported_main_name VARCHAR(50),
    reported_main_realm VARCHAR(100),
    reported_alt_names TEXT[],
    is_in_guild BOOLEAN,
    verification_attempts INTEGER DEFAULT 0,
    last_verification_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    verified_player_id INTEGER REFERENCES guild_identity.players(id),
    website_invite_sent BOOLEAN DEFAULT FALSE,
    website_invite_code VARCHAR(50),
    roster_entries_created BOOLEAN DEFAULT FALSE,
    discord_role_assigned BOOLEAN DEFAULT FALSE,
    dm_sent_at TIMESTAMPTZ,
    dm_completed_at TIMESTAMPTZ,
    deadline_at TIMESTAMPTZ,
    escalated_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### common schema (infrastructure — shared across sites)

```sql
CREATE TABLE common.guild_ranks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    level INTEGER NOT NULL UNIQUE,
    discord_role_id VARCHAR(20),
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE common.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    phone VARCHAR(20),
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE common.discord_config (
    id SERIAL PRIMARY KEY,
    guild_discord_id VARCHAR(20) NOT NULL,
    role_sync_interval_hours INTEGER DEFAULT 24,
    default_announcement_channel_id VARCHAR(20),
    last_role_sync_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE common.invite_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(20) NOT NULL UNIQUE,
    player_id INTEGER REFERENCES guild_identity.players(id),
    created_by_player_id INTEGER REFERENCES guild_identity.players(id),
    used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    generated_by VARCHAR(30) DEFAULT 'manual',
    onboarding_session_id INTEGER REFERENCES guild_identity.onboarding_sessions(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE common.member_availability (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
    day_of_week VARCHAR(10) NOT NULL,
    available BOOLEAN DEFAULT TRUE,
    notes TEXT,
    auto_signup BOOLEAN DEFAULT FALSE,
    wants_reminders BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, day_of_week)
);
```

### patt schema (features)

```sql
CREATE TABLE patt.campaigns (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    type VARCHAR(20) NOT NULL DEFAULT 'ranked_choice',
    picks_per_voter INTEGER DEFAULT 3,
    min_rank_to_vote INTEGER NOT NULL,
    min_rank_to_view INTEGER,
    start_at TIMESTAMPTZ NOT NULL,
    duration_hours INTEGER NOT NULL,
    status VARCHAR(20) DEFAULT 'draft',
    early_close_if_all_voted BOOLEAN DEFAULT TRUE,
    discord_channel_id VARCHAR(20),
    agent_enabled BOOLEAN DEFAULT TRUE,
    agent_chattiness VARCHAR(10) DEFAULT 'normal',
    created_by_player_id INTEGER REFERENCES guild_identity.players(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patt.campaign_entries (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    image_url TEXT,
    sort_order INTEGER DEFAULT 0,
    player_id INTEGER REFERENCES guild_identity.players(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patt.votes (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    player_id INTEGER REFERENCES guild_identity.players(id),
    rankings JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(campaign_id, player_id)
);

CREATE TABLE patt.campaign_results (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE UNIQUE,
    results JSONB NOT NULL,
    total_votes INTEGER,
    calculated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patt.contest_agent_log (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    message_sent TEXT,
    discord_message_id VARCHAR(25),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patt.mito_quotes (
    id SERIAL PRIMARY KEY,
    quote TEXT NOT NULL,
    context TEXT,
    added_by_player_id INTEGER REFERENCES guild_identity.players(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patt.mito_titles (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    added_by_player_id INTEGER REFERENCES guild_identity.players(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Operations & Deployment

- **Tests:** 228+ pass (24 skip when no DB); regression suite at `tests/regression/` requires live DB
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
- Phase 3.0A: Matching transparency — link_source/confidence on player_characters, coverage dashboard
- Phase 3.0B: Iterative rule runner — pluggable matching rules, progressive discovery, per-rule results UI
- Phase 3.0C: Drift Detection — note_mismatch, link_contradicts_note, duplicate_discord, stale_discord_link rules; drift_scanner.py orchestrator; drift panel on Data Quality page
- Phase 2.6: Onboarding system (built but NOT activated — on_member_join not wired)
- Phase 2.7: Data Model Migration — Clean 3NF rebuild (complete)
  - `common.guild_members` and `common.characters` eliminated from all code
  - Reference tables added: `guild_identity.roles`, `classes`, `specializations`
  - `guild_identity.persons` renamed to `players` with main/offspec FKs, discord_user_id, website_user_id
  - `guild_identity.player_characters` bridge table added
  - All FKs repointed from guild_members → players across models, services, routes, templates, tests
  - Alembic migration 0007 created; data migration script at `scripts/migrate_to_players.py`
  - 202 unit tests pass, 59 skipped (DB-dependent or legacy script tests)

### Current Phase
- Phase 3.0C: Drift Detection — **COMPLETE**

### What Exists
- sv_common.identity package: ranks, players, characters CRUD (`src/sv_common/identity/`)
- sv_common.auth package: passwords (bcrypt), JWT (PyJWT), invite codes (`src/sv_common/auth/`)
- sv_common.discord package: bot client, role sync (DiscordUser+Player), DM dispatch, channel posting (`src/sv_common/discord/`)
- sv_common.guild_sync package: Blizzard API client, identity engine, integrity checker, Discord sync, addon processor, scheduler, crafting sync + service, rules registry + mitigations engine, attribution functions (Phase 3.0A), matching_rules package (Phase 3.0B), **drift_scanner.py + detect_link_note_contradictions + detect_duplicate_discord_links (Phase 3.0C)**
- Crafting Corner: `/crafting-corner` public page, `/api/crafting/*` routes, profession/recipe DB tables, adaptive sync cadence
- Admin Crafting Sync page: `/admin/crafting-sync` — force refresh, season config, sync stats
- Data Quality Engine: `rules.py` (8-rule registry including 3 drift rules), `mitigations.py` (targeted fix functions + `run_auto_mitigations`), refactored `integrity_checker.py` (named detect functions + drift detectors), `drift_scanner.py` (orchestrates drift rules)
- Admin Data Quality page: `/admin/data-quality` — rule stats, open counts, recent findings, manual scan/fix triggers; **Drift Detection panel** with per-rule status, Run Drift Scan button (`POST /admin/drift/scan`, `GET /admin/drift/summary`)
- Auth API: `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`
- Auth middleware: `get_current_player()`, `require_rank(level)` deps in `src/patt/deps.py`
- Cookie-based auth for page routes: `get_page_player()`, `require_page_rank(level)` in deps.py
- Admin API: `/api/v1/admin/*` — all routes protected (Officer+ rank required)
- Public API: `/api/v1/guild/ranks`, `/api/v1/guild/roster` (public, no auth required)
- Discord bot starts as background task during FastAPI lifespan (skipped if no token configured)
- Campaign service: full lifecycle (draft→live→closed) with ranked-choice voting
- Contest agent: Discord milestone posts, auto-activate/close campaigns
- Onboarding system: conversation.py, provisioner.py, deadline_checker.py, commands.py (dormant)
- PATTSync WoW addon + companion app (functional, syncing guild notes)
- Full regression test suite
- Web UI: login, register, vote, admin campaigns, admin roster, public landing page
- Art vote campaign configured and previously run

### Key Data State
- `guild_identity.players`: **EMPTY** — run `scripts/migrate_to_players.py` to populate from guild_members
- `guild_identity.wow_characters`: ~320 rows from Blizzard API syncs
- `guild_identity.discord_users`: populated from Discord bot syncs
- `common.guild_members`: ~40 rows (legacy — source for migration script)
- `common.characters`: legacy data (source for migration script, to be dropped post-migration)
- Reference tables (roles, classes, specializations): seeded via Alembic migration 0007

### Pending / Known Gaps
- `scripts/migrate_to_players.py`: run on prod to migrate guild_members → players
- `guild_identity.identity_engine`: still references pre-2.7 schema (persons/discord_members/identity_links) — tests skipped, needs update
- `scripts/migrate_sheets.py`: legacy Phase 5 script still imports removed models — tests skipped
