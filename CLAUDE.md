# PATT Guild Platform — CLAUDE.md

> **Read this file first.** Master context for the Pull All The Things guild platform.
> Updated at the end of every build phase.

---

## Project Identity

- **Project:** Pull All The Things (PATT) Guild Platform
- **Repo:** `Shadowedvaca/PullAllTheThings-site` (GitHub)
- **Domain:** pullallthethings.com
- **Owner:** Mike (Discord: Trog, Character: Trogmoon, Balance Druid, Sen'jin)
- **Guild:** "Pull All The Things" — WoW guild, casual heroic raiding, real-life first, zero-toxicity
- **Podcast:** "Salt All The Things" — companion podcast, co-hosted by Trog and Rocket

---

## What This Is

A web platform for the PATT guild providing:
- **Guild identity system** — players, characters, ranks, tied to Discord roles and Blizzard API data
- **Authentication** — invite-code registration via Discord DM, password login
- **Voting campaigns** — ranked-choice voting on images, polls, book club picks, etc.
- **Discord integration** — bot for role sync, DMs, contest updates, announcements, crafting orders
- **Admin tools** — campaign management, roster management, rank configuration, crafting sync
- **Blizzard API integration** — guild roster sync, character profiles, item levels, profession/recipe data
- **Crafting Corner** — guild-wide recipe directory with Discord guild order system
- **GuildSync addon** — WoW Lua addon + companion app for guild/officer note sync

The platform uses **shared common services** (`sv_common`) reusable by other sites.

---

## Architecture

```
Three servers (see reference/git-cicd-workflow.md for full inventory):
  dev:  my-web-apps-dev  — shared CX23, Falkenstein
  test: my-web-apps-test — shared CX23, Falkenstein
  prod: hetzner          — CPX21, Hillsboro OR

Prod Server
├── Nginx (reverse proxy) → Docker container (prod:8100)
│
├── PostgreSQL 16
│   ├── common.*         (users, guild_ranks, discord_config, invite_codes, screen_permissions,
│   │                     site_config, rank_wow_mapping)
│   ├── patt.*           (campaigns, votes, entries, results, contest_agent_log,
│   │                     guild_quotes, guild_quote_titles, player_availability,
│   │                     raid_seasons, raid_events, raid_attendance, recurring_events)
│   ├── ref.*            (classes [+blizzard_class_id], specializations, hero_talents,
│   │                     bis_list_sources — all moved from guild_identity, complete)
│   ├── landing.*        (blizzard_items, blizzard_item_sources, blizzard_item_icons,
│   │                     blizzard_item_sets, blizzard_journal_instances,
│   │                     blizzard_journal_encounters, blizzard_item_quality_tracks,
│   │                     blizzard_appearances, bis_scrape_raw, crafted_items,
│   │                     wowhead_tooltips)
│   ├── enrichment.*     (items, item_sources, item_recipes, item_seasons, item_set_members,
│   │                     tier_tokens, bis_entries, trinket_ratings, item_popularity — stored procs rebuild all)
│   ├── viz.*            (slot_items, tier_piece_sources, crafters_by_item, bis_recommendations, item_popularity)
│   ├── config.*         (bis_scrape_targets, slot_labels, wowhead_invtypes)
│   └── guild_identity.* (players, wow_characters, discord_users, player_characters,
│                          roles, audit_issues, sync_log,
│                          onboarding_sessions, professions, profession_tiers, recipes,
│                          character_recipes, crafting_sync_config, discord_channels,
│                          raiderio_profiles, battlenet_accounts, wcl_config,
│                          character_parses, raid_reports, character_report_parses)
│
├── Guild Portal App (Python 3.11+ / FastAPI, guild_portal package)
│   ├── API routes + Admin pages + Public pages (Jinja2)
│   └── Background tasks (role sync, contest agent, Blizzard sync, crafting sync)
│
├── Guild Bot (discord.py, runs within the app process)
│   ├── Role sync, DM dispatch, contest agent, campaign announcements, Discord member sync
│   ├── Onboarding conversation flow (active, gated by enable_onboarding flag)
│   └── Crafting Corner guild order embeds
│
├── sv_common (shared Python package)
│   ├── auth, discord, identity, notify, db, config_cache, crypto
│   └── guild_sync/ (Blizzard API, identity engine, scheduler, crafting, onboarding,
│                     progression_sync, raiderio_client, warcraftlogs_client, wcl_sync,
│                     bnet_character_sync, drift_scanner, raid_booking_service)
│
├── GuildSync WoW Addon (wow_addon/GuildSync/)
└── Companion App (companion_app/guild_sync_watcher.py)
```

> **Key paths:** `src/guild_portal/` (app), `src/sv_common/` (shared services)
> See `reference/DIRECTORY.md` for the full annotated tree.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Web Framework | FastAPI |
| Templates | Jinja2 (server-rendered) |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 + Alembic |
| Discord | discord.py 2.x |
| Auth | JWT (PyJWT) + bcrypt |
| Blizzard API | httpx + OAuth2 |
| Testing | pytest + pytest-asyncio + httpx |
| Process Manager | Docker (docker compose) |
| Reverse Proxy | Nginx |

---

## Design Language

> See `reference/DESIGN.md` for full color palette, typography, and layout patterns.

Dark fantasy / WoW tavern aesthetic. Gold accent (`#d4a84b`), dark backgrounds (`#0a0a0b`/`#141416`), Cinzel headers, Source Sans Pro body. Role colors: Tank=`#60a5fa`, Healer=`#4ade80`, Melee=`#f87171`, Ranged=`#fbbf24`. All colors use CSS custom properties; accent overridden at runtime from `config_cache`.

Admin pages extend `base_admin.html` (NOT `base.html`) — provides shared site-header, collapsible sidebar, app-shell scroll layout.

---

## Directory Structure

> See `reference/DIRECTORY.md` for the full annotated tree.

Key paths:
- `src/guild_portal/` — main application (app.py, api/, pages/, templates/, static/)
- `src/sv_common/` — shared services (auth/, discord/, identity/, guild_sync/, config_cache.py, crypto.py)
- `src/guild_portal/templates/base_admin.html` — admin base template
- `src/guild_portal/static/js/players.js` — Player Manager drag-and-drop
- `src/guild_portal/static/css/main.css` — global styles
- `alembic/versions/` — migration scripts

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://patt_user:PASSWORD@localhost:5432/patt_db

# Auth
JWT_SECRET_KEY=generate-a-strong-random-key   # must be 32+ bytes
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Discord Bot
DISCORD_BOT_TOKEN=your-bot-token-here         # also stored encrypted in DB
DISCORD_GUILD_ID=your-discord-server-id       # also loaded from DB at runtime

# Server
APP_ENV=production
APP_PORT=8100
APP_HOST=0.0.0.0

# Blizzard API (stored encrypted in site_config, but env vars as fallback)
BLIZZARD_CLIENT_ID=your-blizzard-client-id
BLIZZARD_CLIENT_SECRET=your-blizzard-client-secret

# Battle.net OAuth token encryption (separate from JWT key)
BNET_TOKEN_ENCRYPTION_KEY=generate-a-fernet-key

# Guild sync config (also configurable via Admin → Site Config)
GUILD_REALM_SLUG=senjin
GUILD_NAME_SLUG=pull-all-the-things

# Companion app API key
GUILD_SYNC_API_KEY=generate-a-strong-random-key

# NOTE: Channel IDs (audit, crafters corner, raid) are configured via Admin UI,
# stored in common.discord_config — NOT in .env.
```

---

## Database Schema

> Full schema reference: `docs/SCHEMA.md` — current through **migration 0177**.

Key design gotchas (read before writing any DB query):
- `guild_identity.players` is the central identity entity — FK is `players.discord_user_id → discord_users.id`; `discord_users` has **no** `player_id` column
- `common.guild_members` and `common.characters` are **DROPPED** (migration 0139)
- All Discord channel IDs in `common.discord_config` (Admin UI), not `.env`
- `site_config` is single-row, loaded at startup into `sv_common.config_cache`; all modules read from cache
- `enrichment.*` tables are TRUNCATE-rebuilt by stored procs — never FK target from stable tables
- Gear plan slot values use `main_hand_2h`/`main_hand_1h`, never `main_hand`

---

## Deployment & Operations

> See `reference/DEPLOY.md` for Docker environments and server quirks.
> **Git & CI/CD workflow:** `reference/git-cicd-workflow.md` — canonical rules for branching, merging, and releasing.

- **CRITICAL: Never touch prod without explicit permission from Mike.** No SSH against prod DB, no docker exec on prod containers, no prod tags. Dev and test are fair game.
- **Dev:** manual trigger — `gh workflow run deploy-dev.yml -f branch=feature/my-thing`
- **Test:** auto-deploys on push to `main` (i.e. merged PR)
- **Prod:** auto-deploys on `prod-v*` tag — `git tag prod-vX.Y.Z && git push main prod-vX.Y.Z`
- Local tests: `.venv/Scripts/pytest tests/unit/ -v` (no DB needed for unit tests)

---

## Conventions

### Code Style
- Python: Black formatter, isort for imports, type hints everywhere
- SQL: Lowercase keywords, snake_case identifiers
- JavaScript: Vanilla JS, const/let (no var, no framework)
- CSS: Custom properties for all colors/spacing, BEM-ish class names
- HTML: Jinja2 templates, semantic HTML5

### Naming
- Database tables: snake_case, plural (`players`, `wow_characters`, `campaign_entries`)
- API routes: `/api/v1/resource-name` (kebab-case)
- Template files: `snake_case.html`

### Error Handling
- API endpoints: `{"ok": true, "data": {...}}` or `{"ok": false, "error": "message"}`
- All DB operations wrapped in try/except with proper rollback
- User-facing errors friendly; technical details logged server-side

### Git & CI/CD
> Full workflow rules: `reference/git-cicd-workflow.md`

- Commit messages: `feat:` / `fix:` / `docs:` / `chore:` / `refactor:`
- Branch types: `feature/*` (MINOR bump), `fix/*` (PATCH), `hotfix/*` (PATCH — fast lane to prod), `chore/*`, `refactor/*` (no bump)
- Always `--no-ff` on merges; delete branches after merge
- Tag format: `prod-vMAJOR.MINOR.PATCH` (e.g. `prod-v0.2.1`) — prod deploys on this pattern only

### Testing
- See `TESTING.md` for full strategy
- Every phase includes tests; tests must pass before phase is complete
- Run: `pytest tests/ -v` from project root

---

## Current Build Status

> **UPDATE THIS SECTION AT THE END OF EVERY PHASE**
> Full phase-by-phase history: `reference/PHASE_HISTORY.md`

### Current Phase
- **Phase 1.8 — User Activity Logging** — **ALL PHASES COMPLETE** on `feature/user-activity-logging` — ready to merge + tag
  - **Phase A COMPLETE** (commit 312ca88, migration 0178): `common.users` +`last_active_at/last_login_at/login_count`; `common.user_activity` daily rollup table; login stamping in `POST /api/v1/auth/login` **and** `POST /login` (form handler in `auth_pages.py` — fixed commit d533675; both must stamp or browser logins won't record)`
  - **Phase B COMPLETE** (commit c43a34a): `ActivityMiddleware` in `src/guild_portal/middleware/activity.py`; registered in `app.py`; fires background upsert after each authenticated response; skips static/polling paths; 25 unit tests. 1841/1847 suite-wide (6 pre-existing).
  - **Phase C COMPLETE** (commit 0baafeb): Admin Users page — extended query with activity data; `_rel_time()` helper; new stat pills (Active This Week, Never Logged In); new columns (Last Active, Last Login, Logins, 7d Views); expand row showing pages visited this week as tag chips; default sort by `last_active_at DESC NULLS LAST`; 31 unit tests. 1872/1878 suite-wide.
  - **Phase D COMPLETE** (commit 8259b6c): `run_activity_prune()` in `scheduler.py`; deletes `common.user_activity` rows older than 90 days; registered as weekly Sunday 3:30 AM UTC; 4 unit tests. 1876/1882 suite-wide.
- **prod-v0.22.5 — COMPLETE** (migration 0178, PR #39). Phase 1.8 User Activity Logging fully shipped.
- **prod-v0.22.6 — COMPLETE** (hotfix/crafting-sync-6hr). Crafting sync changed from daily to every 6 hours (0/6/12/18 UTC); weekly cadence guard removed; display updated in admin, Crafting Corner, and guide.
- **Last migration:** 0178 (`common.user_activity` table + `common.users` activity columns)
- **Last prod tag:** `prod-v0.22.6`
- **Active branch:** `main`

> Full phase-by-phase history: `reference/PHASE_HISTORY.md`

### What Exists

> Full page/route inventory: `docs/ARCHITECTURE.md` (auth levels, process flows). Admin pages: `docs/OPERATIONS.md`.

**sv_common packages:** `identity`, `auth`, `discord`, `guild_sync` (Blizzard API, scheduler, crafting, onboarding, progression, Raider.IO, WCL, bnet sync, drift scanner, raid booking, AH pricing, attendance_processor), `errors`, `feedback`, `guide_links`

**Public pages:** `/` (index), `/roster` (Avg Raid Parse + Roster Needs), `/crafting-corner`, `/guide`, `/feedback`

**Member pages:** `/my-characters` (unified character sheet — Gear/Raid/M+/Parses/Profs/Market tabs), `/profile` (Battle.net link/unlink)

**Admin pages (Officer+):** campaigns, players, users, availability, raid-tools, data-quality, crafting-sync, bot-settings, reference-tables, audit-log, attendance, quotes, error-routing, progression, warcraft-logs, ah-pricing. GL-only: site-config, gear-plan (BIS sync dashboard), blizzard-api

**Background systems:** Discord bot (role sync, DMs, onboarding, contest agent), GuildSync WoW addon + companion app, setup wizard (`/setup`), auto-booking (`raid_booking_service.py`), Battle.net OAuth + daily token refresh

### Known Gaps / Dormant Features
- **Signup snapshot** — scheduler job runs at event start, not end. On test/dev `Guild sync scheduler skipped` (missing credentials) is expected; Re-snapshot button works manually.
- **u.gg BIS scan rate limiting** — bulk "Sync All" triggers 403s partway through. "Re-sync Errors" button retries with a 2s delay; run it 2–3 times to clear all errors.
- **Legacy M+ dungeons** — prior-expansion dungeons in the current M+ rotation aren't covered by Section A (Catch Up). Run "Sync Legacy Dungeons" once after first deploy; takes several minutes as a background task.
- **Enrich & Classify must run after Section A (Catch Up)** — fetches Wowhead tooltips + runs tier classification. Without it, Midnight tier pieces have `armor_type=NULL` and won't match in `tier_token_attrs`, and crafted items won't appear in slot drawers (quality filter requires Wowhead epic tooltip).
- **Crafted item quality filter is strict (epic only)** — only items with `class="q4"` in `wowhead_tooltip_html` appear in slot drawers. Blues and greens are intentionally excluded.
