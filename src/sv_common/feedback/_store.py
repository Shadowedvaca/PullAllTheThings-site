"""
asyncpg queries for common.feedback_submissions.
Internal — called only by sv_common.feedback.__init__.
"""
from __future__ import annotations
from typing import Optional

import asyncpg


async def _insert_submission(
    pool: asyncpg.Pool,
    program_name: str,
    score: int,
    raw_feedback: str,
    is_authenticated_user: bool,
    is_anonymous: bool,
    contact_info: Optional[str],
    privacy_token: Optional[str],
) -> int:
    """Insert a local feedback record. Returns new row id."""
    row = await pool.fetchrow(
        """
        INSERT INTO common.feedback_submissions
            (program_name, score, raw_feedback,
             is_authenticated_user, is_anonymous,
             contact_info, privacy_token)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        program_name,
        score,
        raw_feedback.strip(),
        is_authenticated_user,
        is_anonymous,
        contact_info,
        privacy_token,
    )
    return row["id"]


async def _update_hub_ref(
    pool: asyncpg.Pool,
    submission_id: int,
    hub_feedback_id: int,
) -> None:
    """Store the Hub's returned id after successful ingest."""
    await pool.execute(
        """
        UPDATE common.feedback_submissions
        SET hub_feedback_id = $1,
            hub_synced_at   = NOW()
        WHERE id = $2
        """,
        hub_feedback_id,
        submission_id,
    )
