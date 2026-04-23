# PATT Database Schema

> **Canonical schema reference — current through migration 0177.**
> Detailed DDL for the original core tables (migrations 0001–0044) is below.
> Newer tables are documented in the schema overview section immediately below.

---

## Schema Overview (current through migration 0177)

Nine schemas in use. `enrichment`, `landing`, `viz`, `ref`, `config`, `log` were added during the Gear Plan Schema Overhaul (prod-v0.20.0+).

### `common` — shared infrastructure
`guild_ranks`, `users`, `invite_codes`, `screen_permissions`, `rank_wow_mapping`, `error_log`, `error_routing`, `feedback_submissions`, `guide_sites`

`discord_config` — single-row bot config; columns include `bot_token_encrypted`, 7 attendance toggle columns, `attendance_excuse_if_unavailable BOOL`, `attendance_excuse_if_discord_absent BOOL`, `landing_zone_channel_id`

`site_config` — single-row app config; loaded at startup into `config_cache`. Columns include `blizzard_client_id/secret_encrypted`, `current_mplus_season_id`, `enable_onboarding`, `connected_realm_id`, `active_connected_realm_ids`, 7 SMTP/email fields (added 0175).

> `common.guild_members` and `common.characters` **DROPPED** (migration 0139).

### `guild_identity` — identity + guild sync

**Identity:**
- `players` — central entity; `discord_user_id FK→discord_users.id` (**FK is on players, not discord_users**)
- `discord_users` — `discord_id, username`; **no `player_id` column**; join via `players.discord_user_id`
- `wow_characters` — `+in_guild BOOL`, `+last_equipment_sync TIMESTAMPTZ` (0066), `+race VARCHAR(40)` (0080), `+last_progression_sync`, `+last_profession_sync`
- `player_characters` — bridge table; `+link_source`, `+confidence`
- `roles`, `onboarding_sessions`, `audit_issues`, `sync_log`

**Gear plan:**
- `gear_plans` — `+simc_imported_at TIMESTAMPTZ`, `+equipped_source VARCHAR(10) DEFAULT 'blizzard'` (0094)
- `gear_plan_slots` — `blizzard_item_id` (no item_id FK — dropped Phase E); slot values use `main_hand_2h`/`main_hand_1h`, never `main_hand`; `excluded_item_ids INTEGER[]`
- `character_equipment` — `blizzard_item_id NOT NULL` (no item_id FK)
- `item_sources` — `blizzard_item_id NOT NULL`, `UNIQUE(blizzard_item_id, instance_type, encounter_name)`; no item_id FK
- `item_recipe_links` — `blizzard_item_id NOT NULL`, `recipe_id FK→recipes`, `UNIQUE(blizzard_item_id, recipe_id)`; no item_id FK
- `tier_token_attrs` — `blizzard_item_id` is PK (not FK)

**WCL / progression / market:**
`raiderio_profiles`, `character_parses`, `raid_reports`, `character_report_parses`, `character_raid_progress`, `character_mythic_plus`, `tracked_achievements`, `character_achievements`, `progression_snapshots`, `tracked_items`, `item_price_history`, `raid_boss_counts`

**Crafting:**
`professions`, `profession_tiers`, `recipes`, `character_recipes`, `crafting_sync_config`

**Discord:**
`discord_channels`, `battlenet_accounts`, `wcl_config`

> **Dropped:** `wow_items` (0146), `v_tier_piece_sources` (0145), `bis_list_entries` + `trinket_tier_ratings` (0131), `specializations` + `hero_talents` + `bis_list_sources` moved to `ref` (0130)

### `ref` — static WoW reference data
- `classes` — `+blizzard_class_id` (0127)
- `specializations` — moved from `guild_identity` (0130)
- `hero_talents` — moved from `guild_identity` (0130)
- `bis_list_sources` — u.gg Raid/M+/Overall (`origin='ugg'`); Method Overall/Raid/M+; Archon M+ + Archon Raid (`origin='archon'`, `content_type dungeon/raid` — 0173)

### `patt` — app features
`campaigns`, `campaign_entries`, `votes`, `campaign_results`, `contest_agent_log`, `guild_quotes` (`+subject_id`), `guild_quote_titles` (`+subject_id`), `quote_subjects`, `player_availability`, `recurring_events`, `voice_attendance_log`

`raid_seasons` — `+blizzard_mplus_season_id`, `+quality_ilvl_map JSONB`, `+crafted_ilvl_map JSONB` (0099), `+tier_set_ids INTEGER[]`

`raid_events` — `+voice_channel_id`, `+voice_tracking_enabled`, `+attendance_processed_at`, `+is_deleted BOOLEAN` (0062), `+signup_snapshot_at` (0063)

`raid_attendance` — `+minutes_present`, `+first_join_at`, `+last_leave_at`, `+joined_late`, `+left_early`, `+was_available BOOLEAN`, `+raid_helper_status VARCHAR(20)` (0063)

`attendance_rules` — `id, name, group_label, group_type CHECK('promotion'/'warning'/'info'), is_active, target_rank_ids INTEGER[], result_rank_id FK→guild_ranks ON DELETE SET NULL, conditions JSONB, sort_order` (0064)

### `config` — scraping + slot configuration
`bis_scrape_targets` — `source_id FK→ref.bis_list_sources`, `spec_id FK→ref.specializations`, `hero_talent_id FK→ref.hero_talents`, `content_type`, `url`, `preferred_technique CHECK` (includes `json_embed_archon` — 0174), `status`, `items_found`, `last_fetched`, `is_active`, `check_interval_days`, `next_check_at` (0175); `UNIQUE(source_id, spec_id, url)`

`slot_labels` — `page_label PK VARCHAR(40)`, `slot_key VARCHAR(20)`; 65+ labels; `NULL slot_key` = expand to paired slots (rings, trinkets)

`wowhead_invtypes` — `invtype_id PK INTEGER`, `slot_key VARCHAR(20) NOT NULL`; 20 Blizzard invtype codes (Wowhead-specific)

`bis_section_overrides` — `spec_id, source_id, content_type, section_key, secondary_section_key VARCHAR(100), primary_note, match_note, secondary_note VARCHAR(100)`; `PK(spec_id, source_id, content_type)`; `secondary_section_key` triggers merge pass in `rebuild_bis_from_landing`

### `landing` — raw ingest from Blizzard API + Wowhead
`blizzard_items`, `blizzard_item_sources`, `blizzard_item_icons`, `blizzard_item_sets`, `blizzard_journal_instances`, `blizzard_journal_encounters`, `blizzard_item_quality_tracks`, `blizzard_appearances`, `crafted_items`, `wowhead_tooltips`

`bis_scrape_raw` — `+content_hash VARCHAR(64)` (0175), `+source_updated_at TIMESTAMPTZ` (0173; stores Archon page.lastUpdated)

`bis_page_sections` — `spec_id, source_id, page_url, section_key, section_title, sort_order, content_type, is_trinket_section, row_count, is_outlier, outlier_reason, scraped_at`; `UNIQUE(spec_id, source_id, section_key)`

`bis_daily_runs` — added 0175; scrape job run stats

### `enrichment` — processed + classified items (TRUNCATE-rebuilt by stored procs)
- `items` — ~11,700 rows; `primary_stats TEXT[]` (0172)
- `item_sources` — ~8,150 rows
- `item_recipes` — ~200 rows
- `item_seasons` — ~970 rows
- `item_set_members` — ~4,900 rows; **used by stored procs only, no Python refs**
- `tier_tokens` — 21 rows
- `bis_entries` — ~6,500 rows; `guide_order SMALLINT NOT NULL DEFAULT 1`; `bis_note VARCHAR(100)` nullable; weapon slots are `main_hand_2h` or `main_hand_1h`
- `trinket_ratings` — ~1,200 rows
- `item_popularity`

### `viz` — read-only views over enrichment
`slot_items` (`+primary_stats TEXT[]`, `+weapon_plan_slot`), `tier_piece_sources`, `crafters_by_item`, `bis_recommendations`, `item_popularity`

### `log` — operational logs
`bis_scrape_log` — status CHECK includes `'unchanged'` (added 0177)

---

## Key Design Notes

- `guild_identity.players` is the central identity entity — 1:1 FK to `discord_users` and `common.users`
- The FK is `players.discord_user_id → discord_users.id` — **always join as `LEFT JOIN discord_users du ON du.id = p.discord_user_id`**, never the other direction
- All Discord channel IDs in `common.discord_config` (Admin UI), not `.env`
- `site_config` is single-row, loaded at startup into `sv_common.config_cache`; all modules read from cache
- `rank_wow_mapping` maps WoW guild rank indices (0–9) to platform rank IDs
- `enrichment.*` tables are TRUNCATE-rebuilt by stored procs — never FK target from stable tables

---

## guild_identity schema — Reference Tables

> **Note:** `classes`, `specializations`, and `hero_talents` moved to the `ref` schema (migration 0130). The DDL below reflects the original structure; the tables now live under `ref.*`.

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

---

## guild_identity schema — External Data (from APIs, never manually edited)

```sql
-- WoW characters from guild roster (Blizzard API + GuildSync addon)
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
    last_progression_sync TIMESTAMPTZ,   -- added 0034
    last_profession_sync TIMESTAMPTZ,    -- added 0034
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
    no_guild_role_since TIMESTAMPTZ,     -- added 0031; set when last guild role removed
    removed_at TIMESTAMPTZ,
    first_seen TIMESTAMPTZ DEFAULT NOW()
);

-- All Discord channels scraped from the server by the bot (added 0020)
-- Used to populate channel pickers in Admin UI
CREATE TABLE guild_identity.discord_channels (
    id SERIAL PRIMARY KEY,
    discord_channel_id VARCHAR(25) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    channel_type VARCHAR(20) NOT NULL,   -- text, voice, category, forum, announcement, stage
    category_name VARCHAR(100),
    category_id VARCHAR(25),
    position INTEGER DEFAULT 0,
    is_nsfw BOOLEAN DEFAULT FALSE,
    is_public BOOLEAN DEFAULT TRUE,      -- FALSE = @everyone denied view_channel
    visible_role_names TEXT[],           -- roles with view access when not public
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## guild_identity schema — Core Entities

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
    timezone VARCHAR(50) DEFAULT 'America/Chicago',
    auto_invite_events BOOLEAN DEFAULT FALSE,       -- auto-sign-up for raid events
    crafting_notifications_enabled BOOLEAN DEFAULT FALSE,
    on_raid_hiatus BOOLEAN DEFAULT FALSE,           -- hide from public roster + availability grid
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Character ownership bridge — with attribution metadata
CREATE TABLE guild_identity.player_characters (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL UNIQUE REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    link_source VARCHAR(30) DEFAULT 'unknown',  -- note_key, exact_name, fuzzy_name, manual, battlenet_oauth, etc.
    confidence VARCHAR(15) DEFAULT 'unknown',   -- high, medium, low, confirmed, unknown
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, character_id)
);

-- Confirmed note-key → player aliases (built up as characters are linked)
CREATE TABLE guild_identity.player_note_aliases (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    alias VARCHAR(50) NOT NULL,
    source VARCHAR(30) DEFAULT 'note_match',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, alias)
);

-- Self-service character claim/unclaim audit log
CREATE TABLE guild_identity.player_action_log (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    action VARCHAR(30) NOT NULL,
    character_id INTEGER REFERENCES guild_identity.wow_characters(id) ON DELETE SET NULL,
    character_name VARCHAR(50),  -- denormalized, survives character deletion
    realm_slug VARCHAR(50),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## guild_identity schema — Crafting Corner

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

---

## guild_identity schema — Progression & API Data (added Phase 4.3–4.6)

```sql
-- Per-boss raid kill counts per character per difficulty (added 0034)
CREATE TABLE guild_identity.character_raid_progress (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    raid_name VARCHAR(100) NOT NULL,
    raid_id INTEGER NOT NULL,
    difficulty VARCHAR(20) NOT NULL,
    boss_name VARCHAR(100) NOT NULL,
    boss_id INTEGER NOT NULL,
    kill_count INTEGER NOT NULL DEFAULT 0,
    last_synced TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(character_id, boss_id, difficulty)
);

-- M+ dungeon best runs per character per season (added 0034)
CREATE TABLE guild_identity.character_mythic_plus (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    season_id INTEGER NOT NULL,
    overall_rating NUMERIC(7, 1) DEFAULT 0,
    dungeon_name VARCHAR(100) NOT NULL,
    dungeon_id INTEGER NOT NULL,
    best_level INTEGER DEFAULT 0,
    best_timed BOOLEAN DEFAULT FALSE,
    best_score NUMERIC(7, 1) DEFAULT 0,
    last_synced TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(character_id, season_id, dungeon_id)
);

-- Achievements to track across the roster (added 0034)
CREATE TABLE guild_identity.tracked_achievements (
    id SERIAL PRIMARY KEY,
    achievement_id INTEGER NOT NULL UNIQUE,
    achievement_name VARCHAR(200) NOT NULL,
    category VARCHAR(50) DEFAULT 'general',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Per-character achievement completions (added 0034)
CREATE TABLE guild_identity.character_achievements (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    achievement_id INTEGER NOT NULL,
    achievement_name VARCHAR(200) NOT NULL,
    completed_at TIMESTAMPTZ,
    last_synced TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(character_id, achievement_id)
);

-- Weekly progression snapshots for trend tracking (added 0034)
CREATE TABLE guild_identity.progression_snapshots (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    raid_kills_json JSONB,
    mythic_rating NUMERIC(7, 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(character_id, snapshot_date)
);

-- Raider.IO M+ scores per character per season (added 0036)
CREATE TABLE guild_identity.raiderio_profiles (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    season VARCHAR(30) NOT NULL,
    overall_score NUMERIC(7, 1) DEFAULT 0,
    dps_score NUMERIC(7, 1) DEFAULT 0,
    healer_score NUMERIC(7, 1) DEFAULT 0,
    tank_score NUMERIC(7, 1) DEFAULT 0,
    score_color VARCHAR(7),
    raid_progression VARCHAR(100),
    best_runs JSONB DEFAULT '[]',
    recent_runs JSONB DEFAULT '[]',
    profile_url VARCHAR(255),
    last_synced TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(character_id, season)
);

-- Battle.net OAuth account links (added 0037)
CREATE TABLE guild_identity.battlenet_accounts (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL UNIQUE REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    bnet_id VARCHAR(50) NOT NULL UNIQUE,
    battletag VARCHAR(100) NOT NULL,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMPTZ,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_refreshed TIMESTAMPTZ,
    last_character_sync TIMESTAMPTZ
);

-- Warcraft Logs API config (single row, added 0039)
CREATE TABLE guild_identity.wcl_config (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR(100),
    client_secret_encrypted VARCHAR(500),
    wcl_guild_name VARCHAR(100),
    wcl_server_slug VARCHAR(50),
    wcl_server_region VARCHAR(5) DEFAULT 'us',
    is_configured BOOLEAN NOT NULL DEFAULT FALSE,
    last_sync TIMESTAMPTZ,
    last_sync_status VARCHAR(20),
    last_sync_error TEXT,
    sync_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Best WCL parse per character per boss per spec (added 0039)
CREATE TABLE guild_identity.character_parses (
    id SERIAL PRIMARY KEY,
    character_id INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    encounter_id INTEGER NOT NULL,
    encounter_name VARCHAR(100) NOT NULL,
    zone_id INTEGER NOT NULL,
    zone_name VARCHAR(100) NOT NULL,
    difficulty INTEGER NOT NULL,
    spec VARCHAR(50) NOT NULL,
    percentile NUMERIC(5, 1) NOT NULL,
    amount NUMERIC(12, 1),
    report_code VARCHAR(20),
    fight_id INTEGER,
    fight_date TIMESTAMPTZ,
    last_synced TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(character_id, encounter_id, difficulty, spec)
);

-- WCL raid report summary (added 0039)
CREATE TABLE guild_identity.raid_reports (
    id SERIAL PRIMARY KEY,
    report_code VARCHAR(20) NOT NULL UNIQUE,
    title VARCHAR(200),
    raid_date TIMESTAMPTZ NOT NULL,
    zone_id INTEGER,
    zone_name VARCHAR(100),
    owner_name VARCHAR(50),
    boss_kills INTEGER DEFAULT 0,
    wipes INTEGER DEFAULT 0,
    duration_ms BIGINT,
    attendees JSONB DEFAULT '[]',
    report_url VARCHAR(255),
    last_synced TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- AH items to price-track (added 0040)
CREATE TABLE guild_identity.tracked_items (
    id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL UNIQUE,
    item_name VARCHAR(200) NOT NULL,
    category VARCHAR(50) DEFAULT 'consumable',
    display_order INTEGER DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    added_by_player_id INTEGER REFERENCES guild_identity.players(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Hourly AH price snapshots (added 0040)
-- connected_realm_id = 0 means region-wide commodity price
CREATE TABLE guild_identity.item_price_history (
    id SERIAL PRIMARY KEY,
    tracked_item_id INTEGER NOT NULL REFERENCES guild_identity.tracked_items(id) ON DELETE CASCADE,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    min_buyout BIGINT NOT NULL,
    median_price BIGINT,
    mean_price BIGINT,
    quantity_available INTEGER NOT NULL DEFAULT 0,
    num_auctions INTEGER NOT NULL DEFAULT 0,
    connected_realm_id INTEGER NOT NULL,
    UNIQUE(tracked_item_id, snapshot_at)
);
```

---

## guild_identity schema — System Tables

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

-- Onboarding sessions
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

---

## common schema (infrastructure — shared across sites)

```sql
CREATE TABLE common.guild_ranks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    level INTEGER NOT NULL UNIQUE,
    scheduling_weight INTEGER NOT NULL DEFAULT 0,  -- used in availability weighted scores
    discord_role_id VARCHAR(20),
    wow_rank_index INTEGER UNIQUE,                 -- maps to Blizzard guild rank index
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE common.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- All channel IDs stored here; configured via Admin UI (no .env IDs)
CREATE TABLE common.discord_config (
    id SERIAL PRIMARY KEY,
    guild_discord_id VARCHAR(20) NOT NULL,
    role_sync_interval_hours INTEGER DEFAULT 24,
    bot_dm_enabled BOOLEAN DEFAULT FALSE,
    feature_invite_dm BOOLEAN DEFAULT FALSE,
    feature_onboarding_dm BOOLEAN DEFAULT FALSE,
    bot_token_encrypted TEXT,                      -- added 0033; Fernet-encrypted bot token
    raid_helper_api_key VARCHAR(200),
    raid_helper_server_id VARCHAR(25),
    raid_creator_discord_id VARCHAR(25),
    raid_channel_id VARCHAR(25),
    raid_voice_channel_id VARCHAR(25),
    raid_default_template_id VARCHAR(50) DEFAULT 'wowretail2',
    audit_channel_id VARCHAR(25),
    raid_event_timezone VARCHAR(50) DEFAULT 'America/New_York',
    raid_default_start_time VARCHAR(5) DEFAULT '21:00',
    raid_default_duration_minutes INTEGER DEFAULT 120,
    -- Attendance tracking columns (added 0041)
    attendance_feature_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    attendance_min_pct SMALLINT NOT NULL DEFAULT 75,
    attendance_late_grace_min SMALLINT NOT NULL DEFAULT 10,
    attendance_early_leave_min SMALLINT NOT NULL DEFAULT 10,
    attendance_trailing_events SMALLINT NOT NULL DEFAULT 8,
    attendance_habitual_window SMALLINT NOT NULL DEFAULT 5,
    attendance_habitual_threshold SMALLINT NOT NULL DEFAULT 3,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- DB-driven Settings nav — screen visibility by rank level
CREATE TABLE common.screen_permissions (
    id SERIAL PRIMARY KEY,
    screen_key VARCHAR(50) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    url_path VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    category_label VARCHAR(100) NOT NULL,
    category_order INTEGER DEFAULT 0,
    nav_order INTEGER DEFAULT 0,
    min_rank_level INTEGER NOT NULL DEFAULT 4,
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

-- Single-row guild configuration (added 0032; columns added through 0040)
-- Loaded at startup into sv_common.config_cache; all modules read from cache
CREATE TABLE common.site_config (
    id SERIAL PRIMARY KEY,
    guild_name VARCHAR(100) NOT NULL DEFAULT 'My Guild',
    guild_tagline VARCHAR(255),
    guild_mission TEXT,
    discord_invite_url VARCHAR(255),
    accent_color_hex VARCHAR(7) NOT NULL DEFAULT '#d4a84b',
    realm_display_name VARCHAR(50),
    home_realm_slug VARCHAR(50),
    guild_name_slug VARCHAR(100),
    logo_url VARCHAR(500),
    enable_guild_quotes BOOLEAN NOT NULL DEFAULT FALSE,
    enable_contests BOOLEAN NOT NULL DEFAULT TRUE,
    setup_complete BOOLEAN NOT NULL DEFAULT FALSE,
    blizzard_client_id VARCHAR(100),               -- added 0033; stored in DB (not .env)
    blizzard_client_secret_encrypted TEXT,         -- added 0033; Fernet-encrypted
    current_mplus_season_id INTEGER,               -- added 0034; Blizzard season ID for M+ queries
    enable_onboarding BOOLEAN NOT NULL DEFAULT TRUE, -- added 0038
    connected_realm_id INTEGER,                    -- added 0040; cached guild connected realm
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Maps WoW guild rank indices (0–9) to platform rank IDs (added 0032)
CREATE TABLE common.rank_wow_mapping (
    id SERIAL PRIMARY KEY,
    wow_rank_index INTEGER NOT NULL UNIQUE,
    guild_rank_id INTEGER NOT NULL REFERENCES common.guild_ranks(id)
);
```

---

## patt schema (features)

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

-- Random quote pool (renamed from mito_quotes in 0032)
CREATE TABLE patt.guild_quotes (
    id SERIAL PRIMARY KEY,
    quote TEXT NOT NULL,
    subject_id INTEGER REFERENCES patt.quote_subjects(id) ON DELETE CASCADE,  -- added 0044
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Quote titles / honorifics (renamed from mito_titles in 0032)
CREATE TABLE patt.guild_quote_titles (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    subject_id INTEGER REFERENCES patt.quote_subjects(id) ON DELETE CASCADE,  -- added 0044
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Per-person quote subjects — one row per person with a quote collection (added 0044)
-- command_slug becomes the Discord slash command name (e.g. 'mito' → /mito)
CREATE TABLE patt.quote_subjects (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    command_slug VARCHAR(32) NOT NULL UNIQUE,  -- slug format: ^[a-z][a-z0-9_-]{0,30}$
    display_name VARCHAR(100) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(player_id)  -- one subject per player
);

-- Player availability: time windows per day of week (0=Mon … 6=Sun)
CREATE TABLE patt.player_availability (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
    day_of_week INTEGER NOT NULL,          -- 0=Monday … 6=Sunday
    earliest_start TIME NOT NULL,
    available_hours NUMERIC(3,1) NOT NULL, -- e.g. 2.5 = 2h30m
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, day_of_week)
);

-- WoW content season (e.g. The War Within Season 2)
CREATE TABLE patt.raid_seasons (
    id SERIAL PRIMARY KEY,
    expansion_name VARCHAR(50),
    season_number INTEGER,
    start_date DATE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    is_new_expansion BOOLEAN DEFAULT FALSE,
    blizzard_mplus_season_id INTEGER,      -- added 0035; Blizzard season ID for API calls
    created_at TIMESTAMPTZ DEFAULT NOW()
    -- display_name computed in code as "{expansion_name} Season {season_number}"
);

-- Event-day config: drives schedule, raid tools, and auto-booking
CREATE TABLE patt.recurring_events (
    id SERIAL PRIMARY KEY,
    label VARCHAR(100) NOT NULL,
    event_type VARCHAR(30) DEFAULT 'raid',
    day_of_week INTEGER NOT NULL,          -- 0=Monday … 6=Sunday
    default_start_time TIME NOT NULL,
    default_duration_minutes INTEGER DEFAULT 120,
    discord_channel_id VARCHAR(25),
    raid_helper_template_id VARCHAR(50) DEFAULT 'wowretail2',
    is_active BOOLEAN DEFAULT TRUE,
    display_on_public BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- A single scheduled raid night
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
    recurring_event_id INTEGER REFERENCES patt.recurring_events(id),
    auto_booked BOOLEAN DEFAULT FALSE,
    raid_helper_payload JSONB,
    voice_channel_id VARCHAR(25),            -- added 0041; which voice channel to monitor
    voice_tracking_enabled BOOLEAN NOT NULL DEFAULT TRUE,  -- added 0041
    attendance_processed_at TIMESTAMPTZ,     -- added 0041; set when processor runs
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Signup and attendance tracking per event
CREATE TABLE patt.raid_attendance (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES patt.raid_events(id),
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id),
    signed_up BOOLEAN DEFAULT FALSE,
    attended BOOLEAN DEFAULT FALSE,
    character_id INTEGER REFERENCES guild_identity.wow_characters(id),
    noted_absence BOOLEAN DEFAULT FALSE,
    source VARCHAR(20) DEFAULT 'manual',
    -- Voice attendance data (added 0041)
    minutes_present SMALLINT,
    first_join_at TIMESTAMPTZ,
    last_leave_at TIMESTAMPTZ,
    joined_late BOOLEAN,
    left_early BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(event_id, player_id)
);

-- Raw voice channel join/leave events per raid (added 0041)
CREATE TABLE patt.voice_attendance_log (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES patt.raid_events(id) ON DELETE CASCADE,
    discord_user_id VARCHAR(25) NOT NULL,
    channel_id VARCHAR(25) NOT NULL,
    action VARCHAR(10) NOT NULL,           -- 'join' or 'leave'
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
