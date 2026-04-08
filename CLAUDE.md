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
│   └── guild_identity.* (players, wow_characters, discord_users, player_characters,
│                          roles, classes, specializations, audit_issues, sync_log,
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

> Full DDL: `reference/SCHEMA.md`. Current through **migration 0059**.

| Schema | Key tables |
|--------|-----------|
| `common` | `guild_ranks`, `users`, `discord_config` (+`bot_token_encrypted`, +7 attendance columns, +`attendance_excuse_if_unavailable`, +`attendance_excuse_if_discord_absent`), `invite_codes`, `screen_permissions`, `site_config` (+`blizzard_client_id/secret_encrypted`, `current_mplus_season_id`, `enable_onboarding`, `connected_realm_id`, +`active_connected_realm_ids`), `rank_wow_mapping` |
| `guild_identity` | `players` (central entity), `wow_characters` (+`last_progression_sync`, +`last_profession_sync`, +**`in_guild`**, +**`last_equipment_sync`** — 0066, +**`race VARCHAR(40)`** — 0080), `discord_users` (+`no_guild_role_since`), `player_characters` (bridge, +`link_source`/`confidence`), `roles`, `classes`, `specializations`, `audit_issues`, `sync_log`, `onboarding_sessions`, `professions`, `profession_tiers`, `recipes`, `character_recipes`, `crafting_sync_config`, `discord_channels`, `raiderio_profiles`, `battlenet_accounts`, `wcl_config`, `character_parses`, `raid_reports`, `character_raid_progress`, `character_mythic_plus`, `tracked_achievements`, `character_achievements`, `progression_snapshots`, `tracked_items`, `item_price_history`, **`wow_items`**, **`item_sources`**, **`hero_talents`**, **`bis_list_sources`** (5 seed rows; display names updated to "u.gg Raid/M+/Overall" — 0075; `origin='archon'` stays as code id), **`bis_list_entries`** (`hero_talent_id=NULL` for Wowhead entries — 0076), **`character_equipment`**, **`gear_plans`**, **`gear_plan_slots`**, **`bis_scrape_targets`** (Wowhead targets use `hero_talent_id=NULL` — 0076), **`bis_scrape_log`** (all 0066) |
| `patt` | `campaigns`, `campaign_entries`, `votes`, `campaign_results`, `contest_agent_log`, `guild_quotes` (+`subject_id`), `guild_quote_titles` (+`subject_id`), `quote_subjects`, `player_availability`, `raid_seasons` (+`blizzard_mplus_season_id`), `raid_events` (+`voice_channel_id`, +`voice_tracking_enabled`, +`attendance_processed_at`, +`is_deleted` BOOLEAN — 0062, +`signup_snapshot_at` — 0063), `raid_attendance` (+`minutes_present`, +`first_join_at`, +`last_leave_at`, +`joined_late`, +`left_early`, +`was_available` BOOLEAN, +`raid_helper_status` VARCHAR(20) — 0063), `recurring_events`, `voice_attendance_log`, **`attendance_rules`** (id, name, group_label, group_type CHECK('promotion'/'warning'/'info'), is_active, target_rank_ids INTEGER[], result_rank_id FK→guild_ranks, conditions JSONB, sort_order, created_at — 0064) |

**Key design notes:**
- `guild_identity.players` is the central identity entity — 1:1 FK to `discord_users` and `common.users`
- Character ownership via `player_characters` bridge (`link_source` + `confidence` attribution metadata)
- `common.guild_members` and `common.characters` are **legacy** — still in DB, removed from ORM/code
- All Discord channel IDs in `common.discord_config` (Admin UI), not `.env`
- `site_config` is single-row, loaded at startup into `sv_common.config_cache`; all modules read from cache
- `rank_wow_mapping` maps WoW guild rank indices (0–9) to platform rank IDs

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
- **Phase UI-1B (complete)** — Stat strip + center panel switching at `/my-characters-new`. New `GET /api/v1/me/character/{id}/summary` endpoint (avg_ilvl, mplus_score/color, raid_summary, avg_parse, profession_count). Slim HUD tab bar (Gear/M+/Raid/Parses/Profs/Market) across top of center panel — always visible, gold underline on active tab, detail area below. Replaced initial card grid after design review (Option B mockup selected). Phase UI-1A also complete: foundation/header (migration 0080, race field, centered header with WoW icons, guide badge links, top-right selector).
- **Branch:** `feature/gear-plan-phase-1d`
- **Tests:** 1264 pass (2 pre-existing bnet failures unchanged)
- **Last migration:** 0080
- **Last prod tag:** `prod-v0.11.2`
- **Active branch:** `feature/gear-plan-phase-1d`
- **Next:** Phase UI-1C — Paperdoll redesign (new two-box slot cards: upgrade box + equipped box). No API changes; reuses existing gear plan endpoints.

### What Exists
- **sv_common packages:** identity (ranks, players, chars), auth (bcrypt, JWT, invite codes), discord (bot, role sync, DM, channels, voice_attendance), guild_sync (Blizzard API, scheduler, crafting, onboarding, progression, Raider.IO, WCL, bnet character sync, drift scanner, raid booking, AH pricing, attendance_processor), **errors** (report_error, resolve_issue, get_unresolved — Phase 6.1), **feedback** (submit_feedback() — Phase F.2; stores local record + syncs de-identified payload to Hub at shadowedvaca.com), **guide_links** (pure URL builder — Phase G)
- **Public pages:** `/` (index), `/roster` (**Avg Raid Parse column** — sourced from `character_report_parses`, color-coded, links to WCL profile), `/crafting-corner`, `/guide`, `/feedback` (score + free-text form, auth-aware) — no login required
- **Member pages** (logged-in required): `/my-characters` — character selector + stat panel + **Spec Guide Links panel** (Phase G — Wowhead/Icy Veins/u.gg badges with spec dropdown) + progression panel (raid progress + M+ score; Phase 5.1) + WCL parse panel (sourced from `character_report_parses`; Phase 5.2) + Market panel (realm-aware AH prices; Phase 5.3) + Crafting & Raid Prep panel (Phase 5.4) + **Refresh Characters button** (H.3); `/profile` — Battle.net section: Refresh Characters + Unlink + 24-hour note when linked, Link Battle.net with `?next=/profile` when unlinked (H.4); **`/gear-plan`** — Personal gear plan (Phase 1D) — 16-slot table, expand drawer, BIS population, SimC import/export, upgrade track computation; **`/my-characters-new`** — Unified character sheet in progress (Phase UI-1B) — centered header with WoW icons + guide badges; slim HUD stat strip (Gear/M+/Raid/Parses/Profs/Market) with real data; active tab gold underline; detail area below strip
- **Admin pages** (Officer+ required): `/admin/campaigns`, `/admin/players` (Player Manager), `/admin/users` (expired-token indicator — H.4), `/admin/availability`, `/admin/raid-tools`, `/admin/data-quality`, `/admin/crafting-sync`, `/admin/bot-settings`, `/admin/reference-tables` (**Guide Sites section** — Phase G), `/admin/audit-log`, `/admin/site-config` (GL only), `/admin/progression`, `/admin/warcraft-logs`, `/admin/ah-pricing`, `/admin/attendance`, `/admin/quotes`, `/admin/error-routing`, `/admin/gear-plan` (GL only — BIS sync dashboard)
- **Settings pages** (rank-gated): Availability, Character Claims, Guide
- **Auth API:** `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`
- **Public API:** `/api/v1/guild/ranks`, `/api/v1/guild/roster` (+`avg_parse`, `wcl_url` per char), `/api/v1/guild/progression`, `/api/v1/guild/parses`, `/api/v1/guild/ah-prices?realm_id=N`, `POST /api/v1/feedback` (public, no auth required)
- **Battle.net OAuth:** `GET /auth/battlenet`, `GET /auth/battlenet/callback`, `DELETE /api/v1/auth/battlenet`; character auto-claim on OAuth; daily token refresh scheduler
- **Onboarding:** active, fires on `on_member_join`, gated by `enable_onboarding` site_config flag
- **Setup wizard:** `/setup` → `/setup/complete` — 9-step first-run wizard; guard middleware redirects until `setup_complete=TRUE`
- **Auto-booking:** `raid_booking_service.py` — books next week's raid 10–20 min after current raid starts
- **GuildSync addon** + **companion app** — functional, syncing guild notes via `/guildsync` WoW slash command

### Known Gaps / Dormant Features
- `guild_identity.identity_engine`: some tests skipped due to import error — pre-existing, non-blocking
- **Liberation of Undermine** (encounters 3212–3214) returns 0 WCL rankings — WCL has not yet published rankings for that tier. Will populate automatically once WCL processes it.
- **`compute_attendance` in `wcl_sync.py`** — JSONB `json.loads()` bug fixed in prod-v0.8.3. WCL Attendance admin tab should now work.
- **Signup snapshot** — scheduler job runs at event start, not end. On test/dev `Guild sync scheduler skipped` (missing credentials) is expected; Re-snapshot button works manually.
- **256 `wow_items` with `slot_type='other'`** — items stubbed before the Wowhead `slotbak` regression was fixed. Re-run "Sync Loot Tables" on `/admin/gear-plan` to trigger `enrich_unenriched_items()` which picks up all `slot_type='other'` rows.
- **u.gg BIS scan rate limiting** — ~69 healer/tank targets returned 403 on prod (Hillsboro OR IP) during the bulk fresh re-sync at prod-v0.11.0. Use "Re-sync Errors" button on `/admin/gear-plan` (after rate limit clears) to retry only failed targets without a full re-scan.
