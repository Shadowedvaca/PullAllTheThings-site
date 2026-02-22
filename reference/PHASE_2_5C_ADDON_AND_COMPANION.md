# Phase 2.5C: WoW Addon (PATTSync) & Companion App

## Overview

This phase creates two components:
1. **PATTSync** — a WoW addon that exports guild roster data (including notes) to SavedVariables
2. **patt-sync-watcher** — a Python script that runs on the gaming PC, watches for new exports, and uploads them to the API

## Task 1: WoW Addon — PATTSync

### File: PATTSync.toc

The TOC (Table of Contents) file tells WoW how to load the addon.

```
## Interface: 110100
## Title: PATTSync - Guild Roster Exporter
## Notes: Exports guild roster data for Pull All The Things guild management tools
## Author: Trog
## Version: 1.0.0
## SavedVariables: PATTSyncDB
## DefaultState: enabled

PATTSync.lua
```

**Note on Interface version:** `110100` is for Retail WoW (The War Within, 11.1.0).
Update this number when new patches drop. Check the current interface version with
`/run print((select(4, GetBuildInfo())))` in-game.

### File: PATTSync.lua

```lua
-- PATTSync: Guild Roster Exporter for Pull All The Things
-- Exports guild roster data including notes to SavedVariables
-- A companion app on the PC watches for changes and uploads to the API
--
-- Usage:
--   - Automatically exports when you open the guild roster (up to 4x per day)
--   - Type /pattsync to manually trigger an export
--   - Type /pattsync status to see last export time and cooldown
--   - Type /pattsync force to export regardless of cooldown
--

-- SavedVariables storage
PATTSyncDB = PATTSyncDB or {}

-- Constants
local ADDON_NAME = "PATTSync"
local ADDON_VERSION = "1.0.0"
local COOLDOWN_SECONDS = 6 * 60 * 60  -- 6 hours = 4x per day max
local MAX_RETRIES = 3
local RETRY_DELAY = 2  -- seconds between retries

-- State
local isExporting = false
local exportRetries = 0

-- Colors for chat output
local GOLD = "|cFFD4A84B"
local GREEN = "|cFF4ADE80"
local RED = "|cFFF87171"
local WHITE = "|cFFE8E8E8"
local RESET = "|r"

-- ============================================================
-- Utility Functions
-- ============================================================

local function Print(msg)
    DEFAULT_CHAT_FRAME:AddMessage(GOLD .. "[PATTSync]" .. RESET .. " " .. msg)
end

local function GetTimestamp()
    return time()
end

local function FormatTimeDiff(seconds)
    if seconds < 60 then
        return string.format("%d seconds", seconds)
    elseif seconds < 3600 then
        return string.format("%d minutes", math.floor(seconds / 60))
    else
        local hours = math.floor(seconds / 3600)
        local mins = math.floor((seconds % 3600) / 60)
        return string.format("%dh %dm", hours, mins)
    end
end

local function IsCooldownActive()
    local lastExport = PATTSyncDB.lastExportTime or 0
    local elapsed = GetTimestamp() - lastExport
    return elapsed < COOLDOWN_SECONDS, COOLDOWN_SECONDS - elapsed
end

-- ============================================================
-- Guild Roster Export
-- ============================================================

local function ExportGuildRoster(forcedExport)
    -- Check if we're in a guild
    if not IsInGuild() then
        Print(RED .. "You are not in a guild!" .. RESET)
        return
    end
    
    -- Check cooldown (unless forced)
    if not forcedExport then
        local onCooldown, remaining = IsCooldownActive()
        if onCooldown then
            Print(WHITE .. "Export on cooldown. Next export available in " .. 
                  GREEN .. FormatTimeDiff(remaining) .. RESET)
            return
        end
    end
    
    -- Prevent concurrent exports
    if isExporting then
        Print(WHITE .. "Export already in progress..." .. RESET)
        return
    end
    
    isExporting = true
    exportRetries = 0
    
    -- Request guild roster data from the server
    -- This triggers GUILD_ROSTER_UPDATE when data is ready
    C_GuildInfo.GuildRoster()
    
    Print(WHITE .. "Requesting guild roster data..." .. RESET)
end

local function ProcessGuildRoster()
    local numMembers = GetNumGuildMembers()
    
    if numMembers == 0 then
        -- Data not ready yet, retry
        exportRetries = exportRetries + 1
        if exportRetries <= MAX_RETRIES then
            Print(WHITE .. "Waiting for guild data (attempt " .. 
                  exportRetries .. "/" .. MAX_RETRIES .. ")..." .. RESET)
            C_Timer.After(RETRY_DELAY, function()
                C_GuildInfo.GuildRoster()
            end)
            return
        else
            Print(RED .. "Failed to get guild roster data after " .. 
                  MAX_RETRIES .. " attempts." .. RESET)
            isExporting = false
            return
        end
    end
    
    -- Build the roster data
    local characters = {}
    local exportTime = GetTimestamp()
    
    for i = 1, numMembers do
        local name, rankName, rankIndex, level, classDisplayName, 
              zone, note, officerNote, isOnline, status, 
              classFileName, achievementPoints, achievementRank,
              isMobile, isSoREligible, standingID = GetGuildRosterInfo(i)
        
        if name then
            -- WoW returns names as "Name-Realm"
            local charName, realmName = strsplit("-", name)
            
            -- Get last online info
            local yearsOffline, monthsOffline, daysOffline, hoursOffline = GetGuildRosterLastOnline(i)
            local lastOnlineStr = ""
            if isOnline then
                lastOnlineStr = "Online"
            elseif yearsOffline then
                if yearsOffline > 0 then
                    lastOnlineStr = string.format("%dy %dm %dd", yearsOffline, monthsOffline, daysOffline)
                elseif monthsOffline > 0 then
                    lastOnlineStr = string.format("%dm %dd", monthsOffline, daysOffline)
                elseif daysOffline > 0 then
                    lastOnlineStr = string.format("%dd %dh", daysOffline, hoursOffline)
                else
                    lastOnlineStr = string.format("%dh", hoursOffline)
                end
            end
            
            table.insert(characters, {
                name = charName or name,
                realm = realmName or GetRealmName(),
                class = classDisplayName or "Unknown",
                classFile = classFileName or "UNKNOWN",
                level = level or 0,
                rank = rankIndex or 99,
                rankName = rankName or "Unknown",
                zone = zone or "",
                note = note or "",
                officerNote = officerNote or "",
                isOnline = isOnline or false,
                lastOnline = lastOnlineStr,
                achievementPoints = achievementPoints or 0,
            })
        end
    end
    
    -- Store in SavedVariables
    PATTSyncDB.lastExport = {
        exportTime = exportTime,
        exportTimeISO = date("!%Y-%m-%dT%H:%M:%SZ", exportTime),
        addonVersion = ADDON_VERSION,
        guildName = GetGuildInfo("player") or "Unknown",
        memberCount = #characters,
        characters = characters,
    }
    
    PATTSyncDB.lastExportTime = exportTime
    PATTSyncDB.totalExports = (PATTSyncDB.totalExports or 0) + 1
    
    isExporting = false
    
    Print(GREEN .. "Export complete! " .. RESET .. WHITE .. 
          #characters .. " characters exported." .. RESET)
    Print(WHITE .. "The companion app will upload this data automatically." .. RESET)
end

-- ============================================================
-- Event Handling
-- ============================================================

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("GUILD_ROSTER_UPDATE")

-- Track if we've already exported for this guild window opening
local guildWindowExportDone = false

frame:SetScript("OnEvent", function(self, event, ...)
    if event == "ADDON_LOADED" then
        local addonName = ...
        if addonName == ADDON_NAME then
            -- Initialize DB
            PATTSyncDB = PATTSyncDB or {}
            PATTSyncDB.totalExports = PATTSyncDB.totalExports or 0
            
            Print(GREEN .. "v" .. ADDON_VERSION .. RESET .. 
                  WHITE .. " loaded. Type " .. GOLD .. "/pattsync" .. 
                  WHITE .. " for commands." .. RESET)
            
            -- Hook into guild frame opening to auto-export
            -- CommunitiesFrame is the modern guild UI
            if CommunitiesFrame then
                CommunitiesFrame:HookScript("OnShow", function()
                    if not guildWindowExportDone then
                        local onCooldown = IsCooldownActive()
                        if not onCooldown then
                            Print(WHITE .. "Guild window opened — auto-exporting roster..." .. RESET)
                            ExportGuildRoster(false)
                            guildWindowExportDone = true
                            
                            -- Reset the flag after cooldown window
                            C_Timer.After(COOLDOWN_SECONDS, function()
                                guildWindowExportDone = false
                            end)
                        end
                    end
                end)
            end
            
            -- Also try hooking GuildFrame for classic-style UI
            hooksecurefunc("ToggleGuildFrame", function()
                if not guildWindowExportDone then
                    local onCooldown = IsCooldownActive()
                    if not onCooldown then
                        ExportGuildRoster(false)
                        guildWindowExportDone = true
                        C_Timer.After(COOLDOWN_SECONDS, function()
                            guildWindowExportDone = false
                        end)
                    end
                end
            end)
        end
    
    elseif event == "GUILD_ROSTER_UPDATE" then
        if isExporting then
            ProcessGuildRoster()
        end
    end
end)

-- ============================================================
-- Slash Commands
-- ============================================================

SLASH_PATTSYNC1 = "/pattsync"
SLASH_PATTSYNC2 = "/patt"

SlashCmdList["PATTSYNC"] = function(msg)
    msg = strtrim(msg):lower()
    
    if msg == "" or msg == "help" then
        Print(GOLD .. "PATTSync Commands:" .. RESET)
        Print(WHITE .. "  /pattsync" .. RESET .. " — Export guild roster (respects cooldown)")
        Print(WHITE .. "  /pattsync force" .. RESET .. " — Export regardless of cooldown")
        Print(WHITE .. "  /pattsync status" .. RESET .. " — Show last export info")
        Print(WHITE .. "  /pattsync help" .. RESET .. " — Show this help")
        
    elseif msg == "force" then
        Print(GOLD .. "Forcing export..." .. RESET)
        ExportGuildRoster(true)
        
    elseif msg == "status" then
        local lastTime = PATTSyncDB.lastExportTime or 0
        local totalExports = PATTSyncDB.totalExports or 0
        
        if lastTime > 0 then
            local elapsed = GetTimestamp() - lastTime
            Print(WHITE .. "Last export: " .. GREEN .. 
                  FormatTimeDiff(elapsed) .. " ago" .. RESET)
            
            local lastExport = PATTSyncDB.lastExport
            if lastExport then
                Print(WHITE .. "  Members exported: " .. GREEN .. 
                      (lastExport.memberCount or "?") .. RESET)
            end
            
            local onCooldown, remaining = IsCooldownActive()
            if onCooldown then
                Print(WHITE .. "  Next export in: " .. GOLD .. 
                      FormatTimeDiff(remaining) .. RESET)
            else
                Print(WHITE .. "  Status: " .. GREEN .. "Ready to export" .. RESET)
            end
        else
            Print(WHITE .. "No exports yet this session." .. RESET)
        end
        
        Print(WHITE .. "  Total exports (all time): " .. GREEN .. 
              totalExports .. RESET)
        
    else
        ExportGuildRoster(false)
    end
end
```

### Addon Installation Instructions

```
1. Navigate to your WoW addons folder:
   C:\Program Files (x86)\World of Warcraft\_retail_\Interface\AddOns\

2. Create a folder called "PATTSync"

3. Place these files inside:
   PATTSync/
     PATTSync.toc
     PATTSync.lua

4. Restart WoW or type /reload

5. The addon will auto-export when you open the guild window (J key)
   or you can type /pattsync to manually trigger

6. After export, /reload or log out to flush SavedVariables to disk
   (The companion app watches for file changes automatically)
```

### SavedVariables Output Location

After export, the data is written to:
```
World of Warcraft/_retail_/WTF/Account/<ACCOUNT_NAME>/SavedVariables/PATTSync.lua
```

The file will contain a Lua table that looks like:
```lua
PATTSyncDB = {
    lastExportTime = 1740153600,
    totalExports = 5,
    lastExport = {
        exportTime = 1740153600,
        exportTimeISO = "2025-02-21T12:00:00Z",
        addonVersion = "1.0.0",
        guildName = "Pull All The Things",
        memberCount = 45,
        characters = {
            {
                name = "Trogmoon",
                realm = "Sen'jin",
                class = "Druid",
                classFile = "DRUID",
                level = 80,
                rank = 0,
                rankName = "Guild Leader",
                zone = "Dornogal",
                note = "GM / Mike",
                officerNote = "Discord: Trog",
                isOnline = true,
                lastOnline = "Online",
                achievementPoints = 12500,
            },
            -- ... more characters
        },
    },
}
```

## Task 2: Companion App — patt-sync-watcher

A standalone Python script that runs on the gaming PC. It watches the SavedVariables
file for changes and uploads new exports to the PATT API.

### File: companion_app/requirements.txt

```
watchdog>=3.0.0
httpx>=0.25.0
python-dotenv>=1.0.0
```

### File: companion_app/.env.example

```bash
# PATT Sync Companion App Configuration

# URL of your PATT API server
PATT_API_URL=https://pullallthething.com/api

# API key for authentication (generate one on the server)
PATT_API_KEY=your_api_key_here

# Path to your WoW SavedVariables folder
# Find your account name in: WoW/_retail_/WTF/Account/
WOW_SAVED_VARIABLES_PATH=C:\Program Files (x86)\World of Warcraft\_retail_\WTF\Account\YOUR_ACCOUNT_NAME\SavedVariables

# How often to check for file changes (seconds) — watchdog handles real-time,
# this is a fallback poll interval
POLL_INTERVAL=30

# Set to true for verbose logging
DEBUG=false
```

### File: companion_app/patt_sync_watcher.py

```python
#!/usr/bin/env python3
"""
PATT Sync Companion App

Watches for WoW addon SavedVariables updates and uploads guild roster data
to the PATT API server automatically.

Runs as a background process on the gaming PC.
Minimal resource usage — just watches a single file for changes.

Usage:
    python patt_sync_watcher.py

Or install as a Windows service / startup task.
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Load config
load_dotenv()

API_URL = os.getenv("PATT_API_URL", "https://pullallthething.com/api")
API_KEY = os.getenv("PATT_API_KEY", "")
WOW_SV_PATH = os.getenv("WOW_SAVED_VARIABLES_PATH", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# File to watch
TARGET_FILE = "PATTSync.lua"

# Minimum time between uploads (prevent rapid-fire on multiple writes)
UPLOAD_DEBOUNCE_SECONDS = 10

# Setup logging
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("patt_sync_watcher.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("PATTSync")


class LuaParser:
    """
    Minimal Lua table parser for SavedVariables files.
    
    WoW SavedVariables files are simple Lua assignments like:
        PATTSyncDB = { ... }
    
    This parser handles the subset of Lua used in SavedVariables:
    - Tables (nested)
    - Strings (double and single quoted)
    - Numbers (int and float)
    - Booleans (true/false)
    - nil
    - Array-style tables (sequential integer keys)
    - Hash-style tables (string keys with ["key"] or key = val syntax)
    """
    
    @staticmethod
    def parse_file(filepath: str) -> dict:
        """Parse a SavedVariables .lua file and return as Python dict."""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Find the main assignment: PATTSyncDB = { ... }
        match = re.search(r'PATTSyncDB\s*=\s*(\{.*\})\s*$', content, re.DOTALL)
        if not match:
            raise ValueError("Could not find PATTSyncDB table in file")
        
        table_str = match.group(1)
        result, _ = LuaParser._parse_value(table_str, 0)
        return result
    
    @staticmethod
    def _skip_whitespace_and_comments(s: str, pos: int) -> int:
        """Skip whitespace and Lua comments."""
        while pos < len(s):
            # Skip whitespace
            if s[pos] in ' \t\n\r':
                pos += 1
            # Skip line comments
            elif pos + 1 < len(s) and s[pos:pos+2] == '--':
                if pos + 3 < len(s) and s[pos+2:pos+4] == '[[':
                    # Block comment
                    end = s.find(']]', pos + 4)
                    pos = end + 2 if end != -1 else len(s)
                else:
                    # Line comment
                    end = s.find('\n', pos)
                    pos = end + 1 if end != -1 else len(s)
            else:
                break
        return pos
    
    @staticmethod
    def _parse_value(s: str, pos: int):
        """Parse a Lua value starting at pos. Returns (value, new_pos)."""
        pos = LuaParser._skip_whitespace_and_comments(s, pos)
        
        if pos >= len(s):
            return None, pos
        
        ch = s[pos]
        
        # Table
        if ch == '{':
            return LuaParser._parse_table(s, pos)
        
        # String (double-quoted)
        if ch == '"':
            return LuaParser._parse_string(s, pos, '"')
        
        # String (single-quoted)  
        if ch == "'":
            return LuaParser._parse_string(s, pos, "'")
        
        # Boolean or nil
        for keyword, value in [('true', True), ('false', False), ('nil', None)]:
            if s[pos:pos+len(keyword)] == keyword:
                next_pos = pos + len(keyword)
                if next_pos >= len(s) or not s[next_pos].isalnum():
                    return value, next_pos
        
        # Number (including negative)
        if ch.isdigit() or ch == '-' or ch == '.':
            return LuaParser._parse_number(s, pos)
        
        raise ValueError(f"Unexpected character '{ch}' at position {pos}")
    
    @staticmethod
    def _parse_string(s: str, pos: int, quote: str):
        """Parse a quoted string."""
        pos += 1  # Skip opening quote
        result = []
        while pos < len(s):
            ch = s[pos]
            if ch == '\\':
                pos += 1
                if pos < len(s):
                    esc = s[pos]
                    if esc == 'n': result.append('\n')
                    elif esc == 't': result.append('\t')
                    elif esc == '\\': result.append('\\')
                    elif esc == quote: result.append(quote)
                    else: result.append(esc)
                    pos += 1
            elif ch == quote:
                return ''.join(result), pos + 1
            else:
                result.append(ch)
                pos += 1
        raise ValueError("Unterminated string")
    
    @staticmethod
    def _parse_number(s: str, pos: int):
        """Parse a number (int or float)."""
        start = pos
        if s[pos] == '-':
            pos += 1
        while pos < len(s) and (s[pos].isdigit() or s[pos] == '.'):
            pos += 1
        num_str = s[start:pos]
        if '.' in num_str:
            return float(num_str), pos
        return int(num_str), pos
    
    @staticmethod
    def _parse_table(s: str, pos: int):
        """Parse a Lua table (array or hash)."""
        pos += 1  # Skip '{'
        result = {}
        array_index = 1
        is_array = True
        
        while True:
            pos = LuaParser._skip_whitespace_and_comments(s, pos)
            
            if pos >= len(s):
                break
            
            if s[pos] == '}':
                pos += 1
                break
            
            # Skip commas and semicolons between entries
            if s[pos] in ',;':
                pos += 1
                continue
            
            # Check for ["key"] = value syntax
            if s[pos] == '[':
                is_array = False
                pos += 1
                pos = LuaParser._skip_whitespace_and_comments(s, pos)
                key, pos = LuaParser._parse_value(s, pos)
                pos = LuaParser._skip_whitespace_and_comments(s, pos)
                if pos < len(s) and s[pos] == ']':
                    pos += 1
                pos = LuaParser._skip_whitespace_and_comments(s, pos)
                if pos < len(s) and s[pos] == '=':
                    pos += 1
                value, pos = LuaParser._parse_value(s, pos)
                result[key] = value
            
            # Check for key = value syntax (identifier key)
            elif s[pos].isalpha() or s[pos] == '_':
                # Look ahead for '='
                key_start = pos
                while pos < len(s) and (s[pos].isalnum() or s[pos] == '_'):
                    pos += 1
                key = s[key_start:pos]
                
                pos = LuaParser._skip_whitespace_and_comments(s, pos)
                
                if pos < len(s) and s[pos] == '=':
                    is_array = False
                    pos += 1
                    value, pos = LuaParser._parse_value(s, pos)
                    result[key] = value
                else:
                    # It was a value, not a key
                    # Backtrack and parse as value
                    pos = key_start
                    value, pos = LuaParser._parse_value(s, pos)
                    result[array_index] = value
                    array_index += 1
            
            else:
                # Array element
                value, pos = LuaParser._parse_value(s, pos)
                result[array_index] = value
                array_index += 1
        
        # Convert to list if it's a pure array
        if is_array and result:
            max_key = max(k for k in result.keys() if isinstance(k, int)) if result else 0
            if max_key > 0 and all(isinstance(k, int) for k in result.keys()):
                return [result.get(i) for i in range(1, max_key + 1)], pos
        
        return result, pos


class SyncWatcher(FileSystemEventHandler):
    """Watches for changes to the PATTSync SavedVariables file."""
    
    def __init__(self):
        self.last_upload_time = 0
        self.last_export_time = 0
    
    def on_modified(self, event):
        """Called when a file in the watched directory is modified."""
        if event.is_directory:
            return
        
        filename = os.path.basename(event.src_path)
        if filename != TARGET_FILE:
            return
        
        logger.info("Detected change in %s", TARGET_FILE)
        self._process_file(event.src_path)
    
    def _process_file(self, filepath: str):
        """Parse the SavedVariables file and upload if there's new data."""
        # Debounce: don't upload too frequently
        now = time.time()
        if now - self.last_upload_time < UPLOAD_DEBOUNCE_SECONDS:
            logger.debug("Debouncing — too soon since last upload")
            return
        
        try:
            # Wait a moment for the file to finish writing
            time.sleep(1)
            
            data = LuaParser.parse_file(filepath)
            
            last_export = data.get("lastExport")
            if not last_export:
                logger.warning("No lastExport data found in SavedVariables")
                return
            
            export_time = last_export.get("exportTime", 0)
            
            # Only upload if this is a NEW export
            if export_time <= self.last_export_time:
                logger.debug("Export time hasn't changed, skipping upload")
                return
            
            characters = last_export.get("characters", [])
            if not characters:
                logger.warning("No characters in export data")
                return
            
            logger.info(
                "New export detected: %d characters, exported at %s",
                len(characters),
                last_export.get("exportTimeISO", "unknown"),
            )
            
            # Transform to API format
            api_payload = {
                "characters": [
                    {
                        "name": char.get("name", ""),
                        "realm": char.get("realm", ""),
                        "class": char.get("class", ""),
                        "level": char.get("level", 0),
                        "rank": char.get("rank", 99),
                        "rank_name": char.get("rankName", "Unknown"),
                        "guild_note": char.get("note", ""),
                        "officer_note": char.get("officerNote", ""),
                        "is_online": char.get("isOnline", False),
                        "last_online": char.get("lastOnline", ""),
                        "zone": char.get("zone", ""),
                    }
                    for char in characters
                ],
                "addon_version": last_export.get("addonVersion", "unknown"),
                "uploaded_by": "companion_app",
            }
            
            # Upload to API
            self._upload(api_payload)
            
            self.last_export_time = export_time
            self.last_upload_time = now
            
        except Exception as e:
            logger.error("Error processing SavedVariables: %s", e, exc_info=True)
    
    def _upload(self, payload: dict):
        """Upload parsed data to the PATT API."""
        url = f"{API_URL}/guild-sync/addon-upload"
        
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={
                    "X-API-Key": API_KEY,
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(
                    "Upload successful: %d characters processed",
                    result.get("characters_received", 0),
                )
            else:
                logger.error(
                    "Upload failed (HTTP %d): %s",
                    response.status_code,
                    response.text[:500],
                )
        
        except httpx.ConnectError:
            logger.error("Cannot connect to API at %s — is the server running?", API_URL)
        except Exception as e:
            logger.error("Upload error: %s", e, exc_info=True)


def validate_config():
    """Validate configuration before starting."""
    errors = []
    
    if not API_URL:
        errors.append("PATT_API_URL is not set")
    if not API_KEY:
        errors.append("PATT_API_KEY is not set")
    if not WOW_SV_PATH:
        errors.append("WOW_SAVED_VARIABLES_PATH is not set")
    elif not os.path.isdir(WOW_SV_PATH):
        errors.append(f"WOW_SAVED_VARIABLES_PATH does not exist: {WOW_SV_PATH}")
    
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        logger.error("Copy .env.example to .env and fill in your values")
        sys.exit(1)


def main():
    """Main entry point."""
    print("=" * 50)
    print("  PATT Sync Companion App")
    print("  Watching for WoW addon exports...")
    print("=" * 50)
    
    validate_config()
    
    watch_path = WOW_SV_PATH
    target_file = os.path.join(watch_path, TARGET_FILE)
    
    logger.info("Watching: %s", watch_path)
    logger.info("Target file: %s", TARGET_FILE)
    logger.info("API endpoint: %s", API_URL)
    
    # Process existing file on startup (in case we missed an export)
    if os.path.exists(target_file):
        logger.info("Found existing SavedVariables file, checking for unuploaded data...")
        watcher = SyncWatcher()
        watcher._process_file(target_file)
    else:
        watcher = SyncWatcher()
        logger.info("No existing file found — waiting for first export from WoW addon")
    
    # Start watching
    observer = Observer()
    observer.schedule(watcher, watch_path, recursive=False)
    observer.start()
    
    try:
        while True:
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        observer.stop()
    
    observer.join()
    logger.info("Companion app stopped.")


if __name__ == "__main__":
    main()
```

### File: companion_app/README.md

```markdown
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
   `C:\Program Files (x86)\World of Warcraft\_retail_\WTF\Account\12345678\SavedVariables`

## Running on Startup (Windows)

1. Press Win+R, type `shell:startup`, hit Enter
2. Create a shortcut to `patt_sync_watcher.py` in that folder
3. Or create a .bat file with:
   ```batch
   @echo off
   cd /d "C:\path\to\companion_app"
   python patt_sync_watcher.py
   ```

## How It Works

1. The PATTSync WoW addon exports guild roster data to SavedVariables
2. SavedVariables are written to disk on /reload or logout
3. This companion app detects the file change
4. It parses the Lua data and uploads it to the PATT API
5. The server processes the data and updates the identity system

## Troubleshooting

- **"No existing file found"**: You haven't exported from the addon yet. 
  Open your guild window in WoW or type `/pattsync`
- **"Cannot connect to API"**: Check your PATT_API_URL in .env
- **"Invalid API key"**: Check your PATT_API_KEY in .env
- **Data not updating**: Make sure to /reload or log out after exporting 
  to flush SavedVariables to disk
```

## Testing Requirements for Phase 2.5C

1. **Lua parser tests:**
   - Test parsing simple tables
   - Test parsing nested tables
   - Test parsing arrays vs hash tables
   - Test string escaping
   - Test a realistic SavedVariables file
   - Test handling of malformed input

2. **Companion app tests:**
   - Test file change detection (mock watchdog)
   - Test debouncing (rapid changes don't cause multiple uploads)
   - Test API upload success/failure handling
   - Test startup with existing file
   - Test config validation

3. **Addon tests (manual):**
   - Install addon, verify it loads without errors
   - Open guild window, verify auto-export triggers
   - Type /pattsync, verify manual export works
   - Type /pattsync status, verify cooldown tracking
   - Type /pattsync force, verify cooldown bypass
   - Verify SavedVariables file is written on /reload
