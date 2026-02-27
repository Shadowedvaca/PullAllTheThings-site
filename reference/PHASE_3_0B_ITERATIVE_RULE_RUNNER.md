# Phase 3.0B — Iterative Rule Runner (Progressive Discovery)

> **Goal:** Refactor matching so rules run in sequence, each building on what
> the last discovered. Rules loop until nothing new is found. Each rule is a
> pluggable function — adding a new pattern means adding one function and
> registering it.

---

## Philosophy: Progressive Discovery

Matching isn't a single pass or a scoring system. It's a collection of
simple rules that help each other. One rule finds a name match and creates
a player. The next rule sees that player, looks at the guild note, and
attaches three more alts. A third rule sees those character names and
recognizes the Discord user whose name is a mashup of two of them.

Each rule is simple on its own. Together they build a picture. And when you
notice a pattern the system misses, you write a new rule and plug it in.

**The key mechanism:** Run all rules in order. Count what changed. If anything
changed, run them all again. Stop when a full pass produces zero new links.
This is cheap — a guild of 50-80 characters converges in 2-3 passes.

---

## What This Phase Produces

1. A `MatchingRule` protocol — standard interface for all matching rules
2. A rule runner that executes rules iteratively until convergence
3. Refactored existing matching logic into individual rule functions
4. Per-rule result tracking — each rule reports what it found, logged to sync_log
5. Admin UI updates — "Run Matching" shows per-rule results after execution
6. Foundation for adding new rules in future phases (Phase 3.0D+)

## What This Phase Does NOT Do

- No new matching patterns (just refactoring existing logic into rules)
- No drift detection (Phase 3.0C)
- No changes to the database schema (uses attribution from Phase 3.0A)
- No changes to when matching runs (still admin-triggered or scheduler)

---

## Prerequisites

- Phase 3.0A complete (link_source and confidence columns exist)
- Read CLAUDE.md and TESTING.md

---

## Architecture

### Rule Interface

```python
# src/sv_common/guild_sync/matching_rules/base.py

from dataclasses import dataclass, field
from typing import Protocol

import asyncpg


@dataclass
class RuleResult:
    """What a single rule produced in one pass."""
    rule_name: str
    players_created: int = 0
    chars_linked: int = 0
    discord_linked: int = 0
    details: list[str] = field(default_factory=list)

    @property
    def changed_anything(self) -> bool:
        return self.players_created > 0 or self.chars_linked > 0 or self.discord_linked > 0


class MatchingRule(Protocol):
    """Interface for a matching rule. All rules follow this shape."""

    name: str
    description: str
    link_source: str     # What to stamp on player_characters.link_source
    order: int           # Lower = runs first

    async def run(self, conn: asyncpg.Connection, context: "MatchingContext") -> RuleResult:
        """
        Execute this rule. Use context for shared state.
        Return what was found/created.
        """
        ...
```

### Matching Context

Shared state loaded once and refreshed between passes. Rules read from it
and write back to it as they create links.

```python
# src/sv_common/guild_sync/matching_rules/base.py

@dataclass
class MatchingContext:
    """Shared state for a matching run. Loaded once, refreshed between passes."""

    # All active characters not yet linked (shrinks as rules link them)
    unlinked_chars: list[dict]

    # All Discord users with guild roles (keyed for fast lookup)
    all_discord: list[dict]

    # discord_user_id → player_id cache (grows as rules create players)
    discord_player_cache: dict[int, int]

    # Characters grouped by note key (computed once, consumed by rules)
    note_groups: dict[str, list[dict]]     # key → [char, char, ...]
    no_note_chars: list[dict]              # chars with no guild note

    # Minimum rank filter (optional)
    min_rank_level: int | None = None

    def refresh_unlinked(self, conn: asyncpg.Connection):
        """Re-query unlinked characters. Called between passes."""
        ...
```

### Rule Runner

```python
# src/sv_common/guild_sync/matching_rules/runner.py

async def run_matching_rules(
    pool: asyncpg.Pool,
    min_rank_level: int | None = None,
    max_passes: int = 5,
) -> dict:
    """
    Execute all registered rules iteratively until convergence.

    Returns combined stats from all passes.
    """
    rules = get_registered_rules()  # sorted by order
    context = await build_context(pool, min_rank_level)

    all_results: list[RuleResult] = []
    pass_number = 0

    while pass_number < max_passes:
        pass_number += 1
        pass_changed = False

        for rule in rules:
            async with pool.acquire() as conn:
                result = await rule.run(conn, context)
                all_results.append(result)
                if result.changed_anything:
                    pass_changed = True

        if not pass_changed:
            break

        # Refresh context — re-query unlinked chars so next pass
        # sees the updated state
        context = await build_context(pool, min_rank_level)

    return {
        "passes": pass_number,
        "converged": not pass_changed or pass_number < max_passes,
        "results": [
            {"rule": r.rule_name, "players_created": r.players_created,
             "chars_linked": r.chars_linked, "discord_linked": r.discord_linked,
             "details": r.details}
            for r in all_results
        ],
        "totals": {
            "players_created": sum(r.players_created for r in all_results),
            "chars_linked": sum(r.chars_linked for r in all_results),
            "discord_linked": sum(r.discord_linked for r in all_results),
        }
    }
```

### Rule Registry

```python
# src/sv_common/guild_sync/matching_rules/__init__.py

from .note_group_rule import NoteGroupRule
from .name_match_rule import NameMatchRule

def get_registered_rules() -> list:
    """Return all matching rules in execution order."""
    return sorted([
        NoteGroupRule(),
        NameMatchRule(),
    ], key=lambda r: r.order)
```

---

## Task 1: Create the matching_rules Package

**Directory:** `src/sv_common/guild_sync/matching_rules/`

```
matching_rules/
├── __init__.py          # Registry + get_registered_rules()
├── base.py              # MatchingRule protocol, MatchingContext, RuleResult
├── runner.py            # run_matching_rules() — the iterative loop
├── note_group_rule.py   # Rule 1: group by note key → find Discord
└── name_match_rule.py   # Rule 2: no-note chars → character name matching
```

---

## Task 2: Implement Rule 1 — Note Group Rule

**File:** `src/sv_common/guild_sync/matching_rules/note_group_rule.py`

This is a direct extraction of the existing note-group logic from
`identity_engine.py`'s `run_matching()`.

```python
class NoteGroupRule:
    name = "note_group"
    description = "Group characters by guild note key, find Discord user for each group"
    link_source = "note_key"  # or "note_key_stub" if no Discord found
    order = 10

    async def run(self, conn, context) -> RuleResult:
        result = RuleResult(rule_name=self.name)

        for note_key, chars in context.note_groups.items():
            # Skip groups where ALL chars are already linked
            unlinked = [c for c in chars if c["id"] in _unlinked_ids(context)]
            if not unlinked:
                continue

            discord_user, match_type = _find_discord_for_key(note_key, context.all_discord)
            # Create/find player, link chars (same logic as current _create_player_group)
            # Stamp link_source and confidence from Phase 3.0A
            ...

        return result
```

**Key behavior preserved:**
- Characters with the same note key are grouped under one player
- If the Discord user already has a player, reuse it
- If no Discord match, create a stub player
- `upsert_note_alias()` called when a note key links to a player

---

## Task 3: Implement Rule 2 — Name Match Rule

**File:** `src/sv_common/guild_sync/matching_rules/name_match_rule.py`

Extraction of the no-note fallback from `run_matching()`.

```python
class NameMatchRule:
    name = "name_match"
    description = "Match characters (no guild note) by character name to Discord username"
    link_source = "exact_name"  # or "fuzzy_name"
    order = 20

    async def run(self, conn, context) -> RuleResult:
        result = RuleResult(rule_name=self.name)

        for char in context.no_note_chars:
            if char["id"] not in _unlinked_ids(context):
                continue

            char_norm = normalize_name(char["character_name"])
            discord_user, match_type = _find_discord_for_key(char_norm, context.all_discord)

            if discord_user:
                # Create/find player, link char
                # link_source = "exact_name" if exact, "fuzzy_name" if substring
                ...

        return result
```

---

## Task 4: Update `run_matching()` to Use the Rule Runner

**File:** `src/sv_common/guild_sync/identity_engine.py`

Replace the body of `run_matching()` with a call to `run_matching_rules()`:

```python
async def run_matching(pool: asyncpg.Pool, min_rank_level: int | None = None) -> dict:
    """Run the iterative matching engine. Delegates to the rule runner."""
    from .matching_rules.runner import run_matching_rules
    return await run_matching_rules(pool, min_rank_level=min_rank_level)
```

**Important:** Keep `run_matching()` as the public API. External callers
(scheduler, admin endpoints, tests) should not need to change. The rule
runner is an internal implementation detail.

Keep all existing helper functions in identity_engine.py:
- `normalize_name()` — used by rules and integrity checker
- `_extract_note_key()` — used by rules and integrity checker
- `_find_discord_for_key()` — used by rules, mitigations, integrity checker
- `_note_still_matches_player()` — used by integrity checker and mitigations

These are shared utilities. Rules import them from identity_engine.

---

## Task 5: Update Admin UI — Show Per-Rule Results

**File:** `src/patt/pages/admin_pages.py` + `src/patt/templates/admin/data_quality.html`

The "Run Matching" button on the Data Quality page should show results
broken down by rule:

```
Matching Complete — 2 passes, converged ✓

  Pass 1:
    note_group:  3 players created, 8 chars linked, 3 with Discord
    name_match:  1 player created, 1 char linked, 1 with Discord

  Pass 2:
    note_group:  0 new  (nothing changed)
    name_match:  0 new  (nothing changed)

  Totals: 4 players, 9 chars linked
```

Also update the matching panel in Player Manager (if it exists) to use the
same runner and show per-rule breakdown.

Update the matching API endpoint (`POST /api/v1/admin/matching/run`) to
return the full result dict from `run_matching_rules()`.

---

## Task 6: Update Scheduler Integration

**File:** `src/sv_common/guild_sync/scheduler.py`

The scheduler calls `run_matching()` — no changes needed since we kept the
same public API. But update the Discord summary report to include per-rule
breakdown if matching was run as part of the pipeline.

---

## Task 7: Move Existing `identity_engine.py` Logic

This is the most delicate part. The goal is to move logic into rules without
breaking anything.

### What moves INTO matching_rules/:

- The main loop body of `run_matching()` (note-group processing + no-note fallback)
- The `_create_player_group()` helper (shared utility, keep in identity_engine.py
  or move to base.py — either works, just import correctly)

### What stays in identity_engine.py:

- `normalize_name()` — shared utility
- `_extract_note_key()` — shared utility
- `_find_discord_for_key()` — shared utility (updated return type from Phase 3.0A)
- `_note_still_matches_player()` — used by integrity_checker/mitigations
- `relink_note_changed_characters()` — called from addon sync, not part of matching
- `run_matching()` — thin wrapper that delegates to runner

### Import structure:

```
matching_rules/base.py      → imports nothing from guild_sync
matching_rules/runner.py    → imports from base.py
matching_rules/note_group_rule.py  → imports from base.py, identity_engine
matching_rules/name_match_rule.py  → imports from base.py, identity_engine
identity_engine.py          → imports from matching_rules.runner (only in run_matching)
```

No circular imports. Rules depend on identity_engine utilities.
identity_engine depends on the runner only inside `run_matching()`.

---

## Task 8: Tests

### Unit Tests

**`tests/unit/test_rule_runner.py`** (NEW):
- Runner stops when no rules produce changes (convergence)
- Runner respects max_passes limit
- Multiple passes: rule A creates a player in pass 1, rule B uses it in pass 2
- Empty input (no unlinked chars) → immediate convergence, zero passes of work
- Each rule's RuleResult correctly reports counts

**`tests/unit/test_note_group_rule.py`** (NEW):
- Characters with same note key grouped under one player
- Discord user found via exact username → high confidence
- Discord user found via substring → medium confidence
- No Discord found → stub player with low confidence
- Existing player reused when Discord user already linked
- Note alias upserted on successful link

**`tests/unit/test_name_match_rule.py`** (NEW):
- Character name exactly matches Discord username → linked
- Character name substring matches → linked with medium confidence
- No match → skipped (not stub-created; only note_group creates stubs)
- Already-linked characters skipped

**`tests/unit/test_run_matching_compat.py`** (NEW):
- `run_matching()` returns same stat shape as before (backward compat)
- `run_matching(min_rank_level=...)` filter still works
- External callers don't need to change

### Integration Tests

- Full matching run on test data produces same results as before refactor
- Multi-pass convergence works with real DB
- Scheduler pipeline still works end-to-end

---

## Files Changed

| File | Change |
|------|--------|
| `src/sv_common/guild_sync/matching_rules/__init__.py` | **NEW** — registry |
| `src/sv_common/guild_sync/matching_rules/base.py` | **NEW** — protocol, context, result |
| `src/sv_common/guild_sync/matching_rules/runner.py` | **NEW** — iterative runner |
| `src/sv_common/guild_sync/matching_rules/note_group_rule.py` | **NEW** — note group rule |
| `src/sv_common/guild_sync/matching_rules/name_match_rule.py` | **NEW** — name match rule |
| `src/sv_common/guild_sync/identity_engine.py` | Thin out `run_matching()`, keep utilities |
| `src/patt/pages/admin_pages.py` | Per-rule result display |
| `src/patt/templates/admin/data_quality.html` | Per-rule breakdown in matching results |
| `tests/unit/test_rule_runner.py` | **NEW** |
| `tests/unit/test_note_group_rule.py` | **NEW** |
| `tests/unit/test_name_match_rule.py` | **NEW** |
| `tests/unit/test_run_matching_compat.py` | **NEW** |

---

## Future Rules (Phase 3.0D+)

These are NOT built in this phase. They're documented here so the rule
runner design accounts for them:

- **Multi-Character Discord Name Rule** (`order: 30`): Discord user's display_name
  contains multiple character names separated by `/` or `-` (e.g. "TrogMoon/Rivermane").
  If we know those characters belong to a player, match the Discord user to that player.

- **Alias Learning Rule** (`order: 40`): When the note_group rule links a group with
  note key "Sho" to a player named "Shodoom", learn that "Sho" is an alias. Next time
  a different character has note "Sho", skip to that player directly. (Partially exists
  via `player_note_aliases` table — this rule would use it more aggressively.)

- **Cross-Reference Rule** (`order: 50`): If a stub player has 3 characters, and one
  of those character names appears in another character's guild note as "alt of X",
  link the second character to the same player.

Each of these is one file and one registry entry. That's the power of this design.

---

## Acceptance Criteria

- [ ] `matching_rules` package exists with clean separation
- [ ] `NoteGroupRule` produces same results as old `run_matching()` note-group logic
- [ ] `NameMatchRule` produces same results as old no-note fallback
- [ ] Rule runner iterates until convergence (verified by test)
- [ ] `run_matching()` public API unchanged — external callers don't break
- [ ] Per-rule breakdown shown in admin UI after matching run
- [ ] All new tests pass
- [ ] All existing tests still pass (backward compatibility)

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-3.0b: iterative rule runner with progressive discovery"`
- [ ] Update CLAUDE.md "Current Build Status" section
