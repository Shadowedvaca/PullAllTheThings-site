# PATT Guild Platform — Architectural Review

> **Purpose:** Canonical architectural reference. All other technical documentation should derive from or reference this document.
> **Audience:** Engineers, contributors, and maintainers of the Pull All The Things guild platform.
> **Scope:** Full stack — from Hetzner metal to browser and Discord client.

---

## Table of Contents

1. [The Big Rocks](#1-the-big-rocks)
2. [Process Flows](#2-process-flows)
3. [External Integrations](#3-external-integrations)
4. [Logical Architecture](#4-logical-architecture)
5. [Physical Architecture](#5-physical-architecture)
6. [Data Architecture](#6-data-architecture)
7. [Security Architecture](#7-security-architecture)
8. [Configuration Architecture](#8-configuration-architecture)

---

## 1. The Big Rocks

The platform is composed of five major concerns. Everything in the codebase maps to one or more of these.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       PATT GUILD PLATFORM                               │
│                                                                         │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐               │
│  │   IDENTITY    │  │   ACTIVITY    │  │  ENGAGEMENT   │               │
│  │   SYSTEM      │  │   TRACKING    │  │   SYSTEM      │               │
│  │               │  │               │  │               │               │
│  │ Who is in the │  │ Raids, attend-│  │ Campaigns,    │               │
│  │ guild. Linking│  │ ance, availa- │  │ voting, quotes│               │
│  │ Discord ↔ WoW │  │ bility, sign- │  │ crafting      │               │
│  │ ↔ Website     │  │ ups, WCL/RIO  │  │ orders        │               │
│  └───────────────┘  └───────────────┘  └───────────────┘               │
│                                                                         │
│  ┌───────────────┐  ┌───────────────────────────────────┐              │
│  │  AUTOMATION   │  │         ADMINISTRATION             │              │
│  │  ENGINE       │  │                                    │              │
│  │               │  │  Officer tooling: player manager,  │              │
│  │ Sync jobs,    │  │  raid tools, data quality, site    │              │
│  │ Discord bot,  │  │  config, error routing, reports    │              │
│  │ auto-booking, │  │                                    │              │
│  │ onboarding    │  │                                    │              │
│  └───────────────┘  └───────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Identity System

The core. Every other subsystem depends on it.

A **Player** is the platform's central identity entity. It is the junction point between three external identity spaces:
- A **Discord user** (who the person is on the guild server)
- One or more **WoW characters** (who they play in-game)
- A **website account** (their portal login)

The platform continuously ingests identity data from Blizzard's API and the Discord server, then uses a rule-based matching engine to link characters to players. Officers can also manually manage links. Without a resolved player identity, the attendance, engagement, and admin systems have nothing to work with.

### 1.2 Activity Tracking

Tracks guild participation over time:
- **Raid events** (scheduled via Raid-Helper, tracked via voice channel presence + signup data)
- **Attendance records** (who showed up, how long, their availability status, Raid-Helper signup status)
- **Auto-excuse logic** (configurable rules applied at query time for unavailability and Discord absences)
- **Performance data** (WCL parses ingested nightly, Raider.IO scores synced periodically)

### 1.3 Engagement System

Member-facing features:
- **Voting campaigns** (ranked-choice image/option voting with a contest agent that posts to Discord)
- **Guild quotes** (Discord slash commands tied to member profiles)
- **Crafting Corner** (guild-wide recipe directory + Discord order embeds)
- **Feedback** (scored + free-text; de-identified sync to Hub)

### 1.4 Automation Engine

All background work — nothing here is triggered by a user request:
- **APScheduler jobs** (~10 recurring tasks) for Blizzard sync, Discord sync, WCL, Raider.IO, crafting, AH pricing, integrity checks, progression snapshots, error digest
- **Discord bot** with event handlers (member joins/leaves, voice state changes, role updates)
- **Auto-booking** loop creates next week's Raid-Helper event ~15 min after the current raid starts
- **Onboarding flow** — DM conversation with new Discord members (Battle.net OAuth, role provisioning)

### 1.5 Administration

Everything officers and the guild leader use to manage the above:
- **Player Manager** — drag-and-drop identity linking, character assignment, rank management
- **Raid Tools** — event creation, attendance grid, re-processing, CSV export
- **Data Quality** — integrity issue viewer, manual mitigation triggers
- **Site Config** — feature flags, API credentials, guild metadata, attendance thresholds
- **Reference Tables** — ranks, guide sites, wow rank mapping
- **Error Routing** — severity rules for centralized error categorization

---

## 2. Process Flows

### 2.1 Member Onboarding

The path from Discord join to fully provisioned guild member.

```
Discord member joins server
        │
        ▼
[Bot: on_member_join]
  Is onboarding enabled?
  ├─ NO  → Manual invite code flow (officer sends code via DM)
  └─ YES ↓
        │
        ▼
[OnboardingConversation.start()]
  DM member: "Welcome — link your Battle.net account"
        │
        ▼
[Member clicks Battle.net OAuth link]
  /auth/battlenet → Blizzard authorize → /auth/battlenet/callback
  Tokens encrypted → stored in battlenet_accounts
        │
        ▼
[Provisioner: verify_and_provision()]
  1. Fetch characters from Battle.net
  2. Match characters to guild roster (in_guild check)
  3. Create Player record
  4. Link discord_user ↔ player ↔ wow_characters
  5. Assign Discord role (lowest qualifying rank)
  6. Generate invite code → DM member
        │
        ▼
[Member registers on portal]
  POST /api/v1/auth/register with invite code
  Creates website User, links to player
  Returns JWT → sets patt_token cookie
        │
        ▼
Fully provisioned: Discord + WoW + Website all linked
```

**Fallback paths:**
- DMs closed → bot posts to `landing_zone_channel_id`
- OAuth timeout → `deadline_checker` re-sends after 30 min
- Non-guild character → provisioned with Initiate rank, flagged for review

### 2.2 Nightly Sync Pipeline

Runs across multiple scheduler jobs. Order matters; identity data must precede activity data.

```
┌─────────────────────────────────────────────────────────┐
│                  NIGHTLY SYNC PIPELINE                   │
│                (approximate daily sequence)              │
│                                                          │
│  03:00 UTC  Crafting Sync                                │
│             └─ Blizzard API: professions, recipes        │
│             └─ Upsert: character_recipes                 │
│                                                          │
│  03:15 UTC  Battle.net Character Refresh                 │
│             └─ Decrypt OAuth tokens (battlenet_accounts) │
│             └─ Fetch char list per linked account        │
│             └─ Update in_guild flag, link new characters │
│                                                          │
│  05:00 UTC  WCL Sync                                     │
│             └─ Fetch zone reports (GraphQL)              │
│             └─ Per-encounter rankings per character      │
│             └─ Upsert: character_report_parses           │
│                                                          │
│  Every 6hr  Blizzard Roster Sync (01,07,13,19 UTC)       │
│  (4x/day)   └─ Guild roster from Battle.net API         │
│             └─ Upsert: wow_characters (rank, ilvl, spec) │
│             └─ Integrity check → audit_issues            │
│             └─ Optional: Discord report                  │
│                                                          │
│  Every 15m  Discord Sync                                 │
│             └─ Fetch member list + roles from Discord    │
│             └─ Upsert: discord_users                     │
│             └─ Reconcile rank ↔ Discord role             │
│                                                          │
│  Hourly     AH Pricing Sync (:15 past the hour)          │
│             └─ Fetch tracked item prices per realm       │
│             └─ Upsert: item_price_history                │
│                                                          │
│  Sunday     Weekly Progression Sweep (04:30 UTC)         │
│  weekly     └─ Raid encounter kill counts (Blizzard)     │
│             └─ M+ ratings, achievement progress          │
│             └─ Snapshot: progression_snapshots           │
│                                                          │
│  Sunday     Weekly Error Digest (06:00 UTC)              │
│  weekly     └─ Unresolved audit_issues → Discord report  │
└─────────────────────────────────────────────────────────┘
```

### 2.3 Raid Night Flow

What happens around a guild raid event.

```
[Tuesday 7:55 PM ET — ~10–20 min after raid start]
Auto-booking loop fires:
  ├─ Creates next Tuesday's Raid-Helper event
  └─ Posts raid announcement embed to raid channel

[Tuesday 8:00 PM ET — raid start]
Signup Snapshot job fires (scheduler, 30-min loop):
  ├─ Calls Raid-Helper API → fetches current signups
  ├─ For each player: resolves was_available (player_availability table)
  ├─ Writes raid_helper_status per attendance row
  └─ Stamps raid_events.signup_snapshot_at

[Raid in progress]
VoiceAttendanceCog:
  ├─ on_voice_state_update → logs join/leave to voice_attendance_log
  └─ Records first_join_at, last_leave_at, total minutes

[Tuesday ~10:00 PM ET — raid ends]
[30 min later — attendance post-processor fires]
process_event() in attendance_processor.py:
  ├─ Reads voice_attendance_log for event window
  ├─ Resolves each Discord member → player_id (via discord_users + players)
  ├─ Marks joined_late / left_early flags
  ├─ Upserts raid_attendance rows
  └─ Stamps raid_events.attendance_processed_at

[Admin views attendance grid]
_compute_auto_excused() in admin_routes.py:
  ├─ Reads attendance_excuse_if_unavailable setting
  ├─ Reads attendance_excuse_if_discord_absent setting
  └─ Applied at query time — retroactively, no re-processing needed
```

### 2.4 Authentication Flow

```
[New member — invite code path]
  Officer generates invite code (Admin → Players → Generate Invite)
  Code stored: invite_codes (8-char, 72-hr expiry, tied to player_id)
  Officer DMs code to member

  Member visits /register?code=XXXXXXXX
  POST /api/v1/auth/register { code, username, password }
    ├─ Validate code (exists, unused, not expired)
    ├─ Create User (bcrypt hash)
    ├─ Link player.website_user_id
    ├─ Consume invite code
    └─ Return JWT

[Returning member]
  POST /api/v1/auth/login { username, password }
    ├─ Fetch user by username
    ├─ bcrypt.verify(password, hash)
    └─ Return JWT { user_id, member_id, rank_level, exp }

[Every request]
  JWT extracted from:
    ├─ Authorization: Bearer <token>  (API calls)
    └─ Cookie: patt_token=<token>     (browser page renders)
  deps.py validates signature + expiry
  Injects CurrentUser into route handler
```

### 2.5 Identity Matching

How characters get linked to players without manual intervention.

```
[Input]
  wow_characters table: characters with guild_note (officer note) values
  discord_users table: Discord members with display names, usernames

[Identity Engine: identity_engine.py]
  Step 1: Group characters by guild_note value
          (note = who the officer declared them to be)
  Step 2: For each note group, attempt Discord user match:
          ├─ Exact match: note == discord username
          ├─ Display name match: note == display_name
          └─ Substring match: note substring in name (fallback)
  Step 3: Create or update Player entity
          └─ player_characters rows with link_source + confidence

[Matching Rules: matching_rules/runner.py]
  Runs iteratively until convergence or max passes:
  ├─ primary_char: highest-ilvl char in group = main
  ├─ note_grouping: chars sharing a note → same player
  ├─ discord_hint: note contains Discord username fragment
  ├─ family_pattern: common naming patterns (alt names like "Alt-Trogmoon")
  └─ (additional rules pluggable)

[Output]
  player_characters rows: player_id ↔ character_id
  Players with resolved main_character_id
  Audit issues for unresolved/conflicting cases
```

---

## 3. External Integrations

### 3.1 Integration Map

```
                        ┌─────────────────────┐
                        │   PATT Platform      │
                        │   (FastAPI app)      │
                        └──────────┬──────────┘
                                   │
          ┌────────────┬───────────┼────────────┬────────────┐
          │            │           │            │            │
          ▼            ▼           ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
    │ Blizzard │ │ Discord  │ │Raider  │ │Warcraft  │ │Raid      │
    │ Battle   │ │ API /    │ │  .IO   │ │  Logs    │ │ Helper   │
    │ .net API │ │ Bot      │ │  API   │ │  API     │ │ API      │
    └──────────┘ └──────────┘ └────────┘ └──────────┘ └──────────┘
    Guild roster  Role sync,   M+ scores,  Parse data,  Event
    Characters,   DMs, events, raid prog,  zone reports creation,
    Professions,  voice track  gear iLvl               signup data
    OAuth login
```

### 3.2 Blizzard Battle.net API

| Property | Detail |
|----------|--------|
| Base URL | `https://us.api.blizzard.com` |
| Auth | OAuth2 client credentials (auto-refresh) |
| Credentials | `site_config.blizzard_client_secret_encrypted` (Fernet) + env fallback |
| Rate limit | 36,000 req/hr |
| Sync frequency | 4×/day (01, 07, 13, 19 UTC) + manual trigger |
| Key calls | Guild roster, character profiles, professions, M+ ratings, raid encounters |
| Notes | `blizzard_character_id` is the stable key — names change, this doesn't |

### 3.3 Discord (discord.py + REST)

| Property | Detail |
|----------|--------|
| Library | discord.py 2.x |
| Intents | `members=True`, `voice_states=True`, `message_content=True` |
| Token storage | Encrypted in `discord_config.bot_token_encrypted` (Fernet, JWT-key-derived) |
| Sync frequency | Member/role sync every 15 min; events driven |
| Key events | `on_member_join`, `on_member_remove`, `on_member_update`, `on_voice_state_update` |
| Key capabilities | Role assignment, DMs, slash commands, embed posting, channel list sync |
| Bot token DM gate | `bot_dm_enabled=FALSE` by default — must be enabled in discord_config |

### 3.4 Raider.IO

| Property | Detail |
|----------|--------|
| Base URL | `https://raider.io/api/v1` |
| Auth | None (public API) |
| Rate limit | ~300 req/min; client uses 30 concurrent with 1s delays |
| Key data | M+ overall score, best/recent runs, raid progression, gear iLvl |
| Storage | `raiderio_profiles` (season='current', score, raid_progression) |

### 3.5 Warcraft Logs

| Property | Detail |
|----------|--------|
| Base URL | `https://www.warcraftlogs.com/api/v2/client` |
| Protocol | GraphQL |
| Auth | OAuth2 client credentials |
| Credentials | `wcl_config` table (encrypted) |
| Rate limit | ~3,600 points/hr |
| Sync frequency | Daily at 05:00 UTC |
| Key data | Zone reports, per-boss rankings per character per report |
| Storage | `raid_reports` (zone/encounter metadata) + `character_report_parses` (percentile per boss) |
| Response shape | `reportData.report.rankings` → `{"data": [fight objects with roles.tanks/healers/dps.characters]}` |

### 3.6 Raid-Helper

| Property | Detail |
|----------|--------|
| API key storage | `discord_config.raid_helper_api_key` |
| Server ID | `discord_config.raid_helper_server_id` |
| Usage | Create raid events (template-based), fetch current signups for snapshot |
| Called by | Auto-booking loop (event creation), signup snapshot job (attendance data) |

### 3.7 Battle.net OAuth (Account Linking)

This is distinct from the service account API auth above. This is the per-member OAuth flow for linking a player's personal Battle.net account to their portal profile.

| Property | Detail |
|----------|--------|
| Flow | Authorization Code (PKCE-compatible) |
| Endpoints | `/auth/battlenet` → Blizzard authorize → `/auth/battlenet/callback` |
| Token storage | `battlenet_accounts` table, tokens encrypted with `BNET_TOKEN_ENCRYPTION_KEY` (Fernet) |
| Usage | Fetch owned characters, profession data, identify non-guild alts |
| Refresh | Daily 03:15 UTC refresh job |

---

## 4. Logical Architecture

The logical architecture describes how concerns are separated in code, independent of where they physically run.

### 4.1 Layer Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        PRESENTATION LAYER                        │
│                                                                   │
│   Jinja2 server-rendered HTML (public, member, admin pages)      │
│   Vanilla JS (drag-drop player manager, dynamic UI elements)     │
│   CSS custom properties (design tokens from config_cache)        │
└──────────────────────────────┬────────────────────────────────────┘
                               │ HTTP / WebSocket
┌──────────────────────────────▼────────────────────────────────────┐
│                         API LAYER                                  │
│                                                                    │
│   FastAPI route handlers   guild_portal/api/ + guild_portal/pages/ │
│   sv_common/guild_sync/api/routes.py                               │
│   Response contract: {"ok": true, "data": {...}}                   │
│   Auth: deps.py (JWT via Bearer OR cookie)                         │
│   Rate limiting: login endpoint (10 req/60s)                       │
│   Middleware: security headers, setup guard                        │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│                       SERVICE LAYER                                │
│                                                                    │
│   guild_portal/services/      sv_common/guild_sync/               │
│   ├─ campaign_service.py      ├─ scheduler.py (APScheduler)       │
│   ├─ contest_agent.py         ├─ attendance_processor.py          │
│   ├─ raid_booking_service.py  ├─ crafting_sync.py                 │
│   ├─ availability_service.py  ├─ progression_sync.py              │
│   ├─ vote_service.py          ├─ identity_engine.py               │
│   ├─ guide_links_service.py   ├─ integrity_checker.py             │
│   └─ error_routing.py         ├─ wcl_sync.py                      │
│                               ├─ drift_scanner.py                 │
│                               └─ onboarding/                      │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│                      INTEGRATION LAYER                             │
│                                                                    │
│   sv_common/guild_sync/                                            │
│   ├─ blizzard_client.py    (Battle.net roster + character API)     │
│   ├─ raiderio_client.py    (Raider.IO public API)                  │
│   ├─ warcraftlogs_client.py (WCL GraphQL)                          │
│   └─ raid_helper_service.py (Raid-Helper event creation)          │
│                                                                    │
│   sv_common/discord/                                               │
│   ├─ bot.py                (discord.py lifecycle + events)         │
│   ├─ role_sync.py          (Discord role ↔ guild rank)             │
│   └─ dm.py                 (direct message dispatch)              │
│                                                                    │
│   sv_common/auth/                                                  │
│   └─ (jwt, passwords, invite_codes)                                │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│                        DATA LAYER                                  │
│                                                                    │
│   sv_common/db/                                                    │
│   ├─ engine.py             (SQLAlchemy async engine factory)       │
│   ├─ models.py             (100+ ORM models, 3 schemas)            │
│   └─ seed.py               (default ranks, guide sites)           │
│                                                                    │
│   alembic/versions/        (migrations 0001–0063)                 │
│   PostgreSQL 16             (schemas: common, patt, guild_identity)│
└───────────────────────────────────────────────────────────────────┘
```

### 4.2 Package Responsibility Boundary

The codebase has two top-level packages with a deliberate boundary:

**`guild_portal`** — Application layer. Everything here is specific to this guild's portal:
- HTTP route handlers (API + pages)
- Jinja2 templates and static assets
- Application-specific services (campaigns, contests, booking)
- App startup and configuration

**`sv_common`** — Platform layer. Designed to be reusable across multiple guild sites:
- Auth primitives (JWT, bcrypt, invite codes)
- Database ORM models and engine
- Discord bot infrastructure
- All external API clients
- The entire guild sync engine
- Configuration cache

The implication: `guild_portal` may import from `sv_common` freely. `sv_common` must never import from `guild_portal`. This keeps the shared services portable.

### 4.3 Concurrency Model

The application runs in a single Python process with cooperative async concurrency:

```
Process: uvicorn (asyncio event loop)
  │
  ├─ FastAPI ASGI app (request handlers — async)
  │
  ├─ Discord bot (discord.py asyncio client — coroutine-based)
  │   └─ Started as asyncio.create_task() in FastAPI lifespan
  │
  ├─ APScheduler (AsyncIOScheduler — jobs run as asyncio coroutines)
  │   └─ ~10 scheduled jobs sharing the event loop
  │
  ├─ Background loops (asyncio.create_task)
  │   ├─ campaign_service.check_campaigns() (continuous loop)
  │   ├─ contest_agent (continuous loop)
  │   └─ raid_booking_service (5-min poll loop)
  │
  └─ SQLAlchemy async sessions (asyncpg driver — fully async)
```

All database access is async (asyncpg). External API calls use httpx async client. The Discord bot runs in the same event loop as FastAPI — they share the event loop but not database sessions (each operation opens its own session).

**Key constraint:** asyncpg has no synchronous API. Any synchronous path that needs the DB must use `asyncio.run()` or be refactored. Never block the event loop with synchronous I/O.

### 4.4 Configuration Hierarchy

Configuration is resolved in priority order (highest → lowest):

```
1. common.discord_config (DB row)   ← runtime, per-guild, hot-reloadable
   Bot token, Discord IDs, attendance settings, Raid-Helper key

2. common.site_config (DB row)       ← runtime, per-guild, hot-reloadable
   Guild name, colors, Blizzard creds, feature flags, realm config
   Loaded into config_cache at startup, cached in-process

3. Environment variables / .env      ← deploy-time, per-environment
   DATABASE_URL, JWT_SECRET_KEY, BNET_TOKEN_ENCRYPTION_KEY
   Used as fallbacks when DB config not yet populated (first-run)

4. guild_portal/config.py (Pydantic) ← code-time defaults
   Parsed from env at process start; immutable after startup
```

---

## 5. Physical Architecture

### 5.1 Infrastructure Overview

Three environments on three separate servers. Dev and test share CX23 nodes; prod is dedicated.

```
my-web-apps-dev — shared CX23, Falkenstein DE
┌─────────────────────────────────────────────────────┐
│  Nginx → dev.pullallthethings.com (htpasswd auth)   │
│  docker-compose.dev.yml                             │
│  app (port 8100) + db (PostgreSQL 16)               │
└─────────────────────────────────────────────────────┘

my-web-apps-test — shared CX23, Falkenstein DE
┌─────────────────────────────────────────────────────┐
│  Nginx → test.pullallthethings.com (htpasswd auth)  │
│  docker-compose.test.yml                            │
│  app (port 8100) + db (PostgreSQL 16)               │
└─────────────────────────────────────────────────────┘

hetzner / prod — dedicated CPX21, Hillsboro OR
┌─────────────────────────────────────────────────────┐
│  Nginx → pullallthethings.com                       │
│  docker-compose.guild.yml                           │
│  app-prod (port 8100) + db-prod (PostgreSQL 16)     │
└─────────────────────────────────────────────────────┘
```

### 5.2 Container Layout

One **app container** runs all three co-located processes:
- FastAPI web server (uvicorn, port 8100 on every server)
- Discord bot (discord.py, asyncio task in FastAPI lifespan)
- APScheduler + background loops (asyncio tasks in FastAPI lifespan)

All three share the same Python process, event loop, and database connection pool. This is intentional — the guild platform is low-traffic and the tight coupling between web, bot, and scheduler is a feature (shared config cache, shared DB session factory).

### 5.3 Environments

| Env | Server | Port | Deploy trigger | Purpose |
|-----|--------|------|---------------|---------|
| prod | `hetzner` | 8100 | `git tag prod-vX.Y.Z` → GitHub Actions | Live site |
| test | `my-web-apps-test` | 8100 | Merge to `main` → GitHub Actions | Post-merge validation |
| dev | `my-web-apps-dev` | 8100 | Manual `gh workflow run deploy-dev.yml -f branch=X` | Feature work |

Each environment has its own database. Migrations run automatically on container startup via `docker-entrypoint.sh` → `alembic upgrade head`.

### 5.4 CI/CD Pipeline

```
Developer
  │
  ├─ gh workflow run deploy-dev.yml -f branch=feature/X
  │     └─ SSH to my-web-apps-dev → git pull + docker build + compose up → health check
  │
  ├─ PR merged to main
  │     └─ GitHub Actions: auto-deploy to my-web-apps-test (~60s)
  │
  └─ git tag prod-vX.Y.Z && git push origin prod-vX.Y.Z
        └─ GitHub Actions: auto-deploy to hetzner/prod (~60s)
```

**Critical rules:**
- Never SSH-deploy manually — always let CI handle it
- `git push` before `gh workflow run` — workflow pulls from GitHub
- Migrations auto-run on startup — no manual `alembic upgrade` needed
- Never touch prod without explicit owner authorization

### 5.5 Networking

- **Nginx** on each server terminates TLS and proxies the subdomain to `localhost:8100`
- Dev and test are behind HTTP basic auth (`/etc/nginx/htpasswd/`)
- **Internal Docker network**: app container reaches PostgreSQL as `db:5432` (Docker service name). No external DB exposure.
- **External calls**: outbound from app container to Battle.net, Discord, Raider.IO, WCL, Raid-Helper. No inbound webhooks (Discord bot uses long-polling gateway, not webhooks).

---

## 6. Data Architecture

### 6.1 Schema Map

Three PostgreSQL schemas with clear ownership:

```
common                  patt                    guild_identity
──────────────          ────────────────────    ──────────────────────
guild_ranks             campaigns               players  ◄── central
users                   campaign_entries        discord_users
discord_config          votes                   wow_characters
site_config             campaign_results        player_characters (bridge)
invite_codes            contest_agent_log       roles / classes / specs
screen_permissions      guild_quotes            onboarding_sessions
rank_wow_mapping        guild_quote_titles      audit_issues / sync_log
error_log               quote_subjects          battlenet_accounts
error_routing           player_availability     raiderio_profiles
feedback_submissions    raid_seasons            wcl_config
guide_sites             raid_events             raid_reports
                        raid_attendance         character_report_parses
                        recurring_events        character_raid_progress
                        voice_attendance_log    character_mythic_plus
                                                professions / recipes
                                                character_recipes
                                                discord_channels
                                                progression_snapshots
                                                tracked_items / prices
```

**`common`** — Shared primitives. Could be used by a different guild's portal with no changes.

**`patt`** — Platform features. Campaign voting and raid activity belong here. Coupled to the guild's schedule and culture.

**`guild_identity`** — The identity graph. The most complex schema. `players` is the hub; everything else links to it.

### 6.2 Identity Graph

```
discord_users ──────────────────────────────────────────────┐
(discord_id, username)                                      │
                                                             │
                                    players ◄───────────────┘
                                    (discord_user_id FK)
                                    (main_character_id FK)
                                    (offspec_character_id FK)
                                    (guild_rank_id FK)
                                    (website_user_id FK)
                                         │
                                    player_characters (bridge)
                                    (link_source, confidence)
                                         │
                              wow_characters ◄───────────────┐
                              (blizzard_character_id,         │
                               character_name, realm,         │
                               class_id, active_spec_id,      │
                               guild_rank, in_guild,          │
                               ilvl, last_*_sync)             │
                                                              │
                              character_report_parses ────────┘
                              raiderio_profiles
                              character_raid_progress
                              character_mythic_plus
                              character_recipes
```

**Key design rule:** The FK is `players.discord_user_id → discord_users.id`. There is no `player_id` on `discord_users`. Always join as: `LEFT JOIN discord_users du ON du.id = p.discord_user_id`.

### 6.3 Raid Attendance Model

```
raid_events ──────────────────────────────────────────┐
(event_date, start_time_utc, end_time_utc,             │
 attendance_processed_at, signup_snapshot_at,          │
 is_deleted)                                           │
      │                                                │
      └──────► raid_attendance ◄──────────────── players
               (player_id FK, event_id FK,
                attended BOOL,
                minutes_present, joined_late, left_early,
                was_available BOOL,       ◄── snapshot data
                raid_helper_status,       ◄── snapshot data
                excuse_note, noted_absence)

voice_attendance_log
(discord_user_id, event_id, joined_at, left_at)
  └── processed into raid_attendance by attendance_processor.py
```

**Critical time zone note:** `event_date` is the local ET calendar date. `start_time_utc` is always the *next* UTC day (7–9 PM ET = midnight UTC). Never use `event_date` to match external records (WCL, Raid-Helper) — always use the UTC time window.

---

## 7. Security Architecture

### 7.1 Authentication & Authorization

| Mechanism | Used For | Storage |
|-----------|----------|---------|
| bcrypt password hash | Website login | `users.password_hash` |
| JWT (HS256) | Session token | HTTP-only cookie `patt_token` (30-day max_age) |
| Invite codes | Registration gating | `invite_codes` (8-char, 72-hr, single-use) |
| Battle.net OAuth2 | Character linking | `battlenet_accounts` (tokens Fernet-encrypted) |
| API key | Addon upload endpoint | `.env` `GUILD_SYNC_API_KEY` |

### 7.2 Encryption

Two Fernet encryption contexts:

**JWT-key-derived context** (`crypto.py`): Used for Discord bot token, Raid-Helper API key, Blizzard client secret, WCL credentials. The JWT secret is the root — if it changes, all these secrets must be re-entered.

**Dedicated BNet context** (`BNET_TOKEN_ENCRYPTION_KEY`): Used exclusively for Battle.net OAuth tokens (access + refresh). Separate key so BNet tokens can be rotated without affecting Discord/Blizzard credentials.

### 7.3 Rank-Based Access Control

Routes are gated by `rank_level` extracted from the JWT payload:

```
Public         — No auth required (/, /roster, /crafting-corner, /guide, /feedback)
Member         — Any logged-in user (/my-characters, /profile, /availability)
Officer        — rank_level >= officer threshold (/admin/*, most admin APIs)
Guild Leader   — rank_level >= GL threshold (/admin/site-config, GL-only features)
```

`screen_permissions` table in `common` schema allows DB-driven per-screen rank gates (managed via Admin UI). This is checked in `deps.py` against the JWT rank_level.

---

## 8. Configuration Architecture

### 8.1 Startup Sequence

```
1. docker-entrypoint.sh
   └─ alembic upgrade head  (apply pending migrations)
   └─ uvicorn guild_portal.app:create_app

2. create_app() lifespan (startup)
   ├─ SQLAlchemy engine created (asyncpg pool)
   ├─ config_cache populated from common.site_config
   ├─ Discord bot started (asyncio.create_task)
   ├─ APScheduler started (AsyncIOScheduler)
   └─ Background loops started (campaign, contest, booking)
```

### 8.2 Runtime Config Update

Operators change configuration through the Admin UI (no restart needed):

- **Site Config page** → writes `site_config` row → `config_cache.refresh()` called → all modules see new value within seconds
- **Bot Settings page** → writes `discord_config` row → next scheduler run reads new values
- **Attendance thresholds** → writes `discord_config` columns → auto-excuse logic reads fresh values on each attendance query (no re-processing needed)

### 8.3 Feature Flags

| Flag | Table | Effect |
|------|-------|--------|
| `setup_complete` | site_config | If FALSE, middleware redirects all routes to `/setup` |
| `enable_onboarding` | site_config | Bot's `on_member_join` starts DM flow |
| `enable_guild_quotes` | site_config | Guild quote slash commands registered |
| `enable_contests` | site_config | Contest agent runs |
| `bot_dm_enabled` | discord_config | Bot will send DMs (off by default) |
| `attendance_feature_enabled` | discord_config | Voice tracking cog loaded |
| `attendance_excuse_if_unavailable` | discord_config | Auto-excuse absent players with no availability |
| `attendance_excuse_if_discord_absent` | discord_config | Auto-excuse absent players not in Discord |
