# Phase 2.9 — Data Quality Engine

> **Goal:** Replace the current blunt-force automation (run_matching on every upload,
> create stubs for everything) with a targeted, rules-based data quality system.
> Each rule detects a specific issue and has a known mitigation tool.
> An admin UI makes the whole system visible and governable.

---

## Problem With the Current System

The current guild sync pipeline does too much indiscriminately:

1. Every addon upload triggers `run_matching()` — a full scan of ALL unlinked characters
2. `run_matching()` creates **stub players** for every character it can't match to Discord
3. This floods the players table with noise records for alts, bank toons, ex-members, etc.
4. The integrity checker logs issues but there's no path from "issue found" to "fix it"
5. No visibility into what rules exist, how often they fire, or whether fixes work

**What we want instead:**
- Each detected issue type has a specific, targeted mitigation
- No bulk operations run unless explicitly needed
- The audit log becomes a health dashboard: rules → findings → fixes
- Admin can see all rules, their issue counts, and trigger fixes manually

---

## Architecture

### Three Components

```
┌─────────────────────────────────────────────────────────┐
│  Rules Registry                                          │
│  Defined in code — each rule has:                        │
│    - issue_type (matches audit_issues.issue_type)        │
│    - name, description                                   │
│    - severity                                            │
│    - detect() function                                   │
│    - mitigate() function (optional — some need human)    │
│    - auto_mitigate: bool                                 │
└─────────────┬───────────────────────────────────────────┘
              │ detects → logs to
              ▼
┌─────────────────────────────────────────────────────────┐
│  audit_issues table (already exists)                     │
│  issue_type, severity, summary, details, issue_hash,     │
│  created_at, resolved_at, resolved_by                    │
└─────────────┬───────────────────────────────────────────┘
              │ triggers (if auto_mitigate=True, or manually)
              ▼
┌─────────────────────────────────────────────────────────┐
│  Mitigation Tools                                        │
│  Targeted Python functions, one per rule                 │
│  Operate only on the specific affected rows              │
│  Log their results back to audit_issues.resolved_by      │
└─────────────────────────────────────────────────────────┘
```

### Rules vs Current Behavior

| Current Behavior | New Behavior |
|-----------------|--------------|
| `run_matching()` on every upload — creates stubs for all unlinked chars | **Removed from addon pipeline**. Only runs as an admin-triggered action. |
| Note change detected → `relink_note_changed_characters()` + `run_matching()` | Note change → log `note_mismatch` issue → targeted relink for just that character |
| `run_integrity_check()` finds orphans, logs them | Same, but each issue type now has a mitigation tool attached |
| No admin visibility into rules | New admin "Data Quality" tab shows all rules, issue counts, mitigation status |

---

## Rules to Implement

### Rule: `note_mismatch`
- **Trigger:** Guild note on a WoW character changed, and the new note key doesn't match the player the character is currently linked to
- **Detection:** During `sync_addon_data()`, when `note_changed=True` AND character is linked to a player
- **Severity:** `warning`
- **Auto-mitigate:** Yes
- **Mitigation:** Unlink character from current player; attempt to find correct player by new note key; if found link it; if not found leave unlinked (log as `orphan_wow`)
- **Resolves when:** Character successfully re-linked to the correct player

### Rule: `orphan_wow`
- **Trigger:** WoW character in guild has no player link
- **Detection:** Existing integrity checker check 1
- **Severity:** `warning`
- **Auto-mitigate:** No (requires human judgment or Discord match to be present)
- **Mitigation (manual trigger):** Attempt note-key matching against existing players and Discord users. If confident match found, create link. If not, leave for admin.
- **Resolves when:** Character has a player_characters entry

### Rule: `orphan_discord`
- **Trigger:** Discord member has a guild role but no player record
- **Detection:** Existing integrity checker check 2
- **Severity:** `warning`
- **Auto-mitigate:** No
- **Mitigation (manual trigger):** Attempt to match Discord display_name to a note key on an unlinked character group. If confident, create player + link.
- **Resolves when:** Discord user has a linked player

### Rule: `role_mismatch`
- **Trigger:** Player's in-game rank doesn't match their Discord role
- **Detection:** Existing integrity checker check 3
- **Severity:** `warning`
- **Auto-mitigate:** No (role changes require Discord bot action)
- **Mitigation (manual trigger):** Update player's Discord role to match in-game rank via bot
- **Resolves when:** Discord role matches in-game rank

### Rule: `stale_character`
- **Trigger:** WoW character hasn't logged in for >30 days
- **Detection:** Existing integrity checker check 4
- **Severity:** `info`
- **Auto-mitigate:** No
- **Mitigation:** Admin review only — informational
- **Resolves when:** Character logs in (Blizzard API sync updates last_login_timestamp)

---

## Files to Change

### `src/sv_common/guild_sync/rules.py` ← **NEW FILE**
The rules registry. A dict of `issue_type → RuleDefinition` dataclass.

```python
@dataclass
class RuleDefinition:
    issue_type: str
    name: str
    description: str
    severity: str          # 'info', 'warning', 'error'
    auto_mitigate: bool
    mitigate_fn: Optional[Callable]  # async fn(pool, issue_row) -> bool

RULES: dict[str, RuleDefinition] = {
    "note_mismatch": RuleDefinition(...),
    "orphan_wow": RuleDefinition(...),
    "orphan_discord": RuleDefinition(...),
    "role_mismatch": RuleDefinition(...),
    "stale_character": RuleDefinition(...),
}
```

### `src/sv_common/guild_sync/integrity_checker.py` ← **REFACTOR**
- Each check becomes a standalone `async def detect_<rule>(conn) -> list[Finding]`
- `run_integrity_check()` iterates the rules registry and calls each detect function
- No logic changes to detection — just restructure into named functions
- Remove auto-mitigation from the checker itself (mitigation is separate)

### `src/sv_common/guild_sync/mitigations.py` ← **NEW FILE**
One mitigation function per rule. These are the targeted fix scripts.

```python
async def mitigate_note_mismatch(pool, issue_row) -> bool:
    """Unlink character from wrong player; re-link to correct one if found."""
    ...

async def mitigate_orphan_wow(pool, issue_row) -> bool:
    """Attempt note-key match to existing player. Returns True if linked."""
    ...

async def mitigate_orphan_discord(pool, issue_row) -> bool:
    """Attempt match from Discord username to character note key."""
    ...

async def mitigate_role_mismatch(pool, issue_row, discord_bot) -> bool:
    """Update Discord role to match in-game rank."""
    ...
```

### `src/sv_common/guild_sync/db_sync.py` ← **MINOR CHANGE**
- `sync_addon_data()` still detects `note_changed_ids`
- Instead of returning IDs for the scheduler to pass to `relink_note_changed_characters()`,
  it logs `note_mismatch` audit issues directly (one per changed character)
- The scheduler then calls `run_auto_mitigations()` which processes all pending auto-mitigate issues

### `src/sv_common/guild_sync/scheduler.py` ← **REFACTOR**
- `run_addon_sync()` pipeline becomes:
  1. `sync_addon_data()` — write notes, log `note_mismatch` issues
  2. `run_integrity_check()` — detect all other issue types
  3. `run_auto_mitigations()` — process issues where `auto_mitigate=True`
  4. `send_sync_summary()` — Discord report
- **Remove** `run_matching()` call from addon upload pipeline
- `run_matching()` remains available as an admin-triggered action only
- Add `run_auto_mitigations(pool)` — iterates unresolved issues with auto_mitigate=True, calls their mitigation fn

### `src/patt/pages/admin_pages.py` ← **ADD ROUTE**
New route: `GET /admin/data-quality`

Returns:
- All rules from the registry (name, description, severity, auto_mitigate)
- For each rule: count of open issues, count resolved (last 30 days), last triggered
- Recent audit_issues list (paginated), grouped by issue_type

### `src/patt/templates/admin/data_quality.html` ← **NEW TEMPLATE**
New admin tab: "Data Quality"

Layout:
```
┌─────────────────────────────────────────────────────────┐
│  DATA QUALITY RULES                                      │
│                                                          │
│  [note_mismatch]  Guild Note Mismatch      ⚡ Auto-fix  │
│  Open: 0   Resolved (30d): 3   Last: 2h ago             │
│  "Character's guild note changed to a different player"  │
│                                        [Run Scan] [Fix All] │
│                                                          │
│  [orphan_wow]     Unlinked WoW Character  👤 Manual     │
│  Open: 12  Resolved (30d): 8   Last: 1h ago             │
│  "Character in guild with no player record"              │
│                                        [Run Scan] [Attempt Fix] │
│  ...                                                     │
└─────────────────────────────────────────────────────────┘
│  RECENT FINDINGS (last 50)                               │
│  [warning] orphan_wow   'Dashdashdash' has no player link    2h ago │
│  [warning] note_mismatch 'Rivermane' note changed Elrek→Mito 3h ago │
│  ...                                                     │
└─────────────────────────────────────────────────────────┘
```

API endpoints (admin-only):
- `POST /api/v1/admin/data-quality/scan` — run all detection rules now
- `POST /api/v1/admin/data-quality/scan/{issue_type}` — run one rule's detection
- `POST /api/v1/admin/data-quality/fix/{issue_id}` — run mitigation for a specific issue
- `POST /api/v1/admin/data-quality/fix-all/{issue_type}` — run mitigation for all open issues of a type

---

## What Does NOT Change

- `audit_issues` table schema — no migration needed
- `run_matching()` still exists — moved to admin-only trigger
- The integrity checker's detection logic — refactored, not rewritten
- The companion app / addon pipeline — unchanged

---

## What We Remove / Stop Doing

- `relink_note_changed_characters()` in `identity_engine.py` — replaced by `mitigate_note_mismatch()`
- `run_matching()` call from the addon upload scheduler pipeline
- Stub player creation during automated runs (stubs may still be created if admin manually triggers a full match)

---

## Scheduler Pipeline After This Phase

```
Addon upload arrives
  └─ sync_addon_data()           [writes notes, logs note_mismatch issues]
  └─ run_integrity_check()       [detects orphans, role mismatches, etc.]
  └─ run_auto_mitigations()      [fixes note_mismatch issues automatically]
  └─ send_sync_summary()         [Discord report if notable]

Blizzard sync (4x/day)
  └─ sync_blizzard_roster()      [update characters]
  └─ run_integrity_check()       [detect new issues]
  └─ run_auto_mitigations()      [fix what can be auto-fixed]
  └─ send_sync_summary()

Discord sync (every 15 min)
  └─ sync_discord_members()      [update discord_users]
  └─ run_integrity_check()       [detect new issues — especially role_mismatch]
  └─ run_auto_mitigations()

Admin: Manual full match
  └─ run_matching()              [admin-only, creates players with Discord links]
  └─ run_integrity_check()
  └─ send_sync_summary()
```

---

## Execution Order

1. Create `rules.py` — registry dataclass + all 5 rule definitions (no mitigation fns yet)
2. Refactor `integrity_checker.py` — named detect functions, keep behavior identical
3. Create `mitigations.py` — implement `mitigate_note_mismatch()` first (most urgent)
4. Update `db_sync.py` — log `note_mismatch` issues instead of returning IDs
5. Update `scheduler.py` — new pipeline, add `run_auto_mitigations()`, remove `run_matching()` from addon path
6. Add admin route + template — data quality page with rule stats and recent findings
7. Wire API endpoints for manual scan/fix triggers
8. Update INDEX.md and CLAUDE.md

---

## Testing

- Unit test each `detect_<rule>()` function with fixture data
- Unit test each `mitigate_<rule>()` function — verify it links/unlinks correctly and marks issue resolved
- Integration test: full addon upload pipeline → note change → auto-mitigation fires → issue resolved
- Admin page: loads without error, rule stats are accurate
- Verify `run_matching()` is NOT called during normal addon/blizzard/discord sync cycles

---

## Definition of Done

- [ ] Rules registry exists; all 5 rules defined
- [ ] Integrity checker refactored into named detect functions
- [ ] `note_mismatch` mitigation implemented and auto-running
- [ ] Addon upload pipeline no longer calls `run_matching()`
- [ ] Admin "Data Quality" tab shows rules, open issue counts, recent findings
- [ ] Admin can manually trigger scan or fix for any rule
- [ ] Audit log resolves issues when mitigations succeed
- [ ] All existing integrity checker tests still pass
- [ ] CLAUDE.md updated
