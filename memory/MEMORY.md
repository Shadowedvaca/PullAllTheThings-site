# PATT Project Memory

## Server Access
- SSH: `ssh hetzner` (alias in ~/.ssh/config → root@5.78.114.224)
- App: `/opt/patt-platform/` — systemd unit `patt`, port 8100
- DB: `sudo -u postgres psql patt_db`
- .env: `/opt/patt-platform/.env`
- Deploy: git push to main → GitHub Actions auto-deploys (~30s)

## Domain / Nginx
- **pullallthethings.com** (with s) — has SSL cert, proxies to :8100 ✓
- pullallthething.com (no s) — HTTP only, temp conf, no SSL
- Always use `pullallthethings.com` for external URLs

## Key File Locations
- Admin page routes: `src/patt/pages/admin_pages.py`
- Admin templates: `src/patt/templates/admin/` (extend `base_admin.html`)
- Base admin layout: `src/patt/templates/base_admin.html` (full-height collapsible sidebar)
- Player Manager JS: `src/patt/static/js/players.js`
- Player Manager CSS: `src/patt/static/css/players.css`
- Main CSS: `src/patt/static/css/main.css`
- Guild sync routes: `src/sv_common/guild_sync/api/routes.py`
- App config: `src/patt/config.py`

## Admin Layout Pattern
All admin pages extend `base_admin.html` (NOT `base.html`). It provides:
- Full-height left sidebar with collapsible toggle (state saved to localStorage)
- `admin-content` div fills remaining width
- Active nav link set via `{% block nav_campaigns %}active{% endblock %}` etc.
- No `max-width` constraint — full screen width used

## Player Manager (/admin/players)
Three-column drag-and-drop interface. Data from `/admin/players-data`.

### Columns
1. **Discord** — live from bot (`guild.members`), shows rank from Discord roles
2. **Players** — `common.guild_members`, editable display name, delete, role icon
3. **Characters** — `common.characters` LEFT JOIN `guild_identity.wow_characters`

### Key features
- Drag Discord → Player to link `discord_id`
- Drag Character → Player to assign `member_id`
- Unlink drop zones for both
- Toggle Main/Alt per character
- **Drill-down** (◎ button on hover): click any item to filter all 3 columns to related items. Gold banner shows active filter with ✕ clear button.
- **? API badge** on characters not found in Blizzard scan (realm slug mismatch common cause)
- **Delete** button (✕) on ? API characters; 🗑 on player cards
- Display name fallback chain: player-set → Discord server name → main char name
- Role icon derived from: main char game role → Discord rank name

### Realm slug JOIN fix
`common.characters.realm` stores "Sen'jin" but `guild_identity.wow_characters.realm_slug` stores "senjin".
JOIN uses: `LOWER(REPLACE(c.realm, '''', ''))` to strip apostrophes.

### Endpoints (cookie auth, officer+)
- `GET /admin/players-data` — all data
- `POST /admin/players/create`
- `DELETE /admin/players/{id}` — uses raw SQL (bypass ORM cascade issue)
- `PATCH /admin/players/{id}/display-name`
- `PATCH /admin/players/{id}/link-discord`
- `PATCH /admin/characters/{id}/assign`
- `PATCH /admin/characters/{id}/main-alt`
- `DELETE /admin/characters/{id}`

## Blizzard API Sync
- Credentials in `/opt/patt-platform/.env`: `BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET`
- Synced 319 characters to `guild_identity.wow_characters`
- Imported 296 new chars to `common.characters` (member_id nullable — altered in prod)
- Scheduled sync: `GuildSyncScheduler` — needs `PATT_AUDIT_CHANNEL_ID` to start (not set yet)
- Manual trigger: `POST /api/guild-sync/blizzard/trigger`

## Addon Upload (patt_sync_watcher)
- Companion app: `companion_app/patt_sync_watcher.py` — runs on Mike's gaming PC
- Watches `H:\World of Warcraft\_retail_\WTF\Account\SHADOWEDVACA2\SavedVariables\PATTSync.lua`
- API key set: `PATT_API_KEY` in server .env and `companion_app/.env`
- Endpoint: `POST /api/guild-sync/addon-upload` — works without scheduler running
- WoW addon: `wow_addon/PATTSync/` — install to WoW AddOns folder
- Use `/pattsync` then `/reload` in WoW to export

## Database Schemas (Phase 2.7 — current)
- `common.*` — platform schema (guild_ranks, users, invite_codes, member_availability)
  - `common.guild_members` and `common.characters` are LEGACY — still in DB but removed from ORM/code
- `guild_identity.*` — guild sync schema (players, wow_characters, discord_users, player_characters, roles, classes, specializations, onboarding_sessions, audit_issues, sync_log)
- `patt.*` — app schema (campaigns, campaign_entries, votes, campaign_results, contest_agent_log, mito_quotes, mito_titles)

## Phase 2.7 Key Changes
- `GuildMember` model removed; replaced by `Player` (guild_identity.players)
- `Character` model removed; replaced by `WowCharacter` (guild_identity.wow_characters)
- Login stores `discord_username.lower()` as `User.email`; Player linked via `website_user_id`
- `Vote.player_id` (was member_id); `Campaign.created_by_player_id` (was created_by)
- `InviteCode.player_id` + `created_by_player_id` (was member_id)
- `role_sync.py` creates/updates `DiscordUser` records; updates linked `Player.guild_rank_id`
- Data migration script: `scripts/migrate_to_players.py` (run on prod to populate players table)
- Reference tables (roles, classes, specializations) seeded in migration 0007

## Phase 2.5 (Revised) — Guild Sync Updated (complete)
- All guild_sync modules now use Phase 2.7 schema (players/discord_users/player_characters)
- identity_engine, integrity_checker, reporter, scheduler, db_sync, api/routes all updated
- audit_issues dropped columns: person_id, first_detected, last_detected, resolution_note
- wow_characters actual column names: `last_login_timestamp` (BigInt ms), `guild_note`, `officer_note`, `blizzard_last_sync`, `realm_name`
- db_sync rank lookup: uses RANK_NAME_MAP → name → common.guild_ranks.id (not level int)
- 207 unit tests pass, 69 skip; committed `925090a`

## Phase 2.6 (Revised) — Onboarding Updated + Bot DM Toggle (complete)
- All onboarding modules updated for player model (no more persons/discord_members/identity_links)
- `common.discord_config.bot_dm_enabled` added (FALSE by default) — migration 0009
- `is_bot_dm_enabled(pool)` in `sv_common.discord.dm` — DM gate used everywhere
- `bot.py` now wires `on_member_join`, `on_member_remove`, `on_member_update`; gets `set_db_pool()` from lifespan
- Slash commands `/onboard-*` registered on bot `on_ready`; all use player model
- `scheduler.run_onboarding_check()` re-enabled; runs every 30 min
- Admin page `/admin/bot-settings` with toggle + session counts
- **To enable onboarding: go to Admin → Bot Settings → flip the toggle**
- 222 unit tests pass, 69 skip

## Phase 3 Architecture Notes
- `patt.recurring_events` = single source of truth for event days
  - Drives: front page schedule (3.2), roster schedule tab (3.3), raid tools day cards (3.4), auto-booking (3.5)
  - One row per active event day; `display_on_public=TRUE` controls front page visibility
- **Raid-Helper API called server-side from FastAPI** — no Google Apps Script proxy needed (avoids CORS)
  - Base URL: `https://raid-helper.dev/api/v2/servers/{serverId}/event`
  - Auth: `Authorization: {apiKey}` header
  - Service module: `src/patt/services/raid_helper_service.py`
- **Auto-invite rules** (consistent across Phase 3.4 preview and Phase 3.5 auto-booking):
  - `rank.level >= 2` AND `player.auto_invite_events = TRUE` → Accepted
  - `rank.level >= 2` AND `player.auto_invite_events = FALSE` → Tentative
  - `rank.level = 1` (Initiate) → Bench
- **Availability-by-day endpoint**: `GET /api/v1/admin/availability-by-day`
  - Created in Phase 3.1, reused in Phase 3.4 (create once, no duplication)
  - Returns weighted scores, role breakdowns, player lists per day
- **Auto-booking window**: checks events that started 10–20 min ago (`auto_booked=FALSE`)
  - Books +7 days if not already booked; marks source `auto_booked=TRUE`
  - Loop runs every 5 min via `asyncio.create_task` in app lifespan
  - Skipped silently if Raid-Helper not configured
- **Migration sequence**: 0013 (recurring_events + discord_config raid cols), 0014 (raid_events additions)
- **Spec→Wowhead code map** (JS, in roster.html): all 39 specs → 2-char codes for Wowhead Comp Analyzer
- **Spec→Raid-Helper map** (Python dict, in raid_helper_service.py): (class, spec) → (className, specName)
- **Legacy static files**: roster.html, roster-view.html → redirect to /roster (301), don't delete immediately

## Phase 3 Reference Files
- `reference/PHASE_3_1_AVAILABILITY_ADMIN.md` — availability page + recurring_events
- `reference/PHASE_3_2_INDEX_REVAMP.md` — live officers, recruiting, schedule on index
- `reference/PHASE_3_3_PUBLIC_ROSTER.md` — /roster page with composition + Wowhead link
- `reference/PHASE_3_4_RAID_TOOLS.md` — admin raid tools + Raid-Helper integration
- `reference/PHASE_3_5_AUTO_BOOKING.md` — auto-booking scheduler service

## Common Gotchas
- **migrate_sheets.py still imports removed models** — legacy script, tests skipped
- **Admin cookie auth**: page routes in `admin_pages.py` use `_require_admin()`. API routes under `/api/v1/admin/` use Bearer token — browser fetch won't work with those.
- **CSS variable in wrong scope**: don't put `--foo: value;` outside a selector block.
- **Bot DM toggle is OFF by default** — `bot_dm_enabled=FALSE` in discord_config. New member joins create sessions in awaiting_dm state. Flip the toggle in Admin → Bot Settings to activate.

## Workflow Preferences
- Always push and let CI deploy (~30s); don't SSH-deploy manually unless urgent
- Hard refresh (`Ctrl+Shift+R`) after CSS/JS changes
- Commit message format: `type: description` (feat/fix/docs/ci)

# currentDate
Today's date is 2026-02-24.
