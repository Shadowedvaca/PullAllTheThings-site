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

> Full DDL: `reference/SCHEMA.md`. Current through **migration 0059**.

| Schema | Key tables |
|--------|-----------|
| `common` | `guild_ranks`, `users`, `discord_config` (+`bot_token_encrypted`, +7 attendance columns, +`attendance_excuse_if_unavailable`, +`attendance_excuse_if_discord_absent`), `invite_codes`, `screen_permissions`, `site_config` (+`blizzard_client_id/secret_encrypted`, `current_mplus_season_id`, `enable_onboarding`, `connected_realm_id`, +`active_connected_realm_ids`), `rank_wow_mapping` |
| `guild_identity` | `players` (central entity), `wow_characters` (+`last_progression_sync`, +`last_profession_sync`, +**`in_guild`**, +**`last_equipment_sync`** — 0066, +**`race VARCHAR(40)`** — 0080), `discord_users` (+`no_guild_role_since`), `player_characters` (bridge, +`link_source`/`confidence`), `roles`, `audit_issues`, `sync_log`, `onboarding_sessions`, `professions`, `profession_tiers`, `recipes`, `character_recipes`, `crafting_sync_config`, `discord_channels`, `raiderio_profiles`, `battlenet_accounts`, `wcl_config`, `character_parses`, `raid_reports`, `character_raid_progress`, `character_mythic_plus`, `tracked_achievements`, `character_achievements`, `progression_snapshots`, `tracked_items`, `item_price_history`, **`item_sources`** (blizzard_item_id NOT NULL, UNIQUE(blizzard_item_id, instance_type, encounter_name) — item_id FK DROPPED Phase E), **`character_equipment`** (blizzard_item_id NOT NULL — item_id FK DROPPED Phase E), **`gear_plans`** (+`simc_imported_at TIMESTAMPTZ`, +`equipped_source VARCHAR(10) DEFAULT 'blizzard'` — 0094), **`gear_plan_slots`** (blizzard_item_id — desired_item_id FK DROPPED Phase E), **`tier_token_attrs`** (blizzard_item_id PK — was token_item_id FK, changed Phase E), **`item_recipe_links`** (blizzard_item_id, recipe_id FK→recipes, confidence INT CHECK 0–100, match_type VARCHAR(50), UNIQUE(blizzard_item_id,recipe_id) — item_id FK DROPPED Phase E) |
| `ref` | `classes` (+`blizzard_class_id` — 0127), **`specializations`** (moved from guild_identity — 0130), **`hero_talents`** (moved from guild_identity — 0130), **`bis_list_sources`** (u.gg Raid/M+/Overall — 0075; moved from guild_identity — 0130; Method Overall/Raid/M+ — 0149; **Archon M+ + Archon Raid — 0173**, origin='archon', content_type dungeon/raid) |
| `patt` | `campaigns`, `campaign_entries`, `votes`, `campaign_results`, `contest_agent_log`, `guild_quotes` (+`subject_id`), `guild_quote_titles` (+`subject_id`), `quote_subjects`, `player_availability`, `raid_seasons` (+`blizzard_mplus_season_id`, +**`quality_ilvl_map JSONB`**, +**`crafted_ilvl_map JSONB`** — 0099), `raid_events` (+`voice_channel_id`, +`voice_tracking_enabled`, +`attendance_processed_at`, +`is_deleted` BOOLEAN — 0062, +`signup_snapshot_at` — 0063), `raid_attendance` (+`minutes_present`, +`first_join_at`, +`last_leave_at`, +`joined_late`, +`left_early`, +`was_available` BOOLEAN, +`raid_helper_status` VARCHAR(20) — 0063), `recurring_events`, `voice_attendance_log`, **`attendance_rules`** (id, name, group_label, group_type CHECK('promotion'/'warning'/'info'), is_active, target_rank_ids INTEGER[], result_rank_id FK→guild_ranks, conditions JSONB, sort_order, created_at — 0064) |
| `config` | **`bis_scrape_targets`** (261 rows after prod-v0.22.0 Discover URLs; source_id FK→ref.bis_list_sources, spec_id FK→ref.specializations, hero_talent_id FK→ref.hero_talents, content_type, url, preferred_technique, status, items_found, last_fetched), **`slot_labels`** (page_label PK VARCHAR(40), slot_key VARCHAR(20) — expanded to 65+ labels in 0165–0167: bracers, Frost DK weapon variants, IV weapon/shield/helmet aliases, Hunter parsing artifacts; **+rings/Rings → NULL — 0173** (Archon paired-slot expand) — NULL slot_key = resolve positionally), **`wowhead_invtypes`** (invtype_id PK INTEGER, slot_key VARCHAR(20) NOT NULL — 20 Blizzard invtype codes, Wowhead-only — 0160), **`bis_section_overrides`** (spec_id, source_id, content_type, section_key, secondary_section_key VARCHAR(100), primary_note VARCHAR(100), match_note VARCHAR(100), secondary_note VARCHAR(100), PK(spec_id,source_id,content_type) — 0162+0164; secondary_section_key triggers merge pass in rebuild_bis_from_landing) |

**Key design notes:**
- `guild_identity.players` is the central identity entity — 1:1 FK to `discord_users` and `common.users`
- Character ownership via `player_characters` bridge (`link_source` + `confidence` attribution metadata)
- `common.guild_members` and `common.characters` are **DROPPED** (migration 0139) — legacy tables fully removed
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
- **prod-v0.22.2 — COMPLETE** (migration 0176, PR #37, fix/archon-bis-selection). Three Archon fixes shipped as patch: (1) `_parse_archon_page()` capped to 1 item per regular slot / 2 per paired slot (trinket, ring) — was returning all ranked rows as BIS; (2) `my_characters.js` `ORIGIN_LABEL` + `_GP_ORIGIN_LABELS` corrected `archon: 'u.gg'` → `'Archon'` (two maps); (3) migration 0176 adds Archon.gg to `common.guide_sites` (template `https://www.archon.gg/wow/builds/{spec}/{class}/raid`, sort_order=4, orange badge). 1733/1739 unit tests (6 pre-existing failures). **Post-deploy action required: run Enrich & Classify in Admin → Gear Plan to rebuild enrichment.bis_entries with corrected top-pick limits.**
- **Daily BIS Updates — Phase 1.7-E COMPLETE** (no migration, branch `feature/daily-bis-updates`). New `sv_common/email.py`: async `send_email()` via aiosmtplib (STARTTLS port 587, SSL port 465), raises `EmailSendError` on failure. New `guild_sync/bis_email.py`: `compose_bis_report()` builds HTML email with stats header, patch signal alert, added/removed BIS item sections grouped by spec, failure notice, and admin panel link. `scheduler.py`: `run_bis_daily_sync()` INSERT now `RETURNING id`; new `_send_bis_daily_email()` method decrypts SMTP password (via `JWT_SECRET_KEY`), composes + sends email, stamps `email_sent_at` on success, appends note on failure — never crashes the job. `admin_routes.py`: `SiteConfigUpdate` gains smtp/email fields; `smtp_password` plaintext encrypted via Fernet before storing as `smtp_password_encrypted`. `site_config.html`: new "Email / Notifications" card with all SMTP fields (password write-only). `requirements.txt`: +`aiosmtplib>=3.0.0`. 20 new tests (test_bis_email.py); 1788 suite-wide (6 pre-existing failures unchanged). Deployed to dev. **Next: Phase 1.7-F — Admin UI (run history, patch signal, scrape targets toggle, manual trigger).**
- **Daily BIS Updates — Phase 1.7-D COMPLETE** (no migration, branch `feature/daily-bis-updates`). Two new helpers in `bis_sync.py`: `_snapshot_bis_entries(conn)` returns `{(spec_id, source_id, slot, blizzard_item_id): item_name}` from `enrichment.bis_entries`; `_compute_delta(before, after)` pure function returns `(added, removed)` item lists. `run_bis_daily_sync()` now: snapshots before rebuild, calls `_rebuild_bis_from_landing` + `_rebuild_trinket_ratings_from_landing` + `_rebuild_item_popularity_from_landing`, snapshots after, computes delta, persists full stats (`bis_entries_before/after`, `trinket_ratings_before/after`, `delta_added/removed` JSONB) to `landing.bis_daily_runs`. Enrichment failures caught + noted without crashing scrape job. `scheduler.py`: adds `json` import + imports `_rebuild_trinket_ratings_from_landing`, `_snapshot_bis_entries`, `_compute_delta`. 15 new tests (35 total in test_bis_daily_sync.py); 1768 suite-wide (6 pre-existing failures unchanged). Deployed to dev.
- **Daily BIS Updates — Phase 1.7-C COMPLETE** (no migration, branch `feature/daily-bis-updates`). `sync_target()`: computes SHA-256 of raw content; skips `landing.bis_scrape_raw` INSERT when hash matches prior row (status=`'unchanged'`); includes `content_hash` in all new inserts. New `_update_target_backoff()` helper: u.gg always 1-day; changed resets to 1-day; unchanged doubles interval capped at 14 days; called at end of every `sync_target()`. `run_bis_daily_sync()` fully implemented (replaces stub): fetches all `is_active=TRUE` targets, splits due vs skipped by `next_check_at`, calls `sync_target()` per due target with 2s intra-source delay, inserts `landing.bis_daily_runs` row with scrape stats. `scheduler.py`: adds `asyncio` import + `sync_target` import. 9 new tests (20 total); 1753 suite-wide (6 pre-existing failures unchanged). Deployed to dev.
- **Daily BIS Updates — Phase 1.7-B COMPLETE** (no migration, branch `feature/daily-bis-updates`). Two new scheduler jobs: `run_encounter_probe()` (CronTrigger `minute=5`) queries `landing.blizzard_journal_encounters` raid count vs `site_config.bis_encounter_count` baseline; on first run seeds baseline; on count increase resets all non-ugg `is_active` targets to `check_interval_days=1, next_check_at=NOW()`, updates `site_config`, and invalidates in-process cache via `set_bis_encounter_baseline()`; exceptions caught and logged, never propagate. `run_bis_daily_sync()` (CronTrigger `hour=4`) is a stub that logs "not yet implemented". Both registered in `GuildSyncScheduler.start()`. `set_bis_encounter_baseline()` added to `config_cache.py`. 11 new tests; 1744 suite-wide (6 pre-existing failures unchanged). Deployed to dev.
- **Daily BIS Updates — Phase 1.7-A COMPLETE** (migration 0175, branch `feature/daily-bis-updates`). Schema foundations — no behavior changes. Migration adds: `is_active/check_interval_days/next_check_at` to `config.bis_scrape_targets`; `content_hash VARCHAR(64)` to `landing.bis_scrape_raw`; new `landing.bis_daily_runs` table; 7 SMTP/email columns to `common.site_config`. Backfills `next_check_at` on all existing targets; backfills `content_hash` via Python loop; silences known-dead targets. `SiteConfig` + `BisScrapeTarget` models updated; `BisDailyRun` model added; `SmtpConfig` dataclass + 3 cache getters added to `config_cache.py`. 30 new tests; 1732 suite-wide (6 pre-existing failures unchanged). Deployed to dev.
- **Archon BIS Extraction — Phase D COMPLETE** (no migration, dev only, branch `feature/archon-bis-extraction`). Merged PR #36 → main → prod-v0.22.1.
- **Archon BIS Extraction — Phase C COMPLETE** (no migration, dev only, branch `feature/archon-bis-extraction`). `get_matrix()` extended: cells now include `source_updated_at` (subquery on `landing.bis_scrape_raw MAX(source_updated_at)` per target). `gear_plan_admin.js` v1.6.0: `archon` → `'Archon.gg'` + `method` → `'Method.gg'` added to `_ORIGIN_LABELS`; `json_embed_archon` mapped in `_techIcon`; Overall plan type hidden for archon (same pattern as ugg — both have no Overall source); `renderCell` tooltip refactored to show "Source updated: {date}" for archon targets alongside "Last synced". No new tests needed (pure UI/data-pass-through). Popularity % from archon already feeds gear plan via `enrichment.item_popularity` aggregate — no code change required. 1690/1696 suite-wide (4 pre-existing failures unchanged). **Post-C fix (v1.6.1):** `SLOT_ORDER` in `gear_plan_admin.js` still had pre-migration-0155 `main_hand` — updated to `main_hand_2h` + `main_hand_1h`; `_slotLabel` extended with both typed variants. Drill-down and cross-reference panels were showing "— missing —" for all weapon slots.
- **Archon BIS Extraction — Phase B COMPLETE** (migration 0174, dev only, branch `feature/archon-bis-extraction`). `_build_url()` archon branch (spec-first/class-second slug order, M+/raid paths); `_TECHNIQUE_ORDER` + `discover_targets()` archon elif; `_parse_archon_page()` pure function (gear-tables section, JSX tag stripping on headers + JSX regex extraction, paired-slot expansion for trinket+rings); `_extract_archon()` (httpx → __NEXT_DATA__ → parse); `_extract()` dispatcher `json_embed_archon` branch; `sync_target()` writes `source_updated_at` for archon; `rebuild_bis_from_landing()` archon branch in pass 1; `rebuild_item_popularity_from_landing()` extended to include archon; `run_archon_sync()` weekly job (Monday 6AM UTC) registered in scheduler. Migration 0174 adds `json_embed_archon` to `bis_scrape_targets.preferred_technique` CHECK. **Two post-deploy fixes:** CHECK constraint missing (0174) + JSX tags in column headers stripped with `re.sub`. 32 new tests; 1690/1696 suite-wide. **Verified on dev:** 80/80 targets success, 10,285 BIS entries (5,257 M+ + 5,028 Raid), 14,385 popularity rows across all 40 specs.
- **Archon BIS Extraction — Phase A COMPLETE** (migration 0173, dev only). `landing.bis_scrape_raw` +`source_updated_at TIMESTAMPTZ`; `config.slot_labels` seeded with `rings`/`Rings` → NULL; `ref.bis_list_sources` seeded with Archon M+ (dungeon, sort 40) + Archon Raid (sort 41), `origin='archon'`.
- **prod-v0.22.0 — COMPLETE** (migrations 0159–0172, merged PR #35, tagged prod-v0.22.0). Full IV BIS extraction pipeline + slot label fixes + Wowhead off-hand fix + adaptive primary_stats. **After deploying to prod: run Enrich & Classify, then Sync BIS Lists.**
- **BIS Note & Guide Folding — Phase 5 COMPLETE** (no migration). `_iv_classify_tab_label` extended with `raid_instance_names: frozenset[str]`. 12 new tests; 1648/1654 suite-wide.
- **BIS Note & Guide Folding — Phase 4 COMPLETE** (no migration, on dev, branch `feature/iv-bis-extraction`). Section Inventory admin UI now exposes merge config for existing overrides. GET `/page-sections` returns full override fields (`secondary_section_key`, `primary_note`, `match_note`, `secondary_note`) in `override_mappings` entries plus `spec_sections` (all distinct sections for the spec+origin) for the secondary dropdown. `gear_plan_admin.js` v1.5.0: section rows with an override show a "Merge" toggle button; clicking expands a sub-row with secondary section dropdown and 3 note inputs, pre-populated from saved override; `saveMergeConfig()` POSTs all 8 fields to the existing `/override` endpoint. 15 new tests in `tests/unit/test_section_inventory_api.py`; 1636/1642 suite-wide. Deployed to dev.
- **BIS Note & Guide Folding — Phase 3 COMPLETE** (migration 0164, on dev, branch `feature/iv-bis-extraction`). `config.bis_section_overrides` gains `secondary_section_key`, `primary_note`, `match_note`, `secondary_note` columns. New `merge_bis_sections(ctx, primary, secondary, override_row)` function handles the merge pass: matching items stamped with `match_note`; new secondary items inserted with `secondary_note` at next guide_order. `_fetch_section_items()` helper fetches named section items from raw HTML (IV or Method). `rebuild_bis_from_landing()` now two-pass: skips merge targets in pass 1, calls `merge_bis_sections()` in pass 2. `SectionOverrideBody` + `set_section_override()` accept the 4 new merge columns. 18 new tests in `tests/unit/test_bis_merge_engine.py`; 1621/1627 suite-wide. Deployed to dev.
- **BIS Note & Guide Folding — Phase 2 COMPLETE** (no migration, on dev, branch `feature/iv-bis-extraction`). Insertion engine extracted from `rebuild_bis_from_landing()` into `insert_bis_items(ctx, items, note, guide_order_start)` + `BisInsertionContext` dataclass. Engine handles weapon resolution, slot counter–based guide_order, FK validation, and bis_note stamping. `rebuild_bis_from_landing()` now delegates to the engine — zero behavior change. 22 new unit tests in `tests/unit/test_bis_insertion_engine.py`; 1603/1609 suite-wide (6 pre-existing healer URL stale tests). Deployed to dev.
- **BIS Note & Guide Folding — Phase 1 COMPLETE** (on dev, migration 0163, branch `feature/iv-bis-extraction`). `bis_note VARCHAR(100)` added to `enrichment.bis_entries`; `viz.bis_recommendations` updated to expose it; gear_plan_service SELECT includes `vbr.bis_note`; my_characters.js v3.1.0 renders it as `.mcn-bis-note` below item name; CSS v2.5.0. 43/43 gear plan service tests pass; 1581/1587 suite-wide (6 pre-existing healer URL stale tests). **Section Inventory sort fix** (no migration): `page_sections` endpoint now sorts combined data by `(class_name, spec_name, section_key)` in Python after building the response — fixes alphabetical order (was returning in spec_id order from DISTINCT ON).
- **IV BIS Extraction — Phase Z COMPLETE** (on dev, migrations 0159–0162, branch `feature/iv-bis-extraction`). Ready for PR → main → prod.
  - **Z.0** (migrations 0159–0160): Unified slot label tables. Dropped `config.method_slot_labels`. Created `config.slot_labels(page_label PK, slot_key)` — 43 universal text labels, no origin column. Created `config.wowhead_invtypes(invtype_id PK, slot_key)` — 20 Blizzard invtype codes (Wowhead-specific). Removed `_UGG_SLOT_MAP` + `_WOWHEAD_SLOT_MAP` hardcoded dicts from `bis_sync.py`. Added `_resolve_text_slot()` shared helper for positional ring/trinket resolution. All text-label parsers (UGG, Method) use `_load_slot_labels(conn)`; Wowhead uses `_load_wowhead_invtypes(conn)`. 1534 unit tests pass.
  - **Z.1** (migration 0161): Created `landing.iv_page_sections` (now superseded). No items column — raw HTML in `landing.bis_scrape_raw`.
  - **Z.2** (no migration): `_extract_icy_veins()` rewrite + dead code removal. 1550 unit tests pass.
  - **Z.2b** (no migration): image_block tab parsing; `_iv_classify_tab_label` + `_iv_parse_from_image_blocks`. 1565 unit tests pass (47 IV tests).
  - **Z.2.5** (migration 0162): Unified `landing.bis_page_sections(id, spec_id, source_id, page_url, section_key, section_title, sort_order, content_type, is_trinket_section, row_count, is_outlier, outlier_reason, scraped_at, UNIQUE(spec_id, source_id, section_key))` — replaces `landing.method_page_sections` + `landing.iv_page_sections`. Unified `config.bis_section_overrides(spec_id, source_id, content_type, section_key, created_at, PK(spec_id, source_id, content_type))` — replaces `config.method_section_overrides` (adds source_id). Data migrated; old tables dropped. Code: `_upsert_method_sections` + `_upsert_iv_sections` retargeted; `_resolve_method_section` + `_resolve_method_bis_from_db` use `bis_section_overrides`+source_id; `_extract_method` gets source_id param; new `_resolve_iv_section()` for override-aware IV section picking; `bis_routes.py` Method endpoints updated. 1565 unit tests pass.
  - **Z.3** (no migration): Unified Section Inventory admin UI. `GET /api/v1/admin/bis/page-sections?source=icy_veins|method` — deduplicates at (spec_id, section_key), returns sections + coverage gaps. `POST/DELETE /api/v1/admin/bis/page-sections/override` — Method broadcasts to all Method sources; IV uses explicit source_id. Replaced Method.gg Section Inventory panel with unified panel: Icy Veins | Method tabs; trinket badge on IV trinket sections; gap rows show source name + missing content type; Re-parse Sections button visible only on Method tab. gear_plan_admin.js v1.4.0. 1565 unit tests pass.
  - **Z.4** (no migration): IV enrichment pipeline. `rebuild_bis_from_landing()` — new `elif source == "icy_veins":` branch using `_iv_parse_sections` + `_resolve_iv_section` (override-aware). `rebuild_trinket_ratings_from_landing()` — SQL broadened to `IN ('wowhead', 'icy_veins')`; routes IV HTML to `_iv_parse_trinkets_from_raw`. New pure helpers: `_iv_parse_bis_from_raw(html, content_type, slot_map)`, `_iv_parse_trinkets_from_raw(html)`. 1577 unit tests pass.
  - **Post-Z fixes (no migration):** Gap Fill button now shows per-target progress (same pattern as Re-Sync Errors) — `gear_plan_admin.js v1.4.2`. `_iv_bis_role()` fixed: `"healer"` → `"healing"` for Midnight expansion (all 7 healer spec URLs were 404ing with old slug). `sync-gaps-btn` added to `_updateButtonStates` rules.
  - **Known remaining gaps (solvable now via Section Inventory merge overrides):** Blood DK IV Overall (hero-talent-split overalls in area_1/area_2 → configure secondary_section_key merge in Section Inventory); Resto Shaman IV Raid (raid tab named after instance names → configure section override, or wait for Phase 1.5-5 IV classifier fix).
- **Previous: Weapon Build Variant — COMPLETE** (prod-v0.21.1, migrations 0155–0158). Full 3-phase feature shipped.
  - **Phase 1** (migration 0155): `main_hand` split into `main_hand_2h`/`main_hand_1h`; `priority` → `guide_order` on `enrichment.bis_entries`. Shipped prod-v0.21.0.
  - **Phase 2** (migrations 0156–0158): gear plan display rules (`_compute_weapon_display`, `_merge_paired_bis`, `show_off_hand` always True); paperdoll/gear table show active weapon slot only; available items drawer shows all weapon types; BIS sort fixed in `_gpRenderUnifiedTable`; Method parser handles multi-link pool rows + alternative items (guide_order 2+); one-hand/two-hand weapon labels added to `config.method_slot_labels`. 1527 unit tests pass.
  - **Phase 3** (no migration): `populate_from_bis` suppresses off_hand when preferred build is 2H — `_apply_off_hand_rule()` helper; Titan's Grip exception (off_hand BIS item slot_type='two_hand' → keep it); clears existing unlocked off_hand slot from plan when suppressed. 1534 unit tests pass.
- **Previous: Gear Plan Schema Overhaul — COMPLETE** — shipped as `prod-v0.20.0` / `prod-v0.20.1`. All phases A–H deployed to prod. Feature branch `feature/gear-plan-schema-overhaul` merged to main. Patch `prod-v0.20.2`: migrated `gear_needs_routes.py` from `guild_identity.item_sources` / `v_tier_piece_sources` to `enrichment.item_sources` / `viz.tier_piece_sources` — fixes duplicate encounters in Roster Needs. **prod-v0.20.4**: gear plan UI polish (guide mode bar inline on heading, crafted items link to Crafting Corner, wowhead trinket ratings always use overall). **prod-v0.20.5**: gear plan popularity column (Pop. %) — last column before action buttons, changes with guide mode, Overall = weighted combined; paired-slot aggregation for rings/trinkets; tier/catalyst items show boss sources in BIS recs and available items.
  - **Phase A** (migration 0104): created `landing`, `enrichment`, and `viz` schemas. Dual-write added to all 5 ingest paths.
  - **Phase B** (migration 0105): enrichment schema tables + stored procedures. 5 tables, 2 helpers, 8 sprocs.
  - **Phase C** (migration 0106): viz schema views (`viz.slot_items`, `viz.tier_piece_sources`, `viz.crafters_by_item`, `viz.bis_recommendations`). 51 unit tests.
  - **Phase D** (no migration): switched `gear_plan_service.py` to read from viz views + enrichment tables. Net: −458 lines, zero tooltip HTML parsing.
  - **Phase E** (migration 0107): enrichment classification overhaul + item_seasons bridge.
  - **Phase F** (migration 0130): `guild_identity.specializations`, `hero_talents`, `bis_list_sources` → `ref` schema.
  - **Phase G** (migration 0131): `guild_identity.bis_list_entries` and `guild_identity.trinket_tier_ratings` dropped.
  - **Phase H** (migration 0132): `blizzard_item_id` added to `guild_identity.item_recipe_links`; `sp_rebuild_item_recipes` rewritten; various enrichment pipeline fixes. 1439 unit tests pass.
  - **Post-ship cleanup** (migrations 0138–0140): retired "Gear Plan / BIS" admin nav tab (0138); dropped `common.guild_members` + `common.characters` (0139); restored `enrichment.item_set_members` incorrectly dropped in 0139 (0140).
  - **Prod baseline captured**: `reference/archive/prod-baseline-2026-04-13/` — 9 CSVs. Dev backup: `reference/archive/dev-backup-2026-04-13.sql`.
- **Previous: Phase 0 (patch fix)** — `prod-v0.19.1`. Pure sort fix for Roster Needs drill panel.
- **Last migration:** 0176 (prod-v0.22.2 — Archon BIS fix + guide site)
- **Last prod tag:** `prod-v0.22.2`
- **Active branch:** `feature/daily-bis-updates` (Phase 1.7-E complete, deployed to dev)
- **Next planned:** Phase 1.7-F — Admin UI (run history, patch signal, scrape targets toggle, manual trigger). **Post-v0.22.2:** run Enrich & Classify to rebuild enrichment.bis_entries with corrected Archon top-pick logic.
- **Post-Phase E patch migrations (0108–0140):**
  - **0108** — `sp_rebuild_items()` fix: used `'unknown'` instead of `'unclassified'`; caused CHECK constraint violation.
  - **0109** — Tier classification fix: removed `OR target_slot='any'` wildcard; added NOT EXISTS guard for real raid/dungeon source rows.
  - **0110–0122** — Various enrichment pipeline fixes (see git log).
  - **0123** — `sp_rebuild_item_seasons` fix: strict `tt.target_slot = ei.slot_type` match; `sp_update_item_categories` strict slot match.
  - **0124** — Evoker armor type fix: moved class ID 13 from leather to mail group in `sp_rebuild_tier_tokens`.
  - **0125** — `tier_set_ids INTEGER[]` on `patt.raid_seasons` (seeded {1978–1990} for Midnight S1); ROBE→chest in `sp_rebuild_items`.
  - **0126** — `playable_class_ids INTEGER[]` + `quality VARCHAR(20)` on `enrichment.items`; epic-only filter for crafted in `viz.slot_items`.
  - **0127** — `ref` schema created; `guild_identity.classes` → `ref.classes`; `blizzard_class_id` added and seeded; tier class filter uses Blizzard IDs.
  - **0128** — `viz.slot_items` source JOIN restricted to active season instance IDs.
  - **0129** — `CLOAK` inventory_type → `back` slot in `sp_rebuild_items`; BIS hero_talent null-safe filter.
  - **0130** — Phase F: `guild_identity.specializations`, `hero_talents`, `bis_list_sources` → `ref` schema.
  - **0131** — Phase G: `guild_identity.bis_list_entries` and `guild_identity.trinket_tier_ratings` dropped.
  - **0132** — Phase H: `blizzard_item_id` added to `item_recipe_links` + backfill; `sp_rebuild_item_recipes` rewritten.
  - **0133–0137** — Various enrichment pipeline fixes (see git log).
  - **0138** — Retired "Gear Plan / BIS" `screen_permissions` row; removed `/admin/gear-plan` route + helper.
  - **0139** — Dropped `common.guild_members` + `common.characters` (legacy, replaced by guild_identity tables).
  - **0140** — Restored `enrichment.item_set_members` (IF NOT EXISTS guard); incorrectly dropped in 0139 — table has no Python refs but is used by stored procedures `sp_update_item_categories`, `sp_rebuild_item_seasons`, `sp_rebuild_all`.

### What Exists
- **sv_common packages:** identity (ranks, players, chars), auth (bcrypt, JWT, invite codes), discord (bot, role sync, DM, channels, voice_attendance), guild_sync (Blizzard API, scheduler, crafting, onboarding, progression, Raider.IO, WCL, bnet character sync, drift scanner, raid booking, AH pricing, attendance_processor), **errors** (report_error, resolve_issue, get_unresolved — Phase 6.1), **feedback** (submit_feedback() — Phase F.2; stores local record + syncs de-identified payload to Hub at shadowedvaca.com), **guide_links** (pure URL builder — Phase G)
- **Public pages:** `/` (index), `/roster` (**Avg Raid Parse column** — sourced from `character_report_parses`, color-coded, links to WCL profile; **Roster Needs section** below Full Roster — Phase 1E.1/1E.2: hierarchical raid table + flat M+ table, color-coded chips, drill panel, Wowhead tooltips), `/crafting-corner`, `/guide`, `/feedback` (score + free-text form, auth-aware) — no login required
- **Member pages** (logged-in required): **`/my-characters`** — Unified character sheet (UI-1A–1H) — centered header + guide badges + **RIO/WCL/Armory links in guides bar**; HUD stat strip; two-box paperdoll (left: Head→Wrist + weapon separator + Main/Off Hand; right: Hands→Trinket 2, **trinket tier badge below ilvl**); **Gear tab** — plan controls, BIS drawer, SimC import/export, Option C slot table (**EQUIPPED/BIS badges on all item lists**; trinket slots add **Trinket Rankings section** with S/A/B/C/D tier list, Raid/M+/Crafted filter tabs, instance·encounter source, tier badge in Equipped/BIS/Available); **Raid tab** — difficulty tabs + per-boss kill list (current season only); **M+ tab** — overall score + per-dungeon table (current season only); **Parses tab** — three stacked sections (per-boss detail / By Difficulty / By Boss); **Profs tab** — profession grid with Wowhead icons + filterable recipe table; **Market tab** — AH price table with gold formatting + category badges; `/gear-plan` → 302 redirect to `/my-characters`; `/profile` — Battle.net section: Refresh Characters + Unlink + 24-hour note when linked, Link Battle.net with `?next=/profile` when unlinked (H.4)
- **Admin pages** (Officer+ required): `/admin/campaigns`, `/admin/players` (Player Manager), `/admin/users` (expired-token indicator — H.4), `/admin/availability`, `/admin/raid-tools`, `/admin/data-quality`, `/admin/crafting-sync`, `/admin/bot-settings`, `/admin/reference-tables` (**Guide Sites section** — Phase G), `/admin/audit-log`, `/admin/site-config` (GL only), `/admin/progression`, `/admin/warcraft-logs`, `/admin/ah-pricing`, `/admin/attendance`, `/admin/quotes`, `/admin/error-routing`, `/admin/gear-plan` (GL only — BIS sync dashboard), `/admin/blizzard-api` (GL only — Blizzard API Explorer proxy)
- **Settings pages** (rank-gated): Availability, Character Claims, Guide
- **Auth API:** `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`
- **Public API:** `/api/v1/guild/ranks`, `/api/v1/guild/roster` (+`avg_parse`, `wcl_url` per char), `/api/v1/guild/progression`, `/api/v1/guild/parses`, `/api/v1/guild/ah-prices?realm_id=N`, `POST /api/v1/feedback` (public, no auth required)
- **Battle.net OAuth:** `GET /auth/battlenet`, `GET /auth/battlenet/callback`, `DELETE /api/v1/auth/battlenet`; character auto-claim on OAuth; daily token refresh scheduler
- **Onboarding:** active, fires on `on_member_join`, gated by `enable_onboarding` site_config flag
- **Setup wizard:** `/setup` → `/setup/complete` — 9-step first-run wizard; guard middleware redirects until `setup_complete=TRUE`
- **Auto-booking:** `raid_booking_service.py` — books next week's raid 10–20 min after current raid starts
- **GuildSync addon** + **companion app** — functional, syncing guild notes via `/guildsync` WoW slash command

### Known Gaps / Dormant Features
- **`character_report_parses.difficulty` stale on prod** — all existing rows have difficulty=3 (Normal) due to a hardcoded bug in `sync_report_parses` (fixed in UI-1F). After deploying to prod, trigger a WCL sync from **Admin → Warcraft Logs** to correct them. The upsert now includes `difficulty = EXCLUDED.difficulty` so every re-queried report row will be corrected automatically.
- `guild_identity.identity_engine`: some tests skipped due to import error — pre-existing, non-blocking
- **Liberation of Undermine** (encounters 3212–3214) returns 0 WCL rankings — WCL has not yet published rankings for that tier. Will populate automatically once WCL processes it.
- **`compute_attendance` in `wcl_sync.py`** — JSONB `json.loads()` bug fixed in prod-v0.8.3. WCL Attendance admin tab should now work.
- **Signup snapshot** — scheduler job runs at event start, not end. On test/dev `Guild sync scheduler skipped` (missing credentials) is expected; Re-snapshot button works manually.
- **u.gg BIS scan rate limiting** — bulk "Sync All" triggers 403s partway through (~94 targets at prod-v0.20.0 deploy). "Re-sync Errors" button has a 2s delay between per-target calls (v1.2.1) to avoid rate limiting. If errors persist, re-run Re-sync Errors a second time — it will clear in 2–3 passes.
- **Legacy M+ dungeons require "Sync Legacy Dungeons"** — prior-expansion dungeons in the current M+ rotation (e.g. Algeth'ar Academy) are not covered by "Sync Loot Tables". Run "Sync Legacy Dungeons" once after first deploy; it runs as a background task and takes several minutes. Refresh Item Sources when done.
- **Process Tier Tokens must re-run after each Sync Loot Tables** — `enrich_catalyst_tier_items()` adds broad per-boss source rows for tier pieces after every "Sync Loot Tables". Those rows are unflagged until "Process Tier Tokens" runs again and calls `flag_junk_sources(flag_tier_pieces=True)`. Correct workflow: Sync Loot Tables → Enrich Items → Process Tier Tokens → Sync BIS Lists (Steps 1–4 in the admin UI).
- **Enrich Items is a prerequisite for Midnight tier piece sourcing** — Phase 3 of the Enrich Items background job calls `enrich_blizzard_metadata()` which fetches `armor_type` from Blizzard API for BIS items in tier slots with no Wowhead tooltip. Without this, Midnight tier pieces have `armor_type=NULL` and won't be matched in `tier_token_attrs`. Run Enrich Items (Step 2) before checking gear plan sourcing on a fresh install.
- **Crafted item pipeline requires two passes** — Sync Crafted Items stubs items in `wow_items` with `slot_type` and `armor_type` from the Blizzard API, but no Wowhead tooltip. Enrich Items must run after to fetch Wowhead tooltips for those stubs (the quality filter requires `class="q4"` in tooltip HTML). If items still don't appear after running Sync Crafted Items, run Enrich Items next. If Wowhead hasn't indexed the item yet (new expansion), the tooltip fetch will fail silently — item won't appear until Wowhead indexes it.
- **Crafted item quality filter is strict (epic only)** — only items with `class="q4"` in `wowhead_tooltip_html` appear in slot drawers. Blues and greens are intentionally excluded. This applies only to crafted items (items in `item_recipe_links`), not to raid/dungeon drops.
