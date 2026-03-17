"""
sv_common.feedback — client-side feedback collection.

Stores raw feedback locally (PII included), generates a one-way privacy token,
and forwards a de-identified payload to the Hub for AI processing.

Public API:
    submit_feedback(pool, ...)  →  dict

Design: no Discord, no FastAPI, no Jinja2. Pure asyncpg + httpx.
Requires: asyncpg.Pool, FEEDBACK_HUB_URL, FEEDBACK_INGEST_KEY, FEEDBACK_PRIVACY_SALT
"""
from __future__ import annotations
import logging
from typing import Optional

import asyncpg

from ._privacy import make_privacy_token
from ._store import _insert_submission, _update_hub_ref
from ._hub_client import post_to_hub
from sv_common.config_cache import get_program_name

logger = logging.getLogger(__name__)

__all__ = ["submit_feedback"]


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
    4. POST de-identified payload to Hub (fire-and-forget)
    5. If Hub responds, store hub_feedback_id on local record

    Returns a dict with the local record's id and hub_feedback_id (may be None).
    Raises ValueError on invalid inputs.
    Local record is always saved even if Hub call fails.
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

    hub_id = await post_to_hub(
        program_name=prog,
        score=score,
        raw_feedback=raw_feedback,
        is_authenticated_user=is_authenticated_user,
        is_anonymous=is_anonymous,
        privacy_token=privacy_token,
    )

    if hub_id is not None:
        await _update_hub_ref(pool, local_id, hub_id)
        logger.info("Feedback submitted: local_id=%d hub_id=%d", local_id, hub_id)
    else:
        logger.info("Feedback submitted locally: local_id=%d (Hub sync pending)", local_id)

    return {
        "id": local_id,
        "hub_feedback_id": hub_id,
        "program_name": prog,
    }
