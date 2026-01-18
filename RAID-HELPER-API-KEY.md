# How to Get Your Raid-Helper API Key

## Important Notes

The Raid-Helper API is primarily designed for **server-level automation** (creating events programmatically, managing signups, etc.). 

**You may not need an API key** if you're just using the copy-paste command approach. The API is optional and adds complexity. The simple workflow (generate command → paste in Discord) works without any API setup.

---

## Option 1: Server Authorization (Recommended)

For most server management tasks, you use **server authorization** rather than a personal API key.

### Step 1: Check if API is enabled for your server

In Discord, try:
```
/apikey show
```

If it says "none" or "not found", the API key hasn't been generated for your server yet.

### Step 2: Generate a Server API Key

In Discord, run:
```
/apikey create
```

This creates an API key tied to your server. You must have **Raid-Helper Manager permissions** on the server to do this.

### Step 3: View Your Key

After creating, run:
```
/apikey show
```

**IMPORTANT:** Keep this key private! Anyone with the key can create/modify events on your server.

### Step 4: Copy the Key

The bot will DM you the API key (it won't post it publicly in the channel for security).

---

## Option 2: User Authorization

Some API endpoints (like viewing events you're signed up for across servers) require **user authorization** instead.

This is done through OAuth and requires:
1. Going to https://raid-helper.dev
2. Logging in with Discord
3. Authorizing the application

However, this is rarely needed for guild management.

---

## API Key Permissions

The server API key allows:
- ✅ Creating events
- ✅ Editing events
- ✅ Deleting events
- ✅ Adding/removing signups
- ✅ Fetching event data
- ✅ Managing DKP
- ✅ Viewing attendance

---

## Using the API

Once you have your key, API calls look like:

```bash
curl -X POST "https://raid-helper.dev/api/v2/servers/{SERVER_ID}/events" \
  -H "Authorization: {YOUR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "leaderId": "YOUR_DISCORD_USER_ID",
    "templateId": 1,
    "date": "2025-01-24",
    "time": "21:00",
    "title": "PATT Prog Raid",
    "channelId": "YOUR_CHANNEL_ID"
  }'
```

---

## Troubleshooting

### "/apikey show" returns nothing or "none"

The key hasn't been created yet. Run `/apikey create` first.

### "/apikey create" says no permission

You need the Raid-Helper Manager role. Ask a server admin to:
1. Run `/setmanagerrole @YourRole` to add your role as a manager
2. Or have someone with permissions generate the key

### "Invalid API key" errors

- Make sure you're using the correct server ID with that key
- The key is server-specific — it won't work on other servers
- Check for extra spaces when copying the key

### Rate limiting

The API has rate limits. If you're making many requests:
- Add delays between calls
- Batch operations where possible

---

## Do You Actually Need the API?

For most guild leaders, **NO**. Here's the comparison:

| Task | Copy-Paste Command | API |
|------|-------------------|-----|
| Create events | ✅ Easy | ✅ Automated |
| Edit events | ✅ /edit command | ✅ Programmatic |
| View signups | ✅ Just look at Discord | ✅ JSON data |
| Pre-populate signups | ❌ Manual | ✅ Automated |
| Complexity | Low | High |

**Use the API if:**
- You want to fully automate event creation
- You want to pre-populate signups from your roster
- You're building integrations with other tools

**Skip the API if:**
- You're okay copy-pasting one command per raid
- You don't need automated signup pre-population
- You want to keep things simple

---

## Alternative: Webhook Notifications

Raid-Helper also supports webhooks for notifications without needing the full API:

```
/webhook set [WEBHOOK_URL]
```

This can post signup notifications to a webhook (like a Google Apps Script that logs to your sheet).

---

## API Documentation Reference

Full API docs: https://raid-helper.dev/documentation/api

Key endpoints:
- `POST /servers/{serverId}/events` — Create event
- `GET /servers/{serverId}/events` — List events
- `POST /servers/{serverId}/events/{eventId}/signups` — Add signup
- `DELETE /servers/{serverId}/events/{eventId}` — Delete event

---

## Summary

1. **For most users:** Skip the API, use copy-paste commands
2. **To get a key:** Run `/apikey create` in Discord (need manager role)
3. **To view key:** Run `/apikey show` (bot DMs it to you)
4. **Keep it secret:** Don't share your API key publicly
