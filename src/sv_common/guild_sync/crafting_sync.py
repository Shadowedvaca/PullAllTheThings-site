"""
Crafting profession sync — fetches known recipes for all guild characters.

Adaptive cadence driven by patt.raid_seasons (the existing season reference table):
- First season of an expansion (is_new_expansion=True): daily for 4 weeks
- Any other season: daily for 2 weeks
- After the daily window: weekly
- Manual override via cadence_override_until in crafting_sync_config

The sync:
1. Loads the current active season from patt.raid_seasons
2. Fetches profession data for every non-removed character in wow_characters
3. Upserts professions, tiers, and recipes into reference tables
4. Updates the character_recipes junction table
5. Logs sync stats to crafting_sync_config
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

from .blizzard_client import BlizzardClient

logger = logging.getLogger(__name__)

# Only sync tiers from this sort_order and above (80 = Dragon Isles+).
# Classic, Outland, Northrend, etc. are excluded — not useful for current guild crafting.
SYNC_MIN_SORT_ORDER = 80

# Expansion sort order: higher = newer (for tier filtering on the frontend)
EXPANSION_SORT_ORDER = {
    "Khaz Algar": 90,
    "Dragon Isles": 80,
    "Shadowlands": 70,
    "Kul Tiran": 65,   # BfA Alliance-side naming
    "Zandalari": 65,   # BfA Horde-side naming
    "Legion": 60,
    "Draenor": 50,
    "Pandaria": 40,
    "Cataclysm": 30,
    "Northrend": 20,
    "Outland": 10,
    "Classic": 0,
}


@dataclass
class CraftingSyncConfig:
    """Runtime representation of guild_identity.crafting_sync_config."""
    id: int
    current_cadence: str
    cadence_override_until: Optional[datetime]
    last_sync_at: Optional[datetime]


@dataclass
class SeasonData:
    """Current season info from patt.raid_seasons."""
    id: int
    name: str
    start_date: datetime          # stored as DATE, treated as UTC midnight
    is_new_expansion: bool


def derive_expansion_name(tier_name: str, profession_name: str) -> tuple[str, int]:
    """Extract expansion name and sort order from a Blizzard tier name.

    Blizzard tier names follow the pattern "{Expansion} {Profession}".
    Example: "Khaz Algar Blacksmithing" → ("Khaz Algar", 90)
    """
    expansion = tier_name.replace(profession_name, "").strip()
    sort_order = EXPANSION_SORT_ORDER.get(expansion, -1)
    return expansion, sort_order


def compute_sync_cadence(
    config: CraftingSyncConfig,
    season: Optional[SeasonData],
) -> tuple[str, int]:
    """
    Determine if we should sync daily or weekly.

    Season data comes from patt.raid_seasons (the shared reference table).

    Returns: (cadence, daily_days_remaining)
        cadence: 'daily' or 'weekly'
        daily_days_remaining: days left in the daily window (0 if weekly)
    """
    now = datetime.now(timezone.utc)

    # Admin override takes priority
    if config.cadence_override_until and now < config.cadence_override_until:
        remaining = (config.cadence_override_until - now).days
        return "daily", max(remaining, 0)

    if not season:
        return "weekly", 0

    # Normalise start_date to UTC midnight for arithmetic
    season_start = season.start_date
    if season_start.tzinfo is None:
        season_start = season_start.replace(tzinfo=timezone.utc)

    days_since_season = (now - season_start).days
    daily_window = 28 if season.is_new_expansion else 14

    if days_since_season <= daily_window:
        return "daily", daily_window - days_since_season

    return "weekly", 0


def get_season_display_name(season: Optional[SeasonData]) -> str:
    """Return the season display name, or a fallback if no season is active."""
    if season:
        return season.name
    return "No season configured"


async def _load_config(conn: asyncpg.Connection) -> Optional[CraftingSyncConfig]:
    """Load the single crafting_sync_config row."""
    row = await conn.fetchrow(
        """SELECT id, current_cadence, cadence_override_until, last_sync_at
           FROM guild_identity.crafting_sync_config
           LIMIT 1"""
    )
    if not row:
        return None
    return CraftingSyncConfig(
        id=row["id"],
        current_cadence=row["current_cadence"],
        cadence_override_until=row["cadence_override_until"],
        last_sync_at=row["last_sync_at"],
    )


async def _load_current_season(conn: asyncpg.Connection) -> Optional[SeasonData]:
    """Load the current active season from patt.raid_seasons."""
    today = datetime.now(timezone.utc).date()
    row = await conn.fetchrow(
        """SELECT id, name, start_date, is_new_expansion
           FROM patt.raid_seasons
           WHERE is_active = TRUE AND start_date <= $1
           ORDER BY start_date DESC
           LIMIT 1""",
        today,
    )
    if not row:
        return None
    start = datetime.combine(row["start_date"], datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    return SeasonData(
        id=row["id"],
        name=row["name"],
        start_date=start,
        is_new_expansion=row["is_new_expansion"],
    )


async def _upsert_profession(
    conn: asyncpg.Connection, profession_id: int, name: str, is_primary: bool
) -> int:
    """Upsert a profession row, return its DB id."""
    row = await conn.fetchrow(
        """INSERT INTO guild_identity.professions (blizzard_id, name, is_primary)
           VALUES ($1, $2, $3)
           ON CONFLICT (blizzard_id) DO UPDATE SET name = EXCLUDED.name
           RETURNING id""",
        profession_id, name, is_primary,
    )
    return row["id"]


async def _upsert_tier(
    conn: asyncpg.Connection,
    profession_db_id: int,
    tier_id: int,
    tier_name: str,
    profession_name: str,
) -> int:
    """Upsert a profession_tier row, return its DB id."""
    expansion_name, sort_order = derive_expansion_name(tier_name, profession_name)
    row = await conn.fetchrow(
        """INSERT INTO guild_identity.profession_tiers
               (profession_id, blizzard_tier_id, name, expansion_name, sort_order)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (blizzard_tier_id) DO UPDATE
               SET name = EXCLUDED.name,
                   expansion_name = EXCLUDED.expansion_name,
                   sort_order = EXCLUDED.sort_order
           RETURNING id""",
        profession_db_id, tier_id, tier_name, expansion_name, sort_order,
    )
    return row["id"]


async def _upsert_recipe(
    conn: asyncpg.Connection,
    recipe_id: int,
    name: str,
    profession_db_id: int,
    tier_db_id: int,
) -> int:
    """Upsert a recipe row, return its DB id."""
    row = await conn.fetchrow(
        """INSERT INTO guild_identity.recipes (blizzard_recipe_id, name, profession_id, tier_id)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (blizzard_recipe_id) DO UPDATE
               SET name = EXCLUDED.name,
                   profession_id = EXCLUDED.profession_id,
                   tier_id = EXCLUDED.tier_id
           RETURNING id""",
        recipe_id, name, profession_db_id, tier_db_id,
    )
    return row["id"]


async def sync_character_recipes(
    conn: asyncpg.Connection,
    character_db_id: int,
    recipe_db_ids: list[int],
) -> dict:
    """
    Replace a character's known recipes.

    Inserts new recipe links and removes any that are no longer known.
    Returns {"added": int, "removed": int}.
    """
    existing_rows = await conn.fetch(
        "SELECT recipe_id FROM guild_identity.character_recipes WHERE character_id = $1",
        character_db_id,
    )
    existing_ids = {r["recipe_id"] for r in existing_rows}
    new_ids = set(recipe_db_ids)

    to_add = new_ids - existing_ids
    to_remove = existing_ids - new_ids

    if to_add:
        await conn.executemany(
            """INSERT INTO guild_identity.character_recipes (character_id, recipe_id)
               VALUES ($1, $2) ON CONFLICT DO NOTHING""",
            [(character_db_id, rid) for rid in to_add],
        )

    if to_remove:
        await conn.execute(
            """DELETE FROM guild_identity.character_recipes
               WHERE character_id = $1 AND recipe_id = ANY($2::int[])""",
            character_db_id, list(to_remove),
        )

    return {"added": len(to_add), "removed": len(to_remove)}


async def run_crafting_sync(
    pool: asyncpg.Pool,
    blizzard_client: BlizzardClient,
    force: bool = False,
) -> dict:
    """
    Main crafting sync entry point.

    Fetches profession data for all active guild characters and upserts
    professions, tiers, recipes, and character_recipes into the DB.

    Returns stats dict with characters_processed, recipes_found, etc.
    """
    start = time.time()

    async with pool.acquire() as conn:
        config = await _load_config(conn)
        if not config:
            logger.error("No crafting_sync_config row found — run migration 0015 first")
            return {"error": "no_config"}

        season = await _load_current_season(conn)
        cadence, _ = compute_sync_cadence(config, season)

        # Skip if weekly cadence and not enough time has passed
        if not force and cadence == "weekly" and config.last_sync_at:
            days_since = (datetime.now(timezone.utc) - config.last_sync_at).days
            if days_since < 7:
                logger.info(
                    "Crafting sync skipped: weekly cadence, only %d days since last sync",
                    days_since,
                )
                return {"skipped": True, "reason": "cadence_weekly"}

        characters = await conn.fetch(
            """SELECT id, character_name, realm_slug
               FROM guild_identity.wow_characters
               WHERE removed_at IS NULL
               ORDER BY character_name"""
        )

    logger.info(
        "Crafting sync starting for %d characters (season: %s, cadence: %s)",
        len(characters),
        season.name if season else "none",
        cadence,
    )

    chars_processed = 0
    recipes_found = 0
    batch_size = 10

    for i in range(0, len(characters), batch_size):
        batch = characters[i:i + batch_size]
        tasks = [
            blizzard_client.get_character_professions(
                c["realm_slug"], c["character_name"]
            )
            for c in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for char, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Crafting fetch failed for %s: %s", char["character_name"], result
                )
                continue

            chars_processed += 1
            char_recipe_ids: list[int] = []

            if result is None:
                async with pool.acquire() as conn:
                    await sync_character_recipes(conn, char["id"], [])
                continue

            async with pool.acquire() as conn:
                for prof in result.professions:
                    prof_db_id = await _upsert_profession(
                        conn,
                        prof["profession_id"],
                        prof["profession_name"],
                        prof.get("is_primary", True),
                    )

                    for tier in prof["tiers"]:
                        _, sort_order = derive_expansion_name(
                            tier["tier_name"], prof["profession_name"]
                        )
                        if sort_order < SYNC_MIN_SORT_ORDER:
                            continue  # Skip old-expansion tiers

                        tier_db_id = await _upsert_tier(
                            conn,
                            prof_db_id,
                            tier["tier_id"],
                            tier["tier_name"],
                            prof["profession_name"],
                        )

                        for recipe in tier["known_recipes"]:
                            recipe_db_id = await _upsert_recipe(
                                conn,
                                recipe["id"],
                                recipe["name"],
                                prof_db_id,
                                tier_db_id,
                            )
                            char_recipe_ids.append(recipe_db_id)
                            recipes_found += 1

                await sync_character_recipes(conn, char["id"], char_recipe_ids)

        if i + batch_size < len(characters):
            await asyncio.sleep(0.5)

    duration = time.time() - start

    # Update config with sync stats
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        cadence_after, _ = compute_sync_cadence(config, season)
        next_sync_delta = timedelta(days=1 if cadence_after == "daily" else 7)
        next_sync = datetime(now.year, now.month, now.day, 3, 0, 0, tzinfo=timezone.utc)
        next_sync = next_sync + next_sync_delta

        await conn.execute(
            """UPDATE guild_identity.crafting_sync_config SET
               last_sync_at = $1,
               next_sync_at = $2,
               last_sync_duration_seconds = $3,
               last_sync_characters_processed = $4,
               last_sync_recipes_found = $5,
               updated_at = $1
               WHERE id = $6""",
            now, next_sync, duration, chars_processed, recipes_found, config.id,
        )

        await conn.execute(
            """INSERT INTO guild_identity.sync_log
               (source, status, characters_found, characters_updated, duration_seconds,
                started_at, completed_at)
               VALUES ('crafting_sync', 'success', $1, $2, $3, $4, $5)""",
            len(characters), chars_processed, duration, now, now,
        )

    logger.info(
        "Crafting sync complete: %d characters processed, %d recipes found, %.1fs",
        chars_processed, recipes_found, duration,
    )
    return {
        "characters_total": len(characters),
        "characters_processed": chars_processed,
        "recipes_found": recipes_found,
        "duration_seconds": duration,
    }
