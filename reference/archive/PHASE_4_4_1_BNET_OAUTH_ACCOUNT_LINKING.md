# Phase 4.4.1 — Battle.net OAuth Account Linking

## Goal

Add Battle.net OAuth2 as a linkable identity to an existing PATT account. Members click
"Connect Battle.net" once, approve the request on Blizzard's own page, and we store a
verified connection between their site account and their Blizzard account. No further
character matching work is needed after this — Phase 4.4.2 handles auto-claim.

This phase is purely the OAuth plumbing: the flow, the token storage, the settings UI,
and the env variable for a dedicated encryption key. No character data is touched here.

---

## Prerequisites

- Phase 4.0 complete (site_config, config_cache)
- Phase 4.1 complete (setup wizard — Blizzard client_id/secret already stored in DB)
- A redirect URI registered in the Blizzard developer portal:
  `{APP_URL}/auth/battlenet/callback`
  Must be registered for ALL environments (prod, test, dev have different domains).

---

## New Environment Variable

```bash
# .env — dedicated encryption key for Battle.net OAuth tokens
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
BNET_TOKEN_ENCRYPTION_KEY=your-fernet-key-here
```

**Why a separate key:**
- `JWT_SECRET_KEY` signs auth sessions. `BNET_TOKEN_ENCRYPTION_KEY` encrypts stored OAuth tokens.
- These are different operations with different security lifecycles.
- Rotating `JWT_SECRET_KEY` (e.g., after a breach) invalidates auth sessions — expected.
  Rotating `BNET_TOKEN_ENCRYPTION_KEY` only invalidates stored OAuth tokens — users re-link once.
- Entangling them means a key rotation that should only affect one destroys both.

This key must be added to `.env.template`, to GitHub Secrets (for all three environments),
and documented in `docs/OPERATIONS.md`.

---

## Database Migration: 0037_battlenet_accounts

### New Table: `guild_identity.battlenet_accounts`

```sql
CREATE TABLE guild_identity.battlenet_accounts (
    id                      SERIAL PRIMARY KEY,
    player_id               INTEGER NOT NULL UNIQUE
                                REFERENCES guild_identity.players(id) ON DELETE CASCADE,
    bnet_id                 VARCHAR(50) NOT NULL UNIQUE,   -- Blizzard's internal account ID (from /userinfo)
    battletag               VARCHAR(100) NOT NULL,         -- e.g., "Trog#1234"
    access_token_encrypted  TEXT NOT NULL,                 -- Fernet-encrypted with BNET_TOKEN_ENCRYPTION_KEY
    refresh_token_encrypted TEXT,                          -- Fernet-encrypted; NULL if Blizzard doesn't issue one
    token_expires_at        TIMESTAMP,                     -- UTC expiry of access token
    linked_at               TIMESTAMP NOT NULL DEFAULT NOW(),
    last_refreshed          TIMESTAMP,                     -- When tokens were last refreshed
    last_character_sync     TIMESTAMP                      -- When we last fetched character list (Phase 4.4.2)
);
CREATE INDEX idx_bnet_player ON guild_identity.battlenet_accounts(player_id);
```

**One row per player.** A player can only link one Battle.net account. If they re-link
(e.g., after unlinking), the row is upserted — same `bnet_id` required. Attempting to
link a `bnet_id` already claimed by a different player returns a 409 error.

---

## Blizzard OAuth2 Details

| Detail | Value |
|--------|-------|
| Authorization URL | `https://oauth.battle.net/authorize` |
| Token URL | `https://oauth.battle.net/token` |
| User Info URL | `https://oauth.battle.net/userinfo` |
| Scopes | `openid wow.profile` |
| Grant Type | Authorization Code |
| Client Credentials | Reuse existing `site_config.blizzard_client_id` and `site_config.blizzard_client_secret_encrypted` |

**`openid`** — provides `sub` (Blizzard's stable account ID) and `battletag` from the userinfo endpoint.
**`wow.profile`** — grants access to `/profile/user/wow` (character list). Used in Phase 4.4.2.

---

## Task 1: OAuth Crypto Helper

### File: `src/sv_common/crypto.py` (extend existing)

Add helpers for the dedicated BNET key:

```python
import os
from cryptography.fernet import Fernet

def get_bnet_fernet() -> Fernet:
    """Return a Fernet instance keyed with BNET_TOKEN_ENCRYPTION_KEY."""
    key = os.environ.get("BNET_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("BNET_TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)

def encrypt_bnet_token(token: str) -> str:
    return get_bnet_fernet().encrypt(token.encode()).decode()

def decrypt_bnet_token(encrypted: str) -> str:
    return get_bnet_fernet().decrypt(encrypted.encode()).decode()
```

---

## Task 2: OAuth Routes

### New File: `src/guild_portal/api/bnet_auth_routes.py`

```python
"""
Battle.net OAuth2 routes.

GET  /auth/battlenet           — Redirect user to Blizzard authorization page
GET  /auth/battlenet/callback  — Exchange code for tokens, store, redirect to settings
DELETE /api/v1/auth/battlenet  — Unlink Battle.net account (removes tokens + character links)
"""
```

#### `GET /auth/battlenet`

1. Load `blizzard_client_id` from `site_config` (via config_cache).
2. Generate a cryptographically random `state` string (16 bytes, hex-encoded).
3. Store `state` in a short-lived signed cookie (`bnet_oauth_state`, 10-minute expiry).
4. Redirect to:
   ```
   https://oauth.battle.net/authorize
     ?client_id={client_id}
     &redirect_uri={APP_URL}/auth/battlenet/callback
     &response_type=code
     &scope=openid+wow.profile
     &state={state}
   ```

**Guard:** User must be logged in (valid session cookie). If not, redirect to login first,
with a `?next=/auth/battlenet` param so they're sent here after login.

#### `GET /auth/battlenet/callback`

1. Validate `state` param matches the signed cookie. If mismatch → 400 error page.
2. Check for `error` param from Blizzard (user denied) → redirect to settings with
   a "Connection cancelled" flash message.
3. Exchange `code` for tokens via POST to `https://oauth.battle.net/token`:
   - `grant_type=authorization_code`
   - `code={code}`
   - `redirect_uri={APP_URL}/auth/battlenet/callback`
   - Basic auth: `(client_id, client_secret)`
4. Call `https://oauth.battle.net/userinfo` with the access token to get `sub` (bnet_id)
   and `battletag`.
5. Check if `bnet_id` is already linked to a *different* player → 409 error page with
   message: "This Battle.net account is already linked to another guild member."
6. Encrypt both tokens with `BNET_TOKEN_ENCRYPTION_KEY`.
7. Upsert into `guild_identity.battlenet_accounts`.
8. Trigger character sync immediately (Phase 4.4.2 function, called inline here).
9. Clear the state cookie. Redirect to `/settings/characters` with a success flash.

#### `DELETE /api/v1/auth/battlenet`

- Requires auth (logged-in user, own account only).
- Deletes the `battlenet_accounts` row for this player.
- Removes all `player_characters` entries with `link_source = 'battlenet_oauth'` for
  this player. Manual links are unaffected.
- Returns `{"ok": true}`.

---

## Task 3: Settings Page Update

### File: `src/guild_portal/templates/settings/characters.html` (or equivalent)

Add a "Battle.net Connection" section above the character list:

```
┌─────────────────────────────────────────────────────┐
│  Battle.net Account                                  │
│                                                      │
│  [Not Connected]                                     │
│                                                      │
│  Connect your Battle.net account to automatically    │
│  claim all your characters on Sen'jin.               │
│                                                      │
│  [Connect Battle.net]                                │
└─────────────────────────────────────────────────────┘
```

When connected:

```
┌─────────────────────────────────────────────────────┐
│  Battle.net Account                          ✓ Linked │
│                                                      │
│  Trog#1234  ·  Linked 2026-03-12                    │
│  12 characters found on Sen'jin                     │
│                                                      │
│  [Unlink]                                           │
└─────────────────────────────────────────────────────┘
```

The "Unlink" button posts `DELETE /api/v1/auth/battlenet` and reloads the page.
Show a confirmation modal before unlinking ("This will remove all auto-claimed characters
from your profile. They can be re-added manually or by re-linking.").

---

## Task 4: Config Update

### File: `src/guild_portal/config.py`

Add the new env var to the Settings model:

```python
bnet_token_encryption_key: str = ""
```

### File: `.env.template`

```bash
# Battle.net OAuth token encryption (separate from JWT key — see docs/OPERATIONS.md)
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
BNET_TOKEN_ENCRYPTION_KEY=
```

### File: `docs/OPERATIONS.md`

Add a section covering:
- What this key is for
- How to generate it
- What happens when you rotate it (tokens become unreadable → users must re-link once)
- How to rotate: generate new key → deploy → users get "please re-link" prompt on next visit

---

## Task 5: ORM Model

### File: `src/sv_common/db/models.py`

```python
class BattlenetAccount(Base):
    __tablename__ = "battlenet_accounts"
    __table_args__ = {"schema": "guild_identity"}

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("guild_identity.players.id"), nullable=False, unique=True)
    bnet_id = Column(String(50), nullable=False, unique=True)
    battletag = Column(String(100), nullable=False)
    access_token_encrypted = Column(Text, nullable=False)
    refresh_token_encrypted = Column(Text)
    token_expires_at = Column(DateTime)
    linked_at = Column(DateTime, nullable=False, server_default=func.now())
    last_refreshed = Column(DateTime)
    last_character_sync = Column(DateTime)

    player = relationship("Player", back_populates="battlenet_account")
```

---

## Error Handling

| Scenario | Behavior |
|----------|---------|
| User denies on Blizzard page | Redirect to settings with "Connection cancelled" flash |
| Blizzard API error during token exchange | Error page with "Battle.net connection failed. Please try again." |
| `bnet_id` already claimed by another player | Error page with "This Battle.net account is already linked to another member. Contact an officer." |
| `BNET_TOKEN_ENCRYPTION_KEY` not set at runtime | 500 on any OAuth route; logged as critical startup warning |
| Token refresh fails (refresh token expired or revoked) | Mark account as needs_relink in DB; show prompt on settings page |

---

## Tests

- Unit test `encrypt_bnet_token` / `decrypt_bnet_token` round-trip
- Unit test `GET /auth/battlenet` — verifies redirect URL contains correct params, state cookie set
- Unit test `GET /auth/battlenet/callback` — state mismatch returns 400
- Unit test callback — user denied (error param) → redirect with flash
- Unit test callback — successful flow with mock Blizzard responses → row upserted
- Unit test callback — `bnet_id` already claimed by different player → 409
- Unit test `DELETE /api/v1/auth/battlenet` — removes row, removes battlenet_oauth links
- Unit test settings page renders "Not Connected" state
- Unit test settings page renders "Connected" state with battletag
- All existing tests pass

---

## Deliverables Checklist

- [ ] Migration 0037 (`guild_identity.battlenet_accounts` table)
- [ ] ORM model `BattlenetAccount`
- [ ] `BNET_TOKEN_ENCRYPTION_KEY` env var — `.env.template`, `config.py`, `docs/OPERATIONS.md`
- [ ] `encrypt_bnet_token` / `decrypt_bnet_token` in `sv_common/crypto.py`
- [ ] `GET /auth/battlenet` route (state generation, redirect)
- [ ] `GET /auth/battlenet/callback` route (code exchange, userinfo fetch, upsert)
- [ ] `DELETE /api/v1/auth/battlenet` route (unlink)
- [ ] Settings page: Battle.net connection section (connected/not-connected states)
- [ ] Unlink confirmation modal
- [ ] GitHub Secrets updated for all 3 environments (`BNET_TOKEN_ENCRYPTION_KEY`)
- [ ] Tests
