# Phase 3.0C — Drift Detection (Real Data Quality)

> **Goal:** Build the actual data quality system. Not "things we haven't
> matched yet" (that's coverage, handled in 3.0A). This is: "things that
> *were* correct and are now *wrong*." Detectable, automatable, rare.

---

## Philosophy

Data quality rules detect drift — the gap between what the database believes
and what the source of truth (guild notes, Discord roles, game state) now says.

The Mito/Elrek case is the textbook example: a character was linked to Elrek's
player because the guild note said "Elrek." Someone corrected the note to "Mito."
Now the DB link is wrong. The note changed, the link didn't follow. That's drift.

These rules are narrow, specific, and automatable. They fire rarely (a handful
of times per month). They fix themselves. And when they can't fix themselves,
they flag the issue clearly for the admin.

Coverage gaps (orphan characters, unmatched Discord users) are NOT drift — they're
the system's current frontier of knowledge. They belong on the coverage dashboard
from Phase 3.0A, not in the data quality engine.

---

## What This Phase Produces

1. A focused set of drift detection rules (3 rules to start)
2. Auto-mitigation for the rules that can self-correct
3. A "Drift Detection" section on the Data Quality page — separate from coverage
4. A scheduled drift scan that runs after each guild sync
5. Clean separation between Phase 2.9's audit_issues (kept for logging) and
   the new drift rules

## What This Phase Does NOT Do

- No new matching rules (Phase 3.0D+)
- No changes to the matching engine or rule runner
- No removal of existing audit_issues — they continue to work as an event log

---

## Prerequisites

- Phase 3.0A complete (link attribution exists)
- Phase 3.0B complete (rule runner exists)
- Read CLAUDE.md and TESTING.md

---

## The Three Drift Rules

### Rule 1: `note_mismatch` — Guild Note Changed, Link Now Wrong

**This already exists from Phase 2.9.** This phase refines it.

- **What it detects:** A character is linked to Player A. The guild note now
  points to Player B (or to no known player). The link and the note disagree.
- **When it runs:** After every addon sync that reports note changes, AND as
  part of the scheduled drift scan.
- **Auto-mitigate:** Yes.
  1. Unlink character from current player.
  2. Attempt to find correct player by new note key.
  3. If found: re-link with `link_source = 'auto_relink'`, `confidence` from match type.
  4. If not found: leave unlinked. Coverage dashboard will show it as unmatched.
  5. Log the action to audit_issues with before/after detail.
- **Fire rate:** A few times per month. Caused by humans correcting notes in-game.

**Refinements from Phase 2.9:**
- Use `_find_discord_for_key()` with the new tuple return (match_type) from Phase 3.0A
- Stamp proper `link_source` and `confidence` on re-links
- Check `player_note_aliases` before declaring a mismatch — if the note key
  is a known alias for the current player, it's not a mismatch

### Rule 2: `link_contradicts_note` — Existing Link Disagrees With Note

Subtly different from `note_mismatch`. This catches cases where:
- The note didn't *change* (no addon sync detected a change)
- But a scheduled scan notices the note key doesn't match the linked player

This covers edge cases like: note was always wrong but nobody noticed, or the
note was manually edited in-game between addon syncs, or the link was created
by a rule that didn't consider the note (e.g., fuzzy name match).

- **What it detects:** Character is linked to a player. The guild note's key
  does not match any known identity for that player (Discord username, display
  name, or known aliases).
- **When it runs:** Scheduled drift scan only (not on every sync — too expensive).
- **Auto-mitigate:** No. Flag for admin review.
  - The note might be correct and the link wrong → admin unlinks
  - The link might be correct and the note irrelevant → admin adds alias
  - The note might be garbage → admin ignores
- **Fire rate:** Rare after initial cleanup. Ongoing for edge cases.

**Implementation:** This is essentially the existing `detect_note_mismatch()`
in integrity_checker.py, but reframed. Currently it logs to audit_issues —
keep that, but add it to the drift detection UI section.

### Rule 3: `duplicate_discord_link` — Player Has Impossible State

- **What it detects:** Two players point to the same Discord user
  (shouldn't happen due to UNIQUE constraint, but can occur via race
  conditions or manual DB edits). OR: a player has `discord_user_id` set
  but the Discord user's `is_present` is FALSE (they left the server).
- **When it runs:** Scheduled drift scan.
- **Auto-mitigate:** Partially.
  - Duplicate Discord links → flag for admin (can't auto-decide which player is correct)
  - Discord user left server → flag as `stale_discord_link`, severity `info`.
    Don't auto-unlink — the person might come back.
- **Fire rate:** Very rare. Mostly informational.

---

## Architecture

### Drift Scanner

```python
# src/sv_common/guild_sync/drift_scanner.py

async def run_drift_scan(pool: asyncpg.Pool) -> dict:
    """
    Run all drift detection rules. Returns summary of findings.

    Called by:
    - Scheduler after guild sync (4x daily)
    - Admin "Run Drift Scan" button
    """
    results = {}

    async with pool.acquire() as conn:
        results["note_mismatch"] = await detect_and_mitigate_note_mismatches(conn, pool)
        results["link_contradicts_note"] = await detect_link_note_contradictions(conn)
        results["duplicate_discord_link"] = await detect_duplicate_discord_links(conn)

    return results
```

### Integration with Existing Scheduler

**File:** `src/sv_common/guild_sync/scheduler.py`

Add `run_drift_scan()` to the post-sync pipeline:

```
sync_addon_data()  →  run_drift_scan()  →  send_sync_summary()
     ↑                       ↑                      ↑
 (writes notes)    (detects drift,          (reports new
                    auto-fixes what          findings to
                    it can)                  Discord)
```

**Important:** `run_matching()` is NOT in this pipeline. Matching is
admin-triggered only (from Phase 2.9 design decision). Drift detection
runs automatically because it's cheap and targeted.

---

## Task 1: Refine `note_mismatch` Detection and Mitigation

**Files:**
- `src/sv_common/guild_sync/integrity_checker.py` — `detect_note_mismatch()`
- `src/sv_common/guild_sync/mitigations.py` — `mitigate_note_mismatch()`

### Refinements:

1. Check `player_note_aliases` before flagging. If the note key is a known alias
   for the player → not a mismatch, skip.

2. When re-linking, use the Phase 3.0A attribution:
   - `link_source = 'auto_relink'`
   - `confidence` = from `_find_discord_for_key()` match_type

3. When mitigation succeeds, add the new note key as an alias for the new player
   (call `upsert_note_alias()`).

4. Update audit_issue details to include:
   - `old_player_id`, `old_player_name`
   - `new_player_id`, `new_player_name` (if re-linked)
   - `note_key`, `match_type`
   - `action_taken`: `"relinked"` | `"unlinked_only"` | `"false_alarm"`

---

## Task 2: Implement `link_contradicts_note` Detection

**File:** `src/sv_common/guild_sync/integrity_checker.py`

New function: `detect_link_note_contradictions()`

```python
async def detect_link_note_contradictions(conn: asyncpg.Connection) -> int:
    """
    Find characters where the guild note key doesn't match ANY known identity
    for their linked player. Different from note_mismatch: this doesn't require
    a note *change* — it's a full scan.

    Skips:
    - Characters with no guild note (nothing to compare)
    - Characters where note key matches Discord username/display_name
    - Characters where note key is in player_note_aliases
    - Characters with link_source = 'manual' and confidence = 'confirmed'
      (human said it's right, note is irrelevant)

    Returns count of new issues created.
    """
```

**Important exclusion:** If `link_source = 'manual'` AND `confidence = 'confirmed'`,
skip. A human explicitly linked this character — the note is overridden by human
judgment. This prevents the drift detector from second-guessing admin decisions.

Issue type: `"link_contradicts_note"`, severity: `"info"` (not auto-mitigated).

---

## Task 3: Implement `duplicate_discord_link` Detection

**File:** `src/sv_common/guild_sync/integrity_checker.py`

New function: `detect_duplicate_discord_links()`

```python
async def detect_duplicate_discord_links(conn: asyncpg.Connection) -> int:
    """
    Detect impossible states in Discord ↔ Player links.

    Sub-checks:
    1. Two players with the same discord_user_id (constraint violation edge case)
    2. Player's discord_user_id points to a Discord user who left (is_present = FALSE)

    Returns count of new issues created.
    """
```

Issue types:
- `"duplicate_discord"`, severity `"error"` — two players, one Discord user
- `"stale_discord_link"`, severity `"info"` — Discord user left server

Neither is auto-mitigated.

---

## Task 4: Create the Drift Scanner

**File:** `src/sv_common/guild_sync/drift_scanner.py` (NEW)

```python
async def run_drift_scan(pool: asyncpg.Pool) -> dict:
    """Run all drift detection rules. Auto-mitigate where possible."""

    async with pool.acquire() as conn:
        # Rule 1: note_mismatch (auto-mitigate)
        note_mismatch_count = await detect_note_mismatch(conn)

        # Rule 2: link_contradicts_note (flag only)
        contradiction_count = await detect_link_note_contradictions(conn)

        # Rule 3: duplicate/stale discord (flag only)
        discord_count = await detect_duplicate_discord_links(conn)

    # Auto-mitigate note_mismatch issues
    mitigated = 0
    if note_mismatch_count > 0:
        mitigated = await run_auto_mitigations(pool)  # existing function

    return {
        "note_mismatch": {"detected": note_mismatch_count, "mitigated": mitigated},
        "link_contradicts_note": {"detected": contradiction_count},
        "duplicate_discord": {"detected": discord_count},
    }
```

---

## Task 5: Integrate with Scheduler

**File:** `src/sv_common/guild_sync/scheduler.py`

Add drift scan to the post-sync pipeline. Update the Discord summary to
include drift findings.

```python
# In the sync pipeline:
drift_results = await run_drift_scan(pool)

# Include in Discord summary:
# "Drift scan: 1 note mismatch detected and auto-fixed, 0 contradictions, 0 stale links"
```

---

## Task 6: Update Data Quality Page — Drift Section

**File:** `src/patt/templates/admin/data_quality.html`

Add a "Drift Detection" section between the coverage dashboard and the
audit findings log. This section is separate from coverage — it shows
things that are *wrong*, not things that are *unknown*.

```
┌─────────────────────────────────────────────────────────────┐
│  MATCHING COVERAGE (from Phase 3.0A)                         │
│  ...                                                         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  DRIFT DETECTION                              [Run Scan]    │
│                                                              │
│  ✅ note_mismatch         0 open    3 fixed (30d)  Auto-fix │
│  ✅ link_contradicts_note 0 open    —              Manual   │
│  ✅ duplicate_discord     0 open    —              Manual   │
│  ✅ stale_discord_link    2 open    —              Info     │
│                                                              │
│  Last scan: 2 hours ago                                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  UNMATCHED CHARACTERS / DISCORD USERS (from Phase 3.0A)      │
│  ...                                                         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  RECENT AUDIT LOG                                            │
│  ...                                                         │
└─────────────────────────────────────────────────────────────┘
```

The drift section shows each rule with:
- Status icon (✅ if 0 open issues, ⚠️ if open issues exist)
- Open issue count
- Resolved count (last 30 days)
- Whether it auto-fixes or requires manual action
- "Run Scan" button triggers `POST /api/v1/admin/drift/scan`

### API Endpoints

- `POST /api/v1/admin/drift/scan` — trigger a drift scan now, return results
- `GET /api/v1/admin/drift/summary` — get current drift status (open counts per rule)

---

## Task 7: Update Rules Registry

**File:** `src/sv_common/guild_sync/rules.py`

Add the new drift rules to the existing RULES registry (from Phase 2.9):

- `link_contradicts_note`: severity `info`, auto_mitigate `False`
- `duplicate_discord`: severity `error`, auto_mitigate `False`
- `stale_discord_link`: severity `info`, auto_mitigate `False`

Keep existing rules: `note_mismatch`, `orphan_wow`, `orphan_discord`,
`role_mismatch`, `stale_character`.

---

## Task 8: Tests

### Unit Tests

**`tests/unit/test_drift_scanner.py`** (NEW):
- Drift scan calls all three detection functions
- Auto-mitigation runs for note_mismatch findings
- Results dict has correct shape
- Scan with no drift → clean results

**`tests/unit/test_link_contradicts_note.py`** (NEW):
- Character with note key matching Discord username → no issue
- Character with note key matching known alias → no issue
- Character with note key NOT matching anything → issue created
- Manual/confirmed links excluded from check
- Characters with no guild note → skipped

**`tests/unit/test_duplicate_discord.py`** (NEW):
- Two players with same discord_user_id → error issue
- Player with departed Discord user → info issue
- Normal state → no issues

### Integration Tests

- Full drift scan on test data with known drift → correct issues found
- Auto-mitigation resolves note_mismatch correctly
- Scheduler pipeline includes drift scan

---

## Files Changed

| File | Change |
|------|--------|
| `src/sv_common/guild_sync/drift_scanner.py` | **NEW** — orchestrates drift rules |
| `src/sv_common/guild_sync/integrity_checker.py` | Add `detect_link_note_contradictions()`, `detect_duplicate_discord_links()` |
| `src/sv_common/guild_sync/mitigations.py` | Refine note_mismatch mitigation (attribution) |
| `src/sv_common/guild_sync/rules.py` | Add new drift rule definitions |
| `src/sv_common/guild_sync/scheduler.py` | Add drift scan to post-sync pipeline |
| `src/patt/pages/admin_pages.py` | Drift scan API endpoints, updated data quality route |
| `src/patt/templates/admin/data_quality.html` | Add drift detection section |
| `tests/unit/test_drift_scanner.py` | **NEW** |
| `tests/unit/test_link_contradicts_note.py` | **NEW** |
| `tests/unit/test_duplicate_discord.py` | **NEW** |

---

## Acceptance Criteria

- [ ] `note_mismatch` refined with alias checking and proper attribution
- [ ] `link_contradicts_note` detects stale links (excluding manual/confirmed)
- [ ] `duplicate_discord_link` detects impossible states
- [ ] Drift scanner runs all rules, auto-mitigates where configured
- [ ] Drift scan integrated into scheduler post-sync pipeline
- [ ] Data Quality page has separate "Drift Detection" section
- [ ] API endpoints for manual drift scan and status summary
- [ ] All new and existing tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-3.0c: drift detection and data quality rules"`
- [ ] Update CLAUDE.md "Current Build Status" section
