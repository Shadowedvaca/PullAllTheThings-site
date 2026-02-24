# PATT Raid Admin System — Complete Setup Guide

## What You're Getting

A complete raid management system that:

1. **Reads your roster** from Google Sheets
2. **Calculates best raid days** based on who's available
3. **Shows top 3 days** ranked by availability with role breakdown (Tank/Healer/DPS)
4. **Generates Discord commands** for instant event creation
5. **Flags invalid Discord names** so you can fix them
6. **Respects member preferences** — auto-signup vs tentative, reminder opt-in
7. **Multi-channel support** — announcement, pug, and notification channels

---

## New Features

### Auto-Signup Preference
Members can choose to be automatically signed up (confirmed) when a raid is posted, or be added as "Tentative" and confirm later. The admin dashboard shows which members will be auto-signed vs tentative.

### Day-of Reminders (Opt-in)
Members who want a reminder at 5pm EST on raid day can check a box. The admin dashboard shows who wants reminders with a 🔔 icon, so you can:
- Set up Raid-Helper's reminder feature to DM only those members
- Or manually ping them yourself

### Multi-Channel Configuration
Configure separate channels for:
- **Signup Channel** — Where the main event embed gets posted
- **Voice Channel** — Links the event to your raid voice room
- **Announcement Channel** — Where @everyone ping goes (guild-general)
- **Pug Channel** — Secondary visibility for signups  
- **Notification Channel** — Where signup notifications go (spam spam spam)

---

## Files Overview

| File | Purpose |
|------|---------|
| `patt-raid-admin.html` | Main admin dashboard |
| `patt-roster-form.html` | Updated signup form with new preferences |
| `patt-roster-backend.js` | Google Apps Script backend |
| `DISCORD-DEVELOPER-MODE.md` | How to enable developer mode & get IDs |
| `RAID-HELPER-API-KEY.md` | How to get your Raid-Helper API key |

---

## Setup Steps

### Step 1: Update Your Google Apps Script

If you already have the roster sheet set up:

1. Go to your Google Sheet → Extensions → Apps Script
2. **Replace** the existing code with `patt-roster-backend.js`
3. Click Deploy → Manage deployments → Edit (pencil icon)
4. Set version to "New version" and click Deploy
5. Copy the web app URL

**Note:** If your existing Availability sheet doesn't have "Auto-Signup" and "Wants Reminders" columns, you'll need to add them manually or delete the sheet and let the script recreate it.

If starting fresh:

1. Create a new Google Sheet
2. Go to Extensions → Apps Script
3. Paste the contents of `patt-roster-backend.js`
4. Deploy as Web App (Execute as: Me, Access: Anyone)
5. Copy the web app URL

### Step 2: Set Up the Admin Dashboard

1. Upload `patt-raid-admin.html` to your GitHub repo (or wherever you host)
2. Open the page in your browser
3. Go to **Settings** tab
4. Enter your Google Apps Script URL
5. Configure your raid defaults (title, time, duration)
6. **New:** Configure all channel IDs (see DISCORD-DEVELOPER-MODE.md for how to get them)
7. Click **Save Settings**

### Step 3: Update the Roster Form

1. Upload `patt-roster-form.html` to replace your old form
2. Members will now see two new checkboxes:
   - Auto-signup to raids
   - Sign up for reminders

### Step 4: Get Your Discord IDs

See `DISCORD-DEVELOPER-MODE.md` for step-by-step instructions.

You'll need:
- Server ID
- Signup Channel ID
- Voice Channel ID  
- Announcement Channel ID (guild-general)
- Pug Channel ID
- Notification Channel ID (spam spam spam)

---

## How It Works

### Member Signup Flow

1. Member fills out roster form
2. They select their available days
3. They choose:
   - **Auto-signup** (checked) = They'll be signed up automatically
   - **Auto-signup** (unchecked) = They'll be added as Tentative
4. They choose:
   - **Reminders** (checked) = They want a 5pm DM on raid day
   - **Reminders** (unchecked) = No day-of reminder

### Admin Workflow

1. Open admin dashboard
2. System shows top 3 days ranked by availability
3. Click a day to see:
   - Who's available (with ✓ Auto or ⚠ Tentative badge)
   - Who wants reminders (🔔 icon)
   - Role breakdown
4. Copy the create command → paste in Discord
5. (Optional) Copy signup commands to pre-populate

### Generated Commands

The system generates different commands based on preferences:

```
# Auto-signed up (5):
/adduser [EVENT_ID] @player1 class:Warrior spec:DPS
/adduser [EVENT_ID] @player2 class:Priest spec:Healer
...

# Tentative (3):
/adduser [EVENT_ID] @player3 class:Mage spec:Tentative
...

# Marked absent (2):
/adduser [EVENT_ID] @player4 status:Absence
...

# Wants 5pm reminder (4): player1, player2, player5, player6
```

---

## Raid-Helper Channel Configuration

In Discord, configure these Raid-Helper settings:

### Announcement Channel
```
/serversettings announcementchannel #guild-general
```

### Signup Notification Channel  
```
/serversettings signupnotificationchannel #spam-spam-spam
```

### Reminders (for opt-in members)
When creating an event, you can add reminders. For members who opted in:
```
/edit [EVENT_ID] reminder:4h
```
This sends a reminder 4 hours before (adjust timing as needed).

**Note:** Raid-Helper sends reminders to all signed-up members. For selective reminders to only opt-in members, you may need to manually DM them or use a different approach.

---

## Data Sheets

The Apps Script creates/manages these sheets:

### Availability
| Column | Description |
|--------|-------------|
| Discord | Username |
| Monday-Sunday | TRUE/FALSE |
| Auto-Signup | TRUE/FALSE |
| Wants Reminders | TRUE/FALSE |
| Notes | Scheduling notes |
| Updated | Timestamp |

### Characters
| Column | Description |
|--------|-------------|
| Discord | Username |
| Character | In-game name |
| Class | WoW class |
| Spec | Specialization |
| Role | Tank/Healer/DPS |
| Main/Alt | Which is main |
| Updated | Timestamp |

### DiscordIDs
| Column | Description |
|--------|-------------|
| Discord Name | What they entered |
| Discord User ID | Actual 18-digit ID |
| Status | Verified/Pending/Invalid |
| Updated | Timestamp |

---

## Troubleshooting

### New columns not showing
If you updated from an older version, add these columns manually to the Availability sheet:
- Column I: "Auto-Signup"
- Column J: "Wants Reminders"

Or delete the Availability sheet and let members re-register.

### "Not connected" status
- Check your Apps Script URL is correct
- Make sure you deployed as Web App with "Anyone" access
- Try redeploying with a new version

### Can't get Discord IDs
- Enable Developer Mode first (see DISCORD-DEVELOPER-MODE.md)
- Right-click the item → Copy ID

### Raid-Helper commands not working
- Make sure you have the Manager role
- Use commands in a channel the bot can see
- Check the bot has proper permissions

---

## Quick Reference

**Create event:**
```
/create title:PATT Prog Raid date:YYYY-MM-DD time:21:00 duration:120 template:1
```

**Add signup:**
```
/adduser [EVENT_ID] @username class:Warrior spec:DPS
```

**Add tentative:**
```
/adduser [EVENT_ID] @username class:Warrior spec:Tentative
```

**Add absent:**
```
/adduser [EVENT_ID] @username status:Absence
```

**Set reminder:**
```
/edit [EVENT_ID] reminder:4h
```
