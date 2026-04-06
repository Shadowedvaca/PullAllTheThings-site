"""
Progression data sync — raid encounters, Mythic+, achievements, weekly snapshots.

Uses Blizzard Profile API endpoints:
  - /encounters/raids     → boss kill counts per difficulty
  - /mythic-keystone-profile/season/{id} → M+ rating + best runs per dungeon
  - /achievements         → filtered to tracked_achievements table

Called from the Blizzard sync pipeline (scheduler.py) for characters that
have logged in since their last_progression_sync timestamp.

Achievement sync runs weekly (Sunday) after progression snapshots are taken.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Optional

import asyncpg

from .blizzard_client import BlizzardClient, should_sync_character
from .raiderio_client import RaiderIOClient, RaiderIOProfile as RIOProfile

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10


def _chunk(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ---------------------------------------------------------------------------
# Raid encounters
# ---------------------------------------------------------------------------


def _parse_raid_encounters(data: dict) -> tuple[list[dict], dict]:
    """Flatten the nested Blizzard encounter response into kill records and boss counts.

    Returns:
        records: list of {raid_name, raid_id, difficulty, boss_name, boss_id, kill_count}
                 Only includes bosses with at least 1 kill.
        boss_counts: dict of {(raid_id, difficulty): total_boss_count}
                     Includes all modes, even those with 0 kills.
    """
    records = []
    boss_counts: dict[tuple[int, str], int] = {}
    for expansion in data.get("expansions", []):
        for instance in expansion.get("instances", []):
            inst = instance.get("instance", {})
            raid_name = inst.get("name", "Unknown")
            raid_id = inst.get("id", 0)

            for mode in instance.get("modes", []):
                diff_type = mode.get("difficulty", {}).get("type", "").lower()
                progress = mode.get("progress", {})
                total = progress.get("total_count", 0)
                if raid_id and diff_type and total > 0:
                    boss_counts[(raid_id, diff_type)] = total
                for enc in progress.get("encounters", []):
                    boss = enc.get("encounter", {})
                    kills = enc.get("completed_count", 0)
                    if kills > 0:
                        records.append({
                            "raid_name": raid_name,
                            "raid_id": raid_id,
                            "difficulty": diff_type,
                            "boss_name": boss.get("name", "Unknown"),
                            "boss_id": boss.get("id", 0),
                            "kill_count": kills,
                        })
    return records, boss_counts


async def sync_raid_progress(
    pool: asyncpg.Pool,
    blizzard_client: BlizzardClient,
    characters: list[dict],
) -> dict:
    """Fetch and store raid encounter kill counts for the given characters.

    characters: list of {id, character_name, realm_slug}
    Returns stats: {synced, skipped, errors}
    """
    stats = {"synced": 0, "skipped": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    for batch in _chunk(characters, _BATCH_SIZE):
        results = await asyncio.gather(
            *[
                blizzard_client.get_character_encounters_raids(
                    c["realm_slug"], c["character_name"]
                )
                for c in batch
            ],
            return_exceptions=True,
        )

        for char, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Raid encounters fetch failed for %s: %s",
                    char["character_name"], result,
                )
                stats["errors"] += 1
                continue

            if result is None:
                stats["skipped"] += 1
                continue

            records, boss_counts = _parse_raid_encounters(result)

            async with pool.acquire() as conn:
                for rec in records:
                    await conn.execute(
                        """
                        INSERT INTO guild_identity.character_raid_progress
                            (character_id, raid_name, raid_id, difficulty,
                             boss_name, boss_id, kill_count, last_synced)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (character_id, boss_id, difficulty)
                        DO UPDATE SET
                            kill_count  = EXCLUDED.kill_count,
                            raid_name   = EXCLUDED.raid_name,
                            boss_name   = EXCLUDED.boss_name,
                            last_synced = EXCLUDED.last_synced
                        """,
                        char["id"],
                        rec["raid_name"], rec["raid_id"], rec["difficulty"],
                        rec["boss_name"], rec["boss_id"], rec["kill_count"], now,
                    )
                # Upsert static boss counts — same for all characters, idempotent
                for (raid_id, difficulty), boss_count in boss_counts.items():
                    await conn.execute(
                        """
                        INSERT INTO guild_identity.raid_boss_counts
                            (raid_id, difficulty, boss_count)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (raid_id, difficulty)
                        DO UPDATE SET boss_count = EXCLUDED.boss_count
                        """,
                        raid_id, difficulty, boss_count,
                    )

            stats["synced"] += 1

        if len(characters) > _BATCH_SIZE:
            await asyncio.sleep(0.5)

    logger.info(
        "Raid progress sync: %d synced, %d skipped, %d errors",
        stats["synced"], stats["skipped"], stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Mythic+ keystone profiles
# ---------------------------------------------------------------------------


def _parse_mythic_plus(data: dict) -> tuple[float, list[dict]]:
    """Parse M+ season response into (overall_rating, list of dungeon records).

    Each dungeon record: {dungeon_name, dungeon_id, best_level, best_timed, best_score}
    Returns (0.0, []) if data is empty or missing.
    """
    if not data:
        return 0.0, []

    overall = data.get("mythic_rating", {}).get("rating", 0.0)
    dungeons = []
    for run in data.get("best_runs", []):
        dungeon = run.get("dungeon", {})
        dungeons.append({
            "dungeon_name": dungeon.get("name", "Unknown"),
            "dungeon_id": dungeon.get("id", 0),
            "best_level": run.get("keystone_level", 0),
            "best_timed": run.get("is_completed_within_time", False),
            "best_score": run.get("mythic_rating", {}).get("rating", 0.0),
        })
    return overall, dungeons


async def sync_mythic_plus(
    pool: asyncpg.Pool,
    blizzard_client: BlizzardClient,
    characters: list[dict],
    season_id: Optional[int] = None,
) -> dict:
    """Fetch and store M+ data for the given characters.

    characters: list of {id, character_name, realm_slug}
    season_id: Blizzard M+ season ID; if None uses the current-season endpoint.
    Returns stats: {synced, skipped, errors}
    """
    stats = {"synced": 0, "skipped": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    for batch in _chunk(characters, _BATCH_SIZE):
        results = await asyncio.gather(
            *[
                blizzard_client.get_character_mythic_keystone_profile(
                    c["realm_slug"], c["character_name"], season_id
                )
                for c in batch
            ],
            return_exceptions=True,
        )

        for char, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.warning(
                    "M+ fetch failed for %s: %s", char["character_name"], result
                )
                stats["errors"] += 1
                continue

            if result is None:
                stats["skipped"] += 1
                continue

            effective_season_id = season_id or result.get("season", {}).get("id", 0)
            if not effective_season_id:
                stats["skipped"] += 1
                continue

            overall_rating, dungeons = _parse_mythic_plus(result)

            async with pool.acquire() as conn:
                for dungeon in dungeons:
                    await conn.execute(
                        """
                        INSERT INTO guild_identity.character_mythic_plus
                            (character_id, season_id, overall_rating,
                             dungeon_name, dungeon_id, best_level, best_timed,
                             best_score, last_synced)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        ON CONFLICT (character_id, season_id, dungeon_id)
                        DO UPDATE SET
                            overall_rating = EXCLUDED.overall_rating,
                            best_level     = EXCLUDED.best_level,
                            best_timed     = EXCLUDED.best_timed,
                            best_score     = EXCLUDED.best_score,
                            last_synced    = EXCLUDED.last_synced
                        """,
                        char["id"], effective_season_id, overall_rating,
                        dungeon["dungeon_name"], dungeon["dungeon_id"],
                        dungeon["best_level"], dungeon["best_timed"],
                        dungeon["best_score"], now,
                    )

            stats["synced"] += 1

        if len(characters) > _BATCH_SIZE:
            await asyncio.sleep(0.5)

    logger.info(
        "M+ sync: %d synced, %d skipped, %d errors",
        stats["synced"], stats["skipped"], stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Achievement sync
# ---------------------------------------------------------------------------


async def _load_tracked_ids(pool: asyncpg.Pool) -> set[int]:
    """Load active tracked achievement IDs from DB."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT achievement_id FROM guild_identity.tracked_achievements WHERE is_active = TRUE"
        )
    return {row["achievement_id"] for row in rows}


async def sync_achievements(
    pool: asyncpg.Pool,
    blizzard_client: BlizzardClient,
    characters: list[dict],
    force_full: bool = False,
) -> dict:
    """Fetch achievements and store only tracked ones.

    characters: list of {id, character_name, realm_slug, last_login_timestamp,
                         last_progression_sync}
    force_full: skip last-login optimization (used for weekly sweep)
    Returns stats: {synced, skipped, errors}
    """
    tracked_ids = await _load_tracked_ids(pool)
    if not tracked_ids:
        logger.info("No active tracked achievements — skipping achievement sync")
        return {"synced": 0, "skipped": len(characters), "errors": 0}

    stats = {"synced": 0, "skipped": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    for batch in _chunk(characters, _BATCH_SIZE):
        results = await asyncio.gather(
            *[
                blizzard_client.get_character_achievements(
                    c["realm_slug"], c["character_name"]
                )
                for c in batch
            ],
            return_exceptions=True,
        )

        for char, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Achievement fetch failed for %s: %s", char["character_name"], result
                )
                stats["errors"] += 1
                continue

            if result is None:
                stats["skipped"] += 1
                continue

            achievements = result.get("achievements", [])
            matched = [a for a in achievements if a.get("id") in tracked_ids]

            async with pool.acquire() as conn:
                for ach in matched:
                    completed_ts = ach.get("completed_timestamp")
                    completed_at = None
                    if completed_ts:
                        completed_at = datetime.fromtimestamp(
                            completed_ts / 1000, tz=timezone.utc
                        )

                    await conn.execute(
                        """
                        INSERT INTO guild_identity.character_achievements
                            (character_id, achievement_id, achievement_name,
                             completed_at, last_synced)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (character_id, achievement_id)
                        DO UPDATE SET
                            achievement_name = EXCLUDED.achievement_name,
                            completed_at     = EXCLUDED.completed_at,
                            last_synced      = EXCLUDED.last_synced
                        """,
                        char["id"], ach["id"],
                        ach.get("achievement", {}).get("name", str(ach["id"])),
                        completed_at, now,
                    )

            stats["synced"] += 1

        if len(characters) > _BATCH_SIZE:
            await asyncio.sleep(0.5)

    logger.info(
        "Achievement sync: %d synced, %d skipped, %d errors",
        stats["synced"], stats["skipped"], stats["errors"],
    )
    return stats


# ---------------------------------------------------------------------------
# Weekly progression snapshots
# ---------------------------------------------------------------------------


async def create_weekly_snapshot(pool: asyncpg.Pool) -> int:
    """Create weekly progression snapshots for all characters.

    Aggregates current raid kills into JSON and stores current M+ rating.
    Should run Sunday before achievement sync.
    Returns count of snapshots created/updated.
    """
    today = date.today()
    created = 0

    async with pool.acquire() as conn:
        characters = await conn.fetch(
            "SELECT id FROM guild_identity.wow_characters WHERE removed_at IS NULL AND in_guild = TRUE"
        )

        for char in characters:
            char_id = char["id"]

            # Aggregate raid kills: {boss_id: {difficulty: kill_count}}
            kill_rows = await conn.fetch(
                """
                SELECT boss_id, difficulty, kill_count
                FROM guild_identity.character_raid_progress
                WHERE character_id = $1
                """,
                char_id,
            )
            raid_kills: dict = {}
            for row in kill_rows:
                boss_key = str(row["boss_id"])
                if boss_key not in raid_kills:
                    raid_kills[boss_key] = {}
                raid_kills[boss_key][row["difficulty"]] = row["kill_count"]

            # Get current overall M+ rating (best across all seasons)
            rating_row = await conn.fetchrow(
                """
                SELECT MAX(overall_rating) AS rating
                FROM guild_identity.character_mythic_plus
                WHERE character_id = $1
                """,
                char_id,
            )
            mythic_rating = float(rating_row["rating"]) if rating_row["rating"] else None

            if not raid_kills and mythic_rating is None:
                continue  # No data to snapshot

            await conn.execute(
                """
                INSERT INTO guild_identity.progression_snapshots
                    (character_id, snapshot_date, raid_kills_json, mythic_rating)
                VALUES ($1, $2, $3::jsonb, $4)
                ON CONFLICT (character_id, snapshot_date)
                DO UPDATE SET
                    raid_kills_json = EXCLUDED.raid_kills_json,
                    mythic_rating   = EXCLUDED.mythic_rating
                """,
                char_id, today,
                json.dumps(raid_kills) if raid_kills else None,
                mythic_rating,
            )
            created += 1

    logger.info("Weekly progression snapshot: %d characters snapshotted for %s", created, today)
    return created


# ---------------------------------------------------------------------------
# Update timestamps
# ---------------------------------------------------------------------------


async def update_last_progression_sync(
    pool: asyncpg.Pool, character_ids: list[int]
) -> None:
    """Stamp last_progression_sync = NOW() for the given character IDs."""
    if not character_ids:
        return
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE guild_identity.wow_characters
            SET last_progression_sync = $1
            WHERE id = ANY($2::int[])
            """,
            now, character_ids,
        )


async def update_last_profession_sync(
    pool: asyncpg.Pool, character_ids: list[int]
) -> None:
    """Stamp last_profession_sync = NOW() for the given character IDs."""
    if not character_ids:
        return
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE guild_identity.wow_characters
            SET last_profession_sync = $1
            WHERE id = ANY($2::int[])
            """,
            now, character_ids,
        )


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


async def load_characters_for_progression_sync(
    pool: asyncpg.Pool, force_full: bool = False
) -> tuple[list[dict], int]:
    """Load characters eligible for raid/M+/achievement sync.

    Returns (filtered_chars, total_chars) where filtered_chars are those
    whose last_login_timestamp > last_progression_sync (or never synced).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, character_name, realm_slug,
                   last_login_timestamp, last_progression_sync
            FROM guild_identity.wow_characters
            WHERE removed_at IS NULL AND in_guild = TRUE
            ORDER BY character_name
            """
        )

    total = len(rows)
    if force_full:
        return [dict(r) for r in rows], total

    eligible = [
        dict(r) for r in rows
        if should_sync_character(
            r["last_login_timestamp"],
            r["last_progression_sync"],
        )
    ]
    return eligible, total


async def load_characters_for_profession_sync(
    pool: asyncpg.Pool, force_full: bool = False
) -> tuple[list[dict], int]:
    """Load characters eligible for profession/crafting sync.

    Returns (filtered_chars, total_chars) where filtered_chars are those
    whose last_login_timestamp > last_profession_sync (or never synced).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, character_name, realm_slug,
                   last_login_timestamp, last_profession_sync
            FROM guild_identity.wow_characters
            WHERE removed_at IS NULL AND in_guild = TRUE
            ORDER BY character_name
            """
        )

    total = len(rows)
    if force_full:
        return [dict(r) for r in rows], total

    eligible = [
        dict(r) for r in rows
        if should_sync_character(
            r["last_login_timestamp"],
            r["last_profession_sync"],
        )
    ]
    return eligible, total


# ---------------------------------------------------------------------------
# Raider.IO sync
# ---------------------------------------------------------------------------


async def sync_raiderio_profiles(
    pool: asyncpg.Pool,
    raiderio_client: RaiderIOClient,
    characters: list[dict],
    default_realm_slug: str,
) -> dict:
    """Fetch Raider.IO profiles and upsert into raiderio_profiles table.

    characters: list of {id, character_name, realm_slug}
    default_realm_slug: fallback realm for characters missing realm_slug
    Returns stats: {synced, total}
    """
    # Map our character dicts to what the RIO client expects
    rio_chars = [
        {
            "id": c["id"],
            "name": c["character_name"],
            "realm_slug": c.get("realm_slug", default_realm_slug),
        }
        for c in characters
    ]

    profiles = await raiderio_client.get_guild_profiles(
        rio_chars, default_realm_slug=default_realm_slug
    )

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        for char_id, profile in profiles.items():
            await conn.execute(
                """
                INSERT INTO guild_identity.raiderio_profiles
                    (character_id, season, overall_score, dps_score, healer_score,
                     tank_score, score_color, raid_progression, best_runs,
                     recent_runs, profile_url, last_synced)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12)
                ON CONFLICT (character_id, season) DO UPDATE SET
                    overall_score    = EXCLUDED.overall_score,
                    dps_score        = EXCLUDED.dps_score,
                    healer_score     = EXCLUDED.healer_score,
                    tank_score       = EXCLUDED.tank_score,
                    score_color      = EXCLUDED.score_color,
                    raid_progression = EXCLUDED.raid_progression,
                    best_runs        = EXCLUDED.best_runs,
                    recent_runs      = EXCLUDED.recent_runs,
                    profile_url      = EXCLUDED.profile_url,
                    last_synced      = EXCLUDED.last_synced
                """,
                char_id,
                "current",
                profile.overall_score,
                profile.dps_score,
                profile.healer_score,
                profile.tank_score,
                profile.score_color,
                profile.raid_progression,
                json.dumps(profile.best_runs),
                json.dumps(profile.recent_runs),
                profile.profile_url,
                now,
            )

    stats = {"synced": len(profiles), "total": len(characters)}
    logger.info(
        "Raider.IO sync: %d/%d profiles stored", stats["synced"], stats["total"]
    )
    return stats
