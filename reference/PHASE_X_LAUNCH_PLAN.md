# Phase X — Launch Plan: Multi-Tenant Platform with Player-First Architecture

> **Status:** DRAFT — actively being defined. Do not implement until this doc is marked READY.
> **Replaces:** PHASE_X_SAAS_PLATFORM.md, PHASE_X_PLUS_1_MULTI_TENANT.md, PHASE_X_INFRA_SCALING.md (all moved to `reference/archive/`)
> **Last updated:** 2026-04-05
> **Session context:** Pivoting from single-tenant SaaS runway to multi-tenant-first, with individual players as first-class users — not an extraction of the guild feature set.

---

## The Core Pivot

The original Phase X plan treated the guild as the tenant and the player dashboard as a bonus feature bolted on the side. That model breaks down the moment you want a player to belong to multiple guilds, or to use the platform without a guild at all.

The new model:

- **Players are platform users.** They exist at the platform level, own their own data (characters, gear plans, profession info, parse history), and persist independently of any guild.
- **Guilds are tenants.** A guild is a separately managed workspace with its own configuration, roster layer, raid tools, and Discord integration. Guild leaders own and manage their guild tenant.
- **The relationship between players and guilds is many-to-many.** A player can be a member of multiple guilds across multiple characters. A guild can have many players. Neither owns the other.
- **Data portability is a core commitment, not a feature tier.** Every player and every guild can export all of their data at any time, in full, for free. No paywalling exports. No holding data hostage. This is non-negotiable and applies regardless of subscription status.

This collapses Phase X and Phase X+1 into a single architecture. We build multi-tenant from day one and never have a migration problem.

---

## 1. Data Architecture

This is the biggest structural change from the current PATT-only design.

### 1.1 Schema Layers — Overview

```
PostgreSQL (one cluster)
│
├── system.*     — platform infrastructure: auth, billing, tenant registry
├── player.*     — player-owned data, independent of any guild
├── platform.*   — shared WoW reference data, owned by no one
└── guild_{slug}.*  — per-guild tenant schema (one per claimed guild)
```

### 1.2 `system.*` — Platform Infrastructure

Auth, billing, tenant registry, and entitlements. Read on every request.

```
system.users
  id, email, email_verified, created_at, last_login_at, is_platform_admin

system.user_auth
  user_id FK→users, password_hash, reset_token, reset_expires_at

system.bnet_accounts
  -- BNet OAuth is REQUIRED to use character features.
  -- One BNet account per platform user (Blizzard enforces this on their end).
  id, user_id FK→users, bnet_id, battletag, region,
  access_token_encrypted, refresh_token_encrypted, token_expires_at, linked_at

system.tenants
  -- Guild tenant registry. Stubs exist before any schema is provisioned.
  id, slug, blizzard_guild_id (stable numeric ID — never name-match),
  realm_slug, region, display_name,
  status CHECK('stub','claimed','active'),
  claimed_by_user_id FK→users, created_at, last_blizzard_seen_at

system.provisioning_log
  tenant_id, step, status, error, occurred_at

-- Entitlement layer (real schema, stubbed logic at launch)
system.features          -- feature_key, display_name, default_free, description
system.plans             -- plan_key, display_name, feature_keys TEXT[]
system.subscriptions     -- subject_type/id, plan_key, status, source, stripe_sub_id, period_end
system.entitlement_grants   -- subject_type/id, feature_key, source, granted_by, expires_at
system.entitlement_overrides -- subject_type/id, feature_key, allow BOOL, reason, set_by

-- Billing (Stripe, stubbed at launch)
system.user_billing      -- user_id, stripe_customer_id
system.tenant_billing    -- tenant_id, stripe_customer_id, stripe_sub_id, billing_status

-- Rate limiting
system.sync_log
  subject_type CHECK('user','tenant'), subject_id,
  sync_type VARCHAR,   -- 'blizzard_roster', 'bnet_characters', 'wcl', 'ah', etc.
  last_run_at, next_allowed_at,
  UNIQUE (subject_type, subject_id, sync_type)
```

### 1.3 `player.*` — Player-Owned Data

Everything a player owns. Survives guild membership changes completely intact.

**BNet OAuth is required.** Characters are sourced exclusively from the Blizzard API via the player's own OAuth token. There is no manual character linking. This is cleaner, more secure, and removes an entire class of data quality problems.

**Multiple Discord accounts are supported.** A player may use different Discord accounts across different guilds. Discord accounts are player-owned, not guild-owned.

**Guild membership is character-derived.** If a player's character is in a WoW guild that exists as a tenant on the platform, a `guild_memberships` row is created automatically. When Blizzard API shows the character has left the guild, the membership is soft-deleted (data preserved, reactivates if they rejoin).

```
player.players
  -- The player profile entity. Replaces guild_identity.players minus guild-specific columns.
  id, user_id FK→system.users UNIQUE,
  display_character_id FK→wow_characters,   -- platform-level "my main"
  display_spec_id FK→platform.specializations,  -- platform-level preferred spec
  created_at

player.discord_accounts
  -- One player, many Discord accounts (different guilds may use different Discords).
  id, user_id FK→system.users,
  discord_id VARCHAR UNIQUE, discord_username,
  linked_at, is_primary BOOLEAN
  -- Guild-specific Discord association handled in guild_{slug}.guild_members

player.wow_characters
  -- All characters on the player's BNet account, all realms.
  id, user_id FK→system.users,
  blizzard_char_id BIGINT UNIQUE,
  name, realm_slug, region,
  class_id FK→platform.classes,
  active_spec_id FK→platform.specializations,
  level, item_level, avatar_url,
  blizzard_guild_id BIGINT,    -- current guild from Blizzard API (null if unguilded)
  blizzard_guild_name VARCHAR, -- denormalized for display without a lookup
  in_guild BOOLEAN,            -- false when Blizzard no longer shows them in a guild
  link_source VARCHAR,         -- 'bnet_oauth' (only supported source)
  last_synced, last_equipment_sync, last_profession_sync

player.guild_memberships
  -- Character-derived guild membership. Soft-deleted on guild leave.
  id, user_id FK→system.users,
  tenant_slug FK→system.tenants,
  blizzard_guild_id BIGINT,
  joined_platform_at, left_at,   -- left_at NULL = currently active
  is_active BOOLEAN,
  UNIQUE (user_id, tenant_slug)

player.character_equipment
  character_id, slot, blizzard_item_id, item_id FK→platform.wow_items,
  item_name, item_level, quality_track VARCHAR(1),
  bonus_ids INTEGER[], enchant_id, gem_ids INTEGER[],
  UNIQUE (character_id, slot)

player.gear_plans
  id, user_id, character_id, spec_id, hero_talent_id,
  bis_source_id FK→platform.bis_list_sources,
  simc_profile TEXT, is_active,
  UNIQUE (user_id, character_id)

player.gear_plan_slots
  id, plan_id FK→gear_plans, slot,
  desired_item_id FK→platform.wow_items,
  blizzard_item_id, item_name, is_locked,
  UNIQUE (plan_id, slot)

player.character_recipes
  character_id, recipe_id FK→platform.recipes, learned_at

player.raiderio_profiles
  id, character_id, season VARCHAR,
  overall_score, score_color, raid_progression JSONB,
  profile_url, last_synced

player.character_report_parses
  -- Source of truth for all WCL parse display. Fully denormalized — no JOIN back to
  -- guild tables needed. Player retains this data after leaving a guild.
  -- Written by guild WCL sync job when processing reports; player is matched by
  -- character name+realm against player.wow_characters at time of ingestion.
  id, character_id,
  blizzard_char_id BIGINT,     -- supplemented from player.wow_characters at match time;
                               -- stable even if character later renames or transfers
  match_source VARCHAR,        -- 'bnet_direct' (char already known) | 'name_realm_match' (matched at ingestion)
                               -- | 'retroactive_match' (matched when player later registered)
  report_code, report_title, report_url,  -- denormalized so player can link back to WCL
  encounter_id, encounter_name,
  zone_id, zone_name, difficulty,
  spec, percentile, amount, raid_date,
  last_synced,
  dispute_status VARCHAR,      -- NULL (no dispute) | 'open' | 'resolved_kept' | 'resolved_reattributed'
  dispute_opened_at,
  UNIQUE (character_id, report_code, encounter_id)

player.character_parses
  -- Legacy pre-0060 parse table. Migrates with the player for historical data.
  -- No new data written here. Retained for historical display only.

player.character_raid_progress
  character_id, zone_id, zone_name, difficulty, boss_kills JSONB, last_synced

player.character_mythic_plus
  character_id, season VARCHAR, overall_score, score_color,
  score_breakdown JSONB, last_synced

player.character_achievements
  character_id, achievement_id FK→platform.tracked_achievements,
  completed_at, last_synced

player.progression_snapshots
  character_id, snapshot_at, data JSONB

player.bnet_character_sync_log
  character_id, attempted_at, status, error_message
```

### 1.4 `platform.*` — Shared WoW Reference Data

Owned by the platform. Read-only to players and guilds. Updated by platform-managed sync jobs (not user-triggered).

```
platform.realms
  blizzard_realm_id, slug, name, region, connected_realm_id

platform.connected_realms
  connected_realm_id, realm_slugs TEXT[]

platform.roles            -- tank / healer / dps / unknown
platform.classes          -- all WoW classes (blizzard_class_id, name, slug)
platform.specializations  -- (blizzard_spec_id, class_id, name, slug, role_id)
platform.hero_talents     -- (spec_id, name, slug)

platform.professions      -- (blizzard_profession_id, name, slug)
platform.profession_tiers -- (profession_id, expansion, tier_name)
platform.recipes          -- (profession_id, tier_id, blizzard_recipe_id, name, output_item_id)

platform.wow_items
  id, blizzard_item_id UNIQUE, name, icon_url,
  slot_type, armor_type, weapon_type, wowhead_tooltip_html

platform.item_sources
  item_id FK→wow_items, source_type, source_name, source_instance,
  blizzard_encounter_id, blizzard_instance_id, quality_tracks TEXT[]

platform.tracked_items
  -- Items the platform monitors for AH prices.
  -- Derived from BIS list entries + commonly needed gems/enchants.
  -- Platform scheduler manages this list, not users.
  id, item_id FK→wow_items, track_reason VARCHAR,  -- 'bis_list','gem','enchant','manual'
  added_at

platform.item_price_history
  -- Realm-based AH price snapshots. Shared data — no per-player copy.
  -- Player view: filter by gear plan items + character realm.
  -- Guild view: filter by all members' gear plan items + guild realm.
  id, tracked_item_id FK→tracked_items, connected_realm_id,
  price BIGINT, quantity INTEGER, snapshot_at,
  UNIQUE (tracked_item_id, connected_realm_id, snapshot_at)

platform.tracked_achievements  -- achievements the platform tracks across all players
platform.guide_sites           -- Wowhead, Icy Veins, u.gg — URL templates per spec

platform.bis_list_sources      -- Archon Raid/M+/Overall, Wowhead, Icy Veins
platform.bis_list_entries      -- source_id, spec_id, hero_talent_id, slot, item_id, priority
platform.bis_scrape_targets    -- scrape job definitions per source/spec
platform.bis_scrape_log        -- scrape history and results
```

### 1.5 `guild_{slug}.*` — Per-Guild Tenant Schema

One schema per claimed guild. Stubs in `system.tenants` have no schema — schema is provisioned on claim.

The guild schema holds organizational data only. It references players by `user_id` (FK→system.users) and characters by `character_id` (FK→player.wow_characters) but owns neither.

```
guild_config
  -- Single-row. Guild identity and top-level settings.
  blizzard_guild_id, display_name, realm_slug, region,
  tagline, accent_color, logo_url, discord_guild_id, created_at

discord_config
  -- Single-row. All Discord channel IDs and bot behavior settings.
  bot_dm_enabled, landing_zone_channel_id, audit_channel_id,
  raid_channel_id, crafters_channel_id, announcement_channel_id,
  attendance_excuse_if_unavailable, attendance_excuse_if_discord_absent,
  [all other existing discord_config columns]

guild_ranks
  id, name, level, color, is_officer, is_gl

rank_wow_mapping
  wow_rank_index, rank_id FK→guild_ranks

guild_members
  -- Lightweight roster. References players, owns guild-context flags.
  -- Soft-deleted when player's character leaves guild in Blizzard API.
  id, user_id FK→system.users,
  rank_id FK→guild_ranks,
  main_character_id FK→player.wow_characters,  -- guild-context main (may differ from platform main)
  discord_account_id FK→player.discord_accounts, -- which of their Discord accounts for this guild
  on_raid_hiatus BOOLEAN DEFAULT FALSE,
  joined_at, is_active BOOLEAN, deactivated_at,
  UNIQUE (user_id)

screen_permissions    -- rank_id, screen_key, can_view
invite_codes          -- code, created_by_user_id, rank_id, max_uses, uses, expires_at
onboarding_sessions   -- Discord onboarding flow state
wcl_config            -- WCL guild ID, API config
audit_issues          -- data quality flags for this guild
sync_log              -- guild-scoped sync history
raid_reports
  -- WCL report metadata for this guild's raids. Guild-scoped.
  -- Character-level parse data is written to player.character_report_parses during sync.
  -- Guild parse view = aggregate over player.character_report_parses for current members.
  report_code, title, report_url, raid_date, zone_id, zone_name,
  owner_name, boss_kills, duration_ms, attendees JSONB,
  encounter_ids INTEGER[], encounter_map JSONB

unmatched_parses
  -- Characters found in WCL reports that don't yet match a platform user.
  -- Re-processed when new players register and their BNet characters match by name+realm.
  -- Guard rails on retroactive matching (see Section 1.7).
  id, report_code, character_name, realm_slug, region,
  encounter_id, encounter_name, zone_id, difficulty,
  spec, percentile, amount, raid_date,
  status VARCHAR CHECK('pending','matched','expired','disputed'),
  matched_character_id FK→player.wow_characters,  -- set when matched
  matched_at, expires_at   -- expires_at set at ingestion based on retention policy
raid_seasons          -- raid season definitions
raid_events           -- scheduled raid events
raid_attendance       -- user_id, event_id, attended, minutes_present, etc.
recurring_events
voice_attendance_log
attendance_rules      -- JSONB condition rules for promotion/warning logic
player_availability   -- user_id, day_of_week availability preferences
campaigns             -- voting campaigns
campaign_entries
votes                 -- user_id, entry_id, rank
campaign_results
contest_agent_log
guild_quotes          -- subject_user_id FK→system.users
guild_quote_titles
quote_subjects
crafting_orders       -- guild-side crafting corner (orders, fulfillment tracking)
crafting_sync_config
discord_channels      -- known Discord channels for this guild's server
```

### 1.6 Key Design Principles

- **BNet OAuth is the only character ownership mechanism.** No manual linking. Characters flow from Blizzard to the player who authorized them.
- **Guild membership is character-derived and soft-deleted.** Platform follows Blizzard. When a character leaves a guild in WoW, membership is deactivated, not deleted. Rejoining reactivates it.
- **`guild_{slug}.guild_members` is a pointer table, not a data table.** Guild-specific context (rank, main, hiatus) lives there. Everything else lives in `player.*`.
- **AH price data is platform-managed and shared.** Player and guild views are filtered queries over `platform.item_price_history` — derived from gear plan slots, not separate tracking tables.
- **`platform.*` is read-only to tenants.** Only platform scheduler jobs write to it. No per-guild or per-player copies of reference data.
- **`system.*` is read on every request.** Keep it lean — only auth, billing state, and tenant registry.
- **Data portability is always available, always free.** Both players and guild leaders can export all of their data at any time via self-serve tooling. Export is never gated behind a subscription tier. See Section 7.

### 1.7 WCL Character Matching & Data Integrity

#### Matching process

When a WCL report is processed:
1. For each character parse (name + realm + region) found in the report:
   - Look up `player.wow_characters` by name+realm+region
   - **If matched:** write directly to `player.character_report_parses` with `blizzard_char_id` supplemented from the matched character row and `match_source='bnet_direct'`
   - **If not matched:** write to `guild_{slug}.unmatched_parses` with an `expires_at` set by retention policy

When a new player registers and connects BNet:
- Their characters are imported with Blizzard character IDs
- `unmatched_parses` is checked for name+realm matches against their characters
- Matches that pass guard rail checks are migrated to `player.character_report_parses` with `match_source='retroactive_match'`

#### Guard rails on retroactive matching

Not every name+realm hit is a safe match. Before migrating an unmatched parse to a player:

- **Character must currently exist on the player's BNet account.** If they transferred or sold the character, the name+realm may collide with a different player's character now.
- **Activity proximity:** the character's `last_synced` date must be reasonably close to the parse date. Configurable window — default 180 days. Prevents matching a character that was inactive or transferred away before the report was run.
- **No collision:** if the same name+realm appears in `player.wow_characters` for more than one user within the activity window, do not auto-match — flag for admin review instead.
- **Unmatched parse expiry:** unmatched parses are retained for a configurable period (default 2 years) then expired. After expiry, retroactive matching is no longer possible for that record.

#### Data integrity principles

Parse data represents what happened in WoW. It is a historical record and **is not editable by players.**

The distinction that resolves most edge cases:

| Action | Who can do it | Notes |
|---|---|---|
| View parse history | Player (their own), guild admins (guild members) | Normal |
| Delete or edit a parse value | Nobody | Historical record — immutable |
| Dispute an attribution | Player | "This character is not mine" |
| Resolve an attribution dispute | Platform admin only | Re-attribute or mark unresolvable |
| Remove a character link | Player (unlink BNet) | Parse records are retained; `blizzard_char_id` preserved for audit |

**Dispute flow:**
1. Player flags a parse as "not my character"
2. `dispute_status` set to `'open'` on the parse record
3. Platform admin review queue surfaces the dispute
4. Admin investigates (BNet account history, character ownership timeline)
5. Resolution: `'resolved_kept'` (attribution confirmed correct) or `'resolved_reattributed'` (moved to correct player or marked unowned)
6. Data is never deleted — only re-attributed or left as unowned

**The high-profile user problem:**

The policy above applies to everyone equally — no special lanes. However, the platform's reputation depends on disputes being resolved *quickly*, not just correctly. A streamer with a wrongly attributed parse that sits open for two weeks is a PR problem regardless of who's at fault.

Mitigation: the platform admin dispute queue needs to be treated as a high-priority operational responsibility. Response time target TBD but should be measured in hours for open disputes, not days. This is a process/operations commitment, not a technical one.

**Unresolved:** The line between "character attribution is wrong" and "I don't want people to see this parse" is not always obvious from a dispute request alone. A bad-faith dispute from a player trying to hide poor performance looks identical to a good-faith dispute from a player whose character was wrongly matched. The review process needs human judgment — no automated resolution. This remains an open area of policy design.

### 1.8 Table Migration Map (Current PATT → New Schema)

| Current table | New home | Notes |
|---|---|---|
| `common.users` | `system.users` | Auth identity |
| `common.guild_ranks` | `guild_patt.guild_ranks` | Guild-specific |
| `common.discord_config` | `guild_patt.discord_config` | Guild-specific |
| `common.site_config` | `guild_patt.guild_config` | Guild-specific |
| `common.invite_codes` | `guild_patt.invite_codes` | Guild-specific |
| `common.screen_permissions` | `guild_patt.screen_permissions` | Guild-specific |
| `common.rank_wow_mapping` | `guild_patt.rank_wow_mapping` | Guild-specific |
| `common.guide_sites` | `platform.guide_sites` | Shared reference |
| `guild_identity.players` | `player.players` | Guild-specific columns removed |
| `guild_identity.discord_users` | `system.bnet_accounts` + `player.discord_accounts` | Split by concern |
| `guild_identity.battlenet_accounts` | `system.bnet_accounts` | Auth-adjacent |
| `guild_identity.wow_characters` | `player.wow_characters` | Player-owned |
| `guild_identity.player_characters` | **Retired** | BNet OAuth replaces manual linking |
| `guild_identity.character_equipment` | `player.character_equipment` | Player-owned |
| `guild_identity.gear_plans` | `player.gear_plans` | Player-owned |
| `guild_identity.gear_plan_slots` | `player.gear_plan_slots` | Player-owned |
| `guild_identity.character_recipes` | `player.character_recipes` | Player-owned |
| `guild_identity.raiderio_profiles` | `player.raiderio_profiles` | Player-owned |
| `guild_identity.character_report_parses` | `player.character_report_parses` | Player-owned |
| `guild_identity.character_parses` | `player.character_parses` | Legacy/historical |
| `guild_identity.character_raid_progress` | `player.character_raid_progress` | Player-owned |
| `guild_identity.character_mythic_plus` | `player.character_mythic_plus` | Player-owned |
| `guild_identity.character_achievements` | `player.character_achievements` | Player-owned |
| `guild_identity.progression_snapshots` | `player.progression_snapshots` | Player-owned |
| `guild_identity.roles/classes/specializations` | `platform.*` | Shared reference |
| `guild_identity.hero_talents` | `platform.hero_talents` | Shared reference |
| `guild_identity.professions/tiers/recipes` | `platform.*` | Shared reference |
| `guild_identity.wow_items` | `platform.wow_items` | Shared reference |
| `guild_identity.item_sources` | `platform.item_sources` | Shared reference |
| `guild_identity.tracked_items` | `platform.tracked_items` | Platform-managed |
| `guild_identity.item_price_history` | `platform.item_price_history` | Realm-based, shared |
| `guild_identity.tracked_achievements` | `platform.tracked_achievements` | Shared reference |
| `guild_identity.bis_list_sources/entries` | `platform.*` | Shared reference |
| `guild_identity.bis_scrape_targets/log` | `platform.*` | Platform infrastructure |
| `guild_identity.audit_issues` | `guild_patt.audit_issues` | Guild-scoped |
| `guild_identity.sync_log` | `guild_patt.sync_log` | Guild-scoped |
| `guild_identity.onboarding_sessions` | `guild_patt.onboarding_sessions` | Guild-scoped |
| `guild_identity.wcl_config` | `guild_patt.wcl_config` | Guild-scoped |
| `guild_identity.raid_reports` | `guild_patt.raid_reports` | Guild-scoped metadata |
| `guild_identity.crafting_sync_config` | `guild_patt.crafting_sync_config` | Guild-scoped |
| `guild_identity.discord_channels` | `guild_patt.discord_channels` | Guild-scoped |
| `patt.*` (all) | `guild_patt.*` | All guild-scoped |

---

## 2. User Types & Authentication

### 2.1 User Types

| Type | Description |
|---|---|
| **Individual player** | Registered on the platform, no guild required. Owns their characters + data. |
| **Guild member** | An individual player who has been linked to a guild tenant (via invite or discovery). Gains access to that guild's tools. |
| **Guild admin (GL/Officer)** | A guild member with elevated permissions in their guild's tenant. Manages roster, raids, etc. |
| **Guild owner** | The player who claimed/provisioned the guild tenant. Billing owner. Can manage admins. |
| **Platform admin** | Mike. Sees everything, manages tenants, billing overrides, etc. |

A single user can be a guild owner of one guild, a member of two others, and use the individual player tools all from the same account.

### 2.2 Authentication

- **Email + password** — required for all accounts. Bcrypt, same as current. Email must be verified before account is active.
- **Battle.net OAuth** — strongly encouraged but not required to register. Prompted immediately after email verification. Required to unlock character features.
- **Invite codes** — retained for guild-gated registration flows (guild leader sends an invite, player registers and is auto-linked to that guild). Optional path, not required for platform registration.
- **No Discord login** — Discord remains a bot/integration layer, not an auth method. Players link their Discord account for guild tools, but don't log in via Discord.

### 2.3 Registration Flow (Individual)

```
1. Email + password registration
2. Email verification
3. "Connect your Battle.net account" prompt (can skip for now, features locked)
4. Characters auto-imported via BNet API
5. Guild discovery: "We found these guilds on your characters. Want to connect?"
6. → If guild exists on platform: request to join (or auto-join if open)
7. → If guild doesn't exist: show as discoverable, optionally notify user when claimed
8. Land on player dashboard (My Characters expanded)
```

### 2.4 Registration Flow (Guild via Invite)

```
1. Player receives invite link/code from guild
2. Click link → registration page pre-seeded with guild context
3. Email + password
4. Email verification
5. BNet connect (prompted, encouraged for character features)
6. Auto-linked to the guild with the invited rank
7. Land on guild member view of the dashboard
```

---

## 3. Player Experience (Platform Layer)

The individual player's home is the platform app — **not** a guild subdomain. All player tools are accessible here regardless of guild membership.

### 3.1 Entry Point & Domain

Individual players land at `app.pullallthethings.com` (or `app.{brand-domain}` once the brand name is decided). Guild subdomains (`{slug}.pullallthethings.com`) are entry points into a specific guild's tools. A logged-in player always has their player context intact regardless of which subdomain they're on — session cookies are scoped to the root domain. See Section 5 for full domain strategy.

### 3.2 Player Dashboard (My Characters, Expanded)

The current My Characters page is a single page with a character selector and panels. At platform scale this expands into a proper multi-page or tabbed area.

**Proposed structure:**

```
/dashboard                 — overview: active character, quick stats, guild activity feed
/dashboard/characters      — character list + stat panels (current My Characters core)
/dashboard/gear            — gear plans, BIS lists, equipment tracking
/dashboard/professions     — all professions across all characters/realms on this BNet account
/dashboard/performance     — WCL parses, M+ scores, progression across characters
/dashboard/market          — AH prices relevant to your realms
/dashboard/guilds          — guilds you belong to; guild selector; join/claim
/dashboard/settings        — account settings, linked accounts, export my data, delete account
```

**Guild context selector:**  
A persistent dropdown or top-bar selector showing which guild is "active." Guild-specific features (Roster, Raids, Attendance, Guild Crafting Corner) are only shown when a guild is selected, and are grayed out with a "Select a guild" prompt when none is chosen. Selecting a guild doesn't navigate away — it filters/unlocks guild panels inline or navigates to the guild subdomain depending on the final domain strategy.

### 3.3 Crafting Corner (Individual Flavor)

The current Crafting Corner is guild-scoped (guild orders, recipe directory per guild). The individual version:
- Shows **all professions and recipes across all characters on the player's BNet account**
- No guild order system — that's the guild tenant feature
- Could show "which of your characters can craft X" — useful for self-service crafting planning
- Could eventually surface crafting requests from guild tenants the player belongs to (cross-layer integration)

### 3.4 Feature Access by Tier

**TBD — monetization not finalized.** Infrastructure must support free, paid, and donation tiers without hard-coding. Design with a `feature_flags` / entitlement check layer. Specific tier assignments will be overlaid once the monetization model is decided.

What we need to be able to express:
- Feature X is available to all registered users
- Feature Y requires a paid subscription (individual)
- Feature Z is only available as part of a guild tenant subscription
- Feature W is available to donors (one-time payment unlocks for N days or permanently)

---

## 4. Guild Tenant Experience

### 4.1 Guild Discovery, Status Hierarchy & Auto-Provisioning

#### Why player-owned data solves the guild split problem

Historical WoW platform drama (WCL's ownership disputes, guild split fights) exists because parse history and progression data lived on the **guild record**. When a guild implodes, data custody becomes a political problem.

In this model, all meaningful player data — parses, progression, gear plans, professions, equipment — lives in `player.*`. The guild is a context, not an owner. When a guild splits:
- Every player's history is intact and theirs regardless of what happens to the guild
- The new guild auto-stubs the moment any member connects BNet
- Players link to the new guild; their history comes with them
- The old guild stub retains only its organizational records (raid events, attendance, config) — which belong to the guild-as-organization, not the players

This means "who owns the guild on the platform" is a much lower-stakes question. There's no valuable player history to fight over.

#### Guild identification

Guilds are identified by **Blizzard guild ID** (numeric, stable), not by name. Name changes, realm transfers, and connected realm quirks do not cause duplicate or orphaned records. Name-based matching is never used.

#### Guild status hierarchy

Guilds exist in one of three statuses, which drives API sync frequency, feature access, and operational cost:

| Status | Trigger | API Sync | Manual Refresh | Features |
|---|---|---|---|---|
| **`stub`** | Found in Blizzard API; ≥1 platform user is a member | Weekly | None | Read-only guild page, basic roster from Blizzard data |
| **`claimed`** | A GM (rank 0) or authorized officer has taken ownership | Weekly | 1× per day, rate-limited | Customization, Discord bot invite, rank config, crafting corner, invite codes |
| **`active`** | Paying subscription attached | Configurable (daily or more) | Per-tier limits | Full feature set, priority sync queue, shorter wait on expensive operations |

**Demotion, not deletion:** If an `active` guild cancels their subscription, status returns to `claimed` (they still have an owner). If the owner un-registers, status returns to `stub` (auto-managed). The guild schema and data are retained — they just lose elevated sync frequency and premium features. Deletion only happens via the retention policy (see Section 4.5).

**Auto-managed stubs are lightweight by design.** A stub guild costs one weekly Blizzard API call. The platform can hold thousands of stubs without meaningful API load.

#### Guild discovery flow

When a player connects Battle.net and characters are imported:
1. Query Blizzard API for the guild each character belongs to (by guild ID).
2. For each guild found:
   - **Known, claimed/active** → link the player as a member automatically.
   - **Known stub** → link the player; show the guild in `/dashboard/guilds` as "Your guild is here — no one has set it up yet."
   - **Unknown** → create a stub record in `system.tenants` (status=`stub`, no schema yet). Link the player.
3. Player sees all their guilds in the dashboard. Guild-specific features are grayed out for stubs.

#### Claiming a guild

- Any character verified as rank 0 (GM) via Blizzard API sees "Set up this guild."
- Claiming provisions the `guild_{slug}.*` schema, runs migrations, seeds from Blizzard data.
- A setup wizard covers: Discord bot invite → channel config → rank mapping → first member invites.
- **No platform admin involvement required.** Fully automated.

**Disputed claims / non-GM officers:** If the GM never joins the platform, a high-rank officer can apply for admin. This is a manual review by platform admin. Policy: follow the Blizzard API. If someone disputes it, that's a guild politics problem, not a platform problem. We document this clearly upfront. At early scale this is a handful of cases per year, not a queue.

#### Provisioning

- Schema creation: automated on claim.
- Discord: GL gets an "Invite Bot" OAuth2 link. One click.
- Blizzard: platform-level credentials. GL does not register a Blizzard app.
- Stripe: payment link on upgrade to `active`. Webhook confirms → status promoted.

### 4.2 API Sync & Rate Limiting

The sync frequency tier model applies to both guilds and individual players. This is how we control platform costs and give paid users a meaningful upgrade.

#### Guild sync tiers

| Status | Background sync | Manual refresh limit | Queue priority |
|---|---|---|---|
| `stub` | Weekly | None | — |
| `claimed` (free) | Weekly | 1× per day | Low |
| `active` (paid tier 1) | Daily | 5× per day | Medium |
| `active` (paid tier 2) | Configurable / near-real-time | Unlimited (reasonable) | High |

#### Player sync tiers

Same principle. A free player gets weekly character syncs and can manually refresh once per day. Paid players get more frequent background syncs and shorter queue wait times for expensive operations.

#### Priority queuing (future monetization lever)

For any features that are compute-heavy, API-intensive, or involve third-party services (SimC, AI recommendations, etc.), a tiered priority queue is a natural monetization mechanism — free users wait longer, paid users get faster results. This is how Raidbots handles SimC at scale. Not in scope for Phase X but worth designing the entitlement layer to support it when needed.

### 4.3 Guild Member Experience

When a guild is selected, the player sees guild-specific panels overlaid on or alongside their player data:

```
Guild context header: [Guild Name] [Realm] [Your Rank]

Guild features (require guild selection):
  /guild/{slug}/roster         — guild roster view
  /guild/{slug}/raids          — raid calendar, event detail, signup
  /guild/{slug}/attendance     — your attendance record
  /guild/{slug}/crafting       — guild crafting corner (orders, directory)
  /guild/{slug}/campaigns      — voting campaigns

Player features (always available, guild-agnostic):
  /dashboard/characters
  /dashboard/gear
  /dashboard/professions
  /dashboard/performance
  /dashboard/market
```

**URL strategy:** Option B — guild context at `{slug}.pullallthethings.com`, player context at `app.pullallthethings.com`. See Section 5.

### 4.4 Guild Admin Tools

Guild admins (Officer+) access a guild-scoped admin panel — functionally equivalent to the current PATT admin pages, but scoped to their guild tenant. GL-only features remain GL-only (tier config, billing, guild claim management).

The current `/admin/*` routes become `/guild/{slug}/admin/*` or live at `{slug}.{domain}/admin/*` depending on domain strategy.

### 4.5 Guild Data Retention

Retention is only triggered when a guild **disappears from the Blizzard API** (identified by guild ID — not name). A guild that simply has no active owner or subscription is just a stub — it stays indefinitely.

When a guild ID is no longer found in the Blizzard API:

1. **Day 0:** Guild marked `status=defunct`. Frozen — readable but no new syncs.
2. **Day 30:** Export triggered automatically — raid events + attendance dumped to JSON/CSV, emailed to last known owner (if any).
3. **Day 30:** Schema soft-deleted (marked inactive, not dropped).
4. **Day 90:** Schema hard-purged.

Player data in `player.*` is unaffected at every step. Only `guild_{slug}.*` organizational records are subject to this policy.

**If a guild un-registers its owner (without disappearing from Blizzard):** status reverts to `stub`. Data retained indefinitely. No retention clock started.

### 4.6 Custom Domains

Premium guild tenants can point `www.theirguildname.com` (or any domain they own) at the platform. The platform resolves the custom domain to the correct guild tenant.

- Platform side: `system.tenant_domains` table — custom_domain → tenant_slug mapping.
- Nginx: `server_name` wildcard + catch-all that resolves via app layer lookup.
- SSL: Let's Encrypt cert per custom domain (ACME HTTP-01 challenge, automated at provisioning time).
- Custom domains are post-launch only. Not in scope for Phase X. See Q3 in open questions.

---

## 5. Domain & URL Strategy

**Domain: `pullallthethings.com` (current) until brand name is decided with marketing.**
**Architecture: Option B — subdomains per guild, unified player app.**

Brand naming is a marketing decision, not a dev decision. When the brand is finalized, the new domain will be pointed at the same infrastructure with no rebuild required.

### Production URL Structure

```
pullallthethings.com               — marketing / landing page (and PATT's own presence)
app.pullallthethings.com           — individual player dashboard
{slug}.pullallthethings.com        — per-guild subdomain (e.g. patt.pullallthethings.com)
www.theirguildname.com             — custom domain → proxied to guild slug (post-launch, if demand exists)
```

**Session cookies:** Scoped to `.pullallthethings.com` — works across all subdomains automatically. A user logged in at `app.pullallthethings.com` is also authenticated at `patt.pullallthethings.com`.

**SSL:** One wildcard cert `*.pullallthethings.com` covers all single-level subdomains (app, all guild slugs). Let's Encrypt DNS-01 challenge via Hetzner DNS API plugin.

### Dev / Test URL Structure

```
dev.app.pullallthethings.com       — player dashboard (dev)
dev.{slug}.pullallthethings.com    — guild subdomains (dev)
test.app.pullallthethings.com      — player dashboard (test)
test.{slug}.pullallthethings.com   — guild subdomains (test)
```

**SSL wrinkle:** `*.pullallthethings.com` only covers one level deep. `dev.app.pullallthethings.com` is two levels deep, so it isn't covered. Mitigation options:
- (A) Add `*.app.pullallthethings.com` and `*.dev.pullallthethings.com` as additional SANs on the cert — Let's Encrypt supports multiple wildcard SANs in one cert with DNS-01 challenge.
- (B) For dev/test, use flat subdomains: `dev-app.pullallthethings.com`, `dev-{slug}.pullallthethings.com` — covered by the existing `*.pullallthethings.com` wildcard. Slightly less clean but zero SSL complexity.

**Recommendation:** Option B (flat `dev-` prefix) for dev/test during build. Revisit when brand domain is finalized — the new domain will have a clean wildcard setup from day one.

### PATT Itself

**First infrastructure step of Phase X:** PATT moves to `patt.pullallthethings.com`. The root domain (`pullallthethings.com`) becomes the platform landing/marketing page. PATT becomes `guild_patt` in the platform — a normal guild tenant, same as any other. This migration is the first concrete action, not the last (unlike the old plan where PATT migrated in last). It establishes the pattern and proves the routing works before any other guild is onboarded.

---

## 6. Monetization Framework

**Launch strategy: everything free, entitlement layer stubbed.** Specific tiers and pricing are TBD pending real usage data. The infrastructure is designed so monetization can be added without touching feature code.

### 6.1 The Entitlement Layer (Built at Launch, Stubbed)

The entitlement system is a **callable service**, not a PATT-specific feature. Any system — Stripe, Patreon, an external API, a manual admin action — can grant or revoke access. The check layer doesn't care how an entitlement was granted.

**Schema:**

```sql
system.features
  -- Registry of every gateable capability
  feature_key    VARCHAR UNIQUE     -- e.g. 'gear_plans_advanced', 'guild_raids'
  display_name   VARCHAR
  default_free   BOOLEAN            -- true = available to all registered users by default
  description    TEXT

system.plans
  -- Named bundles of features (free, individual_pro, guild_standard, etc.)
  -- Defined in config initially; can become DB rows when pricing is finalized
  plan_key       VARCHAR UNIQUE
  display_name   VARCHAR
  feature_keys   TEXT[]             -- which features this plan includes

system.subscriptions
  -- Active subscription state, per user OR per tenant (not both)
  id             SERIAL PRIMARY KEY
  subject_type   VARCHAR CHECK ('user', 'tenant')
  subject_id     INTEGER            -- user.id or tenant.id
  plan_key       VARCHAR
  status         VARCHAR CHECK ('active', 'trialing', 'past_due', 'canceled')
  source         VARCHAR CHECK ('stripe', 'manual', 'external')
  stripe_sub_id  VARCHAR            -- null if source != stripe
  current_period_end TIMESTAMPTZ
  created_at     TIMESTAMPTZ

system.entitlement_grants
  -- One-off feature grants, independent of a subscription plan
  -- This is the external hook: Patreon webhook, admin grant, donation, etc.
  id             SERIAL PRIMARY KEY
  subject_type   VARCHAR CHECK ('user', 'tenant')
  subject_id     INTEGER
  feature_key    VARCHAR            -- FK → system.features
  source         VARCHAR            -- 'stripe_onetime', 'patreon', 'manual', 'external_api', etc.
  granted_by     VARCHAR            -- admin username or system name
  expires_at     TIMESTAMPTZ        -- null = permanent
  created_at     TIMESTAMPTZ

system.entitlement_overrides
  -- Admin-level hard allow/deny per user or tenant, bypasses everything else
  subject_type   VARCHAR CHECK ('user', 'tenant')
  subject_id     INTEGER
  feature_key    VARCHAR
  allow          BOOLEAN            -- true = always allow, false = always deny
  reason         TEXT
  set_by         VARCHAR
```

**The check function:**

```python
async def can(subject_type, subject_id, feature_key) -> bool:
    # Phase X stub: always return True
    return True

    # Future real implementation:
    # 1. Check entitlement_overrides (hard allow/deny)
    # 2. Check entitlement_grants (one-off, check expiry)
    # 3. Check subscriptions → plan → feature_keys
    # 4. Check features.default_free
    # 5. Return False
```

Every feature route calls `await can("user", user_id, "feature_x")` or `await can("tenant", tenant_id, "feature_x")`. During Phase X launch the stub returns `True` and nothing is gated. When billing goes live, the stub is removed — no feature code changes needed.

### 6.2 Supported Payment Models (Infrastructure Ready, Not Wired Yet)

| Model | Mechanism | Notes |
|---|---|---|
| **Free** | Default; `features.default_free = true` | All registered users |
| **Individual subscription** | Stripe monthly/annual → `system.subscriptions` (subject_type='user') | Per-player premium features |
| **Guild subscription** | Stripe monthly/annual → `system.subscriptions` (subject_type='tenant') | Guild platform features |
| **One-time purchase / donation** | Stripe checkout → `system.entitlement_grants` | Unlocks specific features permanently or for a period |
| **External grant** | Patreon webhook, manual admin, external API call → `system.entitlement_grants` | Source field tracks origin; same table, same check |

### 6.3 What Is and Isn't in Phase X Scope

**In scope (Phase X):**
- Schema above created and migrated
- `can()` stub wired into every gated route
- Admin ability to manually insert `entitlement_grants` (a simple admin UI row)
- `system.features` seeded with all gateable feature keys
- **Data export is explicitly seeded as `default_free = TRUE` and is never overridable to false.** It cannot be paywalled by any subscription or entitlement configuration.

**Out of scope until billing phase:**
- Stripe integration
- Subscription management UI
- Billing portal
- Patreon or external webhook handlers
- Actual tier definitions and pricing

---

## 7. Infrastructure & Multi-Tenancy

### 7.1 The Architecture

```
Nginx (one server, wildcard SSL)
  ├── app.{domain} → FastAPI app (player context)
  ├── {slug}.{domain} → FastAPI app (guild tenant context, resolved from Host header)
  ├── www.theirguildname.com → FastAPI app (resolved from system.tenant_domains)
  └── {domain} → marketing site (static or same app, marketing routes)

FastAPI app (one process)
  ├── TenantMiddleware — resolves guild tenant from Host header (if applicable)
  ├── AuthMiddleware — resolves user from session cookie/JWT
  ├── EntitlementMiddleware — loads user's plan/feature flags
  └── Routes — player routes use player.* schema; guild routes use guild_{slug}.* schema

PostgreSQL (one cluster, PgBouncer in front)
  ├── system.*        — auth, billing, tenant registry, custom domains
  ├── player.*        — all player-owned data
  ├── platform.*      — shared reference data
  └── guild_{slug}.*  — one schema per claimed guild tenant
```

### 7.2 DB Session Routing

- Player routes: session uses `system.*` + `player.*` + `platform.*`
- Guild routes: session additionally sets `search_path` to include `guild_{slug}.*`
- `ContextVar` carries (user_id, tenant_slug | None) per request
- No function signature changes needed — the session layer handles `search_path`

### 7.3 Background Scheduler

One shared scheduler, tenant-aware:
- On startup: load all active tenants from `system.tenants`
- Register per-tenant jobs (Blizzard sync, BNet refresh, WCL sync, etc.) with staggered offsets
- Job context carries tenant_slug; sets search_path for that job's DB session
- New tenants added/removed at runtime without restart
- Rate limiting: shared Blizzard API token bucket across all tenant jobs

### 7.4 Discord Bot

One platform bot token. Discord routes events by `guild_id`. The bot:
- Resolves `guild_id` → tenant slug from `system.tenants`
- Loads tenant config from `guild_{slug}.discord_config`
- Dispatches events to per-tenant handlers

Guild leaders invite the bot via a standard Discord OAuth2 invite link. One click, no bot token management.

### 7.5 Blizzard API

Platform-level credentials. No per-guild Blizzard API app setup. Individual players still do their own BNet OAuth (they authorize access to their own account data — this is required by Blizzard ToS and is player-initiated).

### 7.6 Alembic Migrations

Three migration tracks:
- `system` + `player` + `platform` — run on every deploy (affects all users)
- `guild_{slug}` — runner script iterates all active guild tenant schemas and applies pending migrations

---

## 7. Data Portability

Data portability is a core commitment, not a premium feature. Every user and every guild can export their data at any time, for free, without contacting support.

### 7.1 What Players Can Export

Everything in `player.*` that belongs to them:

| Data | Format |
|---|---|
| Characters (all realms, all stats) | JSON / CSV |
| Gear plans and BIS slot assignments | JSON |
| Equipment snapshots | JSON / CSV |
| Parse history (all WCL parses) | JSON / CSV |
| Profession and recipe data | JSON / CSV |
| Raid progression snapshots | JSON |
| M+ scores and history | JSON / CSV |
| Guild membership history | JSON / CSV |
| Account info (email, linked accounts) | JSON |

### 7.2 What Guild Leaders Can Export

Everything in `guild_{slug}.*` for their guild:

| Data | Format |
|---|---|
| Roster (members, ranks, characters) | JSON / CSV |
| Raid events and seasons | JSON / CSV |
| Attendance records | JSON / CSV |
| Crafting orders | JSON / CSV |
| Campaign and voting history | JSON |
| Guild quotes | JSON / CSV |
| Guild configuration (ranks, Discord config) | JSON |
| Availability preferences | CSV |
| Attendance rules | JSON |

### 7.3 Export Principles

- **Self-serve.** Export is triggered from the player dashboard or guild admin panel. No support request needed.
- **Complete.** Exports include all data, not a summary. No artificial limits on date range or record count.
- **Free, always.** Export is never gated behind a subscription tier, no matter what. A player canceling their subscription or a guild going inactive can still export everything before they leave.
- **Portable formats.** JSON for structured/nested data (gear plans, config). CSV for tabular data (roster, attendance, parses) — opens in Excel, importable by other tools.
- **Timely.** Export generation should complete within a reasonable time. Large exports (full guild history) may be async — "we'll email you when it's ready" is acceptable for large datasets.
- **On cancellation/deactivation.** When a guild's status drops to defunct, an export is automatically triggered and emailed to the last known guild owner (see Section 4.5). Players who delete their account are prompted to export first.

### 7.4 What Export Does NOT Include

- `platform.*` reference data (WoW items, BIS lists, etc.) — this is Blizzard/community data, not the user's
- Other players' data — a guild export includes roster info but not another player's full parse history
- Parse records attributed to characters that were later disputed — disputed records are noted but not silently excluded

### 7.5 Build Timing

A basic export endpoint (JSON dump of player data) should ship in **Phase X.3** alongside the player dashboard — players should be able to export from day one. Guild export ships in **Phase X.4** alongside guild infrastructure. Format polish (CSV, scoped exports) can follow in Phase X.7.

---

## 8. Build Sequence

Option A: Foundation-first, PATT migrates early. Chosen over a parallel build (Option B) to avoid running two systems simultaneously and to ensure PATT members benefit from platform improvements early. Chosen over incremental dual-write migration (Option C) because dual-write layers compound in complexity and risk getting stuck half-migrated.

Each phase is its own branch and PR. Phases are sequential — later phases depend on earlier ones being stable.

---

### Phase X.1 — Infrastructure (No Logic Changes to PATT)

**Goal:** New routing and schema scaffolding in place. PATT continues running unchanged, just at a new URL.

- Nginx wildcard SSL cert (`*.pullallthethings.com`) via Let's Encrypt DNS-01
- Subdomain routing: `patt.pullallthethings.com` → existing PATT app (same code, same DB, just new hostname)
- Root domain (`pullallthethings.com`) → marketing placeholder page
- PgBouncer setup in front of PostgreSQL
- New schemas created alongside existing: `system.*`, `player.*`, `platform.*` (empty — no data migration yet)
- `TenantMiddleware`, `AuthMiddleware`, `EntitlementMiddleware` stubs (pass-through, no logic yet)
- `can()` entitlement stub added to codebase, returns `True` everywhere

**PATT impact:** URL changes from `pullallthethings.com` to `patt.pullallthethings.com`. No feature changes, no auth changes. Announce to guild in advance. Bookmarks/links break — communicate clearly.

**Rollback:** Revert Nginx config. Zero data risk.

---

### Phase X.2 — Auth Rebuild

**Goal:** New platform-level auth system live for new users. PATT members still on old auth temporarily.

- `system.users`, `system.user_auth` (email + bcrypt, email verification flow)
- Platform-level BNet OAuth (linked to `system.users`, not guild-scoped)
- New registration UI: player-first flow, guild optional, BNet connect prompted after email verify
- Session cookies scoped to `.pullallthethings.com` (works across all subdomains)
- `app.pullallthethings.com` live — new users can register and connect BNet
- Invite code system retained but now platform-level (guild can issue codes that pre-link to their tenant)
- Old PATT auth (`common.users`, JWT flow) kept running at `patt.pullallthethings.com` in parallel
- **i18n stub:** Babel/gettext installed; all Jinja2 template strings wrapped in `_()` from this point forward; EN-US catalog only; locale-aware date/number formatting used throughout; no hardcoded locale assumptions. Adding a language later = adding a catalog file, not touching code.

**PATT impact:** None yet. PATT members log in exactly as before. New platform auth runs alongside but doesn't touch PATT.

**Rollback:** New auth is additive. Old auth untouched. Safe.

---

### Phase X.3 — Player Schema + Data Migration ⚠️ DISRUPTION WINDOW

**Goal:** `player.*` populated with real data. PATT members migrated to the new auth and player schema. This is the highest-risk phase.

#### Pre-migration steps (before any user impact)
- `player.*` schema fully defined and migrated (empty)
- Migration scripts written and tested against a copy of PATT's production data
- Full database backup taken immediately before cutover
- Rollback procedure documented and tested
- Communication sent to PATT members at least 48 hours in advance

#### The migration (planned maintenance window)
- **Estimated downtime: 30–60 minutes** (PATT goes read-only or offline during cutover)
- Existing `guild_identity.wow_characters`, `character_equipment`, `gear_plans`, `raiderio_profiles`, `character_report_parses`, `character_mythic_plus`, `character_raid_progress`, `professions`, `character_recipes` → migrated to `player.*`
- Existing `common.users` → migrated to `system.users` (passwords rehashed if needed, or migrated as-is since bcrypt is already used)
- BNet OAuth tokens migrated from `guild_identity.battlenet_accounts` → `system.bnet_accounts`
- PATT app restarted pointing at new schemas
- Smoke test: can existing PATT members log in? Do their characters appear? Do gear plans load?

#### Post-migration
- Old `common.users`, relevant `guild_identity.*` tables kept as read-only backup for 30 days, then dropped
- Player dashboard at `app.pullallthethings.com` now has real data for all migrated PATT members
- `patt.pullallthethings.com` now reads character data from `player.*`

**PATT impact:** Planned outage of 30–60 minutes. Members may need to re-authenticate (session tokens invalidated by auth migration). BNet re-link may be required if tokens don't transfer cleanly. All character data, gear plans, and history are preserved — nothing is lost, only moved. **If migration fails mid-flight, rollback to backup and restore old schemas. PATT members lose at most the last 30–60 minutes of activity.**

**Rollback plan:**
1. Restore database from pre-migration backup
2. Revert app config to old schema connections
3. Restart PATT app
4. Total recovery time: ~15 minutes from decision to rollback

---

### Phase X.4 — Guild Infrastructure

**Goal:** Guild tenant schema provisioning working. PATT's guild data migrated. Guild discovery live.

- `guild_{slug}.*` provisioning service: creates schema, runs Alembic migrations, seeds config
- `guild_patt.*` provisioned and PATT's guild data migrated in:
  - `guild_ranks`, `rank_wow_mapping`, `discord_config`, `site_config` → `guild_patt.*`
  - `raid_seasons`, `raid_events`, `raid_attendance`, `recurring_events` → `guild_patt.*`
  - `campaigns`, `guild_quotes`, `player_availability`, `attendance_rules` → `guild_patt.*`
  - `player_guild_memberships` populated for all current PATT members
- Old `guild_identity.*` tables that moved to `player.*` (Phase X.3) and `guild_patt.*` (this phase) decommissioned
- `common.*` guild-specific tables decommissioned
- Blizzard-driven guild discovery: on BNet connect, query guild IDs, auto-create stubs in `system.tenants`
- Guild status hierarchy (`stub` / `claimed` / `active`) enforced
- Sync frequency tiers: stub = weekly, claimed = weekly + 1 manual/day

**PATT impact:** PATT guild tools (raids, attendance, crafting, admin pages) may require a brief restart. No data loss. Guild features at `patt.pullallthethings.com` continue working, now reading from `guild_patt.*`.

**Rollback:** Guild data migration is lower risk than Phase X.3 — guild tables don't affect login or character display. Schema can be rebuilt from backup if needed.

---

### Phase X.5 — Guild Claiming & Self-Serve Provisioning

**Goal:** Any guild can be claimed and set up without Mike's involvement.

- Claim flow: GM rank verification via Blizzard API → schema provisioned → setup wizard launched
- Setup wizard (self-serve GL version):
  1. Invite platform Discord bot to your server (one-click OAuth2 link)
  2. Map Discord channels (announcements, audit, etc.)
  3. Review and map WoW rank structure
  4. Review auto-populated roster (from Blizzard data + existing platform members)
  5. Customize guild page (accent color, tagline)
- Officer claim path (non-GM): submits application → platform admin review queue → manual approval
- "Your guild is here but unclaimed" notification in player dashboard
- Provisioning audit log in `system.provisioning_log`
- Discord bot event routing: `guild_id` → tenant lookup → per-tenant config dispatch

---

### Phase X.6 — Billing Schema & Rate Limiting

**Goal:** Entitlement infrastructure fully in place (still stubbed for most features, but schema real). Rate limits enforced.

- `system.features` seeded with all gateable feature keys
- `system.plans`, `system.subscriptions`, `system.entitlement_grants`, `system.entitlement_overrides` created
- `can()` stub remains `True` — no features actually gated yet
- Platform admin UI: manually insert/revoke `entitlement_grants` (for testing and edge cases)
- `system.sync_log` created: tracks last sync per subject per sync_type, enforces manual refresh rate limits
- Rate limits active: `claimed` guilds limited to 1 manual refresh/day; enforced via `sync_log`

---

### Phase X.7 — Platform Polish & Launch

**Goal:** Platform ready for external guilds. Marketing page live. Onboarding smooth.

- Marketing/landing page at `pullallthethings.com` (placeholder → real content)
- Email flows: verification, welcome, guild claim confirmation, guild invite
- Player dashboard UX polish (the full tabbed expansion from Section 3.2)
- Error handling and edge case coverage for self-serve guild flows
- Load testing (multiple tenants, concurrent syncs)
- Documentation for guild leaders (setup guide, FAQ)
- Soft launch: invite 2–3 known guild leaders to onboard as beta tenants

---

## 9. Open Questions (Tracking)

| # | Question | Status | Notes |
|---|---|---|---|
| Q1 | Product brand & domain name | **Deferred** | Using `pullallthethings.com` until marketing lead joins team; brand is a marketing decision |
| Q2 | Domain strategy — Option A, B, or C? | **Resolved** | Option B (subdomains per guild). See Section 5. |
| Q3 | Guild custom domains — self-serve or assisted? | **Deferred post-launch** | Subdomains only at launch. Custom domains add SSL/ops complexity for uncertain demand; revisit if users ask for it. |
| Q4 | Individual player tier monetization — what's free vs. paid? | **Deferred** | Launch free; entitlement layer stubbed. Tier assignments defined when billing phase begins. |
| Q5 | Guild tier pricing | **Deferred** | Same as Q4 — infrastructure ready, pricing TBD. |
| Q6 | Donation model — what does a donor unlock? | **Deferred** | `entitlement_grants` table supports any model; specifics TBD. External grants (Patreon etc.) supported via same table. |
| Q7 | Redis — introduce for rate limiting / scheduler state? | **Deferred** | Use PostgreSQL (`system.sync_log`) for rate limiting at launch. Introduce Redis when contention becomes a real problem, not before. |
| Q8 | Guild auto-claim by non-GM? | **Resolved** | GM (rank 0) via Blizzard API for self-serve claim. Non-GM officers can apply; manual platform admin review. Policy: follow Blizzard API, don't get involved in guild politics. |
| Q9 | Data retention on guild cancellation | **Resolved** | Retention only triggers on Blizzard API disappearance (by guild ID). Owner un-register → reverts to stub, no deletion. See Section 4.5. |
| Q10 | Player data on guild leave | **Resolved** | All meaningful player data lives in `player.*` and is never owned by the guild. Guild schema only holds organizational records (raids, attendance, config). Player takes everything with them automatically. |
| Q11 | What does the setup wizard look like for a self-serve GL? | **Resolved** | Platform owns Blizzard API credentials and Discord bot (one platform bot, N servers — standard model). GL never touches a developer portal. Wizard steps: invite bot → map channels → map ranks → review auto-populated roster → customize page. |
| Q12 | Multi-region + multi-language? | **Resolved** | Launch US/English only. i18n infrastructure stubbed in codebase from day one (Babel/gettext, all UI strings wrapped). Multi-region considerations documented in `reference/PHASE_Z_MULTI_REGION_NOTES.md`. |

---

## Related Files

- `reference/PHASE_X_SAAS_PLATFORM.md` — superseded by this doc
- `reference/PHASE_X_PLUS_1_MULTI_TENANT.md` — superseded by this doc
- `reference/PHASE_X_INFRA_SCALING.md` — infra benchmarks still valid; server sizing estimates apply
- `reference/PHASE_Y_COLLECTIONS.md` — downstream feature work, depends on this phase
- `reference/gear-plan-1-feature.md` — gear plan feature; will be a player-owned feature in the new model
- `src/guild_portal/pages/setup_pages.py` — setup wizard to be redesigned for self-serve
- `src/sv_common/guild_sync/scheduler.py` — will become multi-tenant scheduler
