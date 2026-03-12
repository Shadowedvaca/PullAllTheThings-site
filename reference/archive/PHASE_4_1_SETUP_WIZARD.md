# Phase 4.1 — First-Run Setup Wizard

## Goal

Build a web-based setup wizard that walks a new guild leader through every configuration
step needed to get Guild Portal running. On first launch (no `site_config` row or
`setup_complete = FALSE`), all routes redirect to `/setup`. The wizard validates each
step before proceeding and creates the first admin account at the end.

---

## Prerequisites

- Phase 4.0 complete (site_config table, rank_wow_mapping, config_cache)
- Migration 0031 applied

---

## First-Run Detection Middleware

### File: `src/patt/app.py`

Add middleware that checks `get_site_config().get("setup_complete")`. If `FALSE` or no
config exists, redirect all requests to `/setup` (except `/setup/*`, `/static/*`, and
`/api/v1/setup/*`).

```python
@app.middleware("http")
async def setup_guard(request: Request, call_next):
    path = request.url.path
    if not get_site_config().get("setup_complete"):
        if not (path.startswith("/setup") or path.startswith("/static")):
            return RedirectResponse("/setup")
    return await call_next(request)
```

---

## Wizard Steps

### Step 1: Welcome

**Route:** `GET /setup`

Simple landing page explaining what Guild Portal is and what the wizard will configure.
No form fields. Just a "Let's Get Started" button → `/setup/guild-identity`.

### Step 2: Guild Identity

**Route:** `GET /setup/guild-identity`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Guild Name | text | Yes | e.g., "Pull All The Things" |
| Tagline | text | No | Shown on front page under guild name |
| Mission Statement | textarea | No | Shown on front page |
| Accent Color | color picker | Yes | Default: #d4a84b |
| Logo URL | text | No | Optional guild logo image URL |

**Save:** `POST /api/v1/setup/guild-identity` → upserts `common.site_config` row.

### Step 3: Discord Bot

**Route:** `GET /setup/discord`

Guided walkthrough with inline instructions (not screenshots — text steps with links):

1. "Open the Discord Developer Portal" → link opens in new tab
2. "Create a New Application and add a Bot"
3. "Enable Server Members Intent"
4. "Copy the bot token and paste it here"

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Bot Token | password | Yes | Validated server-side |

**Verify Button:** `POST /api/v1/setup/verify-discord-token`
- Server-side: attempt `httpx.get("https://discord.com/api/v10/users/@me", headers={"Authorization": f"Bot {token}"})`
- On success: return bot username, bot ID
- Generate invite URL with correct permissions: `https://discord.com/oauth2/authorize?client_id={bot_id}&permissions=268437504&scope=bot`
- Show: "Add the bot to your server using this link"

After bot is in server:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Discord Server | dropdown | Yes | Auto-populated from bot's guild list via `GET /api/v1/setup/discord-guilds` |

**Verify:** `POST /api/v1/setup/verify-discord-guild`
- Confirms bot can see the selected guild, returns member count and role list.
- Stores `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_ID` in `.env` or `common.discord_config`.

**Implementation Note:** Bot token and guild ID must persist across restarts. Two options:
1. Write to `.env` file (simple but requires file write permission)
2. Store encrypted in `common.discord_config` and load during lifespan

**Recommendation:** Store in `common.discord_config` with new columns `bot_token_encrypted`
and `guild_discord_id` (guild_discord_id column already exists). For the token, use Fernet
symmetric encryption with `JWT_SECRET_KEY` as the key derivation seed. This avoids writing
to `.env` and keeps secrets in the DB (which is already the pattern for other config).

Alternatively, keep it simple for v1: store the token in the existing `.env` file and just
validate it here. The setup wizard tells the user to restart after this step if using env
file approach.

**Decision: Use DB storage.** Simpler UX (no restart needed), consistent with how channel IDs
are already stored.

### Step 4: Blizzard API

**Route:** `GET /setup/blizzard`

Guided walkthrough:

1. "Go to the Blizzard Developer Portal" → link
2. "Create a new application"
3. "Copy your Client ID and Client Secret"

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Client ID | text | Yes | |
| Client Secret | password | Yes | |

**Verify:** `POST /api/v1/setup/verify-blizzard`
- Server-side: attempt OAuth2 token request with provided credentials
- On success: fetch guild roster using realm/guild from Step 2
- Return: guild name confirmation, member count, character count

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Realm | searchable dropdown | Yes | Populated from Blizzard realm list API |
| Guild Name | text | Yes | Verified against roster API |

**Store:** Blizzard credentials in `common.site_config` columns (new: `blizzard_client_id`,
`blizzard_client_secret_encrypted`) and realm/guild slugs in existing columns.

### Step 5: Rank Configuration

**Route:** `GET /setup/ranks`

Two-part configuration:

**Part A: Name Your Platform Ranks**

Show the 5-tier structure with editable name fields (pre-filled with defaults):

| Level | Default Name | Field |
|-------|-------------|-------|
| 5 | Guild Leader | text input |
| 4 | Officer | text input |
| 3 | Veteran | text input |
| 2 | Member | text input |
| 1 | Initiate | text input |

**Part B: Map to WoW In-Game Ranks**

Show the guild's in-game rank list (fetched from Blizzard roster data — each character has
a `rank` index 0–9). Display the unique rank indices found in the roster with sample
character names at each rank.

For each WoW rank index, a dropdown to map it to one of the 5 platform ranks:

```
WoW Rank 0 (Trogmoon, Rocketship) → [Guild Leader ▼]
WoW Rank 1 (AltChar, OtherOfficer) → [Officer ▼]
WoW Rank 2 (VetPlayer1, VetPlayer2) → [Veteran ▼]
WoW Rank 3 (Member1, Member2, ...) → [Member ▼]
WoW Rank 4 (NewGuy1, NewGuy2, ...) → [Initiate ▼]
WoW Rank 5 (BankAlt1) → [Initiate ▼]
```

**Save:** `POST /api/v1/setup/ranks`
- Updates `common.guild_ranks` names
- Inserts/updates `common.rank_wow_mapping` rows

### Step 6: Discord Role Mapping

**Route:** `GET /setup/discord-roles`

For each platform rank, a dropdown of Discord roles (fetched from the bot's guild):

```
Guild Leader → [@Guild Leader ▼]
Officer → [@Officers ▼]
Veteran → [@Veteran ▼]
Member → [@Member ▼]
Initiate → [@Initiate ▼]
```

**Save:** `POST /api/v1/setup/discord-roles`
- Updates `common.guild_ranks.discord_role_id` for each rank

### Step 7: Channel Assignment

**Route:** `GET /setup/channels`

Dropdowns for key Discord channels (populated from bot's channel list):

| Channel Purpose | Stored In | Required |
|----------------|-----------|----------|
| Audit Reports | `common.discord_config.audit_channel_id` | Recommended |
| Crafters Corner | `guild_identity.crafting_sync_config.crafters_corner_channel_id` | Optional |
| Raid Announcements | `common.discord_config.raid_announcement_channel_id` | Optional |

All optional — can be configured later in admin.

**Save:** `POST /api/v1/setup/channels`

### Step 8: Create Admin Account

**Route:** `GET /setup/admin-account`

| Field | Type | Required |
|-------|------|----------|
| Display Name | text | Yes |
| Discord Username | text | Yes (used for login) |
| Password | password | Yes (min 8 chars) |
| Confirm Password | password | Yes |

**Save:** `POST /api/v1/setup/create-admin`
- Creates `common.users` row (email = discord_username.lower())
- Creates `guild_identity.players` row with `guild_rank_id` = Guild Leader rank
- Links `players.website_user_id` to the new user
- If Discord username matches a bot-visible member, links `discord_user` automatically

### Step 9: Complete

**Route:** `GET /setup/complete`

- Sets `site_config.setup_complete = TRUE`
- Refreshes config cache
- Shows summary of configured values
- "Go to Admin Dashboard" button → `/admin/players`
- Triggers initial Blizzard sync in background

---

## New Files

| File | Purpose |
|------|---------|
| `src/patt/pages/setup_pages.py` | All GET routes for wizard steps |
| `src/patt/api/setup_routes.py` | All POST/verification API endpoints |
| `src/patt/templates/setup/base_setup.html` | Wizard layout (progress bar, step navigation) |
| `src/patt/templates/setup/welcome.html` | Step 1 |
| `src/patt/templates/setup/guild_identity.html` | Step 2 |
| `src/patt/templates/setup/discord.html` | Step 3 |
| `src/patt/templates/setup/blizzard.html` | Step 4 |
| `src/patt/templates/setup/ranks.html` | Step 5 |
| `src/patt/templates/setup/discord_roles.html` | Step 6 |
| `src/patt/templates/setup/channels.html` | Step 7 |
| `src/patt/templates/setup/admin_account.html` | Step 8 |
| `src/patt/templates/setup/complete.html` | Step 9 |
| `src/patt/static/js/setup.js` | Validation, verify buttons, step transitions |
| `src/patt/static/css/setup.css` | Wizard-specific styling |

---

## UI/UX Design

### Progress Bar

Horizontal step indicator at the top of every step page:

```
[1 ✓] — [2 ✓] — [3 ●] — [4 ○] — [5 ○] — [6 ○] — [7 ○] — [8 ○] — [9 ○]
 Welcome  Guild  Discord  Blizz  Ranks  Roles  Channels  Admin  Done
```

### Verify Buttons

Each credential step (Discord, Blizzard) has a "Verify" button that:
1. Shows a spinner while checking
2. On success: green checkmark + details ("Connected! Found guild X with Y members")
3. On failure: red error + helpful message ("Token invalid — make sure you copied the full token")
4. "Next" button only enables after successful verification

### Styling

Dark theme matching the existing admin pages. Use `base_setup.html` (not `base_admin.html`)
since there's no sidebar or login state during setup.

---

## Security

- Setup routes are only accessible when `setup_complete = FALSE`
- After setup completes, all `/setup/*` routes return 404
- Bot token and Blizzard secret stored encrypted in DB (Fernet with JWT_SECRET_KEY)
- Admin account password hashed with bcrypt (existing pattern)
- No setup data sent to any external service except Discord/Blizzard verification calls

---

## Tests

- Test setup guard middleware (redirects when not complete, passes when complete)
- Test Discord token verification endpoint (mock httpx)
- Test Blizzard credential verification endpoint (mock httpx)
- Test admin account creation (creates user + player + rank)
- Test rank mapping save/load
- Test setup_complete flag blocks re-entry
- All existing tests continue to pass (setup_complete = TRUE in test fixtures)

---

## Deliverables Checklist

- [ ] Setup guard middleware in app.py
- [ ] 9 setup page routes + templates
- [ ] 7 setup API endpoints (save + verify)
- [ ] Progress bar component
- [ ] Discord token verification (live API check)
- [ ] Blizzard credential verification (live API check)
- [ ] Realm search/selection
- [ ] Rank naming + WoW rank mapping UI
- [ ] Discord role mapping UI
- [ ] Channel assignment UI
- [ ] Admin account bootstrap
- [ ] Encrypted credential storage
- [ ] setup.js (validation, verify flow)
- [ ] setup.css (wizard styling)
- [ ] Tests
