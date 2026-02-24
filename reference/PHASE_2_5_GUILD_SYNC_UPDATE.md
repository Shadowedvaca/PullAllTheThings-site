# Phase 2.5 (Revised) — Guild Sync Code Update

> **Status:** Ready to execute
> **Prereqs:** Read CLAUDE.md, TESTING.md, and this file. Phase 2.7 must be complete.
> **Goal:** Update all guild_sync modules to work with the Phase 2.7 player model.
> After this phase, Blizzard API syncs, Discord member syncs, identity matching,
> and integrity checking all run correctly against the current schema.

---

## Background

Phase 2.5A–D built the guild sync system (Blizzard API client, Discord member sync,
identity matching engine, integrity checker, reporter, scheduler, WoW addon, companion
app). That code was written against the original schema and has been dormant since the
Phase 2.7 data model migration.

**This phase is a pure code update.** No new features, no new tables, no new migrations.
Just making every guild_sync module work with the tables and columns that actually exist now.

### What Changed in Phase 2.7

| Old | New | Notes |
|-----|-----|-------|
| `guild_identity.persons` | `guild_identity.players` | Renamed, added many columns |
| `guild_identity.discord_members` | `guild_identity.discord_users` | Renamed, dropped `person_id` |
| `guild_identity.identity_links` | `guild_identity.player_characters` | Bridge table, simpler structure |
| `wow_characters.person_id` | (removed) | Character→player link via `player_characters` bridge |
| `wow_characters.character_class` (VARCHAR) | `wow_characters.class_id` (FK → classes) | Text → FK |
| `wow_characters.character_spec` (VARCHAR) | `wow_characters.active_spec_id` (FK → specializations) | Text → FK |
| `wow_characters.guild_rank` (INTEGER index) | `wow_characters.guild_rank_id` (FK → guild_ranks) | Index → FK |
| `wow_characters.guild_rank_name` (VARCHAR) | (derived via guild_rank_id join) | Column removed |
| `wow_characters.role_category` (VARCHAR) | (derived via active_spec_id → specializations.default_role_id) | Column removed |
| `wow_characters.is_main` (BOOLEAN) | (derived: char = players.main_character_id) | Column removed |
| `wow_characters.realm_name` (VARCHAR) | (still exists, but canonical is `realm_slug`) | No change |
| `discord_members.person_id` | `players.discord_user_id` | Link direction reversed |
| `common.guild_members` | (eliminated) | Replaced by `guild_identity.players` |
| `common.characters` | (eliminated) | Replaced by `guild_identity.wow_characters` |

### Reference Tables (new in 2.7, used for FK resolution)

| Table | Contents |
|-------|----------|
| `guild_identity.roles` | Tank, Healer, Melee DPS, Ranged DPS |
| `guild_identity.classes` | 13 WoW classes with color_hex |
| `guild_identity.specializations` | ~39 specs, each with class_id + default_role_id |
| `common.guild_ranks` | GL/Officer/Veteran/Member/Initiate with level + scheduling_weight |

---

## Task 1: Update `discord_sync.py`

**File:** `src/sv_common/guild_sync/discord_sync.py`

This module syncs Discord server members into the database and handles real-time
join/leave/role-change events.

### All SQL table references:
- `guild_identity.discord_members` → **`guild_identity.discord_users`**

### All column references:
- Remove any reads/writes to `person_id` — the discord→player link now lives on
  `players.discord_user_id`, not on the discord_users table. discord_sync should
  NOT be managing that link; the identity_engine handles it.

### Functions to update:

**`sync_discord_members(pool, guild)`:**
- INSERT/UPSERT against `guild_identity.discord_users`
- Columns to write: `discord_id`, `username`, `display_name`, `highest_guild_role`,
  `all_guild_roles`, `joined_server_at`, `last_sync`, `is_present`
- ON CONFLICT (discord_id) DO UPDATE
- Mark members not in the current guild member list:
  `UPDATE discord_users SET is_present = FALSE, removed_at = NOW() WHERE discord_id NOT IN (...)`

**`on_member_join(pool, member)`:**
- INSERT into `guild_identity.discord_users` (not discord_members)
- No `person_id` to set

**`on_member_remove(pool, member)`:**
- `UPDATE guild_identity.discord_users SET is_present = FALSE, removed_at = NOW() WHERE discord_id = $1`

**`on_member_update(pool, before, after)`:**
- `UPDATE guild_identity.discord_users SET ... WHERE discord_id = $1`

### Import updates:
- If importing SQLAlchemy models, use `DiscordUser` (not `DiscordMember`)

### Helper functions:
- `get_highest_guild_role()` and `get_all_guild_roles()` — no schema changes, these
  parse Discord role objects. Leave as-is.

---

## Task 2: Update `db_sync.py`

**File:** `src/sv_common/guild_sync/db_sync.py`

This module processes Blizzard API roster data into `wow_characters` and processes
addon export data.

### FK resolution strategy:

At the start of each sync run, build lookup caches from the reference tables:

```python
# Build once per sync run
classes_by_name = {}       # {"Druid": 1, "Warrior": 2, ...}
specs_by_name = {}         # {("Balance", 1): 5, ...}  keyed by (spec_name, class_id)
ranks_by_blizzard_index = {}  # {0: rank_id_for_GL, 1: rank_id_for_Officer, ...}
```

For `ranks_by_blizzard_index`: Blizzard returns rank as 0=GL, 1=Officer, 2=Veteran,
3=Member, 4=Initiate. Map these to `guild_ranks.id` by name:

```python
rank_name_by_blizzard_index = {
    0: "Guild Leader",
    1: "Officer",
    2: "Veteran",
    3: "Member",
    4: "Initiate",
}
# Then: ranks_by_blizzard_index[idx] = guild_ranks row WHERE name = rank_name_by_blizzard_index[idx]
```

### `sync_blizzard_roster(pool, characters)`:

For each character from the API:
- Look up `class_id` from `classes_by_name[character_class_name]`
- Look up `active_spec_id` from `specs_by_name[(spec_name, class_id)]`
  - Spec may be NULL if the API doesn't return it; that's fine
- Look up `guild_rank_id` from `ranks_by_blizzard_index[rank_index]`
- INSERT/UPSERT into `wow_characters` with these FK columns
- Do NOT set `person_id` — that column no longer exists. Character→player linking
  is the identity_engine's job.

**Columns to write on wow_characters:**
`character_name`, `realm_slug`, `blizzard_id`, `class_id`, `active_spec_id`, `level`,
`item_level`, `guild_rank_id`, `achievement_points`, `last_login_at`, `profile_json`,
`last_api_sync`, `removed_at`

**Columns that no longer exist (remove writes):**
`character_class`, `character_spec`, `guild_rank`, `guild_rank_name`, `role_category`,
`is_main`, `person_id`, `realm_name` (check — realm_name may still exist as denormalized
field; if it does, keep writing it alongside realm_slug)

### `sync_addon_data(pool, addon_data)`:

Addon exports contain: character_name, realm, guild_note, officer_note, rank_index,
class_name, level.

- Resolve `class_id` and `guild_rank_id` same as above
- Write to: `addon_note`, `addon_officer_note`, `addon_last_sync`
- Resolve and write `class_id`, `guild_rank_id` if provided
- Do NOT write `person_id`, `character_class`, `guild_rank_name`, etc.

---

## Task 3: Update `identity_engine.py`

**File:** `src/sv_common/guild_sync/identity_engine.py`

The matching engine links WoW characters to Discord accounts via player entities.
This is the biggest conceptual change.

### Core concept shift:

**Old model:** Creates `persons` rows. Creates `identity_links` entries to connect
characters and discord members to persons. A person could have multiple identity_links
of different entity_types.

**New model:** Creates `players` rows. Character links go in `player_characters` bridge
table. Discord link is a direct FK on `players.discord_user_id`. Much simpler.

### `run_matching(pool)` — full rewrite of the linking logic:

**Step 1: Gather unlinked entities**

```sql
-- Unlinked WoW characters (in guild, not yet assigned to any player)
SELECT wc.id, wc.character_name, wc.realm_slug, wc.addon_note, wc.addon_officer_note
FROM guild_identity.wow_characters wc
WHERE wc.removed_at IS NULL
  AND wc.id NOT IN (SELECT character_id FROM guild_identity.player_characters)

-- Unlinked Discord users (have guild role, not yet linked to a player)
SELECT du.id, du.discord_id, du.username, du.display_name
FROM guild_identity.discord_users du
WHERE du.is_present = TRUE
  AND du.highest_guild_role IS NOT NULL
  AND du.id NOT IN (
      SELECT discord_user_id FROM guild_identity.players
      WHERE discord_user_id IS NOT NULL
  )
```

**Step 2: Apply matching strategies (unchanged logic, new targets)**

The matching strategies themselves are fine — name normalization, note parsing,
fuzzy matching. What changes is what happens when a match is found:

**Step 3: When character ↔ discord match is found:**

1. Check if a `player` already exists with that `discord_user_id`:
   - YES → Add character to that player via `INSERT INTO player_characters`
   - NO → Continue to step 2
2. Check if a `player` already owns a character that the note says is related
   (e.g., "alt of CharName" — find the player who owns CharName):
   - YES → Add this character to the same player, AND set `discord_user_id` if unset
   - NO → Continue to step 3
3. Create a new player:
   ```sql
   INSERT INTO guild_identity.players (display_name, discord_user_id)
   VALUES ($1, $2) RETURNING id
   ```
   Then: `INSERT INTO guild_identity.player_characters (player_id, character_id)`

**Step 4: Handle orphan characters (no Discord match found)**

Some characters may match each other as alts (via notes) but have no Discord link.
Group them under a single player with `discord_user_id = NULL`. The integrity checker
will flag these for officer review.

### Existing helper functions — no changes needed:
- `normalize_name()` — pure string manipulation
- `extract_discord_hints_from_note()` — pure regex parsing
- `fuzzy_match_score()` — pure comparison

### Removed concepts:
- `identity_links` table — all references removed
- `match_confidence` and `match_source` on links — log to sync_log instead,
  or add optional columns to player_characters later
- `person_id` on wow_characters and discord_members — gone

---

## Task 4: Update `integrity_checker.py`

**File:** `src/sv_common/guild_sync/integrity_checker.py`

### Check: `orphan_wow` — WoW characters with no player

```sql
-- Old: wow_characters WHERE person_id IS NULL
-- New:
SELECT wc.id, wc.character_name, wc.realm_slug
FROM guild_identity.wow_characters wc
WHERE wc.removed_at IS NULL
  AND wc.id NOT IN (SELECT character_id FROM guild_identity.player_characters)
```

### Check: `orphan_discord` — Discord members with guild roles but no player

```sql
-- Old: discord_members WHERE person_id IS NULL AND highest_guild_role IS NOT NULL
-- New:
SELECT du.id, du.discord_id, du.username, du.display_name, du.highest_guild_role
FROM guild_identity.discord_users du
WHERE du.is_present = TRUE
  AND du.highest_guild_role IS NOT NULL
  AND du.id NOT IN (
      SELECT discord_user_id FROM guild_identity.players
      WHERE discord_user_id IS NOT NULL
  )
```

### Check: `rank_mismatch` — in-game rank vs Discord role

```sql
-- Old: compared wow_characters.guild_rank_name to discord highest role
-- New: must go through player → player_characters → wow_characters → guild_ranks
SELECT p.id as player_id, p.display_name,
       du.highest_guild_role as discord_role,
       gr.name as ingame_rank
FROM guild_identity.players p
JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
JOIN guild_identity.player_characters pc ON pc.player_id = p.id
JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
WHERE wc.removed_at IS NULL
  AND du.is_present = TRUE
ORDER BY p.id, gr.level DESC
-- Then: for each player, get their HIGHEST rank (MAX guild_ranks.level)
-- Compare guild_ranks.name to discord_users.highest_guild_role
-- Flag if mismatch (accounting for "Guild Leader" vs "GM" mapping)
```

### Check: `stale_character` — not seen in 30+ days

No schema change needed. Still uses `wow_characters.last_api_sync`.

### Check: `duplicate_discord` — multiple players with same discord_user_id

New check (wasn't possible with old schema, now that discord_user_id is a direct FK):
```sql
SELECT discord_user_id, COUNT(*) FROM guild_identity.players
WHERE discord_user_id IS NOT NULL
GROUP BY discord_user_id HAVING COUNT(*) > 1
```

### Column reference on `audit_issues`:
- `discord_member_id` FK column — the FK target was already updated to `discord_users`
  in migration 0007. The column NAME is still `discord_member_id` in the schema.
  Keep using that column name; just make sure you're inserting `discord_users.id` values.
- `wow_character_id` — unchanged

---

## Task 5: Update `reporter.py`

**File:** `src/sv_common/guild_sync/reporter.py`

### SQL query updates:
- All JOINs on `discord_members` → `discord_users`
- All JOINs on `persons` → `players`
- Display name: `players.display_name` (not `persons.display_name`)

### `send_new_issues_report(pool, channel, force_full=False)`:
- Query `audit_issues` with JOINs to `discord_users` and `wow_characters` for display names
- Embed formatting logic is unchanged

### `send_sync_summary(channel, source, stats, duration)`:
- No schema references — just formats stats into a Discord embed. No changes needed.

---

## Task 6: Update `scheduler.py`

**File:** `src/sv_common/guild_sync/scheduler.py`

### Import updates:
- Verify all imports resolve: `sync_blizzard_roster`, `sync_addon_data` from `db_sync`;
  `sync_discord_members` from `discord_sync`; `run_matching` from `identity_engine`;
  `run_integrity_check` from `integrity_checker`; `send_new_issues_report`,
  `send_sync_summary` from `reporter`

### `run_onboarding_check()`:
- This method calls into Phase 2.6 onboarding code. For now, either:
  - **Stub it out:** `async def run_onboarding_check(self): pass`
  - **Or remove it entirely** and the scheduler job that calls it
- Phase 2.6 will wire it back up once onboarding is updated

### Pipeline flow (unchanged):
1. Blizzard sync → db_sync → match → integrity check → report
2. Discord sync runs on its own interval
3. Addon uploads trigger: db_sync → match → integrity check → report

---

## Task 7: Update `addon_processor.py`

**File:** `src/sv_common/guild_sync/addon_processor.py`

This module parses PATTSync Lua SavedVariables exports and prepares them for db_sync.

- If it passes data to `sync_addon_data()`, the FK resolution happens there. Minimal changes.
- If it writes to the database directly, apply the same FK resolution pattern from Task 2.
- Remove any `person_id` references.

---

## Task 8: Update or Create API Routes

The original Phase 2.5B spec defined routes at `/api/guild-sync/` and `/api/identity/`.
These may or may not be mounted on the main app.

### If routes exist, update them:

| Route | Old Query | New Query |
|-------|-----------|-----------|
| `GET /api/identity/players` | persons with identity_links | players with player_characters + discord_users JOINs |
| `GET /api/identity/orphans/wow` | wow_characters WHERE person_id IS NULL | wow_characters NOT IN player_characters |
| `GET /api/identity/orphans/discord` | discord_members WHERE person_id IS NULL | discord_users NOT IN players.discord_user_id |
| `GET /api/identity/mismatches` | compared text fields | JOIN through player_characters → guild_ranks |
| `POST /api/identity/link` | created identity_link | INSERT player_characters + set players.discord_user_id |
| `DELETE /api/identity/link/{id}` | deleted identity_link | DELETE from player_characters |
| `POST /api/guild-sync/blizzard/trigger` | no schema deps | no changes |
| `POST /api/guild-sync/addon/upload` | calls sync_addon_data | verify payload shape matches |

### If routes don't exist yet:
- Create `src/sv_common/guild_sync/api/routes.py` with the above endpoints
- Mount on the main app in `src/patt/app.py` during lifespan or router include

---

## Task 9: Update `retroactive_provision.py`

**File:** `scripts/retroactive_provision.py`

This script calls `AutoProvisioner.provision_person()` which is Phase 2.6 code.
Since 2.6 isn't updated yet:
- Either skip this file (it's a one-time script that was already run)
- Or stub/disable it so it doesn't import broken modules
- Phase 2.6 will fix the provisioner; this script can be updated then

---

## Task 10: Tests

### Update existing test files:
All tests under `tests/` that reference guild_sync modules need model/table name updates.

### Model/fixture renames:
| Old | New |
|-----|-----|
| `DiscordMember` model/fixture | `DiscordUser` |
| `Person` model/fixture | `Player` |
| `IdentityLink` model/fixture | `PlayerCharacter` |
| `discord_members` table in SQL | `discord_users` |
| `persons` table in SQL | `players` |
| `identity_links` table in SQL | `player_characters` |

### Test cases to verify:

**discord_sync tests:**
- `test_sync_creates_discord_user` — new member appears in discord_users
- `test_sync_updates_existing_discord_user` — role change reflected
- `test_sync_marks_departed_not_present` — left server → is_present = FALSE
- `test_on_member_join_creates_discord_user` — real-time event
- `test_on_member_remove_marks_not_present` — real-time event

**db_sync tests:**
- `test_sync_resolves_class_id` — "Druid" → classes.id
- `test_sync_resolves_spec_id` — "Balance" + class_id → specializations.id
- `test_sync_resolves_guild_rank_id` — rank index 0 → guild_ranks.id for "Guild Leader"
- `test_sync_handles_unknown_class` — gracefully handles classes not in reference table
- `test_sync_handles_null_spec` — spec can be NULL
- `test_addon_sync_writes_notes` — addon_note and addon_officer_note populated

**identity_engine tests:**
- `test_exact_name_match_creates_player` — character name matches discord username
- `test_exact_match_links_to_existing_player` — discord user already has a player
- `test_guild_note_match` — "Discord: username" in note → links character
- `test_officer_note_match` — same for officer note
- `test_alt_grouping_via_notes` — "alt of CharName" groups under same player
- `test_fuzzy_match_below_threshold_skipped` — low score → no link
- `test_no_duplicate_player_creation` — running matching twice doesn't create duplicates

**integrity_checker tests:**
- `test_orphan_wow_detected` — character not in player_characters → flagged
- `test_orphan_discord_detected` — discord user not in players.discord_user_id → flagged
- `test_rank_mismatch_detected` — ingame rank ≠ discord role → flagged
- `test_resolved_issues_not_reflagged` — fixed issues don't reappear
- `test_dedup_same_issue_hash` — same problem doesn't create multiple audit_issues

**scheduler tests:**
- `test_pipeline_sync_match_check_report` — full pipeline runs in order
- `test_only_new_issues_trigger_report` — no new issues → no report sent

### Smoke test update:
Update `tests/unit/test_smoke.py` to import any new models or verify guild_sync modules
import without error.

---

## Acceptance Criteria

- [ ] `discord_sync.py` syncs Discord members into `discord_users` without errors
- [ ] `db_sync.py` syncs Blizzard roster into `wow_characters` with correct FK resolution
- [ ] `identity_engine.py` creates players + player_characters entries from matches
- [ ] `integrity_checker.py` detects orphans/mismatches using new schema
- [ ] `reporter.py` formats audit reports with correct player/discord display names
- [ ] `scheduler.py` orchestrates full pipeline: sync → match → check → report
- [ ] `addon_processor.py` processes PATTSync exports without errors
- [ ] No references to `persons`, `discord_members` (table), `identity_links`,
      `person_id`, `character_class` (text), `guild_rank_name`, `role_category`,
      or `is_main` remain in any guild_sync module
- [ ] No references to `common.guild_members` or `common.characters` remain
- [ ] All guild_sync tests pass
- [ ] All existing tests still pass (no regressions)

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-2.5: guild sync updated for player model"`
- [ ] Update CLAUDE.md: remove "Dormant Code" section, mark guild_sync as operational
- [ ] Update MEMORY.md with completion note
