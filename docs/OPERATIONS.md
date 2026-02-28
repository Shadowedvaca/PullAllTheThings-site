# PATT Platform — Operations Guide

> This guide is for Mike (Trog). Everything you need to run the platform
> day-to-day, without touching code.

---

## Quick Reference

| What | URL / Command |
|------|--------------|
| Guild website | https://pullallthethings.com |
| Admin panel | https://pullallthethings.com/admin/campaigns |
| Health check | https://pullallthethings.com/api/health |
| Server SSH | `ssh hetzner` |
| App directory | `/opt/patt-platform/` |
| App logs | `journalctl -u patt -f` |
| Restart app | `sudo systemctl restart patt` |
| Run migration | `cd /opt/patt-platform && .venv/bin/alembic upgrade head` |

---

## Admin Panel Pages

| Page | URL | Purpose |
|------|-----|---------|
| Campaigns | `/admin/campaigns` | Create/manage voting campaigns |
| Player Manager | `/admin/players` | Link Discord ↔ players ↔ characters; set hiatus |
| Users | `/admin/users` | Website account management |
| Availability | `/admin/availability` | 7-day raid availability grid + event day config |
| Raid Tools | `/admin/raid-tools` | Raid-Helper event builder, roster preview |
| Data Quality | `/admin/data-quality` | Coverage stats, unmatched players/chars, drift issues |
| Crafting Sync | `/admin/crafting-sync` | Force recipe sync, configure season |
| Bot Settings | `/admin/bot-settings` | DM feature toggles |

---

## Creating a Campaign

### Via the Admin Panel (recommended)

1. Log in at https://pullallthethings.com/login
2. Go to **Admin → Campaigns → New Campaign**
3. Fill in the form:
   - **Title** — the campaign name
   - **Description** — shown on the vote page
   - **Type** — Ranked Choice (voters pick top 3)
   - **Picks per voter** — usually 3
   - **Min rank to vote** — Veteran (3) for most campaigns
   - **Min rank to view** — leave blank for public results
   - **Start date/time** — when voting opens (or click Activate manually)
   - **Duration** — use the preset buttons (1 day, 3 days, 1 week, etc.)
   - **Early close** — checked by default (closes when 100% have voted)
   - **Contest agent** — enabled by default with "hype" chattiness
4. Click **Save as Draft**
5. Go to the Entries section and add each entry (name + image URL)
6. When ready, click **Activate** — voting opens immediately

### Getting Google Drive Image URLs

Images must be shared publicly ("Anyone with the link can view").

1. Upload the image to Google Drive
2. Right-click → **Share** → **Anyone with the link** → Copy link
3. Extract the file ID (the part between `/d/` and `/view`)
4. Build the direct image URL:
   `https://drive.google.com/uc?id=FILE_ID&export=view`
5. Paste that URL into the Image URL field

---

## Inviting a New Member

1. Go to **Admin → Player Manager**
2. Find the player card (they should already be visible from Discord sync)
3. If not present, use the **+ Add Player** button
4. Click the **✉** (envelope) button on their card to send a Discord DM invite
5. They'll receive their personal registration link

Invite codes expire in 7 days. If it expires, click ✉ again.

---

## Managing the Roster

**Rank changes:** Discord is the source of truth. Change their Discord role,
and the bot syncs it automatically on the next sync cycle (default: every 24 hours).
To force an immediate sync, restart the app:
```bash
sudo systemctl restart patt
```

**Putting someone on raid hiatus:** In **Admin → Player Manager**, find their card
and check the **Hiatus** checkbox. This hides them from the public roster and the
availability grid without deactivating their account.

**Removing a member:** Set `is_active = false` via the DB or deactivate their
website account in **Admin → Users**.

---

## Raid Tools & Auto-Booking

### Raid-Helper Configuration
Go to **Admin → Raid Tools** → **Raid-Helper Configuration** and ensure these are set:
- API Key (from `/apikey show` in Discord)
- Server ID
- Raid Leader Discord ID
- Raid Channel
- Raid Voice Channel

### Manual Event Creation
Use the **Event Builder** section in Admin → Raid Tools to preview roster availability
and create a Raid-Helper event with one click.

### Auto-Booking
The platform automatically creates next week's raid event 10–20 minutes after the
current raid's start time on each raid day. It posts an announcement in the configured
raid channel. No action needed from you — just ensure Raid-Helper config is set.

---

## Checking on Things

### Is the bot online?
- Look for PATT-Bot in your Discord server member list — it should appear online
- If offline: `sudo systemctl restart patt`

### Is the platform healthy?
```bash
curl https://pullallthethings.com/api/health
```
Should return: `{"status": "ok"}`

### View live logs
```bash
ssh hetzner
journalctl -u patt -f
```
Press `Ctrl+C` to stop.

### Check app status
```bash
ssh hetzner
systemctl status patt
```

---

## Deploying Updates

Updates deploy automatically when code is pushed to the `main` branch on GitHub.
The GitHub Actions workflow:
1. SSH into the server
2. `git pull` latest code
3. Install any new dependencies
4. Run database migrations (`alembic upgrade head`)
5. Restart the app
6. Verify the health check passes

Watch it at: https://github.com/Shadowedvaca/PullAllTheThings-site/actions

To deploy manually:
```bash
ssh hetzner
cd /opt/patt-platform
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
sudo systemctl restart patt
```

---

## Backups

See `docs/BACKUPS.md` for full backup and restore procedures.

**Quick version:**
- Automatic nightly backup at 3:00 AM UTC → `/opt/backups/patt-db/`
- Manual backup: `ssh hetzner && patt-backup.sh`
- Restore: `ssh hetzner && patt-restore.sh`

---

## What the Contest Agent Does

When a campaign has **Agent Enabled** checked, PATT-Bot automatically posts
updates to the campaign's Discord channel as milestones are reached:

| Chattiness | What it posts |
|-----------|--------------|
| **Quiet** | Launch announcement + final results only |
| **Normal** | + first vote, 50% participation, 24h warning, 1h warning, all voted |
| **Hype** | + every lead change, 25%/75% milestones |

The agent checks every 5 minutes. It never posts the same event twice.

---

## Common Issues

### "PATT-Bot is offline"
```bash
sudo systemctl restart patt
```
If it keeps going offline: `journalctl -u patt -n 50`

### "The website isn't loading"
1. Check if the app is running: `systemctl status patt`
2. Check Nginx: `systemctl status nginx`
3. Check health from the server: `curl http://localhost:8100/api/health`

### "Someone can't log in"
- Verify their Discord username matches (case-insensitive — stored as email)
- They may not have registered yet — send a new invite from Player Manager
- If their invite code expired, click ✉ again on their player card

### "A campaign isn't showing up"
- Check if it's in draft status — only live campaigns show on the homepage
- Check the min_rank_to_view setting

### Chrome shows "GitHub 404" after a deploy
Chrome is serving a stale connection from when the repo used GitHub Pages.
Fix: Go to `chrome://net-internals/#sockets` → click **Flush socket pools**, then reload.

---

## Environment Variables

These live in `/opt/patt-platform/.env` on the server. Never commit this file.

```bash
DATABASE_URL=postgresql+asyncpg://patt_user:PASSWORD@localhost:5432/patt_db
JWT_SECRET_KEY=your-secret-key
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_GUILD_ID=your-server-id
BLIZZARD_CLIENT_ID=your-blizzard-client-id
BLIZZARD_CLIENT_SECRET=your-blizzard-client-secret
PATT_GUILD_REALM_SLUG=senjin
PATT_GUILD_NAME_SLUG=pull-all-the-things
PATT_API_KEY=your-companion-app-api-key
APP_ENV=production
APP_PORT=8100
```

> **Note:** Channel IDs (audit channel, crafters corner, raid channel) are configured
> via the Admin UI and stored in `common.discord_config` — not in `.env`.

---

## Contact

If something is deeply broken:
- GitHub: https://github.com/Shadowedvaca/PullAllTheThings-site
- Logs: `journalctl -u patt -n 200`
- DB: `ssh hetzner && sudo -u postgres psql patt_db`
