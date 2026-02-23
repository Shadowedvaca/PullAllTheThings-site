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
| Server SSH | `ssh root@5.78.114.224` |
| App logs | `journalctl -u patt -f` |
| Restart app | `sudo systemctl restart patt` |

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
5. Go to the Entries section and add each entry:
   - Name
   - Image URL (see Google Drive section below)
6. When ready, click **Activate** — voting opens immediately

### Getting Google Drive Image URLs

Images must be shared publicly ("Anyone with the link can view").

1. Upload the image to Google Drive
2. Right-click → **Share** → **Anyone with the link** → Copy link
3. The share link looks like:
   `https://drive.google.com/file/d/1abc123XYZ.../view`
4. Extract the file ID (the part between `/d/` and `/view`):
   `1abc123XYZ...`
5. Build the direct image URL:
   `https://drive.google.com/uc?id=1abc123XYZ...&export=view`
6. Paste that URL into the Image URL field in the admin form

### Via Script (for the art vote)

The art vote has a pre-built setup script. Once you have all 10 image URLs:

1. Open `scripts/setup_art_vote.py` in a text editor
2. Fill in the `FILE_IDS` dictionary at the top with each character's Drive file ID
3. Run the script on the server:
   ```bash
   ssh root@5.78.114.224
   cd /var/www/patt
   source .venv/bin/activate
   python scripts/setup_art_vote.py
   ```
4. Review the campaign at `/admin/campaigns/{id}`
5. Activate it when ready

---

## Inviting a New Member

1. Go to **Admin → Roster**
2. Find the member (they should already be in the roster from Discord sync)
   - If not, click **Add Member** and fill in their Discord username and ID
3. Click **Send Invite** next to their name
4. They'll receive a DM from PATT-Bot with:
   - Their personal invite code
   - A link to https://pullallthethings.com/register

The invite code expires in 7 days. If it expires, send a new one.

---

## Managing the Roster

**Rank changes:** Discord is the source of truth. Change their Discord role,
and the bot syncs it on the next sync cycle (default: every 24 hours).
To force an immediate sync, restart the app:
```bash
sudo systemctl restart patt
```

**Removing a member:** Contacts Trog — this requires a DB change. (Future: admin UI for this.)

---

## Checking on Things

### Is the bot online?
- Look for PATT-Bot in your Discord server member list — it should appear online
- If offline, restart the app: `sudo systemctl restart patt`

### Is the platform healthy?
```bash
curl https://pullallthethings.com/api/health
```
Should return: `{"status": "ok"}`

### View live logs
```bash
ssh root@5.78.114.224
journalctl -u patt -f
```
Press `Ctrl+C` to stop. Press `q` to exit the log viewer.

### Check app status
```bash
ssh root@5.78.114.224
systemctl status patt
```

### View campaign votes (admin)
Go to `/admin/campaigns/{id}` in your browser — shows vote counts, stats, entries.

---

## Deploying Updates

Updates deploy automatically when code is pushed to the `main` branch on GitHub.
The GitHub Actions workflow:
1. SSH into the server
2. `git pull` latest code
3. Install any new dependencies
4. Run database migrations
5. Restart the app
6. Verify the health check passes

You can watch it at: https://github.com/Shadowedvaca/PullAllTheThings-site/actions

To deploy manually:
```bash
ssh root@5.78.114.224
cd /var/www/patt
git pull
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
sudo systemctl restart patt
```

---

## Restarting the Platform

```bash
ssh root@5.78.114.224
sudo systemctl restart patt
```

The app typically comes back online in about 5 seconds. Check with:
```bash
curl https://pullallthethings.com/api/health
```

---

## Database Migrations

Only needed when deploying a new code version that adds DB tables or columns.
The auto-deploy script runs this for you. To run manually:

```bash
ssh root@5.78.114.224
cd /var/www/patt
source .venv/bin/activate
alembic upgrade head
```

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

## Backup and Recovery

The database lives on the Hetzner server at PostgreSQL 16.
There is no automated backup configured yet (future task).

To manually export the database:
```bash
ssh root@5.78.114.224
pg_dump -U patt_user patt_db > patt_backup_$(date +%Y%m%d).sql
```

---

## Common Issues

### "PATT-Bot is offline"
```bash
sudo systemctl restart patt
```
If it keeps going offline, check the logs: `journalctl -u patt -n 50`

### "The website isn't loading"
1. Check if the app is running: `systemctl status patt`
2. Check Nginx: `systemctl status nginx`
3. Check the health endpoint from the server: `curl http://localhost:8100/api/health`

### "Someone can't log in"
- Verify their Discord username matches exactly (case-insensitive)
- Check if they registered — they need to use the invite flow first
- If their invite code expired, send a new one from the admin roster page

### "A campaign isn't showing up"
- Check if it's in draft status — only live campaigns show on the homepage
- Check the min_rank_to_view setting — if set, low-rank members won't see it

---

## Environment Variables

These live in `/var/www/patt/.env` on the server. Never commit this file.

```bash
DATABASE_URL=postgresql+asyncpg://patt_user:PASSWORD@localhost:5432/patt_db
JWT_SECRET_KEY=your-secret-key
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_GUILD_ID=your-server-id
APP_ENV=production
APP_PORT=8100
```

---

## Contact

If something is deeply broken and you can't figure it out, the codebase is at:
- GitHub: https://github.com/Shadowedvaca/PullAllTheThings-site
- Logs: `journalctl -u patt -n 200`
- DB: `psql -U patt_user patt_db` (requires being on the server)
