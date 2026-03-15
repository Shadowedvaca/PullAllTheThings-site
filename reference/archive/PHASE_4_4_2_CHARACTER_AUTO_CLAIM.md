# Phase 4.4.2 — Character Auto-Claim on OAuth

## Goal

When a member successfully links their Battle.net account (Phase 4.4.1), immediately fetch
all WoW characters on their account via `/profile/user/wow` and auto-link those characters
to their player record. These links are **authoritative** — Blizzard told us directly who
owns these characters, so no matching heuristics apply.

OAuth-linked characters are immutable in the Player Manager UI — officers can see them
but cannot reassign them. Manual links remain possible for the rare member who won't use
OAuth, but they are visually distinct and carry no special protections.

---

## Prerequisites

- Phase 4.4.1 complete (`battlenet_accounts` table populated, OAuth flow working)
- `guild_identity.player_characters` with `link_source` and `confidence` columns (exists since Phase 3.0A)
- `guild_identity.wow_characters` table populated with existing characters

---

## No New Migration

This phase uses the existing `player_characters` bridge table with a new `link_source`
value: `'battlenet_oauth'`. No schema changes required.

**New `link_source` value:** `'battlenet_oauth'`
**Confidence value:** `1.0` (maximum — Blizzard confirmed ownership directly)

Existing valid `link_source` values and their new hierarchy:

| link_source | Confidence | Mutability |
|-------------|-----------|-----------|
| `battlenet_oauth` | 1.0 | Immutable (UI-locked) |
| `blizzard_guild` | 0.8 | Reassignable by officer |
| `manual_claim` | 0.5 | Reassignable by officer |
| `guild_note` | 0.6 | Reassignable by officer |
| `addon` | 0.7 | Reassignable by officer |

---

## Blizzard Profile API

### Endpoint: `GET /profile/user/wow`

```
Authorization: Bearer {access_token}
Blizzard-Namespace: profile-us
Accept: application/json
```

Response shape (abbreviated):
```json
{
  "wow_accounts": [
    {
      "id": 12345,
      "characters": [
        {
          "character": { "href": "https://..." },
          "protected_character": { "href": "https://..." },
          "name": "Trogmoon",
          "id": 98765432,
          "realm": { "key": {}, "name": "Sen'jin", "id": 1559, "slug": "senjin" },
          "playable_class": { "key": {}, "name": "Druid", "id": 11 },
          "playable_race": { "key": {}, "name": "Night Elf", "id": 4 },
          "gender": { "type": "MALE", "name": "Male" },
          "faction": { "type": "ALLIANCE", "name": "Alliance" },
          "level": 80,
          "is_ghost": false
        }
      ]
    }
  ]
}
```

A Battle.net account can have multiple WoW accounts (rare, but possible for players who
bought multiple copies of the game). Iterate over all `wow_accounts` and all `characters`
within each.

---

## Task 1: Character Sync Function

### File: `src/sv_common/guild_sync/bnet_character_sync.py` (new file)

```python
"""
Battle.net character sync.

Fetches the character list from /profile/user/wow using a player's stored OAuth
access token, then creates or updates player_characters links for all characters
on the guild's home realm.
"""

async def sync_bnet_characters(pool, player_id: int, access_token: str) -> dict:
    """
    Fetch the character list for a player's Battle.net account and upsert
    player_characters entries with link_source='battlenet_oauth'.

    Returns: {"linked": int, "new_characters": int, "skipped": int}
    """
    # 1. Fetch character list from Blizzard
    # 2. Filter: home realm (from site_config) + level >= 10
    # 3. For each character:
    #    a. Upsert into wow_characters (create if not exists)
    #    b. Upsert into player_characters (link_source='battlenet_oauth', confidence=1.0)
    # 4. Update battlenet_accounts.last_character_sync = NOW()
    # 5. Return stats
```

#### Filtering Rules

- **Realm filter:** Only characters whose `realm.slug` matches `site_config.home_realm_slug`.
  Characters on other realms are ignored.
- **Level filter:** Only characters at level 10 or above. Filters out trial characters
  and level-1 bank alts.
- **Faction filter:** No filter — include both factions (cross-faction guilds exist).

#### Upsert Logic for `wow_characters`

```sql
INSERT INTO guild_identity.wow_characters
    (name, realm_slug, realm_id, class_id, race_id, level, faction, blizzard_character_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (name, realm_slug) DO UPDATE SET
    level = EXCLUDED.level,
    class_id = EXCLUDED.class_id,
    blizzard_character_id = EXCLUDED.blizzard_character_id,
    updated_at = NOW()
RETURNING id
```

#### Upsert Logic for `player_characters`

```sql
INSERT INTO guild_identity.player_characters
    (player_id, character_id, link_source, confidence, linked_at)
VALUES ($1, $2, 'battlenet_oauth', 1.0, NOW())
ON CONFLICT (player_id, character_id) DO UPDATE SET
    link_source = 'battlenet_oauth',
    confidence = 1.0,
    linked_at = NOW()
```

Any existing `player_characters` row for this character (regardless of previous
`link_source`) is upgraded to `battlenet_oauth` with confidence 1.0.

**If a character is already linked to a different player** via a non-OAuth source
(e.g., an officer manually assigned it to the wrong person): keep the OAuth assignment.
The old link is replaced. Log a warning so the officer can investigate.

---

## Task 2: Token Access Helper

### File: `src/sv_common/guild_sync/bnet_character_sync.py`

Add a helper to retrieve and (if needed) refresh the access token before calling the API:

```python
async def get_valid_access_token(pool, player_id: int) -> str | None:
    """
    Return a valid access token for the player. Refreshes if expired.
    Returns None if the account is not linked or the token cannot be refreshed.
    """
    # 1. Query battlenet_accounts for the player
    # 2. Decrypt access_token_encrypted
    # 3. If token_expires_at is in the past and refresh_token available:
    #    - POST to https://oauth.battle.net/token with grant_type=refresh_token
    #    - Encrypt and store new tokens
    #    - Update last_refreshed
    # 4. Return decrypted access token
```

Token refresh uses the same Blizzard client credentials (client_id + client_secret from
site_config) as Basic auth, plus the stored refresh token.

---

## Task 3: Wire into OAuth Callback

### File: `src/guild_portal/api/bnet_auth_routes.py`

In the callback handler (after storing tokens in `battlenet_accounts`), call
`sync_bnet_characters()` immediately so the user sees their characters linked without
any delay:

```python
# After upsert into battlenet_accounts:
sync_stats = await sync_bnet_characters(pool, player_id, access_token)
flash_message = (
    f"Battle.net linked! Found {sync_stats['linked']} characters on Sen'jin."
)
```

---

## Task 4: Player Manager UI — Lock OAuth Links

### File: `src/guild_portal/static/js/players.js`

OAuth-linked characters must be visually distinct and non-draggable.

When the Player Manager loads character data, the API should include `link_source` for
each `player_characters` entry. Characters with `link_source = 'battlenet_oauth'` get:

- A gold lock icon (`🔒`) next to the character name
- `draggable="false"` on the card element
- A tooltip: "Linked via Battle.net — cannot be manually reassigned"
- The remove/reassign option is hidden from the context menu

### File: `src/guild_portal/pages/admin_pages.py`

Update `players-data` endpoint to include `link_source` in each character dict.

---

## Task 5: Scheduler — Periodic Refresh

Characters can level up, change specs, or be deleted. The OAuth token also ages.
Add a daily re-sync job for all players who have linked Battle.net accounts.

### File: `src/sv_common/guild_sync/scheduler.py`

```python
async def run_bnet_character_refresh(self):
    """
    Daily refresh of Battle.net character lists for all linked players.
    Runs at 3:00 AM UTC, after nightly Blizzard sync.
    """
    # 1. Query all players with battlenet_accounts rows
    # 2. For each: get_valid_access_token() → sync_bnet_characters()
    # 3. Log summary: players refreshed, new chars found, tokens refreshed
```

```python
scheduler.add_job(self.run_bnet_character_refresh, "cron", hour=3, minute=0)
```

---

## Task 6: Settings Page — Character Display

### File: `src/guild_portal/templates/settings/characters.html`

Update the character list on the user's Settings page to show:

- A **lock badge** on each OAuth-claimed character: `🔒 Battle.net Verified`
- Manual characters show a plain label (no badge, or a faint "Manually Added" label)
- The "Remove" button is hidden for OAuth characters (they can only be removed by
  unlinking Battle.net entirely, or by an officer)

---

## Handling the "OAuth Refusal" Case

Members who do not connect Battle.net can still manually add characters via Settings.
See Phase 4.4.4 for the simplified manual claim UI. The key principle:

- Manual claims use `link_source = 'manual_claim'`, `confidence = 0.5`
- They are fully reassignable by officers
- They appear visually distinct from OAuth-verified characters
- No automated re-matching, no fuzzy logic, no retry loops

---

## Tests

- Unit test `sync_bnet_characters()` with mock Blizzard response — correct realm/level filtering
- Unit test `sync_bnet_characters()` — new character created in `wow_characters` when not present
- Unit test `sync_bnet_characters()` — existing character upgraded from `guild_note` to `battlenet_oauth`
- Unit test `sync_bnet_characters()` — character on wrong realm is skipped
- Unit test `sync_bnet_characters()` — level < 10 is skipped
- Unit test `get_valid_access_token()` — valid token returned without refresh
- Unit test `get_valid_access_token()` — expired token triggers refresh; new tokens stored
- Unit test `get_valid_access_token()` — no refresh token available → returns None
- Unit test Player Manager API response includes `link_source` per character
- Unit test OAuth-linked character cards are non-draggable in JS (verify attribute set)
- All existing tests pass

---

## Deliverables Checklist

- [ ] `bnet_character_sync.py` — `sync_bnet_characters()` + `get_valid_access_token()`
- [ ] Realm + level filtering applied to Blizzard profile API response
- [ ] `wow_characters` upsert (creates new chars as needed)
- [ ] `player_characters` upsert with `link_source='battlenet_oauth'`, `confidence=1.0`
- [ ] OAuth callback updated to call `sync_bnet_characters()` inline
- [ ] Player Manager API includes `link_source` in character dicts
- [ ] Player Manager UI: lock icon + non-draggable for `battlenet_oauth` characters
- [ ] Settings character list: verification badge for OAuth chars
- [ ] Scheduler: daily `run_bnet_character_refresh()` at 3:00 AM UTC
- [ ] Warning logged when OAuth claim displaces a different player's existing link
- [ ] Tests
