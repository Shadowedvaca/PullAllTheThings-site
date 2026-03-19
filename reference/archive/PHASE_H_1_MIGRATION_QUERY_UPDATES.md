# Phase H.1 — Migration + Query Updates

## Context

**Project:** Pull All The Things Guild Platform (FastAPI / PostgreSQL / Jinja2)
**Branch:** `feature/phase-h-character-sync`
**Pre-condition:** Phase F.3 complete, migration head 0050, 922 tests pass

Read `reference/phase-H.md` for the full phase overview and key decisions before starting.

This sub-phase is purely backend and database. No UI changes. It establishes the
`in_guild` column and ensures every query that should only see guild characters does so.

---

## Goals

1. Add `in_guild BOOLEAN NOT NULL DEFAULT TRUE` to `guild_identity.wow_characters`
2. Update `db_sync.py` (guild roster sync) to write `in_guild = TRUE` on all upserts
3. Update every display/work-list query listed below to add `AND in_guild = TRUE`
4. All existing tests continue to pass (no behaviour change for current data — all existing rows are TRUE)

---

## Migration — 0051

File: `alembic/versions/0051_add_in_guild_to_wow_characters.py`

```python
"""add in_guild to wow_characters

Revision ID: 0051
Revises: 0050
Create Date: <today>
"""
from alembic import op
import sqlalchemy as sa

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wow_characters",
        sa.Column("in_guild", sa.Boolean(), nullable=False, server_default="true"),
        schema="guild_identity",
    )
    # Index for the common filter pattern
    op.create_index(
        "ix_wow_characters_in_guild",
        "wow_characters",
        ["in_guild"],
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_index("ix_wow_characters_in_guild", table_name="wow_characters", schema="guild_identity")
    op.drop_column("wow_characters", "in_guild", schema="guild_identity")
```

---

## ORM Model Update

File: `src/sv_common/db/models.py`

Add field to `WowCharacter` class (after `removed_at`):

```python
in_guild: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
```

---

## db_sync.py — Guild Roster Sync

File: `src/sv_common/guild_sync/db_sync.py`

The guild roster sync must set `in_guild = TRUE` when upserting characters.

**UPDATE existing character (around line 80):**
Add `in_guild = TRUE` to the UPDATE SET clause. It may already be TRUE, but this ensures
any char that re-appears in the guild roster after being a BNet-only char gets flipped correctly.

```sql
UPDATE guild_identity.wow_characters SET
    class_id = $2,
    active_spec_id = $3,
    level = $4,
    item_level = $5,
    ...existing fields...,
    in_guild = TRUE,        -- ADD THIS
    removed_at = NULL
WHERE id = $N
```

**INSERT new character (around line 110):**
Add `in_guild` column with value `TRUE` to the INSERT.

```sql
INSERT INTO guild_identity.wow_characters
    (character_name, realm_slug, realm_name, class_id, ..., in_guild)   -- ADD in_guild
VALUES
    ($1, $2, $3, $4, ..., TRUE)                                          -- ADD TRUE
```

---

## Query Updates

For each file below, add `AND in_guild = TRUE` (raw SQL) or `.where(WowCharacter.in_guild == True)` (ORM) as noted. All existing `removed_at IS NULL` filters are kept; `in_guild` is added alongside them.

---

### 1. `src/guild_portal/api/guild_routes.py`

**Roster API (ORM, ~line 55):**
The roster loads players then selectinloads characters. Players' `main_character_id` / `offspec_character_id` FKs already point to specific chars — no filter needed here as the player record controls which chars are displayed. Verify this is the case; if the query pulls all linked chars, add the filter to the inner join/subquery.

**Raider.IO query (~line 212):**
Add `AND wc.in_guild = TRUE` to the WHERE clause.

```sql
-- Before:
WHERE wc.removed_at IS NULL
-- After:
WHERE wc.removed_at IS NULL AND wc.in_guild = TRUE
```

**Parse leaderboard (~line 280):**
Add `AND wc.in_guild = TRUE` to the WHERE clause.

---

### 2. `src/guild_portal/pages/public_pages.py`

**Role distribution query (~line 120):**
```sql
-- Before:
WHERE p.is_active = TRUE
-- After:
WHERE p.is_active = TRUE AND wc.in_guild = TRUE
```
(This query joins `wow_characters wc ON wc.id = p.main_character_id` — add the filter on wc.)

**Officers page (~line 81):** selectinload via `Player.main_character` — the player FK controls
which character is loaded. No filter change needed here.

**Home realm detection (~line 291):** Single character lookup by ID for the current player's
main. No filter change needed.

---

### 3. `src/guild_portal/api/member_routes.py`

**`/me/characters` ORM query (~line 131):**
Characters are fetched via `player_characters` bridge. After Phase H, a player may have
both in-guild (TRUE) and out-of-guild (FALSE) chars linked. This endpoint will be expanded
in H.3 to return both lists. For now, add `in_guild = True` filter so the main character
selector list only shows guild chars:

```python
# Add to the selectinload / join chain:
.where(WowCharacter.in_guild == True)
```

**`/me/character/{id}/market` (~line 383) and `/me/character/{id}/parses` (~line 442):**
These look up a specific character by ID for the current player. Add a guard:
```python
.where(WowCharacter.id == character_id, WowCharacter.in_guild == True)
```
This prevents using market/parse APIs on out-of-guild chars (which have no guild data).

---

### 4. `src/sv_common/guild_sync/crafting_service.py`

**Crafter count (~line 64):**
```sql
-- Add to JOIN condition:
AND wc.in_guild = TRUE
```

**Crafter details (~line 101):**
```sql
-- Add to WHERE or JOIN:
AND wc.in_guild = TRUE
```

**Recipe search (~line 179):**
```sql
-- Add to JOIN condition:
AND wc.in_guild = TRUE
```

---

### 5. `src/guild_portal/pages/profile_pages.py`

**Unclaimed character inventory (~line 96):**
This shows guild chars that haven't been claimed by any player yet. Out-of-guild BNet chars
are already auto-linked, so they shouldn't appear here.

```python
# Before:
select(WowCharacter).where(WowCharacter.removed_at.is_(None), ...)
# After:
select(WowCharacter).where(
    WowCharacter.removed_at.is_(None),
    WowCharacter.in_guild == True,
    ...
)
```

---

### 6. `src/guild_portal/pages/admin_pages.py`

**Player Manager data table (~line 688):**
The raw SQL query fetches `wow_characters wc`. Add:
```sql
WHERE wc.in_guild = TRUE AND ...existing conditions...
```

**Progression sync status counts (~line 2813):**
```sql
-- Before:
WHERE removed_at IS NULL
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE
```
Apply to all three COUNT queries in that block.

**Data quality linked char count (~line 2038):**
```sql
-- Before:
WHERE wc.removed_at IS NULL
-- After:
WHERE wc.removed_at IS NULL AND wc.in_guild = TRUE
```

**Data quality total count (~line 2034):**
```sql
-- Before:
WHERE removed_at IS NULL
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE
```

**Unlinked chars report (~line 2078):**
Leave as-is. After H, BNet-only chars WILL be in `player_characters` (linked), so they
won't appear here anyway. The query naturally self-corrects.

---

### 7. `src/sv_common/guild_sync/progression_sync.py`

**Progression-eligible characters work list (~line 482):**
```sql
-- Before:
WHERE removed_at IS NULL
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE
```

**Profession-eligible characters work list (~line 513):**
```sql
-- Before:
WHERE removed_at IS NULL
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE
```

**Stamp queries (~lines 438, 456):** These UPDATE by id list — no filter needed (ids come
from the work lists which are already filtered).

**Snapshot trigger (~line 367):**
```sql
-- Before:
WHERE removed_at IS NULL
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE
```

---

### 8. `src/sv_common/guild_sync/crafting_sync.py`

**Profession work list (~line 305):**
```sql
-- Before:
WHERE removed_at IS NULL ORDER BY character_name
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE ORDER BY character_name
```

---

### 9. `src/sv_common/guild_sync/scheduler.py`

**WCL character list (~line 775):**
```sql
-- Before:
WHERE removed_at IS NULL ORDER BY character_name
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE ORDER BY character_name
```

---

### 10. `src/sv_common/guild_sync/attendance_processor.py`

**WCL attendee name lookup (~line 94):**
```sql
-- Before:
WHERE LOWER(wc.character_name) = ANY($1::text[]) AND wc.removed_at IS NULL
-- After:
WHERE LOWER(wc.character_name) = ANY($1::text[]) AND wc.removed_at IS NULL AND wc.in_guild = TRUE
```

---

### 11. `src/sv_common/guild_sync/discord_sync.py`

**Highest rank lookup (~line 187):**
```sql
-- Before:
WHERE pc.player_id = p.id ORDER BY gr.level DESC LIMIT 1
-- After:
WHERE pc.player_id = p.id AND wc.in_guild = TRUE ORDER BY gr.level DESC LIMIT 1
```

---

### 12. `src/sv_common/guild_sync/mitigations.py`

**Unlinked high-rank characters (~line 48):**
```sql
-- Before:
WHERE wc.removed_at IS NULL AND gr.level >= $1 AND wc.id NOT IN (...)
-- After:
WHERE wc.removed_at IS NULL AND wc.in_guild = TRUE AND gr.level >= $1 AND wc.id NOT IN (...)
```

**Unlinked character pool (~line 280):**
```sql
-- Before:
WHERE removed_at IS NULL AND id NOT IN (...)
-- After:
WHERE removed_at IS NULL AND in_guild = TRUE AND id NOT IN (...)
```

---

## Files NOT Changed in H.1

These intentionally see all characters (including `in_guild = FALSE`):
- `integrity_checker.py` — audit tools need full picture
- `onboarding/conversation.py` and `deadline_checker.py` — char lookup by name for new member linking
- `bnet_character_sync.py` — the sync itself, changed in H.2
- `guild_sync/api/routes.py` — companion app admin endpoints
- `raid_booking_service.py` — uses player.main_character_id FK (always a guild char)

---

## Tests

After all changes:

```bash
.venv/Scripts/pytest tests/unit/ -v
```

Expected: all previously passing tests still pass. No new tests needed for H.1
(the changes are additive column + filter changes; integration behaviour is unchanged
since all existing data has `in_guild = TRUE`).

If any test fixtures create `WowCharacter` objects without `in_guild`, SQLAlchemy will
use the server default (`TRUE`) — no fixture changes needed.
