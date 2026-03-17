"""
HTTP client for the Hub ingest endpoint.

Raises HubNotConfiguredError when env vars are absent.
Raises HubSyncError on any network or HTTP failure.
Never swallows exceptions — callers own error reporting.
"""
from __future__ import annotations
import os
from typing import Optional

import httpx


class HubNotConfiguredError(Exception):
    """FEEDBACK_HUB_URL or FEEDBACK_INGEST_KEY is not set."""


class HubSyncError(Exception):
    """Hub ingest call failed (network error, HTTP error, or unexpected response)."""


async def post_to_hub(
    program_name: str,
    score: int,
    raw_feedback: str,
    is_authenticated_user: bool,
    is_anonymous: bool,
    privacy_token: Optional[str],
) -> int:
    """
    POST de-identified payload to Hub ingest endpoint.

    Returns hub_feedback_id (int) on success.
    Raises HubNotConfiguredError if FEEDBACK_HUB_URL or FEEDBACK_INGEST_KEY is unset.
    Raises HubSyncError on any network or HTTP failure.
    """
    hub_url = os.environ.get("FEEDBACK_HUB_URL", "").rstrip("/")
    ingest_key = os.environ.get("FEEDBACK_INGEST_KEY", "")

    if not hub_url or not ingest_key:
        raise HubNotConfiguredError(
            "FEEDBACK_HUB_URL or FEEDBACK_INGEST_KEY not set"
        )

    payload = {
        "program_name":          program_name,
        "score":                 score,
        "raw_feedback":          raw_feedback,
        "is_authenticated_user": is_authenticated_user,
        "is_anonymous":          is_anonymous,
        "privacy_token":         privacy_token,
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{hub_url}/api/feedback/ingest",
                json=payload,
                headers={"X-Ingest-Key": ingest_key},
            )
            resp.raise_for_status()
            data = resp.json()
            hub_id = data.get("hub_feedback_id")
            if hub_id is None:
                raise HubSyncError("Hub response missing hub_feedback_id field")
            return hub_id

    except (HubNotConfiguredError, HubSyncError):
        raise
    except Exception as exc:
        raise HubSyncError(str(exc)) from exc
