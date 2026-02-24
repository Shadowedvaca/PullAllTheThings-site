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

    payload: dict[str, Any] = {
        "leaderId": config.get("raid_creator_discord_id"),
        "templateId": template_id or config.get("raid_default_template_id") or "wowretail2",
        "date": start_time_utc.strftime("%Y-%m-%d"),
        "time": start_time_utc.strftime("%H:%M"),
        "title": title,
        "description": description,
        "channelId": channel_id or config.get("raid_channel_id") or "",
        "duration": duration_minutes,
    }

    if signups:
        payload["signups"] = signups

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_BASE_URL}/servers/{server_id}/event",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=15.0,
        )

    if not resp.is_success:
        raise RaidHelperError(f"Raid-Helper API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    return {
        "event_id": data.get("id"),
        "event_url": data.get("url", ""),
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
            f"{_BASE_URL}/servers/{server_id}",
            headers={"Authorization": api_key},
            timeout=10.0,
        )

    if not resp.is_success:
        raise RaidHelperError(f"Raid-Helper API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    return {
        "connected": True,
        "server_name": data.get("name") or data.get("serverName") or "Unknown",
    }
