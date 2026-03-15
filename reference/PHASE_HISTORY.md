# Phase History

> Full record of completed phases and recent changes.
> Current status summary lives in `CLAUDE.md`.

---

## Completed Phases

- **Phase 0–7:** Platform complete and live (auth, campaigns, voting, Discord bot, contest agent)
- **Phase 2.5A–D:** Guild identity system (Blizzard API, Discord sync, GuildSync addon, integrity checker)
- **Phase 2.6:** Onboarding system (built but NOT activated at the time — on_member_join not wired)
- **Phase 2.7:** Data Model Migration — Clean 3NF rebuild; `players` table as central entity; reference tables; player_characters bridge
- **Phase 2.8:** Crafting Corner — profession/recipe DB, `/crafting-corner` public page, adaptive sync cadence, admin sync page
- **Phase 2.9:** Data Quality Engine — 8-rule registry, targeted mitigations, admin `/admin/data-quality` page
- **Phase 3.0A:** Matching transparency — link_source/confidence on player_characters, coverage dashboard
- **Phase 3.0B:** Iterative rule runner — pluggable matching_rules package, progressive discovery, per-rule results UI
- **Phase 3.0C:** Drift Detection — link_contradicts_note, duplicate_discord, stale_discord_link rules; drift_scanner.py; drift panel on Data Quality page
- **Phase 3.0D:** Player Manager QoL — player deletion guard, `/admin/users` page, alias chips, `_compute_best_rank` helper
- **Phase 3.1:** Admin Availability Dashboard — `patt.recurring_events` table, 7-day availability grid, event day config, `GET /admin/availability`
- **Phase 3.2:** Index Page Revamp — officers, recruiting needs, and weekly schedule all live from DB
- **Phase 3.3:** Public Roster View — `/roster` page with Full Roster, Composition, and Schedule tabs; Wowhead comp link; legacy redirects
- **Phase 3.4:** Admin Raid Tools — Raid-Helper API integration, event builder with roster preview, `GET /admin/raid-tools`
- **Phase 3.5:** Auto-Booking Scheduler — background loop creates next week's Raid-Helper event 10–20 min after raid starts, posts Discord announcement
- **Roster Initiate Filtering + Raid Hiatus (migration 0030):** `on_raid_hiatus` flag on players; initiates filtered from comp tab; New Members box; Show Initiates checkbox on roster
- **Phase 4.0:** Config Extraction & Genericization (migration 0032) — `common.site_config` single-row table, `sv_common.config_cache` in-process cache, `common.rank_wow_mapping`, mito tables renamed to guild_quotes/guild_quote_titles, `/quote` bot command, `/admin/site-config` GL-only page, all hardcoded guild name/color/realm refs removed from code
- **Phase 4.1:** First-Run Setup Wizard (migration 0033) — 9-step web wizard activated when `setup_complete=FALSE`; encrypted credential storage (Fernet/JWT_SECRET_KEY); Discord token/guild verification; Blizzard API verification; rank naming + WoW rank mapping UI; Discord role/channel assignment; admin account bootstrap; guard middleware redirects all routes to `/setup` until complete; setup routes become 404 after completion
- **Phase 4.2:** Docker Packaging & Environments — `Dockerfile`, `docker-entrypoint.sh`, `docker-compose.yml` (generic), `docker-compose.patt.yml` (PATT 3-env), `Caddyfile` + `Caddyfile.patt`, `.env.template`, `.dockerignore`; updated `setup_postgres.sql` to be Docker-generic; updated GitHub Actions deploy workflow to use Docker
- **Phase 4.3:** Blizzard API Expansion & Last-Login Optimization (migration 0034) — 5 new tables (`character_raid_progress`, `character_mythic_plus`, `tracked_achievements`, `character_achievements`, `progression_snapshots`); 2 new columns on `wow_characters` (`last_progression_sync`, `last_profession_sync`); `current_mplus_season_id` on `site_config`; `should_sync_character()` helper; 3 new Blizzard API methods (raids, M+, achievements); `progression_sync.py`; last-login optimization applied to crafting sync; scheduler updated with progression pipeline + weekly sweep (Sunday 4:30 AM); `/admin/progression` page
- **Phase 4.4:** Raider.IO Integration (migration 0036) — `guild_identity.raiderio_profiles` table; `raiderio_client.py`; `sync_raiderio_profiles()` in `progression_sync.py`; scheduler integration; roster API includes `rio_score`, `rio_color`, `rio_raid_prog`, `rio_url`; roster page adds sortable M+ Score and Raid Prog columns; `/api/v1/guild/progression` public endpoint
- **Phase 4.4.1:** Battle.net OAuth Account Linking (migration 0037) — `guild_identity.battlenet_accounts` table; `encrypt_bnet_token`/`decrypt_bnet_token` in `sv_common/crypto.py`; `GET /auth/battlenet` + callback; `DELETE /api/v1/auth/battlenet`; Battle.net Connection section on `/profile`
- **Phase 4.4.2:** Character Auto-Claim on OAuth (no migration) — `bnet_character_sync.py`; `sync_bnet_characters()` + `get_valid_access_token()`; OAuth callback calls sync inline; Player Manager + settings page BNet badges; scheduler: `run_bnet_character_refresh()` daily 3:15 AM UTC
- **Phase 4.4.3:** Onboarding Activation & OAuth Integration (migration 0038) — `enable_onboarding` on `site_config`; `on_member_join` wired; `_auto_provision()` → `oauth_pending`; `update_onboarding_status()` called from bnet callback; deadline_checker; `/onboard-start`, `/onboard-simulate-oauth`, `/resend-oauth` officer commands; bot token loaded from encrypted DB
- **Phase 4.4.4:** Data Quality Simplification (no migration) — Fuzzy matching rules (`NameMatchRule`, `NoteGroupRule`) deleted; `matching_rules/` registry returns `[]`; `note_mismatch` and `link_contradicts_note` retired; Data Quality page: OAuth Coverage panel (verified/total bar + "Send Reminder" per member); `GET /admin/oauth-coverage`; Settings → Characters "Add by Name" form
- **Phase 4.5:** Warcraft Logs Integration (migration 0039) — `wcl_config`, `character_parses`, `raid_reports`; `warcraftlogs_client.py` (OAuth2 + GraphQL); `wcl_sync.py`; scheduler daily 5 AM; `/admin/warcraft-logs` page; `GET /api/v1/guild/parses` public endpoint

---

## Recent Changes

### Phase 4.5 (2026-03-15, migration 0039)
- **Migration 0039:** 3 new tables in `guild_identity` — `wcl_config` (single-row), `character_parses` (best WCL percentile per char/encounter/difficulty/spec), `raid_reports` (guild reports with attendee JSONB). `screen_permission` for `warcraft_logs` added.
- **`warcraftlogs_client.py`:** `WarcraftLogsClient` — OAuth2 client credentials grant, `_query()` GraphQL executor, `get_character_parses()`, `get_guild_reports()`, `get_report_fights()`, `get_character_rankings_for_encounter()`, `verify_credentials()`. `WarcraftLogsError` exception class.
- **`wcl_sync.py`:** `load_wcl_config()`, `sync_guild_reports()` (skips existing, fetches fight details + attendees), `sync_character_parses()` (batched, 2s/batch rate throttle, upserts best-per-encounter), `_parse_zone_rankings()`, `compute_attendance()`.
- **Scheduler:** `run_wcl_sync()` — daily at 5 AM UTC; loads + decrypts config from DB; non-fatal.
- **ORM models:** `WclConfig`, `CharacterParse`, `RaidReport` added to `models.py`.
- **Admin page:** Configuration card, Sync Status card, Recent Reports table, Attendance Grid (last 10 reports × players), Top Parses table.
- **Admin API:** `GET/PATCH /admin/warcraft-logs/config`, `POST /admin/warcraft-logs/verify`, `POST /admin/warcraft-logs/trigger`, `GET /admin/warcraft-logs/reports`, `GET /admin/warcraft-logs/attendance`, `GET /admin/warcraft-logs/parses`.
- **Public API:** `GET /api/v1/guild/parses` — heroic parses grouped by character, no auth.
- **Tests:** 40 new tests in `test_phase_45.py`. **539 tests pass, 69 skip.**

### Phase 4.4.4 (2026-03-15, no migration)
- **Matching rules removed:** `NameMatchRule` and `NoteGroupRule` deleted; `get_registered_rules()` returns `[]`. Character ownership via Battle.net OAuth or manual add only.
- **Rules retired:** `note_mismatch` and `link_contradicts_note` removed from `RULES` registry, `DETECT_FUNCTIONS`, and `run_integrity_check()`. `DRIFT_RULE_TYPES` = `{"duplicate_discord", "stale_discord_link"}`. Stale DB rows purged.
- **Data Quality page:** Coverage panel removed. New **OAuth Coverage** panel: progress bar + unverified member table + "Send Reminder" button.
- **New admin endpoints:** `GET /admin/oauth-coverage`; `POST /admin/players/{player_id}/send-oauth-reminder`.
- **Player Manager:** `players-data` includes `bnet_verified: bool`. `players.js` renders verification badge.
- **Settings → Characters:** "Add by name" form. `POST /api/v1/settings/characters` (DB lookup → Blizzard API fallback, `link_source='manual_claim'`). `DELETE /api/v1/settings/characters/{id}` (403 for battlenet_oauth chars).
- **Tests:** 4 old matching test files deleted; 16 new tests in `test_phase_444.py`. **499 tests pass, 69 skip.**

### Phase 4.4.3 (2026-03-15, migration 0038)
- **Migration 0038:** `enable_onboarding BOOLEAN NOT NULL DEFAULT TRUE` added to `common.site_config`.
- **Bot startup:** asyncpg pool first, then resolves bot token from `discord_config.bot_token_encrypted` (Fernet decrypt), falls back to env. `on_ready` reads `guild_discord_id` from DB.
- **Bot Connection admin UI:** `PATCH /api/v1/admin/bot-connection` (GL-only). Admin → Bot Settings shows Bot Connection card.
- **Onboarding flow:** `on_member_join` checks `is_onboarding_enabled()`. `_auto_provision()` → `oauth_pending`; OAuth DM; `_poll_for_oauth_complete()` (10 × 60s). `update_onboarding_status()` called from bnet callback. Deadline: 24h reminder, 48h `abandoned_oauth`.
- **Conversation simplification:** alts question removed — flow: main character → confirmation → verification.
- **`on_message` DM gate:** help embed suppressed during active onboarding states.
- **Officer commands:** `/onboard-start`, `/onboard-simulate-oauth`, `/resend-oauth`.
- **`set_app_url`/`get_app_url`** added to `config_cache.py`.

### Phase 4.4.2 (2026-03-14, no migration)
- `bnet_character_sync.py` — `sync_bnet_characters()` fetches `/profile/user/wow`, filters home realm + level >= 10, upserts `wow_characters` + `player_characters` (link_source='battlenet_oauth', confidence='high'). `get_valid_access_token()` decrypts stored token, refreshes if expired.
- OAuth callback: `await db.commit()` before calling sync (SQLAlchemy must commit before asyncpg pool sees the row).
- **`player_characters` has `created_at`, NOT `linked_at`** — upsert omits the timestamp column.
- Player Manager + settings page: BNet badges, 🔒 Locked label, manual claim hidden when BNet linked.
- `profile_unclaim_character` blocks unclaiming battlenet_oauth chars.
- **528 tests pass, 69 skip.**

### Phase 4.4 (2026-03-13, migration 0036)
- `raiderio_client.py` — `RaiderIOClient` with `get_character_profile()`, `get_guild_profiles()` (batched), `_parse_profile()`. `sync_raiderio_profiles()` in `progression_sync.py`. Roster API: `rio_score`, `rio_color`, `rio_raid_prog`, `rio_url` on all character dicts. Roster page: M+ Score + Raid Prog columns (sortable). `/api/v1/guild/progression` endpoint. **475 tests pass, 69 skip.**
- Phase 4.3 complete: `should_sync_character()`, 3 new Blizzard API methods, `progression_sync.py`, crafting sync stamps `last_profession_sync`. Scheduler: progression pipeline + weekly sweep Sunday 4:30 AM UTC. `/admin/progression`. Migration 0034: 5 new tables, 2 new columns on `wow_characters`. **455 tests pass, 69 skip.**
- Phase 4.2 complete: Docker packaging. `Dockerfile` + `docker-entrypoint.sh`. Generic `docker-compose.yml`. `docker-compose.guild.yml` (3 envs). GitHub Actions updated. Production migrated from systemd to Docker.
- Phase 4.1 complete: First-Run Setup Wizard. **430 tests pass, 69 skip.**
- Admin nav revamp: `base_admin.html` includes shared `site-header`; app-shell layout with independent scrolling.
- Nginx static path: updated to `src/guild_portal/static/` in live config + `deploy/nginx/`.
- Phase 4.0 complete: genericization + migration 0032. **418 tests pass, 69 skip.**

---

## Recent Bug Fixes

### 2026-03-08 (no migration)
- **Discord sync `$4` type error:** asyncpg NULL inference in CASE expressions — fixed with `$4::varchar` cast in `sync_discord_members()`.
- **Audit report embed overflow:** `reporter.py` char-count-aware batching — flushes before hitting 5900 chars/message.
- **Spurious "Discord member not found" warnings:** `reconcile_player_ranks()` now skips departed members (`is_present=FALSE`).
- **Fully-departed player purge:** `purge_fully_departed_players()` in `discord_sync.py` — deletes player + discord_user + website account when no linked characters AND not present in Discord. Posts embed to audit channel.

### 2026-03-07 (no migration)
- **Player Manager character badges:** replaced legacy `M`/`A` badges + toggle with read-only text labels. Gold `Main` / blue `Off`. SQL CASE returns `'main+offspec'` when `main_character_id = offspec_character_id`.
- **Front page recruiting needs:** query uses `COALESCE(p.main_spec_id, wc.active_spec_id)`, excludes initiates (`gr.level > 1`), filters `on_raid_hiatus IS NOT TRUE`. **`preferred_role` does NOT exist on `guild_identity.players`.**
- **Front page weekly schedule:** was silently empty due to query error corrupting the SQLAlchemy session — fixed as a side effect.
