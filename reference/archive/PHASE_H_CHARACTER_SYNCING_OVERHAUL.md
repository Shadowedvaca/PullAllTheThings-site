# Phase H — Character Syncing Overhaul

## Goal

Blizzard's Battle.net OAuth does not issue refresh tokens. Access tokens expire after 24 hours
and cannot be renewed without user action. The existing system treated this as an error condition,
firing nightly Discord alerts. This phase realigns the platform with how Blizzard OAuth actually
works and improves the character-linking UX so users get maximum value from their one-time
24-hour authorization window.

## Key Decisions

- **`in_guild` boolean on `wow_characters`** — distinguishes guild roster characters (TRUE, all
  existing rows) from Battle.net-discovered characters not yet in the guild (FALSE). Default TRUE
  so existing data is unaffected. All site display/sync queries filter `in_guild = TRUE`. Only
  admin audit and raw sync operations see all rows.
- **Capture all BNet chars at link time** — `sync_bnet_characters` now upserts every character
  level 10+ regardless of realm. New rows created with `in_guild = FALSE`. If the char already
  exists in the guild roster, its `in_guild` value is preserved (stays TRUE).
- **Guild roster sync flips `in_guild = TRUE`** — when a char appears in the Blizzard guild
  roster, `db_sync.py` sets `in_guild = TRUE`. No flip back on removal — `removed_at` handles that.
- **`bnet_token_expired` severity → `"info"`** — token expiry is expected behaviour, not an
  error. Info-level errors are already configured in Error Routing to skip Discord.
- **One smart "Refresh Characters" button** — handles all states (not linked, token valid, token
  expired) transparently. User never thinks about token state.
- **`next` redirect param on OAuth** — stored in state cookie so the OAuth callback redirects
  the user back to whichever page initiated the flow.

## Sub-Phases

| Phase | Name | Key Changes | Migration |
|-------|------|-------------|-----------|
| H.1 | Migration + Query Updates | Add `in_guild` column; update all display/sync queries | Yes (0051) |
| H.2 | BNet Sync Overhaul | Capture all chars; silent token expiry; OAuth callback fixes | No |
| H.3 | API + My Characters Frontend | `POST /api/v1/me/bnet-sync`; Refresh button; out-of-guild section | No |
| H.4 | My Profile + Admin Users Frontend | Profile BNet section rework; Admin Users expired-token indicator | No |

## Branch & Version

- Branch: `feature/phase-h-character-sync`
- Version bump: MINOR (new feature, backward-compatible)
- Tag after H.4 complete and tested: `prod-vX.Y.0`

## Pre-conditions

- Current baseline: Phase F.3 complete, 922 tests pass, 69 skip, migration head 0050
- All sub-phases run sequentially on the same branch
- Each sub-phase ends with passing tests before the next begins

## Query Audit Summary

Queries that need `AND in_guild = TRUE` added (full detail in H.1 doc):

| File | Queries |
|------|---------|
| `guild_portal/api/guild_routes.py` | Roster, Raider.IO, parses leaderboard |
| `guild_portal/pages/public_pages.py` | Role distribution |
| `guild_portal/api/member_routes.py` | `/me/characters`, `/me/character/{id}/market`, `/me/character/{id}/parses` |
| `sv_common/guild_sync/crafting_service.py` | Crafter count, crafter details, recipe search |
| `guild_portal/pages/profile_pages.py` | Unclaimed character inventory |
| `guild_portal/pages/admin_pages.py` | Player Manager, progression sync status |
| `sv_common/guild_sync/progression_sync.py` | Progression and profession work lists |
| `sv_common/guild_sync/crafting_sync.py` | Profession work list |
| `sv_common/guild_sync/scheduler.py` | WCL character list |
| `sv_common/guild_sync/attendance_processor.py` | WCL attendee name lookup |
| `sv_common/guild_sync/discord_sync.py` | Highest rank lookup |
| `sv_common/guild_sync/mitigations.py` | Unlinked character pools |

Queries deliberately NOT filtered (admin/audit/sync see all chars):
- `integrity_checker.py` (all audit checks)
- `onboarding/conversation.py` and `deadline_checker.py` (char lookup by name for linking)
- `bnet_character_sync.py` (the sync itself)
- `db_sync.py` upsert/removal logic
- `guild_sync/api/routes.py` companion app endpoints
- `admin_pages.py` data quality raw counts (admin needs full picture)
