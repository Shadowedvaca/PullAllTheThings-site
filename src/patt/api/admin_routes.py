"""Admin API routes — guild management (unprotected; auth layered on in Phase 2)."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from patt.deps import get_db
from sv_common.identity import characters as char_service
from sv_common.identity import members as member_service
from sv_common.identity import ranks as rank_service

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RankCreate(BaseModel):
    name: str
    level: int
    description: str | None = None
    discord_role_id: str | None = None


class RankUpdate(BaseModel):
    name: str | None = None
    level: int | None = None
    description: str | None = None
    discord_role_id: str | None = None


class MemberCreate(BaseModel):
    discord_username: str
    discord_id: str | None = None
    display_name: str | None = None
    rank_id: int | None = None


class MemberUpdate(BaseModel):
    discord_username: str | None = None
    discord_id: str | None = None
    display_name: str | None = None
    rank_id: int | None = None
    rank_source: str | None = None


class CharacterCreate(BaseModel):
    name: str
    realm: str
    wow_class: str
    spec: str | None = None
    role: str | None = None
    main_alt: str = "main"


class CharacterUpdate(BaseModel):
    name: str | None = None
    realm: str | None = None
    spec: str | None = None
    role: str | None = None
    main_alt: str | None = None


# ---------------------------------------------------------------------------
# Ranks
# ---------------------------------------------------------------------------


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
                "discord_role_id": r.discord_role_id,
                "description": r.description,
            }
            for r in all_ranks
        ],
    }


@router.post("/ranks")
async def create_rank(body: RankCreate, db: AsyncSession = Depends(get_db)):
    try:
        rank = await rank_service.create_rank(
            db,
            name=body.name,
            level=body.level,
            description=body.description,
            discord_role_id=body.discord_role_id,
        )
        return {
            "ok": True,
            "data": {"id": rank.id, "name": rank.name, "level": rank.level},
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.patch("/ranks/{rank_id}")
async def update_rank(
    rank_id: int, body: RankUpdate, db: AsyncSession = Depends(get_db)
):
    try:
        updates = body.model_dump(exclude_none=True)
        rank = await rank_service.update_rank(db, rank_id, **updates)
        return {
            "ok": True,
            "data": {"id": rank.id, "name": rank.name, "level": rank.level},
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.delete("/ranks/{rank_id}")
async def delete_rank(rank_id: int, db: AsyncSession = Depends(get_db)):
    deleted = await rank_service.delete_rank(db, rank_id)
    if not deleted:
        return {"ok": False, "error": f"Rank {rank_id} not found"}
    return {"ok": True, "data": {"deleted": True}}


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/members")
async def list_members(db: AsyncSession = Depends(get_db)):
    all_members = await member_service.get_all_members(db)
    return {
        "ok": True,
        "data": [
            {
                "id": m.id,
                "discord_username": m.discord_username,
                "display_name": m.display_name,
                "discord_id": m.discord_id,
                "rank_id": m.rank_id,
            }
            for m in all_members
        ],
    }


@router.post("/members")
async def create_member(body: MemberCreate, db: AsyncSession = Depends(get_db)):
    try:
        member = await member_service.create_member(
            db,
            discord_username=body.discord_username,
            discord_id=body.discord_id,
            display_name=body.display_name,
            rank_id=body.rank_id,
        )
        return {
            "ok": True,
            "data": {
                "id": member.id,
                "discord_username": member.discord_username,
                "rank_id": member.rank_id,
            },
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.patch("/members/{member_id}")
async def update_member(
    member_id: int, body: MemberUpdate, db: AsyncSession = Depends(get_db)
):
    try:
        updates = body.model_dump(exclude_none=True)
        member = await member_service.update_member(db, member_id, **updates)
        return {"ok": True, "data": {"id": member.id, "rank_id": member.rank_id}}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.get("/members/{member_id}")
async def get_member(member_id: int, db: AsyncSession = Depends(get_db)):
    member = await member_service.get_member_by_id(db, member_id)
    if member is None:
        return {"ok": False, "error": f"Member {member_id} not found"}
    chars = await char_service.get_characters_for_member(db, member_id)
    return {
        "ok": True,
        "data": {
            "id": member.id,
            "discord_username": member.discord_username,
            "display_name": member.display_name,
            "discord_id": member.discord_id,
            "rank_id": member.rank_id,
            "characters": [
                {
                    "id": c.id,
                    "name": c.name,
                    "realm": c.realm,
                    "class": c.class_,
                    "spec": c.spec,
                    "role": c.role,
                    "main_alt": c.main_alt,
                    "armory_url": c.armory_url,
                }
                for c in chars
            ],
        },
    }


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------


@router.get("/members/{member_id}/characters")
async def list_characters(member_id: int, db: AsyncSession = Depends(get_db)):
    chars = await char_service.get_characters_for_member(db, member_id)
    return {
        "ok": True,
        "data": [
            {
                "id": c.id,
                "name": c.name,
                "realm": c.realm,
                "class": c.class_,
                "spec": c.spec,
                "role": c.role,
                "main_alt": c.main_alt,
                "armory_url": c.armory_url,
            }
            for c in chars
        ],
    }


@router.post("/members/{member_id}/characters")
async def add_character(
    member_id: int, body: CharacterCreate, db: AsyncSession = Depends(get_db)
):
    try:
        char = await char_service.create_character(
            db,
            member_id=member_id,
            name=body.name,
            realm=body.realm,
            wow_class=body.wow_class,
            spec=body.spec,
            role=body.role,
            main_alt=body.main_alt,
        )
        return {
            "ok": True,
            "data": {
                "id": char.id,
                "name": char.name,
                "realm": char.realm,
                "armory_url": char.armory_url,
            },
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.patch("/characters/{char_id}")
async def update_character(
    char_id: int, body: CharacterUpdate, db: AsyncSession = Depends(get_db)
):
    try:
        updates = body.model_dump(exclude_none=True)
        char = await char_service.update_character(db, char_id, **updates)
        return {"ok": True, "data": {"id": char.id, "name": char.name}}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.delete("/characters/{char_id}")
async def delete_character(char_id: int, db: AsyncSession = Depends(get_db)):
    deleted = await char_service.delete_character(db, char_id)
    if not deleted:
        return {"ok": False, "error": f"Character {char_id} not found"}
    return {"ok": True, "data": {"deleted": True}}
