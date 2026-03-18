"""
sv_common.feedback — client-side feedback collection.

Stores raw feedback locally (PII included), generates a one-way privacy token,
and forwards a de-identified payload to the Hub for AI processing.

Public API:
    submit_feedback(pool, ...)  →  dict

All Hub sync failures are routed through sv_common.errors (report_error /
resolve_issue). No errors are swallowed silently.

Design: no Discord, no FastAPI, no Jinja2. Pure asyncpg + httpx.
Requires: asyncpg.Pool, FEEDBACK_HUB_URL, FEEDBACK_INGEST_KEY, FEEDBACK_PRIVACY_SALT
"""
from __future__ import annotations
import logging
from typing import Optional

import asyncpg

from ._privacy import make_privacy_token
from ._store import _insert_submission, _update_hub_ref
from ._hub_client import post_to_hub, HubNotConfiguredError, HubSyncError
from sv_common.config_cache import get_program_name
from sv_common.errors import report_error, resolve_issue

logger = logging.getLogger(__name__)

__all__ = ["submit_feedback"]

_SOURCE_MODULE = "sv_common.feedback"
_ISSUE_TYPE = "feedback_hub_sync_failed"


async def submit_feedback(
    pool: asyncpg.Pool,
    score: int,
    raw_feedback: str,
    is_authenticated_user: bool = False,
    contact_info: Optional[str] = None,
    is_anonymous: bool = False,
    program_name: Optional[str] = None,
) -> dict:
    """
    Full feedback submission flow:
    1. Validate inputs
    2. Generate privacy token (one-way hash; None if anonymous or no contact)
    3. Insert local record (PII stored here)
    4. POST de-identified payload to Hub
       - Success: update local record with hub_feedback_id; resolve any open error
       - HubNotConfiguredError: report_error (warning); local record still saved
       - HubSyncError: report_error (warning); local record still saved

    Returns a dict with the local record id and hub_feedback_id (None if Hub failed).
    Raises ValueError on invalid inputs.
    Local record is always saved regardless of Hub outcome.
    """
    if not raw_feedback or not raw_feedback.strip():
        raise ValueError("raw_feedback must not be empty")
    if not (1 <= score <= 10):
        raise ValueError("score must be between 1 and 10")

    prog = program_name or get_program_name()
    privacy_token = make_privacy_token(contact_info, is_anonymous)
    stored_contact = None if is_anonymous else contact_info

    local_id = await _insert_submission(
        pool=pool,
        program_name=prog,
        score=score,
        raw_feedback=raw_feedback,
        is_authenticated_user=is_authenticated_user,
        is_anonymous=is_anonymous,
        contact_info=stored_contact,
        privacy_token=privacy_token,
    )

    hub_id: Optional[int] = None

    try:
        hub_id = await post_to_hub(
            program_name=prog,
            score=score,
            raw_feedback=raw_feedback,
            is_authenticated_user=is_authenticated_user,
            is_anonymous=is_anonymous,
            privacy_token=privacy_token,
        )
        await _update_hub_ref(pool, local_id, hub_id)
        await resolve_issue(pool, _ISSUE_TYPE)
        logger.info("Feedback submitted: local_id=%d hub_id=%d", local_id, hub_id)

    except HubNotConfiguredError as exc:
        await report_error(
            pool,
            issue_type=_ISSUE_TYPE,
            severity="warning",
            summary="Feedback Hub not configured — FEEDBACK_HUB_URL or FEEDBACK_INGEST_KEY missing",
            source_module=_SOURCE_MODULE,
            details={"error": str(exc), "local_id": local_id},
        )
        logger.warning(
            "Feedback Hub not configured; local record saved: local_id=%d", local_id
        )

    except HubSyncError as exc:
        await report_error(
            pool,
            issue_type=_ISSUE_TYPE,
            severity="warning",
            summary=f"Feedback Hub sync failed: {exc}",
            source_module=_SOURCE_MODULE,
            details={"error": str(exc), "local_id": local_id},
        )
        logger.warning(
            "Feedback Hub sync failed; local record saved: local_id=%d error=%s",
            local_id, exc,
        )

    return {
        "id": local_id,
        "hub_feedback_id": hub_id,
        "program_name": prog,
    }
