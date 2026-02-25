"""
Crafting Corner data access layer.

All queries use asyncpg directly (raw SQL). Provides the data the
frontend pages and API routes need without touching ORM models.
"""

import logging
from typing import Optional

import asyncpg

from .crafting_sync import compute_sync_cadence, get_season_display_name, _load_config, _load_current_season

logger = logging.getLogger(__name__)


async def get_profession_list(pool: asyncpg.Pool) -> list[dict]:
    """Return all professions that have at least one known recipe, sorted alphabetically."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.name, p.is_primary
               FROM guild_identity.professions p
               WHERE EXISTS (
                   SELECT 1 FROM guild_identity.recipes r
                   WHERE r.profession_id = p.id
               )
               ORDER BY p.name"""
        )
    return [dict(r) for r in rows]


async def get_expansion_list(pool: asyncpg.Pool, profession_id: int) -> list[dict]:
    """Return all tiers for a profession that have recipes, sorted by sort_order DESC (newest first)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT pt.id, pt.name, pt.expansion_name, pt.sort_order
               FROM guild_identity.profession_tiers pt
               WHERE pt.profession_id = $1
                 AND EXISTS (
                     SELECT 1 FROM guild_identity.recipes r WHERE r.tier_id = pt.id
                 )
               ORDER BY pt.sort_order DESC, pt.name""",
            profession_id,
        )
    return [dict(r) for r in rows]


async def get_recipes_for_filter(
    pool: asyncpg.Pool,
    profession_id: int,
    tier_id: int,
) -> list[dict]:
    """
    Return all recipes for a profession+tier combo, sorted alphabetically.
    Each recipe includes: id, name, blizzard_recipe_id, wowhead_url, crafter_count.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT r.id, r.name, r.blizzard_recipe_id, r.wowhead_url,
                      COUNT(cr.id) AS crafter_count
               FROM guild_identity.recipes r
               LEFT JOIN guild_identity.character_recipes cr ON cr.recipe_id = r.id
               LEFT JOIN guild_identity.wow_characters wc
                   ON wc.id = cr.character_id AND wc.removed_at IS NULL
               WHERE r.profession_id = $1 AND r.tier_id = $2
               GROUP BY r.id, r.name, r.blizzard_recipe_id, r.wowhead_url
               ORDER BY r.name""",
            profession_id, tier_id,
        )
    return [dict(r) for r in rows]


async def get_recipe_crafters(pool: asyncpg.Pool, recipe_id: int) -> dict:
    """
    Return all characters that know a recipe, grouped by guild rank.

    Returns a dict with recipe info and a rank_groups list (ordered by rank level).
    Characters without a player link still appear with NULL discord fields.
    Characters with removed_at are excluded.
    """
    async with pool.acquire() as conn:
        recipe_row = await conn.fetchrow(
            "SELECT id, name, wowhead_url FROM guild_identity.recipes WHERE id = $1",
            recipe_id,
        )
        if not recipe_row:
            return {"recipe": None, "rank_groups": []}

        rows = await conn.fetch(
            """SELECT
                   wc.character_name,
                   wc.realm_slug,
                   cl.name AS character_class,
                   gr.name AS guild_rank_name,
                   gr.level AS guild_rank_level,
                   du.discord_id AS player_discord_id,
                   du.username AS player_discord_username,
                   COALESCE(p.crafting_notifications_enabled, false) AS crafting_notifications_enabled
               FROM guild_identity.character_recipes cr
               JOIN guild_identity.wow_characters wc ON wc.id = cr.character_id
               LEFT JOIN guild_identity.classes cl ON cl.id = wc.class_id
               LEFT JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
               LEFT JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
               LEFT JOIN guild_identity.players p ON p.id = pc.player_id
               LEFT JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               WHERE cr.recipe_id = $1
                 AND wc.removed_at IS NULL
               ORDER BY COALESCE(gr.level, 999) ASC, wc.character_name""",
            recipe_id,
        )

    # Group into rank tiers
    # Display groups: GL/Officer (0-1), Veteran (2), Member (3), Initiate (4+)
    GROUP_MAP = {
        0: "Guild Leader / Officer",
        1: "Guild Leader / Officer",
        2: "Veteran",
        3: "Member",
        4: "Initiate",
    }
    GROUP_ORDER = ["Guild Leader / Officer", "Veteran", "Member", "Initiate", "Unknown"]

    groups: dict[str, list[dict]] = {g: [] for g in GROUP_ORDER}

    for row in rows:
        level = row["guild_rank_level"]
        if level is None:
            group_name = "Unknown"
        elif level <= 1:
            group_name = "Guild Leader / Officer"
        elif level == 2:
            group_name = "Veteran"
        elif level == 3:
            group_name = "Member"
        else:
            group_name = "Initiate"

        groups[group_name].append({
            "character_name": row["character_name"],
            "character_class": row["character_class"],
            "realm_slug": row["realm_slug"],
            "guild_rank_name": row["guild_rank_name"],
            "player_discord_id": row["player_discord_id"],
            "player_discord_username": row["player_discord_username"],
            "crafting_notifications_enabled": row["crafting_notifications_enabled"],
        })

    rank_groups = [
        {"rank_name": g, "crafters": groups[g]}
        for g in GROUP_ORDER
        if groups[g]
    ]

    return {
        "recipe": {
            "id": recipe_row["id"],
            "name": recipe_row["name"],
            "wowhead_url": recipe_row["wowhead_url"],
        },
        "rank_groups": rank_groups,
    }


async def search_recipes(pool: asyncpg.Pool, query: str) -> list[dict]:
    """
    Full-text search across all recipes regardless of profession/expansion.
    Returns: [{id, name, wowhead_url, profession_name, tier_name, crafter_count}]
    Uses ILIKE with wildcards. Limit to 100 results.
    """
    if not query or len(query) < 2:
        return []

    search_term = f"%{query}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT r.id, r.name, r.wowhead_url,
                      p.name AS profession_name,
                      pt.name AS tier_name,
                      pt.expansion_name,
                      COUNT(cr.id) AS crafter_count
               FROM guild_identity.recipes r
               JOIN guild_identity.professions p ON p.id = r.profession_id
               JOIN guild_identity.profession_tiers pt ON pt.id = r.tier_id
               LEFT JOIN guild_identity.character_recipes cr ON cr.recipe_id = r.id
               LEFT JOIN guild_identity.wow_characters wc
                   ON wc.id = cr.character_id AND wc.removed_at IS NULL
               WHERE r.name ILIKE $1
               GROUP BY r.id, r.name, r.wowhead_url, p.name, pt.name, pt.expansion_name
               ORDER BY r.name
               LIMIT 100""",
            search_term,
        )
    return [dict(r) for r in rows]


async def get_sync_status(pool: asyncpg.Pool) -> dict:
    """
    Return sync status for display on the crafting corner page.
    """
    async with pool.acquire() as conn:
        config = await _load_config(conn)
        season = await _load_current_season(conn)

    if not config:
        return {
            "season_name": get_season_display_name(season),
            "last_sync_at": None,
            "next_sync_at": None,
            "current_cadence": "weekly",
            "daily_days_remaining": 0,
        }

    cadence, days_remaining = compute_sync_cadence(config, season)
    return {
        "season_name": get_season_display_name(season),
        "last_sync_at": config.last_sync_at.isoformat() if config.last_sync_at else None,
        "current_cadence": cadence,
        "daily_days_remaining": days_remaining,
    }


async def get_full_config(pool: asyncpg.Pool) -> Optional[dict]:
    """
    Return crafting sync config combined with the current season for the admin page.
    Season data is sourced from patt.raid_seasons.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, current_cadence, cadence_override_until,
                      last_sync_at, next_sync_at, last_sync_duration_seconds,
                      last_sync_characters_processed, last_sync_recipes_found
               FROM guild_identity.crafting_sync_config LIMIT 1"""
        )
        season = await _load_current_season(conn)

    if not row:
        return None

    result = dict(row)
    result["season_name"] = get_season_display_name(season)
    result["season_start_date"] = season.start_date.isoformat() if season else None
    result["is_new_expansion"] = season.is_new_expansion if season else False
    return result


async def get_player_crafting_preference(pool: asyncpg.Pool, player_id: int) -> bool:
    """Return crafting_notifications_enabled for a player."""
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT crafting_notifications_enabled FROM guild_identity.players WHERE id = $1",
            player_id,
        )
    return bool(val)


async def set_player_crafting_preference(
    pool: asyncpg.Pool, player_id: int, enabled: bool
) -> bool:
    """Set crafting_notifications_enabled for a player. Returns True on success."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE guild_identity.players
               SET crafting_notifications_enabled = $1, updated_at = NOW()
               WHERE id = $2""",
            enabled, player_id,
        )
    return result == "UPDATE 1"
