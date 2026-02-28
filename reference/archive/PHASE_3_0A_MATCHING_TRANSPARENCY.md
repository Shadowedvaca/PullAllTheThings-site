# Phase 3.0A — Matching Transparency & Coverage Metrics

> **Goal:** Make the current matching engine tell you what it did and why.
> Every link gets attribution. The Data Quality page becomes a coverage
> dashboard — not a list of "issues" but a measurement of how well the
> system understands your guild.

---

## Philosophy

The matching engine already works. It groups characters by note key, finds
Discord users, creates players. But right now it's a black box — you can't
see *why* a link was made, *how confident* it is, or *what's left unmatched*.

This phase adds transparency without changing matching logic. The engine
does the same work; it just writes down its reasoning.

---

## What This Phase Produces

1. `link_source` and `confidence` columns on `player_characters` — every link
   carries attribution for how and why it was created
2. A coverage metrics API endpoint that computes matching health stats
3. A redesigned Data Quality admin page that shows:
   - Coverage summary (characters matched %, Discord users matched %, players with Discord %)
   - Breakdown by link source (note_key, exact_name, fuzzy, manual, migrated)
   - Breakdown by confidence tier (high, medium, low, confirmed)
   - Unmatched lists: characters without players, Discord users without players
4. Existing matching engine updated to stamp `link_source` and `confidence`
   on every `player_characters` row it creates
5. Player Manager updated to stamp `link_source = 'manual'` and
   `confidence = 'confirmed'` when links are made via drag-and-drop

## What This Phase Does NOT Do

- No new matching rules (that's Phase 3.0B)
- No drift detection (that's Phase 3.0C)
- No changes to when or how matching runs
- No schema changes to audit_issues (leave as-is for now)

---

## Prerequisites

- All previous phases complete (0–7, 2.5A–D, 2.6, 2.7, 2.9)
- Database backup before migration
- Read CLAUDE.md and TESTING.md

---

## Task 1: Schema Migration — Add Link Attribution to player_characters

Create Alembic migration to add columns to `guild_identity.player_characters`:

```sql
ALTER TABLE guild_identity.player_characters
    ADD COLUMN link_source VARCHAR(30) NOT NULL DEFAULT 'unknown',
    ADD COLUMN confidence VARCHAR(15) NOT NULL DEFAULT 'unknown';
```

**Valid values for `link_source`:**

| Value | Meaning |
|-------|---------|
| `note_key` | Characters grouped by shared guild note key, Discord found via note key matching |
| `note_key_stub` | Characters grouped by note key, no Discord user found (stub player) |
| `exact_name` | Character name exactly matched a Discord username/display_name |
| `fuzzy_name` | Character name fuzzy-matched to a Discord name (no note available) |
| `manual` | Linked by an admin via Player Manager drag-and-drop |
| `migrated` | Came from the Phase 2.7 data migration script |
| `onboarding` | Created by the onboarding flow (Phase 2.6, when activated) |
| `auto_relink` | Re-linked by note_mismatch auto-mitigation |
| `unknown` | Legacy rows that predate this column (default for existing data) |

**Valid values for `confidence`:**

| Value | Meaning |
|-------|---------|
| `high` | Exact match on name or note key → Discord username |
| `medium` | Substring/partial match, or note key → Discord display_name |
| `low` | Fuzzy match, or stub player (no Discord link to validate against) |
| `confirmed` | Human verified — manual link via Player Manager, or admin-confirmed |
| `unknown` | Legacy rows (default for existing data) |

After migration, backfill existing rows:
- Rows where the player has `discord_user_id IS NOT NULL` → `link_source = 'unknown', confidence = 'unknown'`
- Rows where the player has `discord_user_id IS NULL` (stub players) → `link_source = 'unknown', confidence = 'low'`

This is intentionally conservative. We don't know why old links exist, so we label them honestly.

---

## Task 2: Update Identity Engine to Stamp Attribution

**File:** `src/sv_common/guild_sync/identity_engine.py`

### Update `_find_discord_for_key()` Return Value

Currently returns `Optional[dict]`. Change to return a tuple of `(Optional[dict], str)` where the second value is the match type:

```python
def _find_discord_for_key(note_key: str, all_discord: list) -> tuple[Optional[dict], str]:
    """
    Returns (discord_user_or_None, match_type).
    match_type is one of: "exact_username", "exact_display", "word_in_display",
                          "substring_username", "substring_display", "none"
    """
```

**Important:** `_find_discord_for_key` is also called from `mitigations.py` and
`integrity_checker.py`. All call sites must be updated to handle the new return type.

### Update `_create_player_group()`

Add `match_type` parameter. Determine `link_source` and `confidence`:

| Scenario | link_source | confidence |
|----------|-------------|------------|
| Discord found via `exact_username` | `note_key` | `high` |
| Discord found via `exact_display` | `note_key` | `high` |
| Discord found via `word_in_display` | `note_key` | `medium` |
| Discord found via `substring_*` | `note_key` | `medium` |
| No Discord found (stub player) | `note_key_stub` | `low` |

Update the INSERT:

```python
await conn.execute(
    """INSERT INTO guild_identity.player_characters
       (player_id, character_id, link_source, confidence)
       VALUES ($1, $2, $3, $4) ON CONFLICT (character_id) DO NOTHING""",
    player_id, char["id"], link_source, confidence,
)
```

### Update No-Note Fallback in `run_matching()`

For characters with no guild note that fall through to character-name matching:

| Scenario | link_source | confidence |
|----------|-------------|------------|
| Character name exact-matches Discord user | `exact_name` | `high` |
| Character name substring/fuzzy-matches | `fuzzy_name` | `medium` |

---

## Task 3: Update Mitigations to Stamp Attribution

**File:** `src/sv_common/guild_sync/mitigations.py`

- `mitigate_note_mismatch()`: re-links → `link_source = 'auto_relink'`, confidence from match type
- `mitigate_orphan_wow()`: links → `link_source` from matching path, confidence from match type
- `mitigate_orphan_discord()`: links → `link_source = 'exact_name'` or `'fuzzy_name'`, confidence from match type

---

## Task 4: Update Player Manager to Stamp Manual Links

**File:** `src/patt/pages/admin_pages.py` (Player Manager drag-and-drop API endpoints)

- Character linked via Player Manager → `link_source = 'manual'`, `confidence = 'confirmed'`
- Discord user linked to a player → any `player_characters` rows for that player with
  `confidence = 'low'` (stubs) upgraded to `confidence = 'medium'`

---

## Task 5: Coverage Metrics API

### Endpoint: `GET /api/v1/admin/matching/coverage`

Admin-only. Returns JSON:

```json
{
    "ok": true,
    "data": {
        "summary": {
            "total_characters": 87,
            "matched_characters": 62,
            "unmatched_characters": 25,
            "character_coverage_pct": 71.3,
            "total_discord_users": 45,
            "matched_discord_users": 38,
            "unmatched_discord_users": 7,
            "discord_coverage_pct": 84.4,
            "total_players": 42,
            "players_with_discord": 38,
            "players_without_discord": 4,
            "discord_link_pct": 90.5
        },
        "by_link_source": { "note_key": 35, "exact_name": 12, "manual": 5, "...": "..." },
        "by_confidence": { "high": 40, "medium": 10, "confirmed": 5, "low": 8 },
        "unmatched_characters": [
            {"id": 101, "character_name": "Dashdashdash", "realm": "Sen'jin",
             "guild_rank": "Initiate", "guild_note": "", "last_login_days_ago": 15}
        ],
        "unmatched_discord_users": [
            {"id": 22, "username": "coolguy42", "display_name": "Cool Guy",
             "highest_guild_role": "Member", "joined_server_at": "2024-11-01"}
        ]
    }
}
```

**Counts:** "total_characters" = active guild chars (`removed_at IS NULL`).
"total_discord_users" = present members with guild roles.
"matched" means has a `player_characters` row (chars) or is referenced by `players.discord_user_id` (Discord).

---

## Task 6: Redesign the Data Quality Admin Page

**File:** `src/patt/templates/admin/data_quality.html`
**Route:** `GET /admin/data-quality`

### New Layout

```
┌─────────────────────────────────────────────────────────────┐
│  MATCHING COVERAGE                                           │
│                                                              │
│  Characters: ████████████░░░░ 62/87 (71%)                   │
│  Discord:    █████████████░░░ 38/45 (84%)                   │
│  Players:    █████████████░░░ 38/42 have Discord (90%)      │
│                                                              │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ By Link Source   │  │ By Confidence   │                   │
│  │ note_key: 35     │  │ high: 40        │                   │
│  │ exact_name: 12   │  │ medium: 10      │                   │
│  │ note_key_stub: 8 │  │ confirmed: 5    │                   │
│  │ manual: 5        │  │ low: 8          │                   │
│  └─────────────────┘  └─────────────────┘                   │
│                                                              │
│  [Run Matching]  [Refresh Stats]                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  UNMATCHED CHARACTERS (25)                                   │
│  Name           Rank      Note        Last Login             │
│  Dashdashdash   Initiate  —           15 days ago            │
│  Mysterymage    Member    "Myst"      2 days ago             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  UNMATCHED DISCORD USERS (7)                                 │
│  Username       Display Name    Role      Joined             │
│  coolguy42      Cool Guy        Member    Nov 2024           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  RECENT AUDIT FINDINGS                                       │
│  (Keep existing audit_issues display from Phase 2.9)         │
└─────────────────────────────────────────────────────────────┘
```

Coverage section fetches from API on page load. Progress bars use gold/dark theme.
Coverage health tint: >80% green, 50-80% amber, <50% red.

---

## Task 7: Update SQLAlchemy Model

**File:** `src/sv_common/db/models.py` — add to `PlayerCharacter`:

```python
link_source: Mapped[str] = mapped_column(
    String(30), nullable=False, server_default="unknown"
)
confidence: Mapped[str] = mapped_column(
    String(15), nullable=False, server_default="unknown"
)
```

---

## Task 8: Tests

### Unit Tests (NEW files)

- **`test_identity_engine_attribution.py`**: `_find_discord_for_key` returns correct match_type per strategy; `_create_player_group` stamps correct link_source/confidence; no-note fallback stamps `exact_name`/`fuzzy_name`
- **`test_coverage_metrics.py`**: correct counts, percentages (including 0/0 = 0%), unmatched lists, breakdowns sum correctly
- **`test_player_manager_attribution.py`**: manual link → `manual`/`confirmed`; stub upgrade on Discord link

### Integration Tests (DB-dependent)

- Migration adds columns with correct defaults
- Backfill labels existing rows correctly
- Full matching run produces attributed links
- Coverage API returns real data

---

## Files Changed

| File | Change |
|------|--------|
| `alembic/versions/0008_link_attribution.py` | **NEW** — migration |
| `src/sv_common/db/models.py` | Add `link_source`, `confidence` to `PlayerCharacter` |
| `src/sv_common/guild_sync/identity_engine.py` | Update `_find_discord_for_key` return, stamp attribution |
| `src/sv_common/guild_sync/mitigations.py` | Stamp attribution on re-links |
| `src/patt/pages/admin_pages.py` | Coverage API, Player Manager attribution, data quality route |
| `src/patt/templates/admin/data_quality.html` | **REWRITE** — coverage dashboard |
| `tests/unit/test_identity_engine_attribution.py` | **NEW** |
| `tests/unit/test_coverage_metrics.py` | **NEW** |
| `tests/unit/test_player_manager_attribution.py` | **NEW** |

---

## Acceptance Criteria

- [ ] `player_characters` has `link_source` and `confidence` columns
- [ ] Existing rows backfilled with `unknown`/`unknown` (stubs get `low`)
- [ ] `run_matching()` stamps correct attribution on new links
- [ ] Player Manager stamps `manual`/`confirmed` on drag-and-drop links
- [ ] Coverage API returns accurate stats
- [ ] Data Quality page shows coverage dashboard with progress bars
- [ ] Unmatched characters and Discord users listed on the page
- [ ] Existing audit findings section preserved at bottom of page
- [ ] All new and existing tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Database backup taken before migration
- [ ] Migration runs cleanly: `alembic upgrade head`
- [ ] Commit: `git commit -m "phase-3.0a: matching transparency and coverage metrics"`
- [ ] Update CLAUDE.md "Current Build Status" section
