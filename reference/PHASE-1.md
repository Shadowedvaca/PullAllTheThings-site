# Phase 1: Common Services — Identity & Guild Data Model

> **Prerequisites:** Read CLAUDE.md and TESTING.md first. Phase 0 must be complete.
> **Goal:** The sv_common package is functional — guild members, ranks, and characters
> can be created, read, updated, and deleted through well-tested service functions.

---

## What This Phase Produces

1. Fully implemented `sv_common.identity` package (members, ranks, characters)
2. CRUD service functions with proper error handling
3. API endpoints for guild management (admin-only for now)
4. Comprehensive unit and integration tests for all identity operations
5. Rank permission checking utility used everywhere going forward

---

## Context From Phase 0

After Phase 0, the following exists:
- PostgreSQL with `common` and `patt` schemas, all tables created via Alembic
- SQLAlchemy ORM models for all tables
- FastAPI app with health check
- pytest framework with conftest, fixtures, database test setup
- Seed data for guild ranks (Initiate through Guild Leader)

---

## Tasks

### 1.1 — Rank Management (`sv_common/identity/ranks.py`)

Implement:
```python
async def get_all_ranks(db: AsyncSession) -> list[GuildRank]
async def get_rank_by_level(db: AsyncSession, level: int) -> GuildRank | None
async def get_rank_by_name(db: AsyncSession, name: str) -> GuildRank | None
async def create_rank(db: AsyncSession, name: str, level: int, description: str = None, discord_role_id: str = None) -> GuildRank
async def update_rank(db: AsyncSession, rank_id: int, **kwargs) -> GuildRank
async def delete_rank(db: AsyncSession, rank_id: int) -> bool
async def member_meets_rank_requirement(db: AsyncSession, member_id: int, required_level: int) -> bool
```

The `member_meets_rank_requirement` function is critical — it's used by voting, visibility,
and every permission check in the platform. Test it thoroughly.

### 1.2 — Guild Member Management (`sv_common/identity/members.py`)

Implement:
```python
async def get_all_members(db: AsyncSession) -> list[GuildMember]
async def get_member_by_id(db: AsyncSession, member_id: int) -> GuildMember | None
async def get_member_by_discord_id(db: AsyncSession, discord_id: str) -> GuildMember | None
async def get_member_by_discord_username(db: AsyncSession, username: str) -> GuildMember | None
async def get_members_by_min_rank(db: AsyncSession, min_level: int) -> list[GuildMember]
async def create_member(db: AsyncSession, discord_username: str, discord_id: str = None, display_name: str = None, rank_id: int = None) -> GuildMember
async def update_member(db: AsyncSession, member_id: int, **kwargs) -> GuildMember
async def link_user_to_member(db: AsyncSession, member_id: int, user_id: int) -> GuildMember
async def get_eligible_voters(db: AsyncSession, min_rank_level: int) -> list[GuildMember]
```

`get_eligible_voters` returns all members at or above the specified rank level
who have a linked user account (i.e., they've registered). This drives the
"all votes are in" early-close check.

### 1.3 — Character Management (`sv_common/identity/characters.py`)

Implement:
```python
async def get_characters_for_member(db: AsyncSession, member_id: int) -> list[Character]
async def get_main_character(db: AsyncSession, member_id: int) -> Character | None
async def create_character(db: AsyncSession, member_id: int, name: str, realm: str, wow_class: str, spec: str = None, role: str = None, main_alt: str = "main") -> Character
async def update_character(db: AsyncSession, char_id: int, **kwargs) -> Character
async def delete_character(db: AsyncSession, char_id: int) -> bool
```

Character roles must be one of: `tank`, `healer`, `melee_dps`, `ranged_dps`.
The `main_alt` field must be `main` or `alt`. Validate these in the service layer.

Build the WoW Armory URL automatically:
```python
def build_armory_url(name: str, realm: str) -> str:
    """Build Blizzard armory URL. Handle special characters in names and realms."""
    # realm: "Sen'jin" → "senjin" (lowercase, strip apostrophes)
    # name: handle accented characters
    clean_realm = realm.lower().replace("'", "").replace(" ", "-")
    return f"https://worldofwarcraft.blizzard.com/en-us/character/us/{clean_realm}/{name.lower()}"
```

### 1.4 — Admin API Endpoints (`patt/api/admin_routes.py`)

These are admin-only routes (no auth middleware yet — that's Phase 2).
For now, they're unprotected. Auth will be layered on in Phase 2.

```
GET    /api/v1/admin/ranks              — List all ranks
POST   /api/v1/admin/ranks              — Create a rank
PATCH  /api/v1/admin/ranks/{id}         — Update a rank (name, level, discord_role_id)
DELETE /api/v1/admin/ranks/{id}         — Delete a rank

GET    /api/v1/admin/members            — List all members (with rank info)
POST   /api/v1/admin/members            — Create a member
PATCH  /api/v1/admin/members/{id}       — Update a member
GET    /api/v1/admin/members/{id}       — Get member detail (with characters)

GET    /api/v1/admin/members/{id}/characters  — List characters for a member
POST   /api/v1/admin/members/{id}/characters  — Add a character
PATCH  /api/v1/admin/characters/{id}          — Update a character
DELETE /api/v1/admin/characters/{id}          — Delete a character
```

All endpoints follow the response convention:
```json
{"ok": true, "data": { ... }}
{"ok": false, "error": "Human-readable error message"}
```

### 1.5 — Guild Management API Endpoints (`patt/api/guild_routes.py`)

Public/read-only routes for guild info:

```
GET  /api/v1/guild/ranks        — List ranks (public info)
GET  /api/v1/guild/roster       — Roster view (display names, characters, ranks)
```

The roster endpoint should return data shaped for a public roster page:
```json
{
    "ok": true,
    "data": {
        "members": [
            {
                "display_name": "Trog",
                "rank": "Guild Leader",
                "main_character": {
                    "name": "Trogmoon",
                    "realm": "Sen'jin",
                    "class": "Druid",
                    "spec": "Balance",
                    "role": "ranged_dps",
                    "armory_url": "https://..."
                }
            }
        ]
    }
}
```

### 1.6 — Tests

**Unit tests (`tests/unit/`):**

`test_ranks.py`:
- test_get_all_ranks_returns_seeded_data
- test_get_rank_by_level_found
- test_get_rank_by_level_not_found
- test_create_rank_with_all_fields
- test_create_rank_duplicate_level_rejected
- test_member_meets_rank_veteran_at_veteran_level (returns True)
- test_member_meets_rank_initiate_at_veteran_level (returns False)
- test_member_meets_rank_officer_at_veteran_level (returns True)

`test_members.py`:
- test_create_member_default_rank
- test_get_member_by_discord_username
- test_get_member_by_discord_id
- test_get_eligible_voters_excludes_unregistered
- test_get_eligible_voters_excludes_low_rank
- test_get_members_by_min_rank

`test_characters.py`:
- test_create_character_builds_armory_url
- test_create_character_senjin_apostrophe_handling
- test_get_main_character
- test_invalid_role_rejected
- test_invalid_main_alt_rejected
- test_duplicate_name_realm_rejected

**Integration tests (`tests/integration/`):**

`test_admin_api.py`:
- test_list_ranks
- test_create_member_via_api
- test_update_member_rank_via_api
- test_add_character_to_member
- test_full_member_detail_includes_characters
- test_roster_endpoint_returns_formatted_data

---

## Acceptance Criteria

- [ ] All rank CRUD operations work and are tested
- [ ] All member CRUD operations work and are tested
- [ ] All character CRUD operations work and are tested
- [ ] Armory URL generation handles special characters (Sen'jin, accented names)
- [ ] `member_meets_rank_requirement` correctly compares rank levels
- [ ] `get_eligible_voters` correctly filters by rank AND registration status
- [ ] Admin API endpoints return proper response format
- [ ] Public roster endpoint returns well-shaped data
- [ ] All unit tests pass
- [ ] All integration tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-1: common services identity and guild data model"`
- [ ] Update CLAUDE.md "Current Build Status" section:
  ```
  ### Completed Phases
  - Phase 0: Server infrastructure, project scaffolding, testing framework
  - Phase 1: Common services — identity & guild data model

  ### Current Phase
  - Phase 2: Authentication & Discord Bot

  ### What Exists
  - sv_common.identity package: ranks, members, characters CRUD
  - Admin API: /api/v1/admin/ranks, members, characters
  - Public API: /api/v1/guild/ranks, roster
  - Full test coverage for identity operations
  ```
