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
- **Phase 4.6:** Auction House Pricing (migration 0040) — `tracked_items`, `item_price_history`; `connected_realm_id` on `site_config`; `ah_sync.py` (commodities + realm fallback); `ah_service.py` (price helpers); hourly scheduler job at :15; `gold` Jinja2 filter; Market Watch card on index; `/admin/ah-pricing` page
- **Phase 4.7:** Voice Attendance (migration 0041) — `voice_attendance_log` raw events; 7 attendance config columns on `discord_config`; `VoiceAttendanceCog`; `attendance_processor.py`; scheduler every 30 min; `/admin/attendance` season grid with excused toggle + CSV export
- **Phase 4.8:** Quotes 2.0 (migration 0044) — `patt.quote_subjects` table; per-subject `/quote` slash commands; admin `/admin/quotes` page; public index picks random active subject; Discord embed attribution
- **Phase 5.0:** My Characters page (no migration) — `/my-characters` member page; `GET /api/v1/me/characters`; `member_routes.py`; character selector, stat panel, SPA-style panel swap, URL `?char=` param
- **Phase 5.1:** Progression Panel (no migration) — `GET /api/v1/me/character/{id}/progression`; raid progress bars per difficulty; Mythic+ score badge with color tiers
- **Phase 5.2:** WCL Parse Panel (no migration) — `GET /api/v1/me/character/{id}/parses`; best-percentile deduplication; difficulty tabs; WCL color tiers; heroic average summary
- **Phase 5.3:** AH Multi-Realm Market Panel (migration 0045) — `active_connected_realm_ids` on `site_config`; `sync_ah_prices()` accepts list of realm IDs; `get_prices_for_realm()` merges commodity+realm; `GET /api/v1/me/character/{id}/market`; index page realm dropdown; My Characters Market Watch panel
- **Phase 5.4:** Crafting & Raid Prep Panel (no migration) — `GET /api/v1/me/character/{id}/crafting`; Section A: what char can craft (profession/expansion dropdowns + search, client-side filtering); Section B: raid consumables with realm-aware AH prices, 24h trend indicators, low-stock flag; `get_consumable_prices_for_realm()` in `ah_service.py`; all My Characters panels made collapsible with localStorage state

---

## Recent Changes

### Gear Plan Schema Overhaul — Phase B (2026-04-14, migration 0105, dev only)
- **Migration 0105:** 5 enrichment tables (`enrichment.items`, `item_sources`, `item_recipes`, `bis_entries`, `trinket_ratings`), 2 helper functions (`_quality_tracks`, `_tooltip_slot`), 8 stored procedures (`sp_rebuild_items`, `sp_rebuild_item_sources`, `sp_rebuild_item_recipes`, `sp_rebuild_bis_entries`, `sp_rebuild_trinket_ratings`, `sp_update_item_categories`, `sp_flag_junk_sources`, `sp_rebuild_all`).
- `sp_update_item_categories()` classifies items in priority order: crafted (has recipe link) → catalyst (quality_track='C') → tier (/item-set= in tooltip + tier slot) → drop (non-junk source row) → unknown.
- `sp_flag_junk_sources()` marks world_boss null-ID rows and tier piece direct drop sources as `is_junk=TRUE`.
- `sp_rebuild_all()` calls all sprocs in the correct dependency order.
- Admin UI: Step 6 "Rebuild Enrichment" button on `/admin/gear-plan`; `POST /api/v1/admin/bis/rebuild-enrichment` returns per-table counts.
- **Parity validated:** enrichment counts match guild_identity exactly (6884 items, 8481 sources, 5524 BIS, 2517 trinket ratings, 43 recipes).
- Transitional: sprocs read from `guild_identity.*`; full landing-based reads follow in Phase D+.

### Gear Plan Schema Overhaul — Phase A (2026-04-13, migration 0104, dev only)
- **Migration 0104:** created `landing`, `enrichment`, and `viz` schemas. `landing` has 5 insert-only tables with JSONB payloads (blizzard_journal_encounters, blizzard_items, wowhead_tooltips, blizzard_appearances, bis_scrape_raw). `enrichment` and `viz` created as empty schemas.
- Dual-write added to all 5 ingest paths: `item_source_sync.py` (journal encounters + appearances), `item_service.py` (Wowhead tooltips + Blizzard item metadata), `bis_sync.py` (BIS scrape content). Landing writes are best-effort (won't break enrichment on failure).
- `_extract_archon()` and `_extract_wowhead()` updated to return `(slots, ..., raw_html)` — raw content passed up to `sync_target()` for landing insert.
- Prod baseline captured: `reference/archive/prod-baseline-2026-04-13/` (9 CSVs). Dev backup: `reference/archive/dev-backup-2026-04-13.sql`.

### Phase 2B + 2C — Quality Track Mapping + Quality-Aware Ilvl Display (2026-04-13, migrations 0096 + 0099, prod-v0.18.0)
- **Migration 0096:** `wow_items.quality_track VARCHAR(1) CHECK (IN 'V','C','H','M')` — tags catalyst items with quality track derived from Blizzard appearance set name suffix. All 64 Midnight catalyst items tagged `quality_track='C'` (appearance sets contain armor-type variants, not quality-tier variants). Migrations 0097+0098 (scrapped approach) ran on dev and cancelled out; net effect is 0096 only.
- **Migration 0099:** `patt.raid_seasons.quality_ilvl_map JSONB` + `crafted_ilvl_map JSONB` — season-specific ilvl bands per quality track. Seeded for Midnight S1: quality `V{233-250} C{246-263} H{259-276} M{272-289}`, crafted `A{220-233} V{233-246} H{259-272} M{272-285}`. `RaidSeason` ORM model updated. Editable via Admin → Reference Tables → Raid Season Ilvl Maps (`PATCH /api/v1/admin/seasons/{id}`).
- **`gear_plan_service.py`:** Two new helpers — `_noncrafted_target_ilvl(is_bis, equipped_ilvl, equipped_track, quality_ilvl_map)` and `_crafted_target_ilvl(is_bis, equipped_track, crafted_ilvl_map)`. Display rules: BIS slot → next track's max ilvl (V→C max, C→H max, H→M max, M→M max); not BIS → equipped ilvl (V max floor for below-V or empty); crafted → H crafted max unless BIS+H or any M equipped → M crafted max. `NEXT_TRACK` dict added. `get_available_items()` now also queries equipped `blizzard_item_id` and desired `blizzard_item_id` (from `gear_plan_slots`) to compute `is_bis` locally. `get_plan_detail()` uses already-computed `is_bis` per slot.
- **`my_characters.js` (v2.6.2):** BIS star paperdoll: replaced static gold star SVG with faded item icon + star SVG overlay wrapped in Wowhead link at upgrade ilvl. `showGoal` icon link appends `?ilvl=goalItem.target_ilvl`. Drawer "Your Goal" link appends `?ilvl=desired.target_ilvl`. Equipped item links (paperdoll, gear table, drawer) append `?ilvl=equipped_ilvl`.
- **`my_characters.css` (v2.0.2):** `.mcn-slot-icon-bis-wrap`, `.mcn-slot-icon--bis-faded`, `.mcn-slot-icon-star-overlay` — overlay layout for faded icon + star.
- **`admin_routes.py`:** `SeasonUpdate` model extended with `quality_ilvl_map` / `crafted_ilvl_map`; `update_season()` persists and returns both.
- **`reference_tables.html`:** New "Raid Season Ilvl Maps" section with JSON textareas + Save button per season.

### Phase 5.4 (2026-03-17, no migration)
- **`member_routes.py`:** `GET /api/v1/me/character/{id}/crafting` — own-character auth; raw SQL joins `character_recipes` + `recipes` + `professions` + `profession_tiers`; returns `craftable` list (with `tier_name`, `expansion_name`) and `consumables` list via `get_consumable_prices_for_realm()`.
- **`ah_service.py`:** `get_consumable_prices_for_realm(pool, realm_id)` — active tracked items filtered to `category IN ('consumable', 'material')`; merges commodity/realm prices; computes 24h `change_pct`; returns `min_buyout_display` + `wowhead_url` (search format).
- **`my_characters.js`:** Section A rebuilt with profession dropdown, expansion dropdown (cascades, disabled until profession selected), search input (cross-prof, ≥2 chars), `_updateCraftingTable()` / `_onCraftProfChange()` helpers, `_craftableAll` module state, `escHtml()` utility. Section A Status column removed. All `mc-prog-card` panels made collapsible via `makeCardsCollapsible()` helper + `mc-prog-card--collapsed` CSS class.
- **`my_characters.css`:** `.mc-craft-filters`, `.mc-craft-select`, `.mc-craft-search`, `.mc-craft-count`, `.mc-craft-expansion`, collapsible card styles (`div.mc-prog-card__title`, `::after` chevron, `.mc-prog-card--collapsed`).
- **Tests:** 35 new tests in `test_phase_54.py`. **813 tests pass, 69 skip.**
- **Tag:** `v0.1.7`

### Phase 5.3 (2026-03-16, migration 0045)
- **Migration 0045:** `active_connected_realm_ids` (JSONB) added to `common.site_config`; unique constraint on `item_price_history` updated to `(tracked_item_id, snapshot_at, connected_realm_id)`.
- **`ah_sync.py`:** `sync_ah_prices(pool, client, connected_realm_ids: list[int])` — commodities stored as `realm_id=0`; per-realm auctions iterated per realm ID. `get_active_connected_realm_ids(pool, client, days=30)` discovers active realms from character login history, caches in `site_config`.
- **`ah_service.py`:** `get_prices_for_realm(pool, realm_id)` merges commodity baseline + realm-specific rows, prefers realm row. `get_available_realms(pool)` returns distinct realm IDs with recent data.
- **Index page:** realm dropdown switcher when >1 realm available; realm-specific row highlight + footnote; `switchMarketRealm()` JS.
- **My Characters:** `#mc-market` panel — realm-aware prices via `GET /api/v1/me/character/{id}/market`.
- **Tests:** `test_phase_53.py`. **778 tests pass, 69 skip.**

### Phase 5.0–5.2 (2026-03-15–16, no migrations)
- **Phase 5.0:** `/my-characters` page (auth-gated); `GET /api/v1/me/characters`; `member_routes.py`; character selector + stat panel; SPA-style panel swap; `?char=` URL param; default: main > offspec > first alphabetically. `my_characters.html`, `my_characters.css`, `my_characters.js`.
- **Phase 5.1:** `GET /api/v1/me/character/{id}/progression`; raid progress per (raid_name, difficulty); M+ score from `CharacterMythicPlus`; color tiers (gray→pink); `renderProgressionPanel()` with progress bars.
- **Phase 5.2:** `GET /api/v1/me/character/{id}/parses`; best-percentile dedup per (boss, difficulty_int); WCL difficulty tab switching; summary bar (best parse + heroic avg); WCL color tiers; `wcl_configured` flag. **747 tests pass, 69 skip** (at 5.2 completion).

### Phase 4.6 (2026-03-15, migration 0040)
- **Migration 0040:** 2 new tables in `guild_identity` — `tracked_items` (item tracking with category/display_order), `item_price_history` (hourly snapshots with min/median/mean/qty/auctions). `connected_realm_id` added to `common.site_config`. `screen_permission` for `ah_pricing` (Officer+). Seeds 8 common consumables/enchants/gems.
- **`blizzard_client.py`:** 3 new methods — `get_connected_realm_id()` (resolves slug → connected realm ID via regex on href), `get_auctions()` (connected-realm non-commodity auctions), `get_commodities()` (region-wide commodity auctions). All use `dynamic-us` namespace.
- **`ah_sync.py`:** `sync_ah_prices()` (commodities first, falls back to realm auctions for missing items), `_aggregate_auctions()` (handles both `unit_price` and `buyout`), `cleanup_old_prices()` (30-day hourly retention, 180-day max).
- **`ah_service.py`:** `copper_to_gold_str()`, `get_current_prices()`, `get_tracked_items_with_prices()` (includes 24h change), `get_price_trend()`, `get_price_change()`.
- **Scheduler:** `run_ah_sync()` — hourly at :15 UTC; auto-resolves and caches `connected_realm_id`; daily cleanup at hour 0.
- **ORM models:** `TrackedItem`, `ItemPriceHistory` added to `models.py`. `SiteConfig.connected_realm_id` added.
- **Jinja2 filter:** `gold` filter registered in `app.py` lifespan (copper → "Xg Ys" display).
- **Index page:** Market Watch card (gated by having priced items); shows min price, qty, snapshot time. CSS in `landing.css`.
- **Admin page:** `/admin/ah-pricing` — tracked items table with 24h change%, add/remove items, sync status, force sync, resolve realm. All via vanilla JS fetch.
- **Tests:** 44 new tests in `test_phase_46.py`. **583 tests pass, 69 skip.**

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
