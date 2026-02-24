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
- **Discord integration** — bot for role sync, DMs, contest updates, announcements
- **Admin tools** — campaign management, roster management, rank configuration, reference table editor
- **Blizzard API integration** — guild roster sync, character profiles, item levels
- **PATTSync addon** — WoW Lua addon + companion app for guild/officer note sync
- **Scheduling system** — weighted availability with timezone-aware time windows, rank-based scoring

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
│   ├── common.*         (users, guild_ranks, discord_config, invite_codes)
│   ├── patt.*           (campaigns, votes, entries, results, contest_agent_log,
│   │                     player_availability, raid_seasons, raid_events,
│   │                     raid_attendance, mito content)
│   └── guild_identity.* (players, wow_characters, discord_users, player_characters,
│                          classes, specializations, roles, audit_issues, sync_log,
│                          onboarding_sessions)
│
├── PATT Application (Python 3.11+ / FastAPI)
│   ├── API routes
│   ├── Admin pages (Jinja2, server-rendered)
│   ├── Public pages (Jinja2, server-rendered)
│   └── Background tasks (role sync, contest agent, Blizzard sync)
│
├── PATT-Bot (discord.py, runs within the app process)
│   ├── Role sync (configurable interval)
│   ├── DM dispatch (registration codes)
│   ├── Contest agent (milestone posts)
│   ├── Campaign announcements
│   ├── Discord member sync
│   └── Onboarding conversation flow (built, not yet activated)
│
├── Common Services (sv_common Python package)
│   ├── sv_common.auth
│   ├── sv_common.discord
│   ├── sv_common.identity
│   ├── sv_common.notify
│   └── sv_common.guild_sync (Blizzard API, identity engine, addon processor, scheduler)
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
│   │   │   ├── members.py            ← Player CRUD (renamed from guild_members in 2.7)
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
│   │       ├── discord_sync.py
│   │       ├── addon_processor.py
│   │       ├── identity_engine.py
│   │       ├── integrity_checker.py
│   │       ├── reporter.py
│   │       ├── scheduler.py
│   │       ├── db_sync.py
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
│   └── seed/
│       └── ranks.json
│
├── reference/                         ← Phase plans and context docs
│   ├── archive/                       ← Completed phase instructions
│   ├── PHASE_2_5_OVERVIEW.md
│   ├── PHASE_2_6_ONBOARDING.md
│   ├── PHASE_2_8_SCHEDULING_AND_ATTENDANCE.md
│   └── INDEX.md
│
├── docs/
│   ├── OPERATIONS.md
│   ├── DISCORD-BOT-SETUP.md
│   ├── RAID-HELPER-API-KEY.md
│   └── shadowedvaca-conversion-plan.md
│
├── memory/
│   └── MEMORY.md
│
└── scripts/
    └── migrate_to_players.py          ← Phase 2.7 data migration (already run)
```

---

## Database Schema

Three PostgreSQL schemas, all in `patt_db`.

### guild_identity schema — Reference Tables

```sql
-- Combat roles (Tank, Healer, Melee DPS, Ranged DPS)
CREATE TABLE guild_identity.roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(30) NOT NULL UNIQUE,
    display_name VARCHAR(30) NOT NULL
);

-- WoW classes (13 classes)
CREATE TABLE guild_identity.classes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(30) NOT NULL UNIQUE,
    color_hex VARCHAR(7)
);

-- WoW specializations (~39 specs, one per class+spec combo)
CREATE TABLE guild_identity.specializations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(30) NOT NULL,
    class_id INTEGER NOT NULL REFERENCES guild_identity.classes(id),
    default_role_id INTEGER NOT NULL REFERENCES guild_identity.roles(id),
    UNIQUE(name, class_id)
);
```

### guild_identity schema — Discord & WoW Data

```sql
-- Discord server members (synced from bot)
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

-- WoW characters from Blizzard API + PATTSync addon
CREATE TABLE guild_identity.wow_characters (
    id SERIAL PRIMARY KEY,
    character_name VARCHAR(50) NOT NULL,
    realm_slug VARCHAR(50) NOT NULL,
    blizzard_id BIGINT UNIQUE,
    class_id INTEGER REFERENCES guild_identity.classes(id),
    active_spec_id INTEGER REFERENCES guild_identity.specializations(id),
    level INTEGER,
    item_level INTEGER,
    guild_rank_id INTEGER REFERENCES common.guild_ranks(id),
    achievement_points INTEGER,
    last_login_at TIMESTAMPTZ,
    profile_json JSONB,
    addon_note TEXT,
    addon_officer_note TEXT,
    addon_last_sync TIMESTAMPTZ,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_api_sync TIMESTAMPTZ,
    removed_at TIMESTAMPTZ,
    UNIQUE(character_name, realm_slug)
);
```

### guild_identity schema — Core Entities

```sql
-- THE PLAYER — the central identity entity
-- Links to Discord (1:1), website user (1:1), characters (1:N via bridge)
-- Main/off-spec start NULL, set by the player on first login
-- Guild rank derived: highest character rank → Discord fallback → admin override
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
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    timezone VARCHAR(50) NOT NULL DEFAULT 'America/Chicago',
    auto_invite_events BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Character ownership bridge: which characters belong to which player
CREATE TABLE guild_identity.player_characters (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL UNIQUE REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, character_id)
);
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
    source VARCHAR(30) NOT NULL,
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
-- Guild rank definitions + Discord role mappings
CREATE TABLE common.guild_ranks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    level INTEGER NOT NULL UNIQUE,       -- 1 = Initiate (lowest), 5 = Guild Leader (highest)
    discord_role_id VARCHAR(20),
    description TEXT,
    scheduling_weight INTEGER NOT NULL DEFAULT 0,  -- 0=Initiate(ignored), 1=Member, 3=Veteran, 5=Officer/GL
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Website login accounts (auth only, no guild data)
CREATE TABLE common.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    phone VARCHAR(20),
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Discord bot configuration (single row)
CREATE TABLE common.discord_config (
    id SERIAL PRIMARY KEY,
    guild_discord_id VARCHAR(20) NOT NULL,
    role_sync_interval_hours INTEGER DEFAULT 24,
    default_announcement_channel_id VARCHAR(20),
    last_role_sync_at TIMESTAMPTZ,
    bot_dm_enabled BOOLEAN NOT NULL DEFAULT FALSE,  -- Phase 2.6: DM kill switch
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Website registration codes
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
```

### patt schema (features)

```sql
-- Voting campaigns
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

-- Campaign entries
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

-- Vote records
CREATE TABLE patt.votes (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
    entry_id INTEGER REFERENCES patt.campaign_entries(id),
    rank INTEGER NOT NULL,
    voted_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(campaign_id, player_id, rank)
);

-- Calculated results
CREATE TABLE patt.campaign_results (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    entry_id INTEGER REFERENCES patt.campaign_entries(id),
    first_place_count INTEGER DEFAULT 0,
    second_place_count INTEGER DEFAULT 0,
    third_place_count INTEGER DEFAULT 0,
    weighted_score INTEGER DEFAULT 0,
    final_rank INTEGER,
    calculated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Contest agent log
CREATE TABLE patt.contest_agent_log (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER REFERENCES patt.campaigns(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    discord_message_id VARCHAR(20),
    posted_at TIMESTAMPTZ DEFAULT NOW()
);

-- Mito content
CREATE TABLE patt.mito_quotes (id SERIAL PRIMARY KEY, quote TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE patt.mito_titles (id SERIAL PRIMARY KEY, title TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW());

-- Player availability windows (day-of-week, timezone-aware)
-- Scheduling weight comes from guild_ranks.scheduling_weight via the player's rank
CREATE TABLE patt.player_availability (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    -- 0=Monday ... 6=Sunday (ISO weekday)
    earliest_start TIME NOT NULL,
    -- In the player's local timezone (players.timezone)
    available_hours NUMERIC(3,1) NOT NULL CHECK (available_hours > 0 AND available_hours <= 16),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (player_id, day_of_week)
);

-- WoW raid/patch seasons for attendance tracking
-- Current season = MAX(start_date) WHERE start_date <= NOW() AND is_active = TRUE
-- No end_date — seasons end when the next one starts
CREATE TABLE patt.raid_seasons (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Scheduled raid events (can be linked to Raid-Helper and Warcraft Logs)
CREATE TABLE patt.raid_events (
    id SERIAL PRIMARY KEY,
    season_id INTEGER REFERENCES patt.raid_seasons(id),
    title VARCHAR(200) NOT NULL,
    event_date DATE NOT NULL,
    start_time_utc TIMESTAMPTZ NOT NULL,
    end_time_utc TIMESTAMPTZ NOT NULL,
    raid_helper_event_id VARCHAR(30),
    discord_channel_id VARCHAR(25),
    log_url VARCHAR(500),
    notes TEXT,
    created_by_player_id INTEGER REFERENCES guild_identity.players(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Per-player attendance records for each raid event
-- Attendance % = attended / (events where player was available on that day_of_week)
CREATE TABLE patt.raid_attendance (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES patt.raid_events(id),
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
    signed_up BOOLEAN DEFAULT FALSE,
    attended BOOLEAN DEFAULT FALSE,
    character_id INTEGER REFERENCES guild_identity.wow_characters(id),
    noted_absence BOOLEAN DEFAULT FALSE,
    source VARCHAR(20) DEFAULT 'manual',
    -- 'manual', 'raid_helper', 'warcraft_logs', 'auto'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (event_id, player_id)
);
```

### Derived Values (NOT stored — computed via joins)

| Value | Derivation |
|---|---|
| Player's main role | `players.main_spec_id → specializations.default_role_id → roles.name` |
| Player's off-spec role | `players.offspec_spec_id → specializations.default_role_id → roles.name` |
| Player's guild rank | `MAX(player_characters → wow_characters.guild_rank_id)` by `guild_ranks.level ASC` |
| Character's role type | If char = main_character_id → 'Main'. If char = offspec_character_id → 'Off-Spec'. Else → 'Alt'. |
| Roster eligible | `players WHERE main_character_id IS NOT NULL AND is_active = TRUE` |
| Rank mismatch audit | Any character with guild_rank_id != player's resolved rank → audit finding |
| Current raid season | `MAX(start_date) WHERE start_date <= NOW() AND is_active = TRUE` |
| Attendance % | `attended / (events WHERE player had availability on that day_of_week)` within season |

### Schema Additions Planned for Phase 3

#### `patt.raid_events` additions (Migration 0014 — Phase 3.4)
```sql
ALTER TABLE patt.raid_events
    ADD COLUMN recurring_event_id INTEGER REFERENCES patt.recurring_events(id),
    ADD COLUMN auto_booked BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN raid_helper_payload JSONB;
```

---

### Eliminated Tables (removed in Phase 2.7)

| Table | Replaced By |
|---|---|
| `common.guild_members` | `guild_identity.players` + direct FKs for discord/website |
| `common.characters` | `guild_identity.wow_characters` (richer Blizzard API data) |
| `guild_identity.persons` | Renamed to `guild_identity.players` |
| `guild_identity.identity_links` | `guild_identity.player_characters` bridge + direct FKs |
| `common.member_availability` | `patt.player_availability` (time windows + weighted scheduling) |

---

## Google Drive Image URLs

Campaign entries use Google Drive image URLs. The URL pattern used in code:
```
https://drive.google.com/uc?id={FILE_ID}&export=view
```
This renders the image directly without the Drive UI wrapper.
Images for the art vote live at: `J:\Shared drives\Salt All The Things\Marketing\Pull All The Things`

---

## Current Build Status

> **UPDATE THIS SECTION AT THE END OF EVERY PHASE**

### Completed Phases
- Phase 0 through 7: Platform complete and live
- Phase 2.5A–D: Guild identity system (Blizzard API, Discord sync, addon, integrity checker)
- Phase 2.6: Onboarding system (built but NOT activated — on_member_join not wired)
- Phase 2.6 (Revised): Onboarding updated for player model + bot DM toggle (complete)
  - `common.discord_config.bot_dm_enabled` added (DEFAULT FALSE — DMs off until Mike enables)
  - `is_bot_dm_enabled()` helper in `sv_common.discord.dm` — checks flag before any DM
  - Admin API: `GET/PATCH /api/v1/admin/bot-settings`, `GET /api/v1/admin/onboarding-stats`
  - Admin page `/admin/bot-settings` — prominent ON/OFF toggle + live session count display
  - `conversation.py` updated: uses `discord_users` table, player model, DM gate in `start()`
  - `provisioner.py` rewritten: `provision_player()` (was `provision_person()`), player model
  - `deadline_checker.py` updated: uses `discord_users`/`verified_player_id`, adds `_resume_awaiting_dm_sessions()`
  - `commands.py` updated: uses player model for resolve command
  - `on_member_join`, `on_member_remove`, `on_member_update` events wired up in `bot.py`
  - `bot.py` gets `set_db_pool()` called from FastAPI lifespan; registers slash commands on_ready
  - `scheduler.run_onboarding_check()` re-enabled (was stubbed since Phase 2.5 revised)
  - Alembic migration 0009 created
  - 222 unit tests pass, 69 skipped
- Phase 2.7: Data Model Migration — Clean 3NF rebuild (complete)
  - `common.guild_members` and `common.characters` eliminated from all code
  - Reference tables added: `guild_identity.roles`, `classes`, `specializations`
  - `guild_identity.persons` renamed to `players` with main/offspec FKs, discord_user_id, website_user_id
  - `guild_identity.player_characters` bridge table added
  - All FKs repointed from guild_members → players across models, services, routes, templates, tests
  - Alembic migration 0007 created; data migration script at `scripts/migrate_to_players.py`
- Phase 2.8: Scheduling, Availability & Attendance Foundation (complete)
  - `common.member_availability` dropped (stale data — all player_ids were NULL)
  - `patt.player_availability` added: time-window availability per player/day (earliest_start, available_hours, timezone)
  - `patt.raid_seasons`, `patt.raid_events`, `patt.raid_attendance` added for attendance tracking
  - `common.guild_ranks.scheduling_weight` added (0–5, Officers/GL count 5x)
  - `guild_identity.players.timezone` and `auto_invite_events` added
  - Admin API: GET/PATCH roles, GET classes/specs, GET/POST/PATCH seasons, updated ranks to include scheduling_weight
  - Admin page `/admin/reference-tables` — inline editing for ranks, roles, seasons; read-only class/spec reference
  - `patt.services.availability_service` and `patt.services.season_service` created
  - Alembic migration 0008 created
- Phase 2.5 (Revised): Guild Sync Code Update (complete)
  - All guild_sync modules updated for Phase 2.7 player model
  - `identity_engine.py`: rewrites matching to use `player_characters` + `players.discord_user_id`
  - `integrity_checker.py`: all checks rewritten for new schema (no dropped columns)
  - `reporter.py`: fixed `first_detected` → `created_at`
  - `scheduler.py`: `run_onboarding_check` stubbed (Phase 2.6 still dormant)
  - `db_sync.py`: rank lookup fixed (by name not level)
  - `api/routes.py`: identity routes updated for new schema; `/identity/players` replaces `/identity/persons`
  - 207 unit tests pass, 69 skipped (DB-dependent or legacy tests)

- Phase 3.1: Admin Availability Dashboard + Event Day System (complete)
  - `patt.recurring_events` table added (migration 0013)
  - `common.discord_config` gains 6 Raid-Helper config columns (migration 0013)
  - `RecurringEvent` ORM model added; `DiscordConfig` model updated
  - Admin API: GET/POST/PATCH/DELETE `/api/v1/admin/recurring-events`
  - Admin API: GET `/api/v1/admin/availability-by-day` (shared with Phase 3.4 raid tools)
  - Admin page `/admin/availability` — 7-day grid with % bars (green/amber/red),
    collapsible player lists with role+rank, event day config table with auto-save
  - Sidebar nav link "Availability" added to base_admin.html

- Phase 3.3: Public Roster View (complete)
  - New public page `/roster` (no auth required) — three tabs: Full Roster, Composition, Schedule
  - Full Roster: sortable table, mains-only default, "Show alts" checkbox, client-side search by name/class/spec, class colors, armory links
  - Composition: role distribution cards (green/amber/red vs targets 2T/4H/6M/6R), class distribution chips, Wowhead comp link with full spec code map
  - Schedule: recurring event days from DB (same query as index page)
  - Updated `GET /api/v1/guild/roster`: returns `player_id`, `rank_name`, `rank_level`, `main_character` (character_id, character_name, realm_slug, class_name, spec_name, role_name, item_level, armory_url), `characters[]` for alt view
  - `app.py`: removed roster/roster-view from legacy file handlers; 301 redirects `/roster.html` → `/roster`, `/roster-view.html` → `/roster`
  - New `src/patt/static/css/roster.css`

### Current Phase
- **No active phase** — platform is up to date

### Planned Phases (not yet implemented)
- Phase 3.2: Index Page Revamp (`reference/PHASE_3_2_INDEX_REVAMP.md`)
  - Officers, recruiting needs, weekly schedule all loaded from DB (no more hardcoded HTML)
  - Links updated: roster.html → /roster
- Phase 3.4: Admin Raid Tools (`reference/PHASE_3_4_RAID_TOOLS.md`)
  - New table additions: `patt.raid_events.recurring_event_id`, `auto_booked`, `raid_helper_payload`
  - New admin page `/admin/raid-tools` — RH config, availability grid, event builder, roster preview
  - Server-side Raid-Helper API calls (no GAS proxy needed)
  - New service: `src/patt/services/raid_helper_service.py`
  - Alembic migration 0014
- Phase 3.5: Auto-Booking Scheduler (`reference/PHASE_3_5_AUTO_BOOKING.md`)
  - Background task polls every 5 min; books next week's event 10–20 min after current event starts
  - New service: `src/patt/services/raid_booking_service.py`

### What Exists
- sv_common.identity package: ranks, players, characters CRUD (`src/sv_common/identity/`)
- sv_common.auth package: passwords (bcrypt), JWT (PyJWT), invite codes (`src/sv_common/auth/`)
- sv_common.discord package: bot client, role sync (DiscordUser+Player), DM dispatch + DM gate, channel posting (`src/sv_common/discord/`)
- sv_common.guild_sync package: Blizzard API client, identity engine, integrity checker, Discord sync, addon processor, scheduler
- Auth API: `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`
- Auth middleware: `get_current_player()`, `require_rank(level)` deps in `src/patt/deps.py`
- Cookie-based auth for page routes: `get_page_member()`, `require_page_rank(level)` in deps.py
- Admin API: `/api/v1/admin/*` — all routes protected (Officer+ rank required); includes ranks, roles, classes, specializations, seasons, bot-settings, onboarding-stats
- Availability service: `src/patt/services/availability_service.py` — CRUD for `patt.player_availability`
- Season service: `src/patt/services/season_service.py` — CRUD for `patt.raid_seasons`
- Admin reference tables page: `/admin/reference-tables` — inline edit ranks, roles, seasons; read-only class/spec reference
- Admin bot settings page: `/admin/bot-settings` — ON/OFF toggle for bot DMs, onboarding session counts
- Admin availability page: `/admin/availability` — 7-day grid with % bars, collapsible player lists, event day config table (auto-save)
- Admin API: GET/POST/PATCH/DELETE `/api/v1/admin/recurring-events` + GET `/api/v1/admin/availability-by-day`
- RecurringEvent ORM model + patt.recurring_events table (migration 0013)
- Public API: `/api/v1/guild/ranks`, `/api/v1/guild/roster`, `/api/v1/guild/availability` (public, no auth required)
- Public roster page: `/roster` — Full Roster / Composition / Schedule tabs; 301 redirects from /roster.html and /roster-view.html
- Discord bot starts as background task during FastAPI lifespan (skipped if no token configured)
- Bot events: `on_member_join` (discord_sync + onboarding), `on_member_remove`, `on_member_update` all wired
- Bot slash commands: `/onboard-status`, `/onboard-resolve`, `/onboard-dismiss`, `/onboard-retry` registered on_ready
- Campaign service: full lifecycle (draft→live→closed) with ranked-choice voting
- Contest agent: Discord milestone posts, auto-activate/close campaigns
- Onboarding system: conversation.py, provisioner.py, deadline_checker.py, commands.py — fully updated for Phase 2.7 player model, **bot_dm_enabled defaults to FALSE**
- guild_sync package: all modules operational; scheduler.run_onboarding_check active (30-min interval)
- PATTSync WoW addon + companion app (functional, syncing guild notes)
- Full regression test suite
- Web UI: login, register, vote, admin campaigns, admin roster, public landing page
- Art vote campaign configured and previously run

### Key Data State
- `guild_identity.players`: 43 rows (migrated from guild_members)
- `guild_identity.player_characters`: 195 rows
- `guild_identity.wow_characters`: ~320 rows from Blizzard API syncs
- `guild_identity.discord_users`: populated from Discord bot syncs
- Reference tables (roles, classes, specializations): seeded via Alembic migration 0007
- All players have `main_character_id`, `main_spec_id`, `offspec_*` as NULL (set on first login)

### Dormant Code
- None — all modules are up to date. Onboarding is wired but gated by `bot_dm_enabled = FALSE`.

---

## Operations & Deployment

- **Tests:** 222+ pass (69 skip when no DB); regression suite at `tests/regression/` requires live DB
- **CI/CD:** GitHub Actions workflow at `.github/workflows/deploy.yml` — auto-deploys on every push to main
  - SSH key: `DEPLOY_SSH_KEY` secret in GitHub repo (ed25519 key authorized on server)
  - Deploy steps: git pull → pip install → alembic upgrade → systemctl restart → health check
- **Alembic migrations:** `0001_initial_schema.py` through `0009_bot_dm_toggle.py`

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
