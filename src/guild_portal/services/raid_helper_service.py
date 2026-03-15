"""Raid-Helper API client.

All HTTP calls to Raid-Helper originate here — no CORS issues because
requests come from the FastAPI server, not the browser.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://raid-helper.dev/api/v2"


class RaidHelperError(Exception):
    """Raised when the Raid-Helper API returns an error."""


# ---------------------------------------------------------------------------
# Spec → Raid-Helper mapping
# ---------------------------------------------------------------------------

# Maps (class_name, spec_name) → (raid_helper_class, raid_helper_spec)
SPEC_TO_RAID_HELPER: dict[tuple[str, str], tuple[str, str]] = {
    ("Death Knight", "Blood"): ("Death Knight", "Blood"),
    ("Death Knight", "Frost"): ("Death Knight", "Frost"),
    ("Death Knight", "Unholy"): ("Death Knight", "Unholy"),
    ("Demon Hunter", "Havoc"): ("Demon Hunter", "Havoc"),
    ("Demon Hunter", "Vengeance"): ("Demon Hunter", "Vengeance"),
    ("Druid", "Balance"): ("Druid", "Balance"),
    ("Druid", "Feral"): ("Druid", "Feral"),
    ("Druid", "Guardian"): ("Druid", "Guardian"),
    ("Druid", "Restoration"): ("Druid", "Restoration"),
    ("Evoker", "Devastation"): ("Evoker", "Devastation"),
    ("Evoker", "Preservation"): ("Evoker", "Preservation"),
    ("Evoker", "Augmentation"): ("Evoker", "Augmentation"),
    ("Hunter", "Beast Mastery"): ("Hunter", "Beast Mastery"),
    ("Hunter", "Marksmanship"): ("Hunter", "Marksmanship"),
    ("Hunter", "Survival"): ("Hunter", "Survival"),
    ("Mage", "Arcane"): ("Mage", "Arcane"),
    ("Mage", "Fire"): ("Mage", "Fire"),
    ("Mage", "Frost"): ("Mage", "Frost"),
    ("Monk", "Brewmaster"): ("Monk", "Brewmaster"),
    ("Monk", "Mistweaver"): ("Monk", "Mistweaver"),
    ("Monk", "Windwalker"): ("Monk", "Windwalker"),
    ("Paladin", "Holy"): ("Paladin", "Holy"),
    ("Paladin", "Protection"): ("Paladin", "Protection"),
    ("Paladin", "Retribution"): ("Paladin", "Retribution"),
    ("Priest", "Discipline"): ("Priest", "Discipline"),
    ("Priest", "Holy"): ("Priest", "Holy"),
    ("Priest", "Shadow"): ("Priest", "Shadow"),
    ("Rogue", "Assassination"): ("Rogue", "Assassination"),
    ("Rogue", "Outlaw"): ("Rogue", "Outlaw"),
    ("Rogue", "Subtlety"): ("Rogue", "Subtlety"),
    ("Shaman", "Elemental"): ("Shaman", "Elemental"),
    ("Shaman", "Enhancement"): ("Shaman", "Enhancement"),
    ("Shaman", "Restoration"): ("Shaman", "Restoration"),
    ("Warlock", "Affliction"): ("Warlock", "Affliction"),
    ("Warlock", "Demonology"): ("Warlock", "Demonology"),
    ("Warlock", "Destruction"): ("Warlock", "Destruction"),
    ("Warrior", "Arms"): ("Warrior", "Arms"),
    ("Warrior", "Fury"): ("Warrior", "Fury"),
    ("Warrior", "Protection"): ("Warrior", "Protection"),
}


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------


async def create_event(
    config: dict[str, Any],
    title: str,
    event_type: str,
    start_time_utc: datetime,
    duration_minutes: int,
    channel_id: str,
    description: str,
    template_id: str = "wowretail2",
    signups: list[dict] | None = None,
) -> dict[str, Any]:
    """POST to Raid-Helper API to create an event.

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

    # Raid-Helper v2 expects `time` as a Unix epoch timestamp (seconds).
    # Sending HH:MM causes it to interpret the time in whatever timezone the
    # Raid-Helper bot defaults to, which is never what we want.
    payload: dict[str, Any] = {
        "leaderId": config.get("raid_creator_discord_id"),
        "templateId": template_id or config.get("raid_default_template_id") or "wowretail2",
        "date": f"{start_time_utc.day}-{start_time_utc.month}-{start_time_utc.year}",
        "time": int(start_time_utc.timestamp()),
        "title": title,
        "description": description,
        "duration": duration_minutes,
    }

    # Raid-Helper signup status codes: 1=Signed Up, 2=Bench, 3=Tentative
    _STATUS_CODE = {"accepted": 1, "bench": 2, "tentative": 3}

    if signups:
        rh_signups = []
        for s in signups:
            entry: dict[str, Any] = {"userId": s["userId"]}
            if "status" in s:
                entry["statusId"] = _STATUS_CODE.get(s["status"], 1)
            if "class" in s:
                entry["className"] = s["class"]
            if "spec" in s:
                entry["specName"] = s["spec"]
            rh_signups.append(entry)
        payload["signups"] = rh_signups

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_BASE_URL}/servers/{server_id}/channels/{effective_channel}/event",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=15.0,
        )

    if not resp.is_success:
        raise RaidHelperError(f"Raid-Helper API error {resp.status_code}: {resp.text[:200]}")

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
