"""
HTTP client for the Hub ingest endpoint.
Fire-and-forget: caller handles None return gracefully.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def post_to_hub(
    program_name: str,
    score: int,
    raw_feedback: str,
    is_authenticated_user: bool,
    is_anonymous: bool,
    privacy_token: Optional[str],
) -> Optional[int]:
    """
    POST de-identified payload to Hub ingest endpoint.

    Returns hub_feedback_id on success, None on any failure.
    Never raises — all exceptions are caught and logged.
    """
    hub_url = os.environ.get("FEEDBACK_HUB_URL", "").rstrip("/")
    ingest_key = os.environ.get("FEEDBACK_INGEST_KEY", "")

    if not hub_url or not ingest_key:
        logger.warning("FEEDBACK_HUB_URL or FEEDBACK_INGEST_KEY not set; skipping Hub sync")
        return None

    payload = {
        "program_name":          program_name,
        "score":                 score,
        "raw_feedback":          raw_feedback,
        "is_authenticated_user": is_authenticated_user,
        "is_anonymous":          is_anonymous,
        "privacy_token":         privacy_token,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{hub_url}/api/feedback/ingest",
                json=payload,
                headers={"X-Ingest-Key": ingest_key},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("hub_feedback_id")

    except Exception as exc:
        logger.error("Hub feedback ingest failed (local record still saved): %s", exc)
        return None
