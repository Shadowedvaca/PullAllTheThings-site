"""
Warcraft Logs sync functions.

Fetches guild raid reports and character parse percentiles from the
Warcraft Logs v2 GraphQL API and stores results in the database.

Rate limit: ~3600 points/hour (1 point per query).
Character parses: ~1 point per character.
Guild reports: ~1 + N points (report list + fight detail per new report).
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from .warcraftlogs_client import WarcraftLogsClient, WarcraftLogsError

logger = logging.getLogger(__name__)


async def load_wcl_config(pool: asyncpg.Pool) -> Optional[dict]:
    """Load the single wcl_config row. Returns None if no row exists."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, client_id, client_secret_encrypted, wcl_guild_name,
                      wcl_server_slug, wcl_server_region, is_configured,
                      sync_enabled, last_sync, last_sync_status, last_sync_error
               FROM guild_identity.wcl_config
               LIMIT 1"""
        )
    return dict(row) if row else None


async def _update_sync_status(
    pool: asyncpg.Pool,
    config_id: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE guild_identity.wcl_config
               SET last_sync = NOW(), last_sync_status = $1,
                   last_sync_error = $2, updated_at = NOW()
               WHERE id = $3""",
            status,
            error,
            config_id,
        )


async def sync_guild_reports(
    pool: asyncpg.Pool,
    wcl_client: WarcraftLogsClient,
    guild_name: str,
    server_slug: str,
    region: str,
) -> dict:
    """Fetch recent guild reports and store in raid_reports table.

    Only fetches fight details for reports not yet stored.
    Returns stats: new_reports, updated, errors.
    """
    stats = {"new_reports": 0, "updated": 0, "errors": 0}
    try:
        data = await wcl_client.get_guild_reports(guild_name, server_slug, region)
        reports = data.get("reportData", {}).get("reports", {}).get("data", [])
    except (WarcraftLogsError, Exception) as exc:
        logger.error("WCL guild reports fetch failed: %s", exc)
        stats["errors"] += 1
        return stats

    for report in reports:
        code = report.get("code")
        if not code:
            continue

        # Check if already stored
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM guild_identity.raid_reports WHERE report_code = $1",
                code,
            )
        if existing:
            continue  # Already have this report

        # New report — fetch fight details + attendance
        try:
            fight_data = await wcl_client.get_report_fights(code)
            report_detail = fight_data.get("reportData", {}).get("report", {})

            # Parse attendees from masterData.actors
            actors = report_detail.get("masterData", {}).get("actors", []) or []
            attendees = [
                {
                    "name": a.get("name"),
                    "class": a.get("subType"),
                    "server": a.get("server"),
                }
                for a in actors
                if a.get("type") == "Player"
            ]

            # Count boss kills
            fights = report_detail.get("fights", []) or []
            boss_kills = sum(1 for f in fights if f.get("kill"))

            # Parse timestamps (WCL gives ms epoch)
            start_ms = report.get("startTime") or report_detail.get("startTime", 0)
            end_ms = report.get("endTime") or report_detail.get("endTime", 0)
            raid_date = None
            if start_ms:
                raid_date = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
            duration_ms = (end_ms - start_ms) if (end_ms and start_ms) else None

            zone = report.get("zone") or report_detail.get("zone") or {}
            owner = report.get("owner") or report_detail.get("owner") or {}

            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO guild_identity.raid_reports
                           (report_code, title, raid_date, zone_id, zone_name,
                            owner_name, boss_kills, duration_ms, attendees, report_url)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
                       ON CONFLICT (report_code) DO UPDATE
                           SET boss_kills = EXCLUDED.boss_kills,
                               attendees  = EXCLUDED.attendees,
                               last_synced = NOW()""",
                    code,
                    report.get("title"),
                    raid_date,
                    zone.get("id"),
                    zone.get("name"),
                    owner.get("name"),
                    boss_kills,
                    duration_ms,
                    json.dumps(attendees),
                    f"https://www.warcraftlogs.com/reports/{code}",
                )
            stats["new_reports"] += 1
            await asyncio.sleep(0.5)  # Space requests
        except Exception as exc:
            logger.warning(
                "Failed to fetch fight details for report %s: %s", code, exc
            )
            stats["errors"] += 1

    return stats


def _parse_zone_rankings(zone_rankings: dict) -> list[dict]:
    """Extract per-boss parse records from a WCL zoneRankings response.

    Returns list of dicts with encounter_id, encounter_name, zone_id, zone_name,
    difficulty, spec, percentile, amount, report_code, fight_id.
    """
    parses = []
    if not isinstance(zone_rankings, dict):
        return parses

    zone_id = zone_rankings.get("zone", 0)
    zone_name = zone_rankings.get("zoneName", "")
    difficulty = zone_rankings.get("difficulty", 4)
    spec = zone_rankings.get("bestSpec") or zone_rankings.get("spec") or "Unknown"

    for ranking in zone_rankings.get("rankings", []):
        encounter = ranking.get("encounter", {})
        enc_id = encounter.get("id", 0)
        enc_name = encounter.get("name", "Unknown")
        percentile = float(ranking.get("rankPercent") or 0)
        amount = ranking.get("bestAmount")
        report = ranking.get("report") or {}

        if enc_id and percentile > 0:
            parses.append({
                "encounter_id": enc_id,
                "encounter_name": enc_name,
                "zone_id": zone_id,
                "zone_name": zone_name,
                "difficulty": difficulty,
                "spec": spec,
                "percentile": percentile,
                "amount": float(amount) if amount else None,
                "report_code": report.get("code"),
                "fight_id": report.get("fightID"),
            })
    return parses


async def sync_character_parses(
    pool: asyncpg.Pool,
    wcl_client: WarcraftLogsClient,
    characters: list[dict],
    server_slug: str,
    region: str,
    zone_id: Optional[int] = None,
    batch_size: int = 5,
) -> dict:
    """Fetch parse percentiles for characters from WCL and store in character_parses.

    characters: list of dicts with at least 'id' (DB id) and 'name'.
    Rate limit: ~1 point per character query — spaces requests at ~2s/batch.
    Returns stats: synced, errors, parse_records.
    """
    stats = {"synced": 0, "errors": 0, "parse_records": 0}

    for i in range(0, len(characters), batch_size):
        batch = characters[i: i + batch_size]
        results = await asyncio.gather(
            *[
                wcl_client.get_character_parses(
                    c["name"], server_slug, region, zone_id
                )
                for c in batch
            ],
            return_exceptions=True,
        )

        for char, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.debug(
                    "WCL parse fetch failed for %s: %s", char.get("name"), result
                )
                stats["errors"] += 1
                continue

            # Extract zoneRankings from response
            char_data = (
                result.get("characterData", {})
                       .get("character") or {}
            )
            zone_rankings_raw = char_data.get("zoneRankings")
            if not zone_rankings_raw:
                continue

            parses = _parse_zone_rankings(zone_rankings_raw)
            if not parses:
                continue

            # Upsert each parse record
            async with pool.acquire() as conn:
                for p in parses:
                    await conn.execute(
                        """INSERT INTO guild_identity.character_parses
                               (character_id, encounter_id, encounter_name, zone_id,
                                zone_name, difficulty, spec, percentile, amount,
                                report_code, fight_id, last_synced)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
                           ON CONFLICT (character_id, encounter_id, difficulty, spec)
                           DO UPDATE SET
                               percentile   = EXCLUDED.percentile,
                               amount       = EXCLUDED.amount,
                               report_code  = EXCLUDED.report_code,
                               fight_id     = EXCLUDED.fight_id,
                               encounter_name = EXCLUDED.encounter_name,
                               zone_name    = EXCLUDED.zone_name,
                               last_synced  = NOW()
                           WHERE EXCLUDED.percentile > character_parses.percentile""",
                        char["id"],
                        p["encounter_id"],
                        p["encounter_name"],
                        p["zone_id"],
                        p["zone_name"],
                        p["difficulty"],
                        p["spec"],
                        p["percentile"],
                        p["amount"],
                        p["report_code"],
                        p["fight_id"],
                    )
                    stats["parse_records"] += 1
            stats["synced"] += 1

        if i + batch_size < len(characters):
            await asyncio.sleep(2.0)  # ~2.5 req/sec — well under 3600/hr

    return stats


async def compute_attendance(
    pool: asyncpg.Pool, limit_reports: int = 10
) -> dict:
    """Compute attendance rates from stored raid_reports.

    Returns {character_name_lower: {raids_attended, raids_possible, rate}}.
    Uses the most recent `limit_reports` reports.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT report_code, raid_date, attendees
               FROM guild_identity.raid_reports
               ORDER BY raid_date DESC
               LIMIT $1""",
            limit_reports,
        )

    if not rows:
        return {}

    total_raids = len(rows)
    counts: dict[str, int] = {}

    for row in rows:
        attendees = row["attendees"] or []
        for a in attendees:
            name = (a.get("name") or "").lower().strip()
            if name:
                counts[name] = counts.get(name, 0) + 1

    return {
        name: {
            "raids_attended": count,
            "raids_possible": total_raids,
            "rate": round(count / total_raids, 2),
        }
        for name, count in counts.items()
    }
