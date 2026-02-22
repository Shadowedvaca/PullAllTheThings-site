# PATT Sync Companion App

Watches for World of Warcraft addon exports and uploads guild roster data
to the Pull All The Things API server automatically.

## Setup

1. Install Python 3.10+
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your values
4. Run: `python patt_sync_watcher.py`

## Finding Your SavedVariables Path

1. Open File Explorer
2. Navigate to your WoW install (usually `C:\Program Files (x86)\World of Warcraft`)
3. Go to: `_retail_\WTF\Account\`
4. Find your account folder (it's a number or your account name)
5. The full path should look like:
   ```
   C:\Program Files (x86)\World of Warcraft\_retail_\WTF\Account\12345678\SavedVariables
   ```

## Running on Startup (Windows)

**Option 1 — Startup folder:**
1. Press `Win+R`, type `shell:startup`, hit Enter
2. Create a shortcut to `patt_sync_watcher.py` in that folder

**Option 2 — Batch file:**
Create a `.bat` file with:
```batch
@echo off
cd /d "C:\path\to\companion_app"
python patt_sync_watcher.py
```
Place the `.bat` file in your startup folder.

## How It Works

1. The PATTSync WoW addon exports guild roster data to SavedVariables
2. SavedVariables are written to disk on `/reload` or logout
3. This companion app detects the file change via `watchdog`
4. It parses the Lua data and uploads it to the PATT API
5. The server processes the data and updates the identity system

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "No existing file found" | Export from the addon first (`/pattsync` in WoW) |
| "Cannot connect to API" | Check `PATT_API_URL` in `.env` |
| "Invalid API key" | Check `PATT_API_KEY` in `.env` |
| Data not updating | Do `/reload` or log out after exporting to flush SavedVariables to disk |
| Seeing stale data | Run `/pattsync force` in WoW to bypass the 6-hour cooldown |
