# PATT-Bot — Discord Developer Portal Setup

Follow these steps to create the Discord bot and connect it to the PATT platform.

---

## Step 1 — Create the Application

1. Go to https://discord.com/developers/applications
2. Click **"New Application"**
3. Name it **"PATT-Bot"** → click **Create**

---

## Step 2 — Create the Bot

1. In the left sidebar, click **Bot**
2. Click **"Add Bot"** → confirm
3. Under the bot's **TOKEN** section, click **"Reset Token"** → copy the token
4. Save it to your `.env` file:
   ```
   DISCORD_BOT_TOKEN=your-token-here
   ```
   > **Keep this token secret.** Never commit it to the repo.

---

## Step 3 — Enable Privileged Gateway Intents

Still on the **Bot** tab, scroll to **Privileged Gateway Intents** and enable:

- ✅ **Server Members Intent** — required to read the member list and roles for role sync

Leave **Message Content Intent** disabled (the bot doesn't read messages).

---

## Step 4 — Invite the Bot to the PATT Discord Server

1. In the left sidebar, click **OAuth2 → URL Generator**
2. Under **Scopes**, check: `bot`
3. Under **Bot Permissions**, check:
   - `View Channels`
   - `Send Messages`
   - `Send Messages in Threads`
   - `Read Message History`
4. Copy the generated URL at the bottom
5. Open the URL in your browser
6. Select the **Pull All The Things** Discord server → click **Authorize**

The bot will now appear in the server's member list as offline (it goes online when the platform starts).

---

## Step 5 — Get the Discord Guild (Server) ID

1. In the Discord app, go to **User Settings → Advanced** and enable **Developer Mode**
2. Right-click the PATT server icon in the sidebar
3. Click **"Copy Server ID"**
4. Save it to your `.env` file:
   ```
   DISCORD_GUILD_ID=your-guild-id-here
   ```

---

## Step 6 — Map Discord Roles to Platform Ranks

For role sync to work, each platform rank needs its corresponding Discord role ID configured.

1. In Discord, go to **Server Settings → Roles**
2. Right-click each role → **"Copy Role ID"** (Developer Mode must be enabled)
3. Use the Admin API to update each rank:

```bash
PATCH /api/v1/admin/ranks/{id}
Body: { "discord_role_id": "123456789012345678" }
```

| Platform Rank | Discord Role Name | Notes |
|--------------|------------------|-------|
| Initiate (1) | Initiate | New members |
| Member (2) | Member | Regular attendees |
| Veteran (3) | Veteran | Key performers |
| Officer (4) | Officer | Guild leadership |
| Guild Leader (5) | Guild Leader | Trog |

---

## Verification

Once the platform is running with the bot token configured, the bot will:

1. Connect to Discord and appear **Online**
2. Log: `PATT-Bot connected as PATT-Bot#XXXX (id=...)`

Check the platform logs:
```bash
journalctl -u patt -f
```

---

## Troubleshooting

**Bot doesn't appear online:**
- Check `DISCORD_BOT_TOKEN` is correct and not expired
- Verify the bot was added to the server (Step 4)

**Role sync isn't working:**
- Verify `DISCORD_GUILD_ID` is correct
- Ensure **Server Members Intent** is enabled (Step 3)
- Ensure `discord_role_id` is set on each rank (Step 6)

**DMs not sending:**
- The target member may have DMs disabled from non-friends
- The bot must share a server with the user to DM them
