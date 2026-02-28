# PATT Project Memory

## Server Access
- SSH: `ssh hetzner` (alias in ~/.ssh/config → root@5.78.114.224)
- App: `/opt/patt-platform/` — systemd unit `patt`, port 8100
- DB: `sudo -u postgres psql patt_db`
- .env: `/opt/patt-platform/.env`
- Deploy: git push to main → GitHub Actions auto-deploys (~30s)
- Migration: `cd /opt/patt-platform && .venv/bin/alembic upgrade head`
- Current migration: **0030** (head)

## Domain / Nginx
- **pullallthethings.com** (with s) — has SSL cert, proxies to :8100 ✓
- Always use `pullallthethings.com` for external URLs

## Key File Locations
- Admin page routes: `src/patt/pages/admin_pages.py`
- Admin API routes: `src/patt/api/admin_routes.py`
- Guild API routes: `src/patt/api/guild_routes.py`
- Admin templates: `src/patt/templates/admin/` (extend `base_admin.html`)
- Public templates: `src/patt/templates/public/`
- Base admin layout: `src/patt/templates/base_admin.html`
- Player Manager JS: `src/patt/static/js/players.js`
- Roster JS+HTML: `src/patt/templates/public/roster.html`
- Main CSS: `src/patt/static/css/main.css`
- Roster CSS: `src/patt/static/css/roster.css`
- Guild sync routes: `src/sv_common/guild_sync/api/routes.py`
- App config: `src/patt/config.py`
- DB models: `src/sv_common/db/models.py`

## Admin Layout Pattern
All admin pages extend `base_admin.html` (NOT `base.html`). Provides:
- Full-height left sidebar with collapsible toggle (localStorage state)
- `admin-content` div fills remaining width
- Active nav link: `{% block nav_campaigns %}active{% endblock %}` etc.
- No `max-width` constraint — full screen width

## Player Model (guild_identity.players) — key columns
- `on_raid_hiatus` BOOL — hides from public roster + availability-by-day grid
- `auto_invite_events` BOOL — player auto-sign-up pref
- `timezone` VARCHAR(50) — player timezone, defaults America/Chicago
- `crafting_notifications_enabled` BOOL
- `main_character_id`, `main_spec_id`, `offspec_character_id`, `offspec_spec_id`

## Player Manager (/admin/players)
Three-column drag-and-drop. Data from `/admin/players-data`.
- **Hiatus toggle**: checkbox on each card → `PATCH /admin/players/{id}/raid-hiatus`
- **Delete guard**: 409 if `website_user_id` set; JS shows "go to Admin → Users"
- **Alias chips**: `POST /admin/players/{id}/aliases`, `DELETE /admin/players/aliases/{alias_id}`
- `_compute_best_rank(db, player_id)` — called after link-discord and assign-character

## Public Roster (/roster)
- Composition tab: role cards use `mainRoster` (rank > 1 only); Wowhead URL excludes initiates
- **New Members box**: initiates (rank === 1) shown below role cards, grouped by role
- **Show Initiates checkbox**: default checked; unchecked hides rank-1 from Full Roster tab
- Hiatus players excluded entirely (filtered at API level in `guild_routes.py`)

## Availability
- `patt.player_availability`: int day_of_week (0=Mon), Time earliest_start, Numeric available_hours
- `/api/v1/admin/availability-by-day`: filters hiatus players via JOIN + `.where(Player.on_raid_hiatus.is_(False))`
- `/api/v1/guild/availability`: public endpoint (no hiatus filter — used for player's own settings)

## Blizzard API Sync
- Credentials: `BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET` in .env
- Manual trigger: `POST /api/guild-sync/blizzard/trigger`
- Audit channel: configured in Admin → Raid Tools (stored in `common.discord_config.audit_channel_id`)

## PATTSync Addon
- Companion app: `companion_app/patt_sync_watcher.py` (runs on Mike's gaming PC)
- Watches: `H:\World of Warcraft\_retail_\WTF\Account\SHADOWEDVACA2\SavedVariables\PATTSync.lua`
- Endpoint: `POST /api/guild-sync/addon-upload`
- In-game: `/pattsync` then `/reload`

## Raid Tools / Auto-Booking
- Raid-Helper config stored in `common.discord_config` (all channel IDs in DB, not .env)
- Auto-booking: `raid_booking_service.py` background loop, polls every 5 min
- Books next week's raid 10–20 min after current raid start time

## Screen Permissions (Settings nav)
- `common.screen_permissions` table — DB-driven nav visibility by min_rank_level
- `src/patt/nav.py` — `load_nav_items()`, `get_min_rank_for_screen()`
- Admin uses `_require_screen(screen_key, request, db)` helper

## Channel IDs
All stored in `common.discord_config`, configured via Admin UI — **never hardcoded in .env**.
- `audit_channel_id` — set in Admin → Raid Tools → Raid-Helper Configuration
- Crafters corner channel — set in Admin → Crafting Sync
- Raid channel, voice channel — set in Admin → Raid Tools

## Bot DM Toggle
Off by default. `common.discord_config.bot_dm_enabled`. Toggle in Admin → Bot Settings.
Feature flags: `feature_invite_dm`, `feature_onboarding_dm` (separate toggles).

## Test Suite
- 409 pass, 69 skip — `pytest tests/unit/ -v`
- Pre-existing skips: identity_engine import error, migrate_sheets legacy models, 1 bot DM gate test
- No DB needed for unit tests; `TEST_DATABASE_URL` needed for integration/regression

## Common Gotchas
- Admin cookie auth: `admin_pages.py` uses `_require_admin()`. API routes under `/api/v1/admin/` use Bearer token.
- `_require_admin` is an alias for `_require_screen("player_manager", ...)` (Officer+ level)
- Remote: `git remote` is named `main`, not `origin` — use `git push main main`
- CSS `--color-gold: #d4a84b` — use this for gold accents, not hardcoded hex
