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
    start_time_local: datetime,
    duration_minutes: int,
    channel_id: str,
    description: str,
    template_id: str = "wowretail2",
    signups: list[dict] | None = None,
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
        "Raid-Helper create_event payload: date=%s time=%s signups=%d",
        rh_date, rh_time, len(signups) if signups else 0,
    )

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


async def add_signups_to_event(
    api_key: str,
    event_id: str,
    signups: list[dict],
) -> tuple[int, int]:
    """Add pre-populated signups to an existing Raid-Helper event.

    Calls PUT /api/v2/events/{eventId}/signup/{userId} for each signup
    concurrently.  Returns (success_count, fail_count).
    """
    # Raid-Helper signup status codes: 1=Signed Up, 2=Bench, 3=Tentative
    _STATUS_CODE = {"accepted": 1, "bench": 2, "tentative": 3}

    async def _add_one(client: httpx.AsyncClient, s: dict) -> bool:
        user_id = s["userId"]
        body: dict[str, Any] = {
            "statusId": _STATUS_CODE.get(s.get("status", "accepted"), 1),
        }
        if s.get("class"):
            body["className"] = s["class"]
        if s.get("spec"):
            body["specName"] = s["spec"]
        try:
            resp = await client.put(
                f"{_BASE_URL}/events/{event_id}/signup/{user_id}",
                headers={"Authorization": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=10.0,
            )
            if not resp.is_success:
                logger.warning(
                    "Raid-Helper signup failed for user %s: %s %s",
                    user_id, resp.status_code, resp.text[:100],
                )
            return resp.is_success
        except Exception as exc:
            logger.warning("Raid-Helper signup error for user %s: %s", user_id, exc)
            return False

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_add_one(client, s) for s in signups])

    ok = sum(results)
    fail = len(results) - ok
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
