# Phase 4.4.4 — Data Quality Simplification

## Goal

Audit every data quality rule, matching rule, and drift detection mechanism through one
lens: **is this solving a problem that OAuth makes irrelevant for verified players?**

Where the answer is yes, remove the code. Don't archive it, don't flag it, don't keep it
"in case" — delete it. The happy path is OAuth. For members who decline OAuth, the fallback
is a simple manual character add UI. The sophisticated fuzzy-matching machinery that existed
to compensate for data uncertainty is no longer earning its keep.

The data quality page shifts its purpose: from "try to figure out who owns what" to
"show who hasn't verified yet and flag real operational problems."

---

## Prerequisites

- Phase 4.4.2 complete (`battlenet_oauth` link source in use, Player Manager shows lock icons)
- Phase 4.4.3 complete (onboarding actively directing new members to OAuth)

---

## No New Migration

This phase is primarily code and UI changes. No new tables. If any DB columns added purely
to support matching heuristics are identified as fully obsolete, they can be dropped as a
cleanup migration — but only if they are confirmed to have no active reads.

---

## Rule Audit

### Rules to REMOVE

These rules exist to find answers that OAuth now provides directly. For verified players,
they are noise. For unverified players, they are effort spent on a problem the member
could solve in 10 seconds by clicking "Connect Battle.net."

| Rule / File | Why Remove |
|-------------|-----------|
| All fuzzy name-matching rules in `matching_rules/` | Matching by name similarity was a workaround for not knowing ownership. OAuth gives us ownership directly. |
| `link_contradicts_note` | The guild note said one thing, the link says another. For OAuth-verified links, the note doesn't matter. For unverified players, present the contradiction as a "please verify" prompt rather than a data quality issue. |
| `missing_character_link` (if it exists as a proactive rule) | Replace with a simple "unverified members" list on the data quality page. |
| Confidence-score-based re-matching logic | Once a link is `battlenet_oauth` confidence=1.0, re-running matching rules against it serves no purpose and can only produce noise. |

### Rules to KEEP (Unchanged)

These rules have nothing to do with character ownership and remain valid:

| Rule | Why Keep |
|------|---------|
| Discord rank sync | Blizzard guild rank → Discord role assignment is a separate concern. Still needed. |
| `duplicate_discord` | Two players pointing at the same Discord user ID. Still valid. |
| `stale_discord_link` | Player linked to a Discord user who is no longer in the server. Still valid. |
| `missing_discord` | Player record with no Discord user linked. Still valid. |
| Rank drift detection | Officers change ranks in-game; Discord roles need to follow. Still valid. |

### Rules to SIMPLIFY

| Rule | Current Behavior | Simplified Behavior |
|------|-----------------|-------------------|
| Any rule that fires for `battlenet_oauth` links | Fires indiscriminately | Add a guard: skip OAuth-verified links entirely |
| `link_contradicts_note` (for unverified players) | Fires as a data quality error | Demote to informational: show as a "please verify" suggestion, not an error |

---

## Task 1: Remove Fuzzy Matching Code

### Files to delete or gut:

Audit the `matching_rules/` package. For each rule module, ask:
1. Does it attempt to infer character ownership from indirect signals (names, notes, Discord handles)?
2. If yes → delete the rule class and remove it from the registry.

Keep only rules in `matching_rules/` that detect **operational problems** (rank drift,
duplicate Discord, stale links) rather than **ownership inference**.

Update the rule registry in `matching_rules/__init__.py` to remove deleted rules.
Update the admin data quality page to no longer reference removed rule names.

---

## Task 2: Update Data Quality Page

### File: `src/guild_portal/templates/admin/data_quality.html`
### File: `src/guild_portal/pages/admin_pages.py`

**Remove from the page:**
- Unmatched characters table (was: "characters with no player match" — now irrelevant)
- Confidence score breakdown (was: visualizing matching uncertainty — now irrelevant)
- Rule stats for deleted rules

**Add to the page:**
- **OAuth Coverage** section at the top:
  ```
  Battle.net Verified:   23 / 31 members  (74%)
  ──────────────────────────────────────────────
  Unverified members (8):
    Rocketboom      No characters linked
    AltPlayer       2 manually-linked chars
    NewGuy          No characters linked
  ```
  Each unverified member has a button: "Send OAuth Reminder" (posts a DM via bot).

- **Verified character conflicts** (rare but important): if an OAuth sync displaces
  a character that was previously linked to a *different* player, flag it here for
  officer review.

**Keep on the page:**
- Discord/rank drift panel
- Duplicate Discord panel
- Stale Discord link panel
- Audit log section

---

## Task 3: Manual Character Add (User Self-Service)

Members who decline OAuth still need a way to add their characters. This should be
simple — not a clever matching system, just a form.

### File: `src/guild_portal/templates/settings/characters.html`

Add an "Add Character" section beneath the character list:

```
┌──────────────────────────────────────────────────────┐
│  Add a Character Manually                             │
│                                                      │
│  Character Name:  [________________]                  │
│  Realm:           [Sen'jin ▾]                        │
│                                                      │
│  [Add Character]                                     │
│                                                      │
│  Note: An officer will be able to see and verify     │
│  manually added characters.                          │
└──────────────────────────────────────────────────────┘
```

### New Route: `POST /api/v1/settings/characters`

```json
{ "character_name": "Trogmoon", "realm_slug": "senjin" }
```

1. Look up `wow_characters` by `(name, realm_slug)`.
   - If found: create `player_characters` with `link_source='manual_claim'`, `confidence=0.5`.
   - If not found: attempt a Blizzard API lookup (`/profile/wow/character/{realm}/{name}`)
     to confirm the character exists. If confirmed, create `wow_characters` entry, then link.
     If Blizzard returns 404, return a friendly error: "Character not found on Sen'jin."
2. Return the linked character data.

### `DELETE /api/v1/settings/characters/{character_id}`

Allows a user to remove a manually-linked character from their own profile.
Only works for `link_source = 'manual_claim'`. Attempting to delete an OAuth-linked
character returns 403: "Battle.net verified characters cannot be removed here.
Unlink your Battle.net account to remove them."

---

## Task 4: Player Manager Updates

### File: `src/guild_portal/templates/admin/players.html`
### File: `src/guild_portal/static/js/players.js`

**Simplify the Player Manager.** Its job is now exception handling, not primary workflow.

- Add a **verification badge** on player cards:
  - Gold shield icon: Battle.net verified (has `battlenet_oauth` links or `battlenet_accounts` row)
  - Grey person icon: Manual/unverified

- **Remove** the drag-based character linking UI for OAuth-verified players. Their
  characters are locked. Dragging is still available for unverified players' manual links.

- **Remove** any "run matching" or "suggest match" buttons that invoked the fuzzy
  matching rules. These served the old system.

- **Add** a "View Verification Status" filter: show only unverified players, or only
  players with no characters at all.

---

## Task 5: Drift Scanner Simplification

### File: `src/sv_common/guild_sync/drift_scanner.py`

The drift scanner was built to detect when indirect evidence contradicted recorded links.
With OAuth as the authoritative source:

- Skip all drift checks for characters with `link_source = 'battlenet_oauth'`. Blizzard
  said they own it. There is no drift.
- Keep drift checks for `manual_claim` and `guild_note` links — these can still go stale.
- Remove any drift rule that was based on fuzzy name comparison or note parsing.

---

## Task 6: Operational Documentation Update

### File: `docs/OPERATIONS.md`

Add a section: **Member Onboarding & Character Verification**

Document the new model clearly:
1. New member joins Discord → bot DMs them → they register + connect Battle.net → done
2. Member who declines OAuth → they manually add characters from Settings
3. Officers see verification status in Player Manager and data quality page
4. The fuzzy matching system has been retired — no manual "run matching" step needed

---

## What the Codebase Looks Like After This Phase

| Component | Before | After |
|-----------|--------|-------|
| `matching_rules/` package | 8+ rules, many inferential | Rules for operational issues only (rank drift, Discord) |
| `drift_scanner.py` | Checks all links | Skips OAuth-verified links entirely |
| Data quality page | Coverage dashboard, unmatched tables, confidence scores, rule stats | OAuth coverage panel, conflict alerts, operational rules only |
| Player Manager | Primary character linking workflow | Exception handler; OAuth chars locked |
| Settings / Characters | No user self-service linking | Manual add form + verification status |
| Onboarding | Ends at registration | Ends at OAuth complete (or acknowledged skip) |

---

## Tests

- Unit test data quality page: OAuth coverage section renders with correct counts
- Unit test manual character add: character found in DB → linked with `manual_claim`
- Unit test manual character add: character not in DB → Blizzard lookup → linked
- Unit test manual character add: Blizzard 404 → friendly error returned
- Unit test `DELETE /api/v1/settings/characters/{id}` — `manual_claim` deleted successfully
- Unit test `DELETE /api/v1/settings/characters/{id}` — `battlenet_oauth` returns 403
- Unit test drift scanner skips `battlenet_oauth` links
- Unit test removed rules are not in the rule registry (absence test)
- Unit test Player Manager API response includes `bnet_verified` field on player cards
- All existing tests pass (some will be deleted along with the rules they tested — that's correct)

---

## Deliverables Checklist

- [ ] Fuzzy name-matching rules removed from `matching_rules/`
- [ ] `link_contradicts_note` demoted to informational for unverified players
- [ ] Data quality page: OAuth coverage section added
- [ ] Data quality page: confidence/unmatched sections removed
- [ ] "Send OAuth Reminder" button on data quality page per unverified member
- [ ] Verified character conflict alerts on data quality page
- [ ] Manual character add form on Settings/Characters page
- [ ] `POST /api/v1/settings/characters` route (with Blizzard lookup fallback)
- [ ] `DELETE /api/v1/settings/characters/{id}` route (manual_claim only)
- [ ] Player Manager: verification badge on player cards
- [ ] Player Manager: "run matching" / "suggest match" buttons removed
- [ ] Player Manager: unverified-only filter
- [ ] Drift scanner skips `battlenet_oauth` links
- [ ] `docs/OPERATIONS.md` updated with new onboarding model
- [ ] Tests (including deletion of tests for removed rules)
