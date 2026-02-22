# PATTSync WoW Addon

Exports the Pull All The Things guild roster (including notes) to SavedVariables
so the companion app can upload it to the PATT API automatically.

## Installation

1. Navigate to your WoW addons folder:
   ```
   C:\Program Files (x86)\World of Warcraft\_retail_\Interface\AddOns\
   ```

2. Create a folder called `PATTSync`

3. Copy these two files inside:
   ```
   PATTSync/
     PATTSync.toc
     PATTSync.lua
   ```

4. Restart WoW or type `/reload`

## Usage

| Command | Action |
|---------|--------|
| `/pattsync` | Export guild roster (respects 6-hour cooldown) |
| `/pattsync force` | Export regardless of cooldown |
| `/pattsync status` | Show last export time and cooldown |
| `/pattsync help` | Show all commands |

The addon also auto-exports when you open the guild window (J key) if the
cooldown has elapsed.

## After Exporting

Type `/reload` or log out — this flushes SavedVariables to disk so the
companion app can detect and upload the new data.

## SavedVariables Location

```
World of Warcraft/_retail_/WTF/Account/<ACCOUNT_NAME>/SavedVariables/PATTSync.lua
```

## Interface Version

The `## Interface:` line in the .toc file must match the current WoW patch.
Check the current value in-game with:
```
/run print((select(4, GetBuildInfo())))
```
Then update `PATTSync.toc` if it differs from `110100`.
