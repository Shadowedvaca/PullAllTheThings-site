# Phase H.3 — API Endpoint + My Characters Frontend

## Context

**Project:** Pull All The Things Guild Platform (FastAPI / PostgreSQL / Jinja2)
**Branch:** `feature/phase-h-character-sync`
**Pre-condition:** Phases H.1 and H.2 complete

Read `reference/phase-H.md` for the full phase overview and key decisions before starting.

This sub-phase adds the smart `POST /api/v1/me/bnet-sync` endpoint and reworks the
My Characters page to include a Refresh Characters button and an out-of-guild characters section.

---

## Goals

1. `POST /api/v1/me/bnet-sync` — single endpoint that handles all token states transparently
2. `GET /api/v1/me/characters` updated to return out-of-guild chars separately
3. My Characters page gets a "Refresh Characters" button with explanatory note
4. My Characters page gets an "Out of Guild Characters" informational section at the bottom

---

## File 1: `src/guild_portal/api/member_routes.py`

### Change A — Update `GET /api/v1/me/characters` response

The current endpoint returns all of a player's linked characters. After H.1, a player may have
both `in_guild = TRUE` and `in_guild = FALSE` characters linked. The endpoint should return them
in two separate lists so the frontend can display them correctly.

Add a second query for out-of-guild chars and include them in the response:

```python
# Existing query (already filtered to in_guild = TRUE from H.1):
# ... returns guild_chars list ...

# New query for out-of-guild chars:
out_of_guild_rows = await db.execute(
    select(WowCharacter)
    .join(PlayerCharacter, PlayerCharacter.character_id == WowCharacter.id)
    .where(
        PlayerCharacter.player_id == current_player.id,
        WowCharacter.in_guild == False,
        WowCharacter.removed_at.is_(None),
    )
    .options(selectinload(WowCharacter.wow_class))
    .order_by(WowCharacter.character_name)
)
out_of_guild_chars = out_of_guild_rows.scalars().all()
```

Add to response:
```python
return {
    "ok": True,
    "data": {
        "characters": [...],                # existing in-guild list
        "out_of_guild_characters": [        # new
            {
                "id": c.id,
                "name": c.character_name,
                "realm": c.realm_slug,
                "level": c.level,
                "class": c.wow_class.name if c.wow_class else None,
            }
            for c in out_of_guild_chars
        ],
        "bnet_linked": ...,                 # existing flag
        "bnet_token_expired": ...,          # existing flag
    }
}
```

### Change B — New `POST /api/v1/me/bnet-sync` endpoint

Add after the existing `/me/characters` endpoint:

```python
@router.post("/bnet-sync")
async def member_bnet_sync(
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """
    Smart character refresh. Handles all three token states:
      - Not linked:     return redirect to /auth/battlenet
      - Token valid:    sync now, return stats
      - Token expired:  return redirect to /auth/battlenet
    The caller (JS) checks for a `redirect` field and navigates if present.
    The `next` query param is forwarded into the redirect URL so the OAuth
    callback returns the user to the page they came from.
    """
    from sv_common.guild_sync.bnet_character_sync import (
        get_valid_access_token,
        sync_bnet_characters,
    )

    pool = request.app.state.guild_sync_pool
    next_url = request.query_params.get("next", "/my-characters")

    # Validate next (prevent open redirect)
    ALLOWED_NEXT = {"/my-characters", "/profile", "/"}
    if next_url not in ALLOWED_NEXT:
        next_url = "/my-characters"

    # Check if BNet is linked
    bnet_row = await db.execute(
        select(BattlenetAccount).where(BattlenetAccount.player_id == current_player.id)
    )
    bnet_account = bnet_row.scalar_one_or_none()

    if not bnet_account:
        return JSONResponse({
            "ok": True,
            "redirect": f"/auth/battlenet?next={next_url}",
        })

    # Check if token is still valid
    access_token = await get_valid_access_token(pool, current_player.id)
    if access_token is None:
        return JSONResponse({
            "ok": True,
            "redirect": f"/auth/battlenet?next={next_url}",
        })

    # Token is valid — sync now
    stats = await sync_bnet_characters(pool, current_player.id, access_token)
    return JSONResponse({"ok": True, "data": stats})
```

Make sure `BattlenetAccount` is imported at the top of the file.

---

## File 2: `src/guild_portal/templates/member/my_characters.html`

### Change A — "Refresh Characters" button in page title area

Find the page title heading (something like `<h1>My Characters</h1>` or similar). Wrap the
title and button in a flex row:

```html
<div class="page-title-row">
  <h1>My Characters</h1>
  <div class="page-title-actions">
    <button id="btn-refresh-chars" class="btn btn--secondary btn--sm">
      Refresh Characters
    </button>
    <p class="refresh-note">
      Connecting with Battle.net lets us see your full character list for 24 hours.
    </p>
  </div>
</div>
```

The button is always rendered. The JS will handle what happens on click based on whether
BNet is linked and the token state (both available from the `/me/characters` API response).

### Change B — Out-of-guild characters section

Add below the existing character panel area (after all stat panels, before closing tags):

```html
{% if out_of_guild_characters %}
<section class="oog-section">
  <h2 class="oog-section__title">Your Characters Outside the Guild</h2>
  <p class="oog-section__note">
    These characters are on your Battle.net account but aren't in the guild yet.
    When you move one over, it will automatically appear in your character list above.
  </p>
  <div class="oog-grid" id="oog-grid">
    <!-- Populated by JS from API response -->
  </div>
</section>
{% endif %}
```

The section is initially hidden; JS populates it after the API call returns `out_of_guild_characters`.
Remove the Jinja2 conditional — let JS control visibility entirely so it works without a page reload
after a refresh.

Replace with:
```html
<section class="oog-section" id="oog-section" hidden>
  <h2 class="oog-section__title">Your Characters Outside the Guild</h2>
  <p class="oog-section__note">
    These characters are on your Battle.net account but aren't in the guild yet.
    When you move one over, it will automatically appear in your character list above.
  </p>
  <div class="oog-grid" id="oog-grid"></div>
</section>
```

---

## File 3: `src/guild_portal/static/js/my_characters.js`

### Change A — Handle `out_of_guild_characters` in API response

After the existing `fetch("/api/v1/me/characters")` call processes its response, add:

```javascript
function renderOutOfGuild(chars) {
  const section = document.getElementById("oog-section");
  const grid = document.getElementById("oog-grid");
  if (!chars || chars.length === 0) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  grid.innerHTML = chars.map(c => `
    <div class="oog-card">
      <span class="oog-card__name">${c.name}</span>
      <span class="oog-card__realm">${c.realm}</span>
      ${c.class ? `<span class="oog-card__class">${c.class}</span>` : ""}
      <span class="oog-card__level">Level ${c.level}</span>
    </div>
  `).join("");
}

// Call after fetching characters:
renderOutOfGuild(data.out_of_guild_characters || []);
```

Also store `bnet_token_expired` flag from the response for use by the refresh button.

### Change B — Refresh Characters button handler

```javascript
document.getElementById("btn-refresh-chars").addEventListener("click", async () => {
  const btn = document.getElementById("btn-refresh-chars");
  btn.disabled = true;
  btn.textContent = "Refreshing...";

  try {
    const resp = await fetch(
      `/api/v1/me/bnet-sync?next=${encodeURIComponent(window.location.pathname)}`,
      { method: "POST" }
    );
    const data = await resp.json();

    if (data.redirect) {
      // Not linked or token expired — go through OAuth
      window.location.href = data.redirect;
      return;
    }

    if (data.ok) {
      // Sync happened — reload character list
      window.location.reload();
    }
  } catch (err) {
    console.error("Character refresh failed:", err);
    btn.disabled = false;
    btn.textContent = "Refresh Characters";
  }
});
```

---

## File 4: `src/guild_portal/static/css/my_characters.css`

Add styles for the page title row, refresh button note, and out-of-guild section:

```css
/* Page title row */
.page-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.page-title-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.25rem;
}

.refresh-note {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  text-align: right;
  max-width: 28ch;
  line-height: 1.3;
  margin: 0;
}

/* Out-of-guild section */
.oog-section {
  margin-top: 2.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--color-border);
}

.oog-section__title {
  font-family: var(--font-display);
  font-size: 1.1rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.5rem;
}

.oog-section__note {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-bottom: 1rem;
}

.oog-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0.75rem;
}

.oog-card {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.75rem;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: 6px;
}

.oog-card__name {
  font-weight: 600;
  color: var(--color-text);
}

.oog-card__realm,
.oog-card__class,
.oog-card__level {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}
```

---

## Tests

New unit/integration tests:

1. **`test_bnet_sync_not_linked_returns_redirect`** — player has no `battlenet_accounts` row;
   verify endpoint returns `{"ok": true, "redirect": "/auth/battlenet?next=/my-characters"}`.

2. **`test_bnet_sync_expired_token_returns_redirect`** — `get_valid_access_token` mocked to
   return None; verify redirect response.

3. **`test_bnet_sync_valid_token_syncs`** — valid token; `sync_bnet_characters` mocked to
   return stats; verify `{"ok": true, "data": {...}}` response.

4. **`test_me_characters_includes_out_of_guild`** — player has both `in_guild = TRUE` and
   `in_guild = FALSE` chars linked; verify response has both lists populated correctly.

5. **`test_next_param_whitelist`** — unknown `next` values fall back to `/my-characters`.

Run: `.venv/Scripts/pytest tests/unit/ -v`
