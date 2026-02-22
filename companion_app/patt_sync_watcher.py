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
        match = re.search(r"PATTSyncDB\s*=\s*(\{.*\})\s*$", content, re.DOTALL)
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
            if s[pos] in " \t\n\r":
                pos += 1
            # Skip line comments
            elif pos + 1 < len(s) and s[pos : pos + 2] == "--":
                if pos + 3 < len(s) and s[pos + 2 : pos + 4] == "[[":
                    # Block comment
                    end = s.find("]]", pos + 4)
                    pos = end + 2 if end != -1 else len(s)
                else:
                    # Line comment
                    end = s.find("\n", pos)
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
        if ch == "{":
            return LuaParser._parse_table(s, pos)

        # String (double-quoted)
        if ch == '"':
            return LuaParser._parse_string(s, pos, '"')

        # String (single-quoted)
        if ch == "'":
            return LuaParser._parse_string(s, pos, "'")

        # Boolean or nil
        for keyword, value in [("true", True), ("false", False), ("nil", None)]:
            if s[pos : pos + len(keyword)] == keyword:
                next_pos = pos + len(keyword)
                if next_pos >= len(s) or not s[next_pos].isalnum():
                    return value, next_pos

        # Number (including negative)
        if ch.isdigit() or ch == "-" or ch == ".":
            return LuaParser._parse_number(s, pos)

        raise ValueError(f"Unexpected character '{ch}' at position {pos}")

    @staticmethod
    def _parse_string(s: str, pos: int, quote: str):
        """Parse a quoted string."""
        pos += 1  # Skip opening quote
        result = []
        while pos < len(s):
            ch = s[pos]
            if ch == "\\":
                pos += 1
                if pos < len(s):
                    esc = s[pos]
                    if esc == "n":
                        result.append("\n")
                    elif esc == "t":
                        result.append("\t")
                    elif esc == "\\":
                        result.append("\\")
                    elif esc == quote:
                        result.append(quote)
                    else:
                        result.append(esc)
                    pos += 1
            elif ch == quote:
                return "".join(result), pos + 1
            else:
                result.append(ch)
                pos += 1
        raise ValueError("Unterminated string")

    @staticmethod
    def _parse_number(s: str, pos: int):
        """Parse a number (int or float)."""
        start = pos
        if s[pos] == "-":
            pos += 1
        while pos < len(s) and (s[pos].isdigit() or s[pos] == "."):
            pos += 1
        num_str = s[start:pos]
        if "." in num_str:
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

            if s[pos] == "}":
                pos += 1
                break

            # Skip commas and semicolons between entries
            if s[pos] in ",;":
                pos += 1
                continue

            # Check for ["key"] = value syntax
            if s[pos] == "[":
                is_array = False
                pos += 1
                pos = LuaParser._skip_whitespace_and_comments(s, pos)
                key, pos = LuaParser._parse_value(s, pos)
                pos = LuaParser._skip_whitespace_and_comments(s, pos)
                if pos < len(s) and s[pos] == "]":
                    pos += 1
                pos = LuaParser._skip_whitespace_and_comments(s, pos)
                if pos < len(s) and s[pos] == "=":
                    pos += 1
                value, pos = LuaParser._parse_value(s, pos)
                result[key] = value

            # Check for key = value syntax (identifier key)
            elif s[pos].isalpha() or s[pos] == "_":
                # Look ahead for '='
                key_start = pos
                while pos < len(s) and (s[pos].isalnum() or s[pos] == "_"):
                    pos += 1
                key = s[key_start:pos]

                pos = LuaParser._skip_whitespace_and_comments(s, pos)

                if pos < len(s) and s[pos] == "=":
                    is_array = False
                    pos += 1
                    value, pos = LuaParser._parse_value(s, pos)
                    result[key] = value
                else:
                    # It was a value, not a key — backtrack and parse as array element
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
            max_key = max((k for k in result.keys() if isinstance(k, int)), default=0)
            if max_key > 0 and all(isinstance(k, int) for k in result.keys()):
                return [result.get(i) for i in range(1, max_key + 1)], pos

        return result, pos


class SyncWatcher(FileSystemEventHandler):
    """Watches for changes to the PATTSync SavedVariables file."""

    def __init__(self):
        self.last_upload_time = 0.0
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
    watcher = SyncWatcher()
    if os.path.exists(target_file):
        logger.info("Found existing SavedVariables file, checking for unuploaded data...")
        watcher._process_file(target_file)
    else:
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
