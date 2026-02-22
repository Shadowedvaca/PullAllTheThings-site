"""Public guild API routes — roster and rank info."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_db
from sv_common.db.models import Character, GuildMember
from sv_common.identity import ranks as rank_service

router = APIRouter(prefix="/api/v1/guild", tags=["guild"])


@router.get("/ranks")
async def list_ranks(db: AsyncSession = Depends(get_db)):
    all_ranks = await rank_service.get_all_ranks(db)
    return {
        "ok": True,
        "data": [
            {
                "id": r.id,
                "name": r.name,
                "level": r.level,
                "description": r.description,
            }
            for r in all_ranks
        ],
    }


@router.get("/roster")
async def get_roster(db: AsyncSession = Depends(get_db)):
    # Load members with their rank preloaded to avoid lazy-load errors
    result = await db.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .order_by(GuildMember.discord_username)
    )
    members = list(result.scalars().all())

    if not members:
        return {"ok": True, "data": {"members": []}}

    # Bulk-load main characters to avoid N+1 queries
    member_ids = [m.id for m in members]
    chars_result = await db.execute(
        select(Character)
        .where(Character.member_id.in_(member_ids))
        .where(Character.main_alt == "main")
    )
    main_chars: dict[int, Character] = {
        c.member_id: c for c in chars_result.scalars().all()
    }

    roster = []
    for member in members:
        main_char = main_chars.get(member.id)
        entry: dict = {
            "display_name": member.display_name or member.discord_username,
            "rank": member.rank.name if member.rank else "Unknown",
            "main_character": None,
        }
        if main_char:
            entry["main_character"] = {
                "name": main_char.name,
                "realm": main_char.realm,
                "class": main_char.class_,
                "spec": main_char.spec,
                "role": main_char.role,
                "armory_url": main_char.armory_url,
            }
        roster.append(entry)

    return {"ok": True, "data": {"members": roster}}
