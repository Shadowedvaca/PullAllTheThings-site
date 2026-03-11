# PATT Database Schema

> **Current schema through migration 0030.** Clean 3NF design with players as the core entity.
> Reference tables normalize WoW classes, specializations, and combat roles.
> Bridge table tracks character ownership with attribution metadata.
> Direct 1:1 FKs for Discord and website accounts.

Three PostgreSQL schemas: `common` (shared infrastructure), `guild_identity` (guild sync + identity), `patt` (app features).

---

## guild_identity schema — Reference Tables

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
    link_source VARCHAR(30) DEFAULT 'unknown',  -- note_key, exact_name, fuzzy_name, manual, etc.
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

-- Onboarding sessions (built, not yet activated)
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

CREATE TABLE patt.mito_quotes (
    id SERIAL PRIMARY KEY,
    quote TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE patt.mito_titles (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
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
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(event_id, player_id)
);
```
