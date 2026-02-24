# Phase 2.7: Data Model Migration — Clean 3NF Rebuild

## Overview

This phase redesigns the data model from the ground up. The current schema has parallel
systems (common.guild_members vs guild_identity.persons, common.characters vs
guild_identity.wow_characters) that were created during the Google Sheets migration era.
This phase eliminates the duplication and establishes a clean 3NF model centered on the
**player** entity.

**What this phase produces:**
1. Reference tables for WoW classes, specializations, and combat roles
2. A redesigned `players` table (renamed from `persons`) as the core entity
3. A `player_characters` bridge table for character ownership
4. Direct 1:1 FKs on players for Discord and website user links (replacing generic identity_links)
5. Main/off-spec declaration fields on the player entity
6. Derived guild rank (highest among characters → Discord fallback → admin override)
7. All existing FK references repointed from `guild_members` → `players`
8. Elimination of: `common.guild_members`, `common.characters`, `guild_identity.identity_links`, `guild_identity.persons` (renamed)
9. Updated SQLAlchemy models, service layer, API routes, and templates
10. A data migration script that maps existing data into the new structure

**What this phase does NOT do:**
- Activate the onboarding system (deferred — schema must be solid first)
- Build new admin UI for the data model (future phase)
- Change the WoW addon or companion app behavior

## Prerequisites

- All previous phases complete (0–7, 2.5A–D, 2.6 built but dormant)
- Database backup taken before migration
- Familiarity with CLAUDE.md for architecture context

## Important Context

- `guild_identity.persons` table is currently **empty** — identity engine was never run on existing data
- `guild_identity.identity_links` table is currently **empty**
- `common.guild_members` has ~40 rows (the actual roster from Google Sheets migration)
- `common.characters` has character data from the same migration
- `guild_identity.wow_characters` has ~320 rows from Blizzard API syncs
- `guild_identity.discord_members` has Discord server members from bot syncs
- The Phase 2.6 onboarding code exists but was **never activated** (on_member_join not wired)
- No live users depend on the tables being dropped — the migration is safe

---

## Task 1: Create Reference Tables

Create these in the `guild_identity` schema. Seed them with current WoW data.

### 1.1 — roles

```sql
CREATE TABLE guild_identity.roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) NOT NULL UNIQUE  -- Tank, Healer, Melee DPS, Ranged DPS
);

-- Seed data
INSERT INTO guild_identity.roles (name) VALUES
    ('Tank'), ('Healer'), ('Melee DPS'), ('Ranged DPS');
```

### 1.2 — classes

```sql
CREATE TABLE guild_identity.classes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(30) NOT NULL UNIQUE,
    color_hex VARCHAR(7)  -- Blizzard class color for UI
);

-- Seed data (all current WoW classes with official colors)
INSERT INTO guild_identity.classes (name, color_hex) VALUES
    ('Death Knight', '#C41E3A'),
    ('Demon Hunter', '#A330C9'),
    ('Druid', '#FF7C0A'),
    ('Evoker', '#33937F'),
    ('Hunter', '#AAD372'),
    ('Mage', '#3FC7EB'),
    ('Monk', '#00FF98'),
    ('Paladin', '#F48CBA'),
    ('Priest', '#FFFFFF'),
    ('Rogue', '#FFF468'),
    ('Shaman', '#0070DD'),
    ('Warlock', '#8788EE'),
    ('Warrior', '#C69B6D');
```

### 1.3 — specializations

Each row is a unique class+spec pair. The `wowhead_slug` is for comp export URLs.
The `default_role_id` maps each spec to its combat role.

```sql
CREATE TABLE guild_identity.specializations (
    id SERIAL PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES guild_identity.classes(id),
    name VARCHAR(50) NOT NULL,
    default_role_id INTEGER NOT NULL REFERENCES guild_identity.roles(id),
    wowhead_slug VARCHAR(50),  -- For comp export: e.g., 'balance-druid'

    UNIQUE(class_id, name)
);
```

**Seed data** — all current class/spec combos. Use the roles table IDs:
Tank=1, Healer=2, Melee DPS=3, Ranged DPS=4 (based on insert order above).

```sql
-- Death Knight
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Death Knight'), 'Blood', 1, 'blood-death-knight'),
    ((SELECT id FROM guild_identity.classes WHERE name='Death Knight'), 'Frost', 3, 'frost-death-knight'),
    ((SELECT id FROM guild_identity.classes WHERE name='Death Knight'), 'Unholy', 3, 'unholy-death-knight');

-- Demon Hunter
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Demon Hunter'), 'Havoc', 3, 'havoc-demon-hunter'),
    ((SELECT id FROM guild_identity.classes WHERE name='Demon Hunter'), 'Vengeance', 1, 'vengeance-demon-hunter');

-- Druid
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Druid'), 'Balance', 4, 'balance-druid'),
    ((SELECT id FROM guild_identity.classes WHERE name='Druid'), 'Feral', 3, 'feral-druid'),
    ((SELECT id FROM guild_identity.classes WHERE name='Druid'), 'Guardian', 1, 'guardian-druid'),
    ((SELECT id FROM guild_identity.classes WHERE name='Druid'), 'Restoration', 2, 'restoration-druid');

-- Evoker
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Evoker'), 'Devastation', 4, 'devastation-evoker'),
    ((SELECT id FROM guild_identity.classes WHERE name='Evoker'), 'Preservation', 2, 'preservation-evoker'),
    ((SELECT id FROM guild_identity.classes WHERE name='Evoker'), 'Augmentation', 4, 'augmentation-evoker');

-- Hunter
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Hunter'), 'Beast Mastery', 4, 'beast-mastery-hunter'),
    ((SELECT id FROM guild_identity.classes WHERE name='Hunter'), 'Marksmanship', 4, 'marksmanship-hunter'),
    ((SELECT id FROM guild_identity.classes WHERE name='Hunter'), 'Survival', 3, 'survival-hunter');

-- Mage
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Mage'), 'Arcane', 4, 'arcane-mage'),
    ((SELECT id FROM guild_identity.classes WHERE name='Mage'), 'Fire', 4, 'fire-mage'),
    ((SELECT id FROM guild_identity.classes WHERE name='Mage'), 'Frost', 4, 'frost-mage');

-- Monk
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Monk'), 'Brewmaster', 1, 'brewmaster-monk'),
    ((SELECT id FROM guild_identity.classes WHERE name='Monk'), 'Mistweaver', 2, 'mistweaver-monk'),
    ((SELECT id FROM guild_identity.classes WHERE name='Monk'), 'Windwalker', 3, 'windwalker-monk');

-- Paladin
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Paladin'), 'Holy', 2, 'holy-paladin'),
    ((SELECT id FROM guild_identity.classes WHERE name='Paladin'), 'Protection', 1, 'protection-paladin'),
    ((SELECT id FROM guild_identity.classes WHERE name='Paladin'), 'Retribution', 3, 'retribution-paladin');

-- Priest
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Priest'), 'Discipline', 2, 'discipline-priest'),
    ((SELECT id FROM guild_identity.classes WHERE name='Priest'), 'Holy', 2, 'holy-priest'),
    ((SELECT id FROM guild_identity.classes WHERE name='Priest'), 'Shadow', 4, 'shadow-priest');

-- Rogue
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Rogue'), 'Assassination', 3, 'assassination-rogue'),
    ((SELECT id FROM guild_identity.classes WHERE name='Rogue'), 'Outlaw', 3, 'outlaw-rogue'),
    ((SELECT id FROM guild_identity.classes WHERE name='Rogue'), 'Subtlety', 3, 'subtlety-rogue');

-- Shaman
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Shaman'), 'Elemental', 4, 'elemental-shaman'),
    ((SELECT id FROM guild_identity.classes WHERE name='Shaman'), 'Enhancement', 3, 'enhancement-shaman'),
    ((SELECT id FROM guild_identity.classes WHERE name='Shaman'), 'Restoration', 2, 'restoration-shaman');

-- Warlock
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Warlock'), 'Affliction', 4, 'affliction-warlock'),
    ((SELECT id FROM guild_identity.classes WHERE name='Warlock'), 'Demonology', 4, 'demonology-warlock'),
    ((SELECT id FROM guild_identity.classes WHERE name='Warlock'), 'Destruction', 4, 'destruction-warlock');

-- Warrior
INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug) VALUES
    ((SELECT id FROM guild_identity.classes WHERE name='Warrior'), 'Arms', 3, 'arms-warrior'),
    ((SELECT id FROM guild_identity.classes WHERE name='Warrior'), 'Fury', 3, 'fury-warrior'),
    ((SELECT id FROM guild_identity.classes WHERE name='Warrior'), 'Protection', 1, 'protection-warrior');
```

**Note:** If Blizzard adds new specs (they did for Evoker this xpac), just INSERT new rows.
The schema handles this without any structural changes.

---

## Task 2: Restructure wow_characters

Modify the existing `guild_identity.wow_characters` table:

1. **Drop** `person_id` column (ownership moves to player_characters bridge)
2. **Drop** `is_main` column (main designation moves to players table)
3. **Drop** `role_category` column (derived from specializations reference table)
4. **Add** `class_id` FK → `guild_identity.classes` (replaces `character_class` text field)
5. **Add** `active_spec_id` FK → `guild_identity.specializations` (replaces `active_spec` text field)
6. **Add** `guild_rank_id` FK → `common.guild_ranks` (replaces `guild_rank` integer + `guild_rank_name` text)
7. **Keep** `character_class` and `active_spec` text fields temporarily for data migration, then drop

Migration steps:
```
1. Add new FK columns (nullable)
2. Populate class_id by matching character_class text → classes.name
3. Populate active_spec_id by matching (class_id, active_spec text) → specializations.(class_id, name)
4. Populate guild_rank_id by matching guild_rank integer → guild_ranks.level
5. Drop old text columns: character_class, active_spec, guild_rank, guild_rank_name
6. Drop person_id, is_main, role_category
```

**Important:** The `active_spec_id` from Blizzard is informational — it reflects the character's
current in-game spec, NOT what they raid as. The raid spec is declared on the player table.

---

## Task 3: Restructure discord_members → discord_users

Rename the table to `discord_users` (clearer naming — these are Discord accounts, not guild members):

```sql
ALTER TABLE guild_identity.discord_members RENAME TO discord_users;
```

Remove the `person_id` column — the link to a player is now a 1:1 FK on the players table:

```sql
ALTER TABLE guild_identity.discord_users DROP COLUMN person_id;
```

Update all indexes and constraints that reference the old table name.

---

## Task 4: Create the Players Table

Rename `persons` → `players` and add new columns:

```sql
ALTER TABLE guild_identity.persons RENAME TO players;

ALTER TABLE guild_identity.players
    ADD COLUMN discord_user_id INTEGER UNIQUE REFERENCES guild_identity.discord_users(id),
    ADD COLUMN website_user_id INTEGER UNIQUE REFERENCES common.users(id),
    ADD COLUMN guild_rank_id INTEGER REFERENCES common.guild_ranks(id),
    ADD COLUMN guild_rank_source VARCHAR(20),  -- 'wow_character', 'discord', 'admin_override'
    ADD COLUMN main_character_id INTEGER REFERENCES guild_identity.wow_characters(id),
    ADD COLUMN main_spec_id INTEGER REFERENCES guild_identity.specializations(id),
    ADD COLUMN offspec_character_id INTEGER REFERENCES guild_identity.wow_characters(id),
    ADD COLUMN offspec_spec_id INTEGER REFERENCES guild_identity.specializations(id);
```

All new columns start NULL. Main/off-spec are set by the player on first login.
Guild rank is computed by application logic (highest character rank → Discord fallback → admin override).

---

## Task 5: Create player_characters Bridge

```sql
CREATE TABLE guild_identity.player_characters (
    id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    character_id INTEGER NOT NULL UNIQUE REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(player_id, character_id)
);

CREATE INDEX idx_player_characters_player ON guild_identity.player_characters(player_id);
```

---

## Task 6: Repoint Foreign Keys

These existing tables have FKs pointing at `common.guild_members`. They need to be
repointed to `guild_identity.players`.

### common.invite_codes
- `member_id` → rename to `player_id`, FK → `guild_identity.players(id)`
- `created_by` → rename to `created_by_player_id`, FK → `guild_identity.players(id)`

### common.member_availability
- `member_id` → rename to `player_id`, FK → `guild_identity.players(id)`
- Update UNIQUE constraint: `UNIQUE(player_id, day_of_week)`

### patt.campaigns
- `created_by` → `created_by_player_id`, FK → `guild_identity.players(id)`

### patt.campaign_entries
- `associated_member_id` → `player_id`, FK → `guild_identity.players(id)`

### patt.votes
- `member_id` → `player_id`, FK → `guild_identity.players(id)`
- Update UNIQUE constraint: `UNIQUE(campaign_id, player_id, rank)`

**Migration approach for each table:**
1. Add new column (nullable)
2. Populate via mapping: `guild_members.id → players.id` (using discord_id as the bridge key)
3. Drop old FK constraint
4. Drop old column
5. Rename new column if needed
6. Add NOT NULL if appropriate

### common.users
- Currently linked to guild_members via `guild_members.user_id → users.id`
- New link: `players.website_user_id → users.id` (already added in Task 4)
- Populate players.website_user_id from the guild_members mapping
- No changes to the users table itself

---

## Task 7: Update onboarding_sessions

The existing `guild_identity.onboarding_sessions` table references `persons` and `discord_members`.
Update:
- `discord_member_id` FK target: `guild_identity.discord_users(id)` (table was renamed)
- `verified_person_id` → rename to `verified_player_id`, FK → `guild_identity.players(id)`

---

## Task 8: Drop Dead Tables

After all data is migrated and FKs are repointed:

```sql
-- Drop in dependency order
DROP TABLE IF EXISTS guild_identity.identity_links;  -- replaced by player_characters + direct FKs
DROP TABLE IF EXISTS common.characters;               -- replaced by guild_identity.wow_characters
DROP TABLE IF EXISTS common.guild_members;             -- replaced by guild_identity.players
```

Also drop migration 0006's `preferred_role` column (superseded by player main/offspec spec declarations):
```sql
ALTER TABLE common.guild_members DROP COLUMN IF EXISTS preferred_role;
-- (This column was on guild_members which is being dropped anyway)
```

---

## Task 9: Data Migration Script

Create `scripts/migrate_to_players.py` — a one-time migration script.

**Logic:**
1. For each row in `common.guild_members`:
   a. Create a `guild_identity.players` row (display_name from guild_members)
   b. Find matching `guild_identity.discord_users` row by discord_id
   c. Set `players.discord_user_id` = that discord_users row
   d. Find matching `common.users` row (via guild_members.user_id)
   e. Set `players.website_user_id` = that users row
   f. Find `guild_identity.wow_characters` rows that were linked via the old person/identity_links
      OR match by character name from `common.characters`
   g. Create `player_characters` bridge rows
   h. Compute guild_rank_id from highest-ranked character
2. Map old guild_members.id → new players.id for FK repointing
3. Update all repointed FK columns using the mapping

**Important:** All main_character_id, main_spec_id, offspec_character_id, offspec_spec_id
stay NULL after migration. Every player sets their own on first login.

---

## Task 10: Update SQLAlchemy Models

Rewrite `src/sv_common/db/models.py` to match the new schema. Key changes:

### New models to add:
- `Role` (guild_identity.roles)
- `WowClass` (guild_identity.classes)
- `Specialization` (guild_identity.specializations)
- `PlayerCharacter` (guild_identity.player_characters)

### Models to rename/restructure:
- `GuildIdentityPerson` → `Player` (guild_identity.players) — add discord_user_id, website_user_id, guild_rank_id, guild_rank_source, main_character_id, main_spec_id, offspec_character_id, offspec_spec_id
- `GuildIdentityDiscordMember` → `DiscordUser` (guild_identity.discord_users) — remove person_id
- `WowCharacter` — remove person_id, is_main, role_category, character_class text, active_spec text, guild_rank int, guild_rank_name; add class_id FK, active_spec_id FK, guild_rank_id FK

### Models to delete:
- `GuildMember` (common.guild_members)
- `Character` (common.characters)
- `IdentityLink` (guild_identity.identity_links)

### Models to update (FK changes):
- `User` — remove relationship to GuildMember
- `InviteCode` — member_id → player_id, created_by → created_by_player_id
- `MemberAvailability` — member_id → player_id
- `Campaign` — created_by → created_by_player_id
- `CampaignEntry` — associated_member_id → player_id
- `Vote` — member_id → player_id
- `OnboardingSession` — verified_person_id → verified_player_id

### Relationships to add on Player:
```python
characters: Mapped[list["PlayerCharacter"]] = relationship(back_populates="player")
discord_user: Mapped[Optional["DiscordUser"]] = relationship()
website_user: Mapped[Optional["User"]] = relationship()
guild_rank: Mapped[Optional["GuildRank"]] = relationship()
main_character: Mapped[Optional["WowCharacter"]] = relationship(foreign_keys=[main_character_id])
main_spec: Mapped[Optional["Specialization"]] = relationship(foreign_keys=[main_spec_id])
offspec_character: Mapped[Optional["WowCharacter"]] = relationship(foreign_keys=[offspec_character_id])
offspec_spec: Mapped[Optional["Specialization"]] = relationship(foreign_keys=[offspec_spec_id])
```

---

## Task 11: Update Service Layer

### Files that reference GuildMember or Character models:

- `src/sv_common/identity/members.py` — rewrite to use Player model
- `src/sv_common/identity/characters.py` — rewrite to use WowCharacter + PlayerCharacter
- `src/sv_common/identity/ranks.py` — may need updates for rank derivation logic
- `src/sv_common/guild_sync/identity_engine.py` — update to create player_characters links instead of identity_links
- `src/sv_common/guild_sync/integrity_checker.py` — update queries for new schema
- `src/sv_common/guild_sync/discord_sync.py` — update to use discord_users table name
- `src/sv_common/guild_sync/db_sync.py` — update character insert/update queries
- `src/sv_common/guild_sync/onboarding/conversation.py` — update for players table
- `src/sv_common/guild_sync/onboarding/provisioner.py` — update for players table
- `src/sv_common/guild_sync/onboarding/deadline_checker.py` — update for players table
- `src/sv_common/guild_sync/onboarding/commands.py` — update queries

### Files that reference guild_members in SQL:
- `src/patt/api/admin_routes.py` — roster management endpoints
- `src/patt/api/guild_routes.py` — public roster endpoint
- `src/patt/api/campaign_routes.py` — voter eligibility
- `src/patt/api/vote_routes.py` — member lookups
- `src/patt/pages/admin_pages.py` — roster admin pages
- `src/patt/services/campaign_service.py` — member references
- `src/patt/services/vote_service.py` — member references
- `src/patt/deps.py` — `get_current_member()` and `require_rank()` auth deps

### Key behavior changes:
- `get_current_member()` in deps.py should resolve User → Player (via players.website_user_id)
- `require_rank()` should check player.guild_rank_id against guild_ranks.level
- Roster endpoint should join players → player_characters → wow_characters → specializations → roles
- Campaign voter eligibility should check player.guild_rank_id

---

## Task 12: Update Templates

Any Jinja2 template that displays member/character data needs updating:
- `src/patt/templates/admin/roster.html` — column changes
- `src/patt/templates/public/index.html` — if it shows roster data
- Any template referencing `member.discord_username`, `member.rank`, `character.main_alt`, etc.

---

## Task 13: Update Tests

All tests referencing the old models need updating. Key test files:
- `tests/unit/test_ranks.py`
- `tests/unit/test_members.py` → rename to `test_players.py`
- `tests/unit/test_characters.py`
- `tests/integration/test_admin_api.py`
- `tests/integration/test_guild_*.py`

### New tests to add:
- Reference table seed verification (all 13 classes, ~39 specs, 4 roles)
- Player creation with discord_user_id and website_user_id
- Player_characters bridge: link/unlink characters
- Main/off-spec declaration: set, change, clear
- Guild rank derivation: highest character rank, Discord fallback, admin override
- Spec → role derivation: spec lookup returns correct default_role
- Rank mismatch audit detection
- Roster query: only players with main_character_id set

---

## Task 14: Alembic Migration

Create `alembic/versions/0007_data_model_migration.py`.

This is a complex migration. Structure it as:

1. Create reference tables (roles, classes, specializations) with seed data
2. Rename persons → players, add new columns
3. Rename discord_members → discord_users, drop person_id
4. Add FK columns to wow_characters (class_id, active_spec_id, guild_rank_id)
5. Populate new FK columns from existing text data
6. Drop old text columns from wow_characters
7. Create player_characters bridge table
8. Add new FK columns to repointed tables (invite_codes, availability, campaigns, entries, votes)
9. Run data migration (guild_members → players mapping)
10. Drop old FK constraints and columns
11. Drop dead tables (identity_links, characters, guild_members)

**The downgrade should be a no-op with a warning** — this migration is not safely reversible.
Take a database backup before running.

---

## Derived Values (NOT stored — computed in application code)

These values are derived via joins, never stored redundantly:

| Value | Derivation |
|---|---|
| Player's main role | `players.main_spec_id → specializations.default_role_id → roles.name` |
| Player's off-spec role | `players.offspec_spec_id → specializations.default_role_id → roles.name` |
| Player's guild rank | `MAX(player_characters → wow_characters.guild_rank_id)` by `guild_ranks.level ASC` |
| Character's role type | If char = main_character_id → 'Main'. If char = offspec_character_id → 'Off-Spec'. Else → 'Alt'. |
| Roster eligible | `players WHERE main_character_id IS NOT NULL AND is_active = TRUE` |
| Rank mismatch | Any character with guild_rank_id != player's resolved rank → audit finding |

---

## Acceptance Criteria

- [ ] Reference tables created and seeded (4 roles, 13 classes, ~39 specializations)
- [ ] wow_characters uses FK references to classes, specializations, guild_ranks (no more text fields for these)
- [ ] discord_users table (renamed from discord_members) has no person_id
- [ ] players table has discord_user_id, website_user_id, guild_rank_id, main/offspec fields
- [ ] player_characters bridge table exists with proper UNIQUE constraints
- [ ] All FK repoints complete (invite_codes, availability, campaigns, entries, votes)
- [ ] identity_links, common.characters, common.guild_members dropped
- [ ] Data migration script successfully moves ~40 guild members → players
- [ ] All SQLAlchemy models match new schema
- [ ] Service layer compiles and works with new models
- [ ] Auth deps (get_current_member → get_current_player) work
- [ ] Existing tests updated and passing
- [ ] New tests for reference tables, bridge, main/offspec, rank derivation

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Database backup taken before migration
- [ ] Migration runs cleanly: `alembic upgrade head`
- [ ] Commit: `git commit -m "phase-2.7: data model migration to clean 3NF"`
- [ ] Update CLAUDE.md "Current Build Status" section
