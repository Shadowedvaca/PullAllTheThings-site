"""Recruiting contest API routes.

Admin routes (GL, rank 5):
    POST   /api/v1/admin/recruiting-contest
    PATCH  /api/v1/admin/recruiting-contest/{contest_id}
    POST   /api/v1/admin/recruiting-contest/{contest_id}/submissions
    PATCH  /api/v1/admin/recruiting-contest/submissions/{sub_id}
    DELETE /api/v1/admin/recruiting-contest/submissions/{sub_id}
    POST   /api/v1/admin/recruiting-contest/{contest_id}/pay-all/{player_id}
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.deps import get_db, require_rank
from sv_common.db.models import Player

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/recruiting-contest", tags=["recruiting"])

_VALID_PAYOUT_TYPES = {"recruit", "promotion", "first_recruit_bonus"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ContestCreate(BaseModel):
    title: str
    description: str | None = None
    deadline: datetime | None = None
    bounty_per_recruit: int = 10000
    promotion_bounty: int = 10000
    leader_bonus: int = 100000
    first_recruit_bonus: int = 5000


class ContestUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    deadline: datetime | None = None
    bounty_per_recruit: int | None = None
    promotion_bounty: int | None = None
    leader_bonus: int | None = None
    first_recruit_bonus: int | None = None
    status: str | None = None


class SubmissionCreate(BaseModel):
    recruiter_player_ids: list[int]       # one submission row created per player
    recruit_display_name: str
    screenshot_url: str | None = None
    payout_type: str                      # 'recruit' | 'promotion' | 'first_recruit_bonus'
    gold_amount: int = 0
    notes: str | None = None


class SubmissionUpdate(BaseModel):
    approved: bool | None = None
    paid: bool | None = None
    notes: str | None = None
    gold_amount: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_contest_or_404(db: AsyncSession, contest_id: int) -> None:
    row = await db.execute(
        text("SELECT id FROM patt.recruiting_contests WHERE id = :id"),
        {"id": contest_id},
    )
    if not row.one_or_none():
        raise HTTPException(status_code=404, detail="Contest not found.")


# ---------------------------------------------------------------------------
# Admin Routes
# ---------------------------------------------------------------------------


@router.post("")
async def create_contest(
    body: ContestCreate,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(require_rank(5)),
):
    result = await db.execute(
        text("""
            INSERT INTO patt.recruiting_contests
                (title, description, deadline, bounty_per_recruit, promotion_bounty,
                 leader_bonus, first_recruit_bonus)
            VALUES
                (:title, :description, :deadline, :bounty_per_recruit, :promotion_bounty,
                 :leader_bonus, :first_recruit_bonus)
            RETURNING id
        """),
        {
            "title": body.title,
            "description": body.description,
            "deadline": body.deadline,
            "bounty_per_recruit": body.bounty_per_recruit,
            "promotion_bounty": body.promotion_bounty,
            "leader_bonus": body.leader_bonus,
            "first_recruit_bonus": body.first_recruit_bonus,
        },
    )
    await db.commit()
    return {"ok": True, "id": result.scalar_one()}


@router.patch("/{contest_id}")
async def update_contest(
    contest_id: int,
    body: ContestUpdate,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(require_rank(5)),
):
    await _get_contest_or_404(db, contest_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    if "status" in updates and updates["status"] not in ("open", "closed"):
        raise HTTPException(status_code=400, detail="Invalid status.")

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["_id"] = contest_id
    await db.execute(
        text(f"UPDATE patt.recruiting_contests SET {set_clauses} WHERE id = :_id"),
        updates,
    )
    await db.commit()
    return {"ok": True}


@router.post("/{contest_id}/submissions")
async def add_submission(
    contest_id: int,
    body: SubmissionCreate,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(require_rank(5)),
):
    """Create one submission row per recruiter in recruiter_player_ids."""
    await _get_contest_or_404(db, contest_id)

    if body.payout_type not in _VALID_PAYOUT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid payout_type.")
    if not body.recruiter_player_ids:
        raise HTTPException(status_code=400, detail="At least one recruiter required.")

    inserted_ids = []
    for pid in body.recruiter_player_ids:
        result = await db.execute(
            text("""
                INSERT INTO patt.recruiting_submissions
                    (contest_id, recruiter_player_id, recruit_display_name,
                     screenshot_url, payout_type, gold_amount, notes)
                VALUES
                    (:contest_id, :pid, :recruit_display_name,
                     :screenshot_url, :payout_type, :gold_amount, :notes)
                RETURNING id
            """),
            {
                "contest_id": contest_id,
                "pid": pid,
                "recruit_display_name": body.recruit_display_name,
                "screenshot_url": body.screenshot_url or None,
                "payout_type": body.payout_type,
                "gold_amount": body.gold_amount,
                "notes": body.notes,
            },
        )
        inserted_ids.append(result.scalar_one())

    await db.commit()
    return {"ok": True, "ids": inserted_ids}


@router.patch("/submissions/{sub_id}")
async def update_submission(
    sub_id: int,
    body: SubmissionUpdate,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(require_rank(5)),
):
    row = await db.execute(
        text("SELECT id FROM patt.recruiting_submissions WHERE id = :id"),
        {"id": sub_id},
    )
    if not row.one_or_none():
        raise HTTPException(status_code=404, detail="Submission not found.")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}

    now = datetime.now(timezone.utc)
    if "approved" in updates:
        updates["approved_at"] = now if updates["approved"] else None
        updates["approved_by_player_id"] = player.id if updates["approved"] else None
    if "paid" in updates:
        updates["paid_at"] = now if updates["paid"] else None

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["_id"] = sub_id
    await db.execute(
        text(f"UPDATE patt.recruiting_submissions SET {set_clauses} WHERE id = :_id"),
        updates,
    )
    await db.commit()
    return {"ok": True}


@router.delete("/submissions/{sub_id}")
async def delete_submission(
    sub_id: int,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(require_rank(5)),
):
    result = await db.execute(
        text("DELETE FROM patt.recruiting_submissions WHERE id = :id RETURNING id"),
        {"id": sub_id},
    )
    await db.commit()
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Submission not found.")
    return {"ok": True}


@router.post("/{contest_id}/pay-all/{player_id}")
async def pay_all_for_recruiter(
    contest_id: int,
    player_id: int,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(require_rank(5)),
):
    """Mark all approved+unpaid submissions for a recruiter as paid."""
    await _get_contest_or_404(db, contest_id)
    now = datetime.now(timezone.utc)
    result = await db.execute(
        text("""
            UPDATE patt.recruiting_submissions
               SET paid = TRUE, paid_at = :now
             WHERE contest_id = :contest_id
               AND recruiter_player_id = :player_id
               AND approved = TRUE
               AND paid = FALSE
            RETURNING id
        """),
        {"now": now, "contest_id": contest_id, "player_id": player_id},
    )
    await db.commit()
    return {"ok": True, "paid_count": len(result.fetchall())}
