# How to Enable Discord Developer Mode & Get IDs

## What is Developer Mode?

Developer Mode is a Discord setting that lets you copy unique IDs for servers, channels, users, and messages. These IDs are 18-digit numbers that look like `123456789012345678`.

You need these IDs to:
- Configure bots (like Raid-Helper)
- Use Discord APIs
- Reference specific channels/users in tools

---

## Enabling Developer Mode

### Desktop (Windows/Mac/Linux)

1. Open Discord
2. Click the **gear icon** (⚙️) next to your username at the bottom left
3. Scroll down the left sidebar to **App Settings**
4. Click **Advanced**
5. Toggle **Developer Mode** to ON (it will turn green/blue)

### Mobile (iOS/Android)

1. Open Discord app
2. Tap your **profile picture** at the bottom right
3. Scroll down and tap **Advanced**
4. Toggle **Developer Mode** to ON

### Web Browser

1. Go to discord.com and log in
2. Click the **gear icon** (⚙️) at the bottom left
3. Click **Advanced** in the left sidebar
4. Toggle **Developer Mode** to ON

---

## Getting IDs (Once Developer Mode is Enabled)

### Server ID

1. Find your server in the left sidebar
2. **Right-click** the server icon
3. Click **Copy Server ID**

Example: `1234567890123456789`

### Channel ID (Text or Voice)

1. Find the channel in your server
2. **Right-click** the channel name
3. Click **Copy Channel ID**

Example: `9876543210987654321`

### User ID

1. Find the user (in a message, member list, or DMs)
2. **Right-click** their username or avatar
3. Click **Copy User ID**

Example: `1122334455667788990`

### Message ID

1. Hover over any message
2. Click the **three dots** (⋯) that appear
3. Click **Copy Message ID**

Example: `5544332211009988776`

### Role ID

1. Go to **Server Settings** → **Roles**
2. Click on a role
3. **Right-click** the role name in the list
4. Click **Copy Role ID**

*Note: Some Discord versions require you to mention the role first, then copy from the message.*

---

## IDs You Need for PATT Raid Admin

| ID Type | Where to Get It | What It's For |
|---------|-----------------|---------------|
| Server ID | Right-click server icon | Identifies your Discord server |
| Signup Channel ID | Right-click your raid-signup channel | Where events get posted |
| Voice Channel ID | Right-click your raid voice channel | Links event to voice room |
| Announcement Channel ID | Right-click guild-general | Where creation pings go |
| Pug Channel ID | Right-click your pug channel | Secondary signup visibility |
| Notification Channel ID | Right-click spam-spam-spam | Where signup notifications go |

---

## Troubleshooting

### "Copy ID" option not showing
- Make sure Developer Mode is actually ON
- Try restarting Discord
- On mobile, you may need to long-press instead of tap

### ID looks wrong (too short/long)
- Discord IDs are always 17-19 digits
- If you copied text instead, try again with right-click → Copy ID specifically

### Can't find Advanced settings
- Update your Discord app to the latest version
- On very old versions, Developer Mode might be under "Appearance" instead

---

## Quick Reference

Once Developer Mode is on, right-click anything to copy its ID:

```
Server icon     → Copy Server ID
#channel        → Copy Channel ID  
🔊 voice        → Copy Channel ID
@username       → Copy User ID
Message         → ⋯ → Copy Message ID
@role           → Copy Role ID (in Server Settings)
```
