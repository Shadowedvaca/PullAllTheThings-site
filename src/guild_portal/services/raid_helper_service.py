"""Raid-Helper API client.

All HTTP calls to Raid-Helper originate here — no CORS issues because
requests come from the FastAPI server, not the browser.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://raid-helper.xyz/api/v2"


class RaidHelperError(Exception):
    """Raised when the Raid-Helper API returns an error."""


# ---------------------------------------------------------------------------
# Spec → Raid-Helper mapping
# ---------------------------------------------------------------------------

# Maps (wow_class_name, wow_spec_name) → (raid_helper_className, raid_helper_specName)
# className must match the wowretail2 template role names: Tank, Healer, Melee, Ranged
# specName must match the exact spec name in the template (some differ from WoW names)
SPEC_TO_RAID_HELPER: dict[tuple[str, str], tuple[str, str]] = {
    # Tanks
    ("Death Knight", "Blood"): ("Tank", "Blood"),
    ("Demon Hunter", "Vengeance"): ("Tank", "Vengeance"),
    ("Druid", "Guardian"): ("Tank", "Guardian"),
    ("Monk", "Brewmaster"): ("Tank", "Brewmaster"),
    ("Paladin", "Protection"): ("Tank", "Protection"),
    ("Warrior", "Protection"): ("Tank", "Protection1"),  # Protection1 = Warrior
    # Healers
    ("Druid", "Restoration"): ("Healer", "Restoration"),
    ("Evoker", "Preservation"): ("Healer", "Preservation"),
    ("Monk", "Mistweaver"): ("Healer", "Mistweaver"),
    ("Paladin", "Holy"): ("Healer", "Holy1"),             # Holy1 = Paladin, Holy = Priest
    ("Priest", "Discipline"): ("Healer", "Discipline"),
    ("Priest", "Holy"): ("Healer", "Holy"),
    ("Shaman", "Restoration"): ("Healer", "Restoration1"),  # Restoration1 = Shaman
    # Melee DPS
    ("Death Knight", "Frost"): ("Melee", "Frost1"),       # Frost1 = DK, Frost = Mage
    ("Death Knight", "Unholy"): ("Melee", "Unholy"),
    ("Demon Hunter", "Havoc"): ("Melee", "Havoc"),
    ("Druid", "Feral"): ("Melee", "Feral"),
    ("Hunter", "Survival"): ("Melee", "Survival"),
    ("Monk", "Windwalker"): ("Melee", "Windwalker"),
    ("Paladin", "Retribution"): ("Melee", "Retribution"),
    ("Rogue", "Assassination"): ("Melee", "Assassination"),
    ("Rogue", "Outlaw"): ("Melee", "Outlaw"),
    ("Rogue", "Subtlety"): ("Melee", "Subtlety"),
    ("Shaman", "Enhancement"): ("Melee", "Enhancement"),
    ("Warrior", "Arms"): ("Melee", "Arms"),
    ("Warrior", "Fury"): ("Melee", "Fury"),
    # Ranged DPS
    ("Druid", "Balance"): ("Ranged", "Balance"),
    ("Evoker", "Devastation"): ("Ranged", "Devastation"),
    ("Evoker", "Augmentation"): ("Ranged", "Augmentation"),
    ("Hunter", "Beast Mastery"): ("Ranged", "Beastmastery"),  # no space
    ("Hunter", "Marksmanship"): ("Ranged", "Marksmanship"),
    ("Mage", "Arcane"): ("Ranged", "Arcane"),
    ("Mage", "Fire"): ("Ranged", "Fire"),
    ("Mage", "Frost"): ("Ranged", "Frost"),
    ("Priest", "Shadow"): ("Ranged", "Shadow"),
    ("Shaman", "Elemental"): ("Ranged", "Elemental"),
    ("Warlock", "Affliction"): ("Ranged", "Affliction"),
    ("Warlock", "Demonology"): ("Ranged", "Demonology"),
    ("Warlock", "Destruction"): ("Ranged", "Destruction"),
}


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------


async def create_event(
    config: dict[str, Any],
    title: str,
    event_type: str,
    start_time_utc: datetime,
    start_time_local: datetime,
    duration_minutes: int,
    channel_id: str,
    description: str,
    template_id: str = "wowretail2",
) -> dict[str, Any]:
    """POST to Raid-Helper API to create an event.

    start_time_local is a timezone-aware datetime in the user's chosen timezone.
    It is used for the Raid-Helper payload (date + time fields), which the
    Raid-Helper bot interprets in its own configured server timezone.  By
    sending the local date/time we match what the user expects to appear in
    Discord regardless of how Raid-Helper's server-side timezone is set.

    Returns {"event_id": ..., "event_url": ..., "payload": ...} on success.
    Raises RaidHelperError on failure.
    """
    server_id = config.get("raid_helper_server_id")
    api_key = config.get("raid_helper_api_key")
    if not server_id or not api_key:
        raise RaidHelperError("Raid-Helper is not configured (missing server_id or api_key)")

    effective_channel = channel_id or config.get("raid_channel_id") or ""
    if not effective_channel:
        raise RaidHelperError("No channel_id configured for event creation")

    # Raid-Helper v2 date/time: D-M-YYYY and HH:MM in the LOCAL timezone.
    # Raid-Helper's Discord bot converts these to a Discord timestamp using its
    # own server-configured timezone, so we send the local (human-readable)
    # values, not UTC.
    rh_date = f"{start_time_local.day}-{start_time_local.month}-{start_time_local.year}"
    rh_time = start_time_local.strftime("%H:%M")

    payload: dict[str, Any] = {
        "leaderId": config.get("raid_creator_discord_id"),
        "templateId": template_id or config.get("raid_default_template_id") or "wowretail2",
        "date": rh_date,
        "time": rh_time,
        "title": title,
        "description": description,
        "duration": duration_minutes,
    }

    logger.info(
        "Raid-Helper create_event payload: date=%s time=%s",
        rh_date, rh_time,
    )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_BASE_URL}/servers/{server_id}/channels/{effective_channel}/event/",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=15.0,
        )

    if not resp.is_success:
        location = resp.headers.get("location", "")
        raise RaidHelperError(
            f"Raid-Helper API error {resp.status_code}"
            + (f" → {location}" if location else "")
            + f": {resp.text[:200]}"
        )

    data = resp.json()
    event = data.get("event", data)
    event_id = event.get("id")
    event_url = (
        f"https://discord.com/channels/{server_id}/{effective_channel}/{event_id}"
        if event_id else ""
    )
    return {
        "event_id": event_id,
        "event_url": event_url,
        "payload": payload,
    }


async def add_signups_to_event(
    api_key: str,
    event_id: str,
    signups: list[dict],
) -> tuple[int, int]:
    """Add pre-populated signups to an existing Raid-Helper event.

    Calls POST /api/v2/events/{eventId}/signups for each signup sequentially
    with a 200 ms delay between requests to avoid rate limiting.
    Signup dicts must have keys: discord_id (or userId), and optionally
    class_name (or class) and spec_name (or spec).
    Returns (success_count, fail_count).
    """
    # Status values that map to specific Raid-Helper slot classes (override role className)
    _STATUS_CLASS = {"tentative": "Tentative", "bench": "Bench", "absence": "Absence"}

    ok = 0
    fail = 0
    async with httpx.AsyncClient() as client:
        for s in signups:
            # Accept both field name conventions
            user_id = s.get("discord_id") or s.get("userId")
            if not user_id:
                continue
            status = s.get("status", "accepted")
            slot_class = _STATUS_CLASS.get(status)
            if slot_class:
                # Non-accepted: sign up into the status slot, no class/spec
                body: dict[str, Any] = {"userId": user_id, "className": slot_class}
            else:
                # Accepted: use role-based class and spec
                class_name = s.get("class_name") or s.get("class")
                spec_name = s.get("spec_name") or s.get("spec")
                body = {"userId": user_id}
                if class_name:
                    body["className"] = class_name
                if spec_name:
                    body["specName"] = spec_name
            logger.info("Raid-Helper signup body for %s: %s", user_id, body)
            try:
                resp = await client.post(
                    f"{_BASE_URL}/events/{event_id}/signups",
                    headers={"Authorization": api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=10.0,
                )
                if resp.is_success:
                    ok += 1
                else:
                    logger.warning(
                        "Raid-Helper signup failed for user %s: %s %s",
                        user_id, resp.status_code, resp.text[:100],
                    )
                    fail += 1
            except Exception as exc:
                logger.warning("Raid-Helper signup error for user %s: %s", user_id, exc)
                fail += 1
            await asyncio.sleep(0.2)

    logger.info("Raid-Helper signups: %d ok, %d failed (event %s)", ok, fail, event_id)
    return ok, fail


async def test_connection(config: dict[str, Any]) -> dict[str, Any]:
    """Make a benign API call to validate Raid-Helper config.

    Returns {"connected": True, "server_name": "..."} on success.
    Raises RaidHelperError on failure.
    """
    server_id = config.get("raid_helper_server_id")
    api_key = config.get("raid_helper_api_key")
    if not server_id or not api_key:
        raise RaidHelperError("Missing raid_helper_server_id or raid_helper_api_key")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_BASE_URL}/servers/{server_id}/events",
            headers={"Authorization": api_key},
            timeout=10.0,
        )

    if not resp.is_success:
        raise RaidHelperError(f"Raid-Helper API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    posted = data.get("postedEvents", [])
    return {
        "connected": True,
        "server_name": f"Server {server_id}",
        "event_count": len(posted),
    }
