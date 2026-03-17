"""
POST /api/v1/feedback
Public — no auth required. Accepts submissions from any visitor.
Calls sv_common.feedback.submit_feedback() which handles local storage + Hub sync.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from guild_portal.deps import COOKIE_NAME, _decode_token
from sv_common.feedback import submit_feedback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackBody(BaseModel):
    score:        int           = Field(..., ge=1, le=10)
    feedback:     str           = Field(..., min_length=1, max_length=5000)
    contact_info: Optional[str] = Field(None, max_length=255)
    is_anonymous: bool          = False


@router.post("")
async def submit_feedback_endpoint(body: FeedbackBody, request: Request):
    pool = request.app.state.guild_sync_pool

    # Best-effort: determine if user is logged in; never block anonymous
    is_authenticated = False
    try:
        token_str = request.cookies.get(COOKIE_NAME)
        if not token_str:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token_str = auth_header[7:]
        if token_str:
            _decode_token(token_str)
            is_authenticated = True
    except Exception:
        pass

    await submit_feedback(
        pool=pool,
        score=body.score,
        raw_feedback=body.feedback,
        is_authenticated_user=is_authenticated,
        contact_info=body.contact_info,
        is_anonymous=body.is_anonymous,
    )
    return {"ok": True}
