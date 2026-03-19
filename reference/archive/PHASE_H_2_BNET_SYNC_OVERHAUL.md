# Phase H.2 — BNet Sync Overhaul

## Context

**Project:** Pull All The Things Guild Platform (FastAPI / PostgreSQL / Jinja2)
**Branch:** `feature/phase-h-character-sync`
**Pre-condition:** Phase H.1 complete (migration 0051 applied, all queries updated)

Read `reference/phase-H.md` for the full phase overview and key decisions before starting.

This sub-phase reworks how Battle.net OAuth tokens and character sync work:
- All characters on a player's account are captured at link time (not just home-realm ones)
- Token expiry is treated as expected, not an error
- The OAuth callback properly cleans up old error records and supports `next` redirects

---

## Goals

1. `sync_bnet_characters` captures every character (level 10+) on the account, new chars get `in_guild = FALSE`
2. `bnet_token_expired` severity changed from `"warning"` to `"info"` everywhere
3. `run_bnet_character_refresh` silently skips players with expired tokens (no Discord ping, just info log)
4. OAuth callback calls `resolve_issue` for both identifier formats on success
5. OAuth flow supports a `?next=` redirect param so users return to the page they came from

---

## File 1: `src/sv_common/guild_sync/bnet_character_sync.py`

### Change A — Remove realm filter, set `in_guild = FALSE` for new chars

**Current logic (~line 224):**
```python
existing_char = await conn.fetchrow(
    """SELECT id FROM guild_identity.wow_characters
       WHERE character_name = $1 AND realm_slug = $2""",
    char_name, realm_slug,
)

if not existing_char and realm_slug != home_realm_slug:
    # Unknown character on a non-home realm — likely a toon on an unrelated server
    skipped += 1
    continue
```

**Replace with:** Remove the `if not existing_char and realm_slug != home_realm_slug` block entirely. Every character (level 10+) proceeds to upsert.

**Current upsert INSERT (~line 250):**
```python
char_row = await conn.fetchrow(
    """INSERT INTO guild_identity.wow_characters
       (character_name, realm_slug, level, class_id)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (character_name, realm_slug) DO UPDATE SET
           level = EXCLUDED.level,
           class_id = COALESCE(EXCLUDED.class_id,
                               guild_identity.wow_characters.class_id),
           removed_at = NULL
       RETURNING id""",
    char_name, realm_slug, level, class_id,
)
```

**Replace with** (add `in_guild = FALSE` on INSERT only, preserve existing on conflict):
```python
char_row = await conn.fetchrow(
    """INSERT INTO guild_identity.wow_characters
       (character_name, realm_slug, level, class_id, in_guild)
       VALUES ($1, $2, $3, $4, FALSE)
       ON CONFLICT (character_name, realm_slug) DO UPDATE SET
           level = EXCLUDED.level,
           class_id = COALESCE(EXCLUDED.class_id,
                               guild_identity.wow_characters.class_id),
           removed_at = NULL
           -- in_guild is intentionally NOT updated on conflict:
           -- if the char is already in the guild roster (TRUE), keep it TRUE
       RETURNING id""",
    char_name, realm_slug, level, class_id,
)
```

### Change B — Severity: `"warning"` → `"info"` in `_refresh_token`

**Around line 72** (no-refresh-token path):
```python
# Before:
await report_error(pool, "bnet_token_expired", "warning", ...)
# After:
await report_error(pool, "bnet_token_expired", "info", ...)
```

**Around line 121** (HTTP refresh failed path):
```python
# Before:
await report_error(pool, "bnet_token_expired", "warning", ...)
# After:
await report_error(pool, "bnet_token_expired", "info", ...)
```

---

## File 2: `src/sv_common/guild_sync/scheduler.py`

### Change — `run_bnet_character_refresh`: expired token is silent skip

**Current expired-token branch (~lines 644-665):**
```python
access_token = await get_valid_access_token(self.db_pool, player_id)
if access_token is None:
    logger.warning("Battle.net refresh: no valid token for %s — skipping", battletag)
    errors += 1
    result = await report_error(
        self.db_pool,
        "bnet_token_expired",
        "warning",
        f"Battle.net token expired for {battletag} — player must re-link ...",
        "scheduler",
        details={"player_id": player_id, "battletag": battletag},
        identifier=battletag,
    )
    await maybe_notify_discord(
        self.db_pool, self.discord_bot, self.audit_channel_id,
        "bnet_token_expired", "warning",
        f"Battle.net token expired for **{battletag}** — player must re-link.",
        result["is_first_occurrence"],
    )
    continue
```

**Replace with** (info log only, no error record, no Discord):
```python
access_token = await get_valid_access_token(self.db_pool, player_id)
if access_token is None:
    logger.info(
        "Battle.net refresh: token expired for %s — skipping until player re-links",
        battletag,
    )
    # Token expiry is expected (Blizzard tokens last 24h, no refresh tokens).
    # The player will re-sync via the Refresh Characters button when they next visit.
    continue
```

Also remove the `errors += 1` increment for this path — a skipped player due to expected
token expiry is not an error in the operational sense. You may want a separate `skipped`
counter for logging purposes.

**Update the summary log** at the end of the method to reflect the new skip count if added.

---

## File 3: `src/guild_portal/api/bnet_auth_routes.py`

### Change A — `resolve_issue` on successful OAuth link

After `await db.commit()` and before the sync call (~line 267), add:

```python
# Clear any open token-expired errors for this player (both identifier formats used historically)
from sv_common.errors import resolve_issue
pool = request.app.state.guild_sync_pool
await resolve_issue(pool, "bnet_token_expired", identifier=battletag)
await resolve_issue(pool, "bnet_token_expired", identifier=str(current_member.id))
```

### Change B — `next` redirect support

**In the authorize route** (`GET /auth/battlenet`, ~line 86):

Read an optional `next` query param and store it in the state cookie value (JSON-encode both):

```python
from urllib.parse import quote

next_url = request.query_params.get("next", "")
state = secrets.token_hex(16)

# Store state + next together in the cookie
import json
state_payload = json.dumps({"state": state, "next": next_url})

# ... build authorize_url using state only (not state_payload) ...
# The cookie holds the full payload; Blizzard just sees the state token
```

Update the cookie set call:
```python
response.set_cookie(
    key=_STATE_COOKIE,
    value=state_payload,   # was: value=state
    ...
)
```

**In the callback route** (`GET /auth/battlenet/callback`, ~line 117):

Parse the cookie to get both state and next:
```python
import json as _json

raw_cookie = request.cookies.get(_STATE_COOKIE, "")
try:
    cookie_data = _json.loads(raw_cookie)
    expected_state = cookie_data.get("state", "")
    next_url = cookie_data.get("next", "")
except (ValueError, AttributeError):
    # Backwards compat: cookie may be a plain state string (old format)
    expected_state = raw_cookie
    next_url = ""
```

**Replace the success redirect** at the bottom of the callback:
```python
# Before:
return RedirectResponse(url="/profile", status_code=302)

# After:
ALLOWED_NEXT_PATHS = {"/my-characters", "/profile", "/"}
redirect_to = next_url if next_url in ALLOWED_NEXT_PATHS else "/profile"
response = RedirectResponse(url=redirect_to, status_code=302)
response.delete_cookie(_STATE_COOKIE)
return response
```

Note: whitelist `next` values to prevent open redirect. Extend `ALLOWED_NEXT_PATHS` as needed.

---

## Seeded Error Routing Rule

The `bnet_token_expired` error type likely already has a routing rule in `common.error_routing`
seeded in migration 0049. Verify that rule exists with `dest_discord = FALSE` or that the
`first_only` + `enabled` flags are set to suppress Discord. Since we're changing severity to
`"info"` and info is already configured to skip Discord in Error Routing, this is a belt-and-
suspenders check. No migration change needed if the rule is already correct.

To verify on prod:
```sql
SELECT * FROM common.error_routing WHERE issue_type = 'bnet_token_expired';
```

---

## Tests

New unit tests to write (`tests/unit/test_bnet_sync.py` or similar):

1. **`test_sync_captures_non_home_realm_chars`** — mock a BNet profile response with chars on
   multiple realms; verify all level-10+ chars are upserted with `in_guild = FALSE`.

2. **`test_sync_preserves_in_guild_true_on_conflict`** — mock a char that already exists in
   `wow_characters` with `in_guild = TRUE`; verify the upsert does NOT flip it to FALSE.

3. **`test_scheduler_skips_expired_token_silently`** — mock `get_valid_access_token` returning
   None; verify no `report_error` call is made and no `maybe_notify_discord` call is made.

4. **`test_oauth_callback_resolves_errors`** — mock a successful OAuth callback; verify
   `resolve_issue` is called for both identifier formats.

5. **`test_oauth_callback_next_redirect`** — verify callback with `next=/my-characters` in
   state cookie redirects to `/my-characters`; verify unknown `next` values fall back to `/profile`.

Run: `.venv/Scripts/pytest tests/unit/ -v`
