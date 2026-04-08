"""Member-facing API routes — personal data for logged-in guild members."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from guild_portal.deps import get_current_player, get_db
from urllib.parse import quote_plus

from sv_common.config_cache import get_site_config
from sv_common.db.models import (
    BattlenetAccount,
    CharacterMythicPlus,
    CharacterRaidProgress,
    Player,
    PlayerCharacter,
    RaiderIOProfile,
    RaidSeason,
    Specialization,
    WclConfig,
    WowCharacter,
    WowClass,
)
from guild_portal.services.guide_links_service import build_links_for_spec, get_enabled_sites

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
    guide_sites: list[dict] | None = None,
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

    # Build guide links for current spec and all class specs
    guide_links: list[dict] | None = None
    class_specs: list[dict] | None = None
    _sites = guide_sites or []

    if char.wow_class and char.active_spec and _sites:
        active_role = char.active_spec.default_role.name if char.active_spec.default_role else "dps"
        guide_links = build_links_for_spec(
            _sites, class_name, spec_name, active_role
        )

    if char.wow_class and char.wow_class.specializations is not None:
        class_specs = []
        for spec in sorted(char.wow_class.specializations, key=lambda s: s.name):
            role_name = spec.default_role.name if spec.default_role else "dps"
            class_specs.append({
                "name": spec.name,
                "role": role_name,
                "guide_links": build_links_for_spec(
                    _sites, class_name, spec.name, role_name
                ),
            })

    role_name = None
    if char.active_spec and char.active_spec.default_role:
        role_name = char.active_spec.default_role.name

    return {
        "id": char.id,
        "character_name": char_name,
        "realm_slug": realm_slug,
        "realm_display": realm_display,
        "class_name": class_name,
        "class_color": class_color,
        "class_emoji": class_emoji,
        "spec_name": spec_name,
        "race": char.race,
        "role": role_name,
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
        "guide_links": guide_links,
        "class_specs": class_specs,
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
    # Load guide sites (cached)
    guide_sites = await get_enabled_sites(db)

    # Load player characters with WoW class + spec + role relationships
    result = await db.execute(
        select(PlayerCharacter)
        .options(
            selectinload(PlayerCharacter.character).options(
                selectinload(WowCharacter.wow_class).selectinload(
                    WowClass.specializations
                ).selectinload(Specialization.default_role),
                selectinload(WowCharacter.active_spec).selectinload(
                    Specialization.default_role
                ),
            )
        )
        .join(PlayerCharacter.character)
        .where(PlayerCharacter.player_id == player.id, WowCharacter.in_guild == True)
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
        characters.append(_build_char_dict(pc, fresh_player, rio_by_char, guide_sites))

    characters.sort(key=lambda c: f"{c['character_name']}-{c['realm_slug']}")

    default_id = _pick_default_character_id(
        characters,
        fresh_player.main_character_id,
        fresh_player.offspec_character_id,
    )

    # Out-of-guild characters linked via BNet
    oog_result = await db.execute(
        select(WowCharacter)
        .join(PlayerCharacter, PlayerCharacter.character_id == WowCharacter.id)
        .where(
            PlayerCharacter.player_id == player.id,
            WowCharacter.in_guild == False,
            WowCharacter.removed_at.is_(None),
        )
        .options(selectinload(WowCharacter.wow_class))
        .order_by(WowCharacter.character_name)
    )
    out_of_guild_chars = list(oog_result.scalars().all())

    # BNet link status
    bnet_result = await db.execute(
        select(BattlenetAccount).where(BattlenetAccount.player_id == player.id)
    )
    bnet_account = bnet_result.scalar_one_or_none()
    bnet_linked = bnet_account is not None
    bnet_token_expired = False
    if bnet_account and bnet_account.token_expires_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        exp = bnet_account.token_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        bnet_token_expired = exp <= now

    return {
        "ok": True,
        "data": {
            "characters": characters,
            "default_character_id": default_id,
            "out_of_guild_characters": [
                {
                    "id": c.id,
                    "name": c.character_name,
                    "realm": c.realm_slug,
                    "level": c.level,
                    "class": c.wow_class.name if c.wow_class else None,
                }
                for c in out_of_guild_chars
            ],
            "bnet_linked": bnet_linked,
            "bnet_token_expired": bnet_token_expired,
        },
    }


@router.post("/bnet-sync")
async def member_bnet_sync(
    request: Request,
    current_player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """
    Smart character refresh. Handles all three token states:
      - Not linked:     return redirect to /auth/battlenet
      - Token valid:    sync now, return stats
      - Token expired:  return redirect to /auth/battlenet
    The caller (JS) checks for a `redirect` field and navigates if present.
    The `next` query param is forwarded into the redirect URL so the OAuth
    callback returns the user to the page they came from.
    """
    from sv_common.guild_sync.bnet_character_sync import (
        get_valid_access_token,
        sync_bnet_characters,
    )

    pool = request.app.state.guild_sync_pool
    next_url = request.query_params.get("next", "/my-characters")

    # Validate next (prevent open redirect)
    ALLOWED_NEXT = {"/my-characters", "/profile", "/"}
    if next_url not in ALLOWED_NEXT:
        next_url = "/my-characters"

    # Check if BNet is linked
    bnet_row = await db.execute(
        select(BattlenetAccount).where(BattlenetAccount.player_id == current_player.id)
    )
    bnet_account = bnet_row.scalar_one_or_none()

    if not bnet_account:
        return JSONResponse({
            "ok": True,
            "redirect": f"/auth/battlenet?next={next_url}",
        })

    # Check if token is still valid
    access_token = await get_valid_access_token(pool, current_player.id)
    if access_token is None:
        return JSONResponse({
            "ok": True,
            "redirect": f"/auth/battlenet?next={next_url}",
        })

    # Token is valid — sync now
    stats = await sync_bnet_characters(pool, current_player.id, access_token)
    return JSONResponse({"ok": True, "data": stats})


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
    # Restrict to the active season's raid tier if one is configured.
    active_season_result = await db.execute(
        select(RaidSeason).where(RaidSeason.is_active == True)
    )
    active_season = active_season_result.scalar_one_or_none()
    current_raid_ids: list[int] = (
        active_season.current_raid_ids or [] if active_season else []
    )

    # Aggregate per (raid_name, difficulty): total bosses and bosses with kills
    raid_filter = [CharacterRaidProgress.character_id == character_id]
    if current_raid_ids:
        raid_filter.append(CharacterRaidProgress.raid_id.in_(current_raid_ids))

    # Join with raid_boss_counts so "total" reflects the real boss count per
    # difficulty, not just how many this character has killed.
    if current_raid_ids:
        raid_rows = await db.execute(
            text("""
                SELECT crp.raid_name, crp.difficulty,
                       SUM(CASE WHEN crp.kill_count > 0 THEN 1 ELSE 0 END) AS killed,
                       COALESCE(MAX(rbc.boss_count), COUNT(*)) AS total
                FROM guild_identity.character_raid_progress crp
                LEFT JOIN guild_identity.raid_boss_counts rbc
                    ON rbc.raid_id = crp.raid_id AND rbc.difficulty = crp.difficulty
                WHERE crp.character_id = :char_id
                  AND crp.raid_id = ANY(:raid_ids)
                GROUP BY crp.raid_name, crp.difficulty
                ORDER BY crp.raid_name, crp.difficulty
            """).bindparams(char_id=character_id, raid_ids=current_raid_ids)
        )
    else:
        raid_rows = await db.execute(
            text("""
                SELECT crp.raid_name, crp.difficulty,
                       SUM(CASE WHEN crp.kill_count > 0 THEN 1 ELSE 0 END) AS killed,
                       COALESCE(MAX(rbc.boss_count), COUNT(*)) AS total
                FROM guild_identity.character_raid_progress crp
                LEFT JOIN guild_identity.raid_boss_counts rbc
                    ON rbc.raid_id = crp.raid_id AND rbc.difficulty = crp.difficulty
                WHERE crp.character_id = :char_id
                GROUP BY crp.raid_name, crp.difficulty
                ORDER BY crp.raid_name, crp.difficulty
            """).bindparams(char_id=character_id)
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

    # Per-boss detail query (for the Raid detail panel in UI-1E)
    if current_raid_ids:
        boss_rows_result = await db.execute(
            text("""
                SELECT crp.raid_name, crp.difficulty, crp.boss_name,
                       crp.boss_id, crp.kill_count
                FROM guild_identity.character_raid_progress crp
                WHERE crp.character_id = :char_id
                  AND crp.raid_id = ANY(:raid_ids)
                ORDER BY crp.difficulty, crp.boss_id
            """).bindparams(char_id=character_id, raid_ids=current_raid_ids)
        )
    else:
        boss_rows_result = await db.execute(
            text("""
                SELECT crp.raid_name, crp.difficulty, crp.boss_name,
                       crp.boss_id, crp.kill_count
                FROM guild_identity.character_raid_progress crp
                WHERE crp.character_id = :char_id
                ORDER BY crp.difficulty, crp.boss_id
            """).bindparams(char_id=character_id)
        )

    raid_bosses = [
        {
            "raid_name": row.raid_name,
            "difficulty": row.difficulty.lower(),
            "boss_name": row.boss_name,
            "boss_id": row.boss_id,
            "killed": (row.kill_count or 0) > 0,
        }
        for row in boss_rows_result
    ]

    # ── Mythic+ score ────────────────────────────────────────────────────────
    # raid_seasons is the single source of truth for the current M+ season ID.
    active_mplus_season_result = await db.execute(
        select(RaidSeason.blizzard_mplus_season_id, RaidSeason.expansion_name, RaidSeason.season_number)
        .where(RaidSeason.is_active == True)
        .order_by(RaidSeason.start_date.desc())
        .limit(1)
    )
    active_mplus_row = active_mplus_season_result.one_or_none()
    season_id: int | None = active_mplus_row.blizzard_mplus_season_id if active_mplus_row else None

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

            season_name = f"Season {season_id}"
            if active_mplus_row and active_mplus_row.expansion_name and active_mplus_row.season_number:
                season_name = f"{active_mplus_row.expansion_name} Season {active_mplus_row.season_number}"
            elif active_mplus_row and active_mplus_row.season_number:
                season_name = f"Season {active_mplus_row.season_number}"

            mythic_plus = {
                "season_name": season_name,
                "overall_score": round(overall_score, 1),
                "best_run_level": best_row.best_level,
                "best_run_dungeon": best_row.dungeon_name,
                "dungeons": sorted(
                    [
                        {
                            "dungeon_name": r.dungeon_name,
                            "best_level": r.best_level or 0,
                            "best_timed": r.best_timed,
                            "best_score": round(float(r.best_score or 0), 1),
                        }
                        for r in mplus_rows
                    ],
                    key=lambda d: d["dungeon_name"],
                ),
            }

    return {
        "ok": True,
        "data": {
            "character_id": character_id,
            "raid_progress": raid_progress,
            "raid_bosses": raid_bosses,
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
    # Verify the character belongs to this player and is a guild character
    pc_result = await db.execute(
        select(PlayerCharacter)
        .join(PlayerCharacter.character)
        .where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
            WowCharacter.in_guild == True,
        )
    )
    if not pc_result.scalar_one_or_none():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # Check WCL config
    wcl_result = await db.execute(select(WclConfig).limit(1))
    wcl_cfg = wcl_result.scalar_one_or_none()
    wcl_configured = bool(wcl_cfg and wcl_cfg.is_configured)

    # Load parses from character_report_parses — zone IDs queried directly,
    # no current_raid_ids dependency. Every row is a kill parse by construction.
    parses = []
    tier_name: str | None = None

    zone_result = await db.execute(
        text("""
            SELECT DISTINCT zone_id
            FROM guild_identity.character_report_parses
            WHERE zone_id IS NOT NULL AND zone_id > 0
        """)
    )
    current_wcl_zone_ids = [row.zone_id for row in zone_result]

    if current_wcl_zone_ids:
        report_parse_result = await db.execute(
            text("""
                SELECT encounter_name, zone_name,
                       MAX(percentile)::numeric(5,1) AS best_pct,
                       MAX(report_code) AS report_code,
                       MAX(raid_date) AS raid_date,
                       MAX(last_synced) AS last_synced
                FROM guild_identity.character_report_parses
                WHERE character_id = :char_id
                  AND zone_id = ANY(:zone_ids)
                GROUP BY encounter_name, zone_name
                ORDER BY encounter_name
            """).bindparams(char_id=character_id, zone_ids=current_wcl_zone_ids)
        )
        for row in report_parse_result:
            if tier_name is None and row.zone_name:
                tier_name = row.zone_name
            recorded_at = None
            if row.raid_date:
                recorded_at = row.raid_date.isoformat()
            elif row.last_synced:
                recorded_at = row.last_synced.isoformat()
            parses.append({
                "boss_name": row.encounter_name,
                "difficulty": _DIFF_NAMES.get(3, "normal"),
                "percentile": float(row.best_pct),
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


@router.get("/character/{character_id}/market")
async def get_character_market(
    character_id: int,
    request: Request,
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return AH market prices for a character's realm, owned by the current member."""
    # Verify the character belongs to this player
    pc_result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
        )
    )
    if not pc_result.scalar_one_or_none():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # Get the character's realm_slug (only guild characters have market data)
    char_result = await db.execute(
        select(WowCharacter).where(WowCharacter.id == character_id, WowCharacter.in_guild == True)
    )
    char = char_result.scalar_one_or_none()
    if not char:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return {"ok": True, "data": {"prices": [], "realm_id": 0, "available": False}}

    try:
        # Determine the connected realm ID for this character
        cfg = get_site_config()
        home_realm_slug = cfg.get("home_realm_slug", "")
        home_connected_realm_id = cfg.get("connected_realm_id") or 0

        if char.realm_slug and char.realm_slug == home_realm_slug:
            realm_id = home_connected_realm_id
        else:
            # Character is on a different realm — use commodity prices (realm_id=0)
            # which cover all tracked guild items (consumables, enchants, gems)
            realm_id = 0

        from sv_common.guild_sync.ah_service import get_prices_for_realm
        prices = await get_prices_for_realm(pool, realm_id)
        prices_filtered = [p for p in prices if p.get("min_buyout") is not None]

        # Derive the most recent snapshot timestamp across all returned rows
        last_updated = None
        for p in prices_filtered:
            snap = p.get("snapshot_at")
            if snap and (last_updated is None or snap > last_updated):
                last_updated = snap

        return {
            "ok": True,
            "data": {
                "prices": prices_filtered,
                "realm_id": realm_id,
                "available": bool(prices_filtered),
                "last_updated": last_updated.isoformat() if last_updated else None,
            },
        }
    except Exception:
        return {"ok": True, "data": {"prices": [], "realm_id": 0, "available": False}}


@router.get("/character/{character_id}/crafting")
async def get_character_crafting(
    character_id: int,
    request: Request,
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return craftable recipes and consumable prices for a character owned by the current member."""
    # Verify the character belongs to this player
    pc_result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
        )
    )
    if not pc_result.scalar_one_or_none():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # Get character info for realm determination (only guild characters have crafting data)
    char_result = await db.execute(
        select(WowCharacter).where(WowCharacter.id == character_id, WowCharacter.in_guild == True)
    )
    char = char_result.scalar_one_or_none()
    if not char:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # ── Craftable recipes ────────────────────────────────────────────────────
    recipe_result = await db.execute(
        text(
            """
            SELECT r.id AS recipe_id,
                   r.name AS recipe_name,
                   p.name AS profession,
                   pt.name AS tier_name,
                   pt.expansion_name
            FROM guild_identity.character_recipes cr
            JOIN guild_identity.recipes r ON r.id = cr.recipe_id
            JOIN guild_identity.professions p ON p.id = r.profession_id
            JOIN guild_identity.profession_tiers pt ON pt.id = r.tier_id
            WHERE cr.character_id = :char_id
            ORDER BY p.name, pt.sort_order DESC, r.name
            """
        ),
        {"char_id": character_id},
    )

    craftable = []
    for row in recipe_result:
        wowhead_url = f"https://www.wowhead.com/search?q={quote_plus(row.recipe_name)}"
        craftable.append({
            "recipe_id": row.recipe_id,
            "recipe_name": row.recipe_name,
            "profession": row.profession,
            "tier_name": row.tier_name,
            "expansion_name": row.expansion_name,
            "rank": None,
            "max_rank": None,
            "can_craft_fully": True,
            "wowhead_url": wowhead_url,
        })

    # ── Consumable prices ────────────────────────────────────────────────────
    consumables: list[dict] = []
    pool = getattr(request.app.state, "guild_sync_pool", None)

    if pool:
        try:
            cfg = get_site_config()
            home_realm_slug = cfg.get("home_realm_slug", "")
            home_connected_realm_id = cfg.get("connected_realm_id") or 0

            if char.realm_slug and char.realm_slug == home_realm_slug:
                realm_id = home_connected_realm_id
            else:
                realm_id = 0

            from sv_common.guild_sync.ah_service import get_consumable_prices_for_realm
            consumables = await get_consumable_prices_for_realm(pool, realm_id)
        except Exception:
            pass  # Prices are non-critical

    return {
        "ok": True,
        "data": {
            "character_id": character_id,
            "craftable": craftable,
            "consumables": consumables,
        },
    }


@router.get("/character/{character_id}/summary")
async def get_character_summary(
    character_id: int,
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Return summary stats for a character: avg ilvl, M+, raid, parses, professions."""
    # Verify ownership
    pc_result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
        )
    )
    if not pc_result.scalar_one_or_none():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    # ── avg ilvl from character_equipment (shirt/tabard excluded at sync time) ──
    ilvl_result = await db.execute(
        text("""
            SELECT ROUND(AVG(item_level)::numeric, 0)::int AS avg_ilvl
            FROM guild_identity.character_equipment
            WHERE character_id = :char_id
        """),
        {"char_id": character_id},
    )
    ilvl_row = ilvl_result.fetchone()
    avg_ilvl = int(ilvl_row.avg_ilvl) if ilvl_row and ilvl_row.avg_ilvl else None

    # ── M+ score + color + raid summary from raiderio_profiles ──────────────
    rio_result = await db.execute(
        text("""
            SELECT overall_score, score_color, raid_progression
            FROM guild_identity.raiderio_profiles
            WHERE character_id = :char_id AND season = 'current'
            LIMIT 1
        """),
        {"char_id": character_id},
    )
    rio_row = rio_result.fetchone()
    mplus_score: float | None = None
    mplus_color: str | None = None
    raid_summary: str | None = None
    if rio_row:
        mplus_score = float(rio_row.overall_score) if rio_row.overall_score else None
        mplus_color = rio_row.score_color
        raid_summary = rio_row.raid_progression  # already "8/8 H" style string

    # ── avg parse from character_report_parses (current zone) ───────────────
    zone_result = await db.execute(
        text("""
            SELECT DISTINCT zone_id
            FROM guild_identity.character_report_parses
            WHERE zone_id IS NOT NULL AND zone_id > 0
        """)
    )
    zone_ids = [r.zone_id for r in zone_result]
    avg_parse: int | None = None
    if zone_ids:
        parse_result = await db.execute(
            text("""
                SELECT AVG(percentile)::numeric(5,1) AS avg_pct
                FROM guild_identity.character_report_parses
                WHERE character_id = :char_id
                  AND zone_id = ANY(:zone_ids)
                  AND percentile > 0
            """).bindparams(char_id=character_id, zone_ids=zone_ids)
        )
        parse_row = parse_result.fetchone()
        if parse_row and parse_row.avg_pct is not None:
            avg_parse = round(float(parse_row.avg_pct))

    # ── profession names + count from character_recipes ──────────────────────
    prof_result = await db.execute(
        text("""
            SELECT DISTINCT p.name AS profession_name
            FROM guild_identity.character_recipes cr
            JOIN guild_identity.recipes r ON r.id = cr.recipe_id
            JOIN guild_identity.professions p ON p.id = r.profession_id
            WHERE cr.character_id = :char_id
            ORDER BY p.name
        """),
        {"char_id": character_id},
    )
    profession_names = [row.profession_name for row in prof_result]
    profession_count = len(profession_names)

    return {
        "ok": True,
        "data": {
            "avg_ilvl": avg_ilvl,
            "mplus_score": mplus_score,
            "mplus_color": mplus_color,
            "raid_summary": raid_summary,
            "avg_parse": avg_parse,
            "profession_count": profession_count,
            "profession_names": profession_names,
        },
    }


@router.get("/character/{character_id}/parses-detail")
async def get_character_parses_detail(
    character_id: int,
    player: Player = Depends(get_current_player),
    db: AsyncSession = Depends(get_db),
):
    """Per-encounter WCL parse breakdown for the Parses detail panel."""
    pc_result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.player_id == player.id,
            PlayerCharacter.character_id == character_id,
        )
    )
    if not pc_result.scalar_one_or_none():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    rows_result = await db.execute(
        text("""
            SELECT encounter_name, zone_id, zone_name, difficulty,
                   MAX(percentile)::numeric(5,1)  AS best_pct,
                   COUNT(*)                        AS total_kills,
                   AVG(percentile)::numeric(5,1)   AS avg_pct,
                   MAX(amount)::numeric(14,1)       AS best_dps
            FROM guild_identity.character_report_parses
            WHERE character_id = :char_id
            GROUP BY encounter_name, zone_id, zone_name, difficulty
            ORDER BY zone_id, difficulty DESC, encounter_name
        """),
        {"char_id": character_id},
    )
    rows = rows_result.fetchall()

    _DIFF_LABEL = {3: "Normal", 4: "Heroic", 5: "Mythic"}

    raid_rows: list[dict] = []
    overall_map: dict[str, dict] = {}  # encounter_name -> highest-difficulty row

    for r in rows:
        entry = {
            "encounter_name": r.encounter_name,
            "zone_id": r.zone_id,
            "zone_name": r.zone_name,
            "difficulty": r.difficulty,
            "difficulty_label": _DIFF_LABEL.get(r.difficulty, str(r.difficulty)),
            "best_pct": float(r.best_pct) if r.best_pct is not None else None,
            "total_kills": int(r.total_kills),
            "avg_pct": float(r.avg_pct) if r.avg_pct is not None else None,
            "best_dps": float(r.best_dps) if r.best_dps is not None else None,
        }
        raid_rows.append(entry)

        # Overall: keep highest difficulty row per encounter name
        enc_key = r.encounter_name
        if enc_key not in overall_map or r.difficulty > overall_map[enc_key]["difficulty"]:
            overall_map[enc_key] = entry

    overall_rows = sorted(overall_map.values(), key=lambda x: x["encounter_name"])

    return {
        "ok": True,
        "data": {
            "raid": raid_rows,
            "mythic_plus": [],  # WCL does not currently return M+ parses
            "overall": overall_rows,
        },
    }
