"""Member-facing API routes — personal data for logged-in guild members."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from guild_portal.deps import get_current_player, get_db
from sv_common.config_cache import get_site_config
from sv_common.db.models import (
    CharacterMythicPlus,
    CharacterParse,
    CharacterRaidProgress,
    Player,
    PlayerCharacter,
    RaiderIOProfile,
    RaidSeason,
    WclConfig,
    WowCharacter,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/me", tags=["member"])

_DIFF_NAMES: dict[int, str] = {1: "lfr", 3: "normal", 4: "heroic", 5: "mythic"}

# Class emoji mapping (matches public_pages.py)
_CLASS_EMOJIS: dict[str, str] = {
    "Druid": "🌿",
    "Paladin": "⚔️",
    "Warlock": "👁️",
    "Priest": "✨",
    "Mage": "🔮",
    "Hunter": "🏹",
    "Warrior": "⚔️",
    "Shaman": "⚡",
    "Monk": "☯️",
    "Death Knight": "💀",
    "Demon Hunter": "🦅",
    "Evoker": "🐉",
    "Rogue": "🗡️",
}


def _build_char_dict(
    pc: PlayerCharacter,
    player: Player,
    rio_by_char: dict[int, RaiderIOProfile],
) -> dict:
    """Build the character data dict for the API response."""
    char = pc.character
    class_name = char.wow_class.name if char.wow_class else None
    class_color = char.wow_class.color_hex if char.wow_class else None
    class_emoji = _CLASS_EMOJIS.get(class_name, "❓") if class_name else "❓"
    spec_name = char.active_spec.name if char.active_spec else None
    realm_slug = char.realm_slug
    realm_display = char.realm_name or realm_slug.replace("-", " ").title()
    char_name = char.character_name

    rio = rio_by_char.get(char.id)
    raiderio_url: str | None = None
    if rio:
        raiderio_url = rio.profile_url or (
            f"https://raider.io/characters/us/{realm_slug}/{char_name}"
        )

    last_synced_at: str | None = None
    if char.blizzard_last_sync:
        last_synced_at = char.blizzard_last_sync.isoformat()

    return {
        "id": char.id,
        "character_name": char_name,
        "realm_slug": realm_slug,
        "realm_display": realm_display,
        "class_name": class_name,
        "class_color": class_color,
        "class_emoji": class_emoji,
        "spec_name": spec_name,
        "avg_item_level": char.item_level,
        "last_login_ms": char.last_login_timestamp,
        "last_synced_at": last_synced_at,
        "is_main": char.id == player.main_character_id,
        "is_offspec": char.id == player.offspec_character_id,
        "link_source": pc.link_source,
        "armory_url": (
            f"https://worldofwarcraft.blizzard.com/en-us/character/us"
            f"/{realm_slug}/{char_name}"
        ),
        "raiderio_url": raiderio_url,
        "wcl_url": (
            f"https://www.warcraftlogs.com/character/us/{realm_slug}/{char_name}"
        ),
    }


def _pick_default_character_id(
    characters: list[dict],
    main_character_id: int | None,
    offspec_character_id: int | None,
) -> int | None:
    """Select the default character: main > offspec > first alphabetically."""
    if not characters:
        return None
    char_id_set = {c["id"] for c in characters}
    if main_character_id and main_character_id in char_id_set:
        return main_character_id
    if offspec_character_id and offspec_character_id in char_id_set:
        return offspec_character_id
    return characters[0]["id"]


@router.get("/characters")
async def get_my_characters(
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return all characters claimed by the current member with stat data."""
    # Load player characters with WoW class + spec relationships
    result = await db.execute(
        select(PlayerCharacter)
        .options(
            selectinload(PlayerCharacter.character).options(
                selectinload(WowCharacter.wow_class),
                selectinload(WowCharacter.active_spec),
            )
        )
        .where(PlayerCharacter.player_id == player.id)
    )
    player_chars = list(result.scalars().all())

    # Batch-load Raider.IO profiles for all claimed characters
    char_ids = [pc.character_id for pc in player_chars if pc.character]
    rio_by_char: dict[int, RaiderIOProfile] = {}
    if char_ids:
        rio_result = await db.execute(
            select(RaiderIOProfile).where(
                RaiderIOProfile.character_id.in_(char_ids),
                RaiderIOProfile.season == "current",
            )
        )
        for r in rio_result.scalars():
            rio_by_char[r.character_id] = r

    # Also reload player to get latest main/offspec IDs
    player_result = await db.execute(
        select(Player).where(Player.id == player.id)
    )
    fresh_player = player_result.scalar_one_or_none() or player

    # Build and sort character list
    characters: list[dict] = []
    for pc in player_chars:
        if not pc.character:
            continue
        characters.append(_build_char_dict(pc, fresh_player, rio_by_char))

    characters.sort(key=lambda c: f"{c['character_name']}-{c['realm_slug']}")

    default_id = _pick_default_character_id(
        characters,
        fresh_player.main_character_id,
        fresh_player.offspec_character_id,
    )

    return {
        "ok": True,
        "data": {
            "characters": characters,
            "default_character_id": default_id,
        },
    }


@router.get("/character/{character_id}/progression")
async def get_character_progression(
    character_id: int,
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return raid progress and M+ score for a character owned by the current member."""
    # Verify the character belongs to this player
    pc_result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
        )
    )
    if not pc_result.scalar_one_or_none():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # ── Raid progress ────────────────────────────────────────────────────────
    # Aggregate per (raid_name, difficulty): total bosses and bosses with kills
    raid_rows = await db.execute(
        select(
            CharacterRaidProgress.raid_name,
            CharacterRaidProgress.difficulty,
            func.count().label("total"),
            func.sum(
                case((CharacterRaidProgress.kill_count > 0, 1), else_=0)
            ).label("killed"),
        )
        .where(CharacterRaidProgress.character_id == character_id)
        .group_by(CharacterRaidProgress.raid_name, CharacterRaidProgress.difficulty)
        .order_by(CharacterRaidProgress.raid_name, CharacterRaidProgress.difficulty)
    )

    raid_by_name: dict[str, dict] = {}
    for row in raid_rows:
        name = row.raid_name
        if name not in raid_by_name:
            raid_by_name[name] = {}
        raid_by_name[name][row.difficulty.lower()] = {
            "killed": int(row.killed or 0),
            "total": int(row.total),
        }

    raid_progress = [
        {"raid_name": name, "difficulties": diffs}
        for name, diffs in raid_by_name.items()
    ]

    # ── Mythic+ score ────────────────────────────────────────────────────────
    cfg = get_site_config()
    season_id: int | None = cfg.get("current_mplus_season_id")

    mythic_plus = None
    if season_id:
        mplus_result = await db.execute(
            select(CharacterMythicPlus).where(
                CharacterMythicPlus.character_id == character_id,
                CharacterMythicPlus.season_id == season_id,
            )
        )
        mplus_rows = list(mplus_result.scalars())

        if mplus_rows:
            overall_score = max(float(r.overall_rating or 0) for r in mplus_rows)
            best_row = max(mplus_rows, key=lambda r: r.best_level or 0)

            # Try to get a human-readable season name from raid_seasons
            season_name = f"Season {season_id}"
            season_result = await db.execute(
                select(RaidSeason).where(
                    RaidSeason.blizzard_mplus_season_id == season_id
                )
            )
            season = season_result.scalar_one_or_none()
            if season:
                if season.expansion_name and season.season_number:
                    season_name = f"{season.expansion_name} Season {season.season_number}"
                elif season.season_number:
                    season_name = f"Season {season.season_number}"

            mythic_plus = {
                "season_name": season_name,
                "overall_score": round(overall_score, 1),
                "best_run_level": best_row.best_level,
                "best_run_dungeon": best_row.dungeon_name,
            }

    return {
        "ok": True,
        "data": {
            "character_id": character_id,
            "raid_progress": raid_progress,
            "mythic_plus": mythic_plus,
        },
    }


@router.get("/character/{character_id}/parses")
async def get_character_parses(
    character_id: int,
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return WCL parse percentiles for a character owned by the current member."""
    # Verify the character belongs to this player
    pc_result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
        )
    )
    if not pc_result.scalar_one_or_none():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # Check WCL config
    wcl_result = await db.execute(select(WclConfig).limit(1))
    wcl_cfg = wcl_result.scalar_one_or_none()
    wcl_configured = bool(wcl_cfg and wcl_cfg.is_configured)

    # Load all parses for this character
    parse_result = await db.execute(
        select(CharacterParse).where(CharacterParse.character_id == character_id)
    )
    all_rows = list(parse_result.scalars().all())

    # Deduplicate: best percentile per (encounter_name, difficulty_int)
    best: dict[tuple, CharacterParse] = {}
    for row in all_rows:
        key = (row.encounter_name, row.difficulty)
        if key not in best or float(row.percentile) > float(best[key].percentile):
            best[key] = row

    # Build parse list
    parses = []
    tier_name: str | None = None
    for row in best.values():
        diff_name = _DIFF_NAMES.get(row.difficulty, str(row.difficulty))
        if tier_name is None and row.zone_name:
            tier_name = row.zone_name
        recorded_at = None
        if row.fight_date:
            recorded_at = row.fight_date.isoformat()
        elif row.last_synced:
            recorded_at = row.last_synced.isoformat()
        parses.append({
            "boss_name": row.encounter_name,
            "difficulty": diff_name,
            "percentile": float(row.percentile),
            "rank_world": None,
            "report_code": row.report_code,
            "recorded_at": recorded_at,
        })

    # Build summary
    summary = None
    if parses:
        best_parse = max(parses, key=lambda p: p["percentile"])
        heroic = [p for p in parses if p["difficulty"] == "heroic"]
        heroic_avg = (
            round(sum(p["percentile"] for p in heroic) / len(heroic), 1)
            if heroic else None
        )
        summary = {
            "best_percentile": best_parse["percentile"],
            "best_boss": best_parse["boss_name"],
            "best_difficulty": best_parse["difficulty"],
            "heroic_average": heroic_avg,
        }

    return {
        "ok": True,
        "data": {
            "character_id": character_id,
            "tier_name": tier_name,
            "wcl_configured": wcl_configured,
            "parses": parses,
            "summary": summary,
        },
    }
