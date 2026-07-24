"""Member API for the seasonal main/off-spec spinner wheel."""

from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.deps import get_current_player, get_db
from guild_portal.services.roster_needs_service import (
    get_open_role_needs,
    get_represented_main_spec_ids,
)
from sv_common.db.models import Player

router = APIRouter(prefix="/api/v1/spec-wheel", tags=["spec-wheel"])


class SpinRequest(BaseModel):
    slot: Literal["main", "offspec"]
    only_open_roles: bool = False
    only_unrepresented: bool = False
    replace: bool = False


class AssignCharacterRequest(BaseModel):
    slot: Literal["main", "offspec"]
    character_id: int


def filter_eligible_specs(
    specs: list[dict],
    open_roles: set[str],
    represented_spec_ids: set[int],
    *,
    only_open_roles: bool,
    only_unrepresented: bool,
) -> list[dict]:
    """Apply optional wheel filters without adding weights or duplicate entries."""
    return [
        spec
        for spec in specs
        if (not only_open_roles or spec["role"] in open_roles)
        and (not only_unrepresented or spec["id"] not in represented_spec_ids)
    ]


async def _active_season(db: AsyncSession):
    result = await db.execute(
        text(
            """
            SELECT id, expansion_name, season_number
            FROM patt.raid_seasons
            WHERE is_active = TRUE
            ORDER BY start_date DESC
            LIMIT 1
            """
        )
    )
    season = result.mappings().one_or_none()
    if season is None:
        raise HTTPException(
            status_code=409,
            detail="No active raid season is configured.",
        )
    return season


async def _all_specs(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT s.id, s.class_id, s.name,
                   c.name AS class_name, c.color_hex,
                   r.name AS role
            FROM ref.specializations s
            JOIN ref.classes c ON c.id = s.class_id
            JOIN guild_identity.roles r ON r.id = s.default_role_id
            ORDER BY c.name, s.name
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def _history(db: AsyncSession, player_id: int, season_id: int) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT sw.slot, sw.roll_count, sw.first_rolled_at, sw.latest_rolled_at,
                   fs.id AS first_id, fs.name AS first_name,
                   fc.id AS first_class_id, fc.name AS first_class_name,
                   fc.color_hex AS first_color_hex, fr.name AS first_role,
                   ls.id AS latest_id, ls.name AS latest_name,
                   lc.id AS latest_class_id, lc.name AS latest_class_name,
                   lc.color_hex AS latest_color_hex, lr.name AS latest_role
            FROM patt.spec_wheel_rolls sw
            JOIN ref.specializations fs ON fs.id = sw.first_spec_id
            JOIN ref.classes fc ON fc.id = fs.class_id
            JOIN guild_identity.roles fr ON fr.id = fs.default_role_id
            JOIN ref.specializations ls ON ls.id = sw.latest_spec_id
            JOIN ref.classes lc ON lc.id = ls.class_id
            JOIN guild_identity.roles lr ON lr.id = ls.default_role_id
            WHERE sw.player_id = :player_id AND sw.season_id = :season_id
            ORDER BY sw.slot
            """
        ),
        {"player_id": player_id, "season_id": season_id},
    )

    histories = []
    for row in result.mappings():
        histories.append(
            {
                "slot": row["slot"],
                "roll_count": row["roll_count"],
                "first_rolled_at": row["first_rolled_at"],
                "latest_rolled_at": row["latest_rolled_at"],
                "first": {
                    "id": row["first_id"],
                    "name": row["first_name"],
                    "class_id": row["first_class_id"],
                    "class_name": row["first_class_name"],
                    "color_hex": row["first_color_hex"],
                    "role": row["first_role"],
                },
                "latest": {
                    "id": row["latest_id"],
                    "name": row["latest_name"],
                    "class_id": row["latest_class_id"],
                    "class_name": row["latest_class_name"],
                    "color_hex": row["latest_color_hex"],
                    "role": row["latest_role"],
                },
            }
        )
    return histories


async def _characters(db: AsyncSession, player_id: int) -> list[dict]:
    result = await db.execute(
        text(
            """
            SELECT wc.id, wc.class_id, wc.character_name,
                   COALESCE(wc.realm_name, wc.realm_slug) AS realm,
                   wc.level
            FROM guild_identity.player_characters pc
            JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
            WHERE pc.player_id = :player_id
            ORDER BY wc.level DESC NULLS LAST,
                     LOWER(wc.character_name),
                     LOWER(COALESCE(wc.realm_name, wc.realm_slug))
            """
        ),
        {"player_id": player_id},
    )
    return [dict(row) for row in result.mappings().all()]


@router.get("")
async def get_spec_wheel_state(
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(get_current_player),
):
    season = await _active_season(db)
    specs = await _all_specs(db)
    open_role_needs = await get_open_role_needs(db)
    represented_ids = await get_represented_main_spec_ids(db)
    history = await _history(db, player.id, season["id"])

    season_name = season["expansion_name"] or "Current"
    if season["season_number"] is not None:
        season_name = f"{season_name} Season {season['season_number']}"

    return {
        "ok": True,
        "data": {
            "season": {"id": season["id"], "name": season_name},
            "specs": specs,
            "open_role_needs": open_role_needs,
            "represented_spec_ids": sorted(represented_ids),
            "history": history,
            "season_roll_count": sum(item["roll_count"] for item in history),
            "characters": await _characters(db, player.id),
        },
    }


@router.post("/spin")
async def spin_spec_wheel(
    body: SpinRequest,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(get_current_player),
):
    season = await _active_season(db)
    existing = await db.execute(
        text(
            """
            SELECT id
            FROM patt.spec_wheel_rolls
            WHERE player_id = :player_id AND season_id = :season_id AND slot = :slot
            """
        ),
        {"player_id": player.id, "season_id": season["id"], "slot": body.slot},
    )
    if existing.scalar_one_or_none() is not None and not body.replace:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "replacement_required",
                "message": "This replaces your latest roll. Your first roll remains saved.",
            },
        )

    specs = await _all_specs(db)
    open_role_needs = await get_open_role_needs(db)
    represented_ids = await get_represented_main_spec_ids(db)
    eligible = filter_eligible_specs(
        specs,
        set(open_role_needs),
        represented_ids,
        only_open_roles=body.only_open_roles,
        only_unrepresented=body.only_unrepresented,
    )
    if not eligible:
        raise HTTPException(
            status_code=409,
            detail="No specializations match the selected filters.",
        )

    selected = secrets.choice(eligible)
    upsert = await db.execute(
        text(
            """
            INSERT INTO patt.spec_wheel_rolls
                (player_id, season_id, slot, first_spec_id, latest_spec_id)
            VALUES
                (:player_id, :season_id, :slot, :spec_id, :spec_id)
            ON CONFLICT (player_id, season_id, slot) DO UPDATE
               SET latest_spec_id = EXCLUDED.latest_spec_id,
                   latest_rolled_at = NOW(),
                   roll_count = patt.spec_wheel_rolls.roll_count + 1
             WHERE :replace = TRUE
            RETURNING roll_count, first_rolled_at, latest_rolled_at
            """
        ),
        {
            "player_id": player.id,
            "season_id": season["id"],
            "slot": body.slot,
            "spec_id": selected["id"],
            "replace": body.replace,
        },
    )
    saved = upsert.mappings().one_or_none()
    if saved is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "replacement_required",
                "message": "This replaces your latest roll. Please confirm and spin again.",
            },
        )

    return {
        "ok": True,
        "data": {
            "slot": body.slot,
            "result": selected,
            "eligible_specs": eligible,
            "slot_roll_count": saved["roll_count"],
            "characters": [
                char
                for char in await _characters(db, player.id)
                if char["class_id"] == selected["class_id"]
            ],
        },
    }


@router.post("/assign-character")
async def assign_rolled_character(
    body: AssignCharacterRequest,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(get_current_player),
):
    season = await _active_season(db)
    roll_result = await db.execute(
        text(
            """
            SELECT sw.latest_spec_id, s.class_id
            FROM patt.spec_wheel_rolls sw
            JOIN ref.specializations s ON s.id = sw.latest_spec_id
            WHERE sw.player_id = :player_id
              AND sw.season_id = :season_id
              AND sw.slot = :slot
            """
        ),
        {"player_id": player.id, "season_id": season["id"], "slot": body.slot},
    )
    roll = roll_result.mappings().one_or_none()
    if roll is None:
        raise HTTPException(status_code=409, detail="Spin this slot before assigning a character.")

    character_result = await db.execute(
        text(
            """
            SELECT wc.id, wc.class_id, wc.character_name,
                   COALESCE(wc.realm_name, wc.realm_slug) AS realm,
                   wc.level
            FROM guild_identity.player_characters pc
            JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
            WHERE pc.player_id = :player_id AND wc.id = :character_id
            """
        ),
        {"player_id": player.id, "character_id": body.character_id},
    )
    character = character_result.mappings().one_or_none()
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found on your account.")
    if character["class_id"] != roll["class_id"]:
        raise HTTPException(
            status_code=400,
            detail="That character's class does not match your latest roll.",
        )

    if body.slot == "main":
        sql = """
            UPDATE guild_identity.players
               SET main_character_id = :character_id,
                   main_spec_id = :spec_id,
                   updated_at = NOW()
             WHERE id = :player_id
        """
    else:
        sql = """
            UPDATE guild_identity.players
               SET offspec_character_id = :character_id,
                   offspec_spec_id = :spec_id,
                   updated_at = NOW()
             WHERE id = :player_id
        """
    await db.execute(
        text(sql),
        {
            "player_id": player.id,
            "character_id": body.character_id,
            "spec_id": roll["latest_spec_id"],
        },
    )
    return {"ok": True, "data": dict(character)}
