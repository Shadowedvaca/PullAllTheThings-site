# Phase H.4 — My Profile + Admin Users Frontend

## Context

**Project:** Pull All The Things Guild Platform (FastAPI / PostgreSQL / Jinja2)
**Branch:** `feature/phase-h-character-sync`
**Pre-condition:** Phases H.1, H.2, and H.3 complete

Read `reference/phase-H.md` for the full phase overview and key decisions before starting.

This sub-phase reworks the Battle.net section on the My Profile page and adds an expired-token
indicator to the Admin Users tab. After this phase, the feature branch is ready for PR and release.

---

## Goals

1. My Profile — Battle.net section shows "Refresh Characters" + "Unlink" when linked (with note)
2. My Profile — "Link Battle.net" only when not linked (existing behaviour, unchanged)
3. My Profile — Refresh Characters uses the same smart `/api/v1/me/bnet-sync` endpoint as H.3
4. Admin Users tab — expired token shown as disabled sync button + red ✕ indicator + legend
5. CLAUDE.md and PHASE_HISTORY.md updated

---

## File 1: `src/guild_portal/pages/profile_pages.py`

### Change — Add token-expired flag to profile page context

Find the route that renders the profile/settings page. Add a check for token expiry:

```python
from datetime import datetime, timezone

# After fetching bnet_account (existing code):
bnet_token_expired = False
if bnet_account and bnet_account.token_expires_at:
    bnet_token_expired = bnet_account.token_expires_at <= datetime.now(timezone.utc)

return templates.TemplateResponse(
    "member/profile/settings.html",    # adjust to actual template path
    {
        ...,                           # existing context
        "bnet_account": bnet_account,
        "bnet_token_expired": bnet_token_expired,
    }
)
```

---

## File 2: Profile settings template (Battle.net section)

Find the template that renders the Battle.net section of My Profile. It is likely at
`src/guild_portal/templates/member/profile/settings.html` or similar — check the route
in `profile_pages.py` to confirm the exact path.

### Current structure (approximate):
```html
{% if bnet_account %}
  <!-- linked state: battletag display + unlink button -->
{% else %}
  <!-- unlinked state: link button -->
{% endif %}
```

### New structure:

```html
<section class="profile-section" id="bnet-section">
  <h2 class="profile-section__title">Battle.net</h2>

  {% if bnet_account %}
    <p class="profile-section__desc">
      Linked as <strong>{{ bnet_account.battletag }}</strong>.
    </p>
    <p class="bnet-note">
      Connecting with Battle.net lets us see your full character list for 24 hours.
      Use Refresh Characters any time to re-authorize and update your list.
    </p>
    <div class="bnet-actions">
      <button id="btn-profile-refresh-chars" class="btn btn--secondary">
        Refresh Characters
      </button>
      <button id="btn-profile-unlink"
              class="btn btn--danger btn--sm"
              data-confirm="Unlink your Battle.net account? Your character links will be kept.">
        Unlink
      </button>
    </div>
  {% else %}
    <p class="profile-section__desc">
      Link your Battle.net account to automatically discover your characters.
    </p>
    <a href="/auth/battlenet?next=/profile" class="btn btn--primary">
      Link Battle.net
    </a>
  {% endif %}
</section>
```

Notes:
- The "Unlink" button keeps its existing behaviour (calls `DELETE /api/v1/auth/battlenet`).
- `bnet_token_expired` is no longer needed as a template variable — the Refresh button
  handles all states transparently (same as My Characters). Remove from template context
  if desired to keep it clean.
- The `next=/profile` on the Link button ensures the OAuth flow returns to Profile.

### CSS additions (add to `main.css` or the profile-specific CSS):

```css
.bnet-note {
  font-size: 0.82rem;
  color: var(--color-text-muted);
  margin: 0.4rem 0 0.8rem;
  max-width: 50ch;
  line-height: 1.4;
}

.bnet-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  flex-wrap: wrap;
}
```

### JS for the Refresh button on Profile (inline script or profile JS file):

```javascript
const profileRefreshBtn = document.getElementById("btn-profile-refresh-chars");
if (profileRefreshBtn) {
  profileRefreshBtn.addEventListener("click", async () => {
    profileRefreshBtn.disabled = true;
    profileRefreshBtn.textContent = "Refreshing...";
    try {
      const resp = await fetch("/api/v1/me/bnet-sync?next=/profile", { method: "POST" });
      const data = await resp.json();
      if (data.redirect) {
        window.location.href = data.redirect;
      } else if (data.ok) {
        // Show a success message or just reload
        window.location.reload();
      }
    } catch {
      profileRefreshBtn.disabled = false;
      profileRefreshBtn.textContent = "Refresh Characters";
    }
  });
}
```

---

## File 3: `src/guild_portal/pages/admin_pages.py`

### Change — Add token-expired status to users list data

Find the route that renders the Admin Users page. The users list query needs to include
token expiry status per player. Add a joined query or a post-fetch lookup:

```python
from datetime import datetime, timezone

# After fetching the player list, build a set of player_ids with expired tokens:
now = datetime.now(timezone.utc)

bnet_rows = await db.execute(
    select(
        BattlenetAccount.player_id,
        BattlenetAccount.token_expires_at,
    ).where(BattlenetAccount.player_id.in_(player_ids))
)
bnet_status = {
    row.player_id: {
        "linked": True,
        "expired": row.token_expires_at is not None and row.token_expires_at <= now,
    }
    for row in bnet_rows
}

# Pass to template:
return templates.TemplateResponse(
    "admin/users.html",
    {
        ...,
        "bnet_status": bnet_status,
    }
)
```

If the users page already loads BNet status (check existing code), adapt rather than duplicate.

---

## File 4: Admin Users template

Find the template at `src/guild_portal/templates/admin/users.html` (or similar path confirmed
from the route).

### Change — Sync button per user row

Find the existing "Sync BNet Characters" button for each user row. Replace with:

```html
{% set bnet_info = bnet_status.get(player.id, {}) %}
{% if bnet_info.get("linked") %}
  {% if bnet_info.get("expired") %}
    <!-- Token expired: disabled button + indicator -->
    <button class="btn btn--sm btn--secondary" disabled title="Token expired — player must refresh">
      Sync Characters
    </button>
    <span class="bnet-expired-indicator" title="Token expired">&#x2715;</span>
  {% else %}
    <!-- Token valid: active button (existing behaviour) -->
    <button class="btn btn--sm btn--secondary js-bnet-sync" data-player-id="{{ player.id }}">
      Sync Characters
    </button>
  {% endif %}
{% else %}
  <!-- Not linked -->
  <span class="text-muted" style="font-size: 0.8rem;">Not linked</span>
{% endif %}
```

### Change — Add legend below the users table

```html
<p class="table-legend">
  <span class="bnet-expired-indicator">&#x2715;</span>
  Token expired — player must use "Refresh Characters" on their My Characters page to re-authorize.
</p>
```

### CSS additions (add to admin CSS or `main.css`):

```css
.bnet-expired-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.2rem;
  height: 1.2rem;
  background: var(--color-danger, #ef4444);
  color: #fff;
  border-radius: 50%;
  font-size: 0.7rem;
  font-weight: 700;
  vertical-align: middle;
  margin-left: 0.3rem;
  cursor: help;
}

.table-legend {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-top: 0.75rem;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
```

---

## CLAUDE.md Update

At the end of this phase, update CLAUDE.md:

- **Current Phase:** Phase H.4 complete — Character Syncing Overhaul
- **Last migration:** 0051 (Phase H.1 — `in_guild` column on `wow_characters`)
- **Last tag:** `prod-vX.Y.0`
- Update "What Exists" section to reflect:
  - `sync_bnet_characters` now captures all account characters (any realm), `in_guild = FALSE`
  - "Refresh Characters" button on My Characters and My Profile
  - Out-of-guild characters section on My Characters
  - Admin Users: expired-token indicator
  - `bnet_token_expired` is info-level, no Discord notification

---

## Final Checklist Before PR

- [ ] H.1: migration 0051 applies cleanly on dev and test
- [ ] H.1: all display queries filter `in_guild = TRUE`
- [ ] H.2: `sync_bnet_characters` upserts all chars; new chars get `in_guild = FALSE`
- [ ] H.2: `bnet_token_expired` severity is `"info"` everywhere
- [ ] H.2: scheduler skips expired tokens silently
- [ ] H.2: OAuth callback resolves errors + honours `next` param
- [ ] H.3: `POST /api/v1/me/bnet-sync` handles all three token states
- [ ] H.3: My Characters shows Refresh button, note, out-of-guild section
- [ ] H.4: My Profile BNet section shows Refresh + Unlink + note when linked
- [ ] H.4: Admin Users shows disabled button + ✕ + legend for expired tokens
- [ ] All tests pass
- [ ] CLAUDE.md updated

---

## Tests

1. **`test_profile_bnet_section_linked`** — profile page with linked BNet renders
   "Refresh Characters" and "Unlink" buttons; note text is present.

2. **`test_profile_bnet_section_unlinked`** — profile page without BNet renders
   "Link Battle.net" only.

3. **`test_admin_users_expired_token_indicator`** — users list with expired token renders
   disabled button and ✕ indicator; non-expired renders active button.

4. **`test_admin_users_not_linked`** — player without BNet shows "Not linked" text.

Run: `.venv/Scripts/pytest tests/unit/ -v`
