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

        # Check if already stored — but re-fetch if encounter_ids is empty (backfill path)
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                """SELECT id, encounter_ids
                   FROM guild_identity.raid_reports
                   WHERE report_code = $1""",
                code,
            )
        if existing and existing["encounter_ids"]:
            continue  # Already have this report with encounter data
        is_update = bool(existing)

        # New report (or backfill missing encounter data) — fetch fight details
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

            # Count boss kills + extract encounter IDs and name map
            fights = report_detail.get("fights", []) or []
            boss_kills = sum(1 for f in fights if f.get("kill"))
            encounter_ids = list({
                f["encounterID"]
                for f in fights
                if f.get("encounterID")
            })
            # Build encounterID→name lookup dict (deduplicated by ID)
            encounter_map: dict[str, str] = {}
            for f in fights:
                enc_id = f.get("encounterID")
                enc_name = f.get("name")
                if enc_id and enc_name and str(enc_id) not in encounter_map:
                    encounter_map[str(enc_id)] = enc_name

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
                            owner_name, boss_kills, duration_ms, attendees,
                            encounter_ids, encounter_map, report_url)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb,
                               $10, $11::jsonb, $12)
                       ON CONFLICT (report_code) DO UPDATE
                           SET boss_kills    = EXCLUDED.boss_kills,
                               attendees     = EXCLUDED.attendees,
                               encounter_ids = EXCLUDED.encounter_ids,
                               encounter_map = EXCLUDED.encounter_map,
                               last_synced   = NOW()""",
                    code,
                    report.get("title"),
                    raid_date,
                    zone.get("id"),
                    zone.get("name"),
                    owner.get("name"),
                    boss_kills,
                    duration_ms,
                    json.dumps(attendees),
                    encounter_ids,
                    json.dumps(encounter_map),
                    f"https://www.warcraftlogs.com/reports/{code}",
                )
            if is_update:
                stats["updated"] += 1
            else:
                stats["new_reports"] += 1
            await asyncio.sleep(0.5)  # Space requests
        except Exception as exc:
            logger.warning(
                "Failed to fetch fight details for report %s: %s", code, exc
            )
            stats["errors"] += 1

    return stats


def _parse_zone_rankings(zone_rankings: dict, zone_name_map: dict[int, str] | None = None) -> list[dict]:
    """Extract per-boss parse records from a WCL zoneRankings response.

    Returns list of dicts with encounter_id, encounter_name, zone_id, zone_name,
    difficulty, spec, percentile, amount, report_code, fight_id.
    """
    parses = []
    if not isinstance(zone_rankings, dict):
        return parses

    zone_id = zone_rankings.get("zone", 0)
    zone_name = (zone_name_map or {}).get(zone_id, "") if zone_id else ""
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

    # Fetch zone name lookup once so parse records have readable names
    try:
        zone_name_map = await wcl_client.get_world_zones()
    except Exception:
        zone_name_map = {}

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

            parses = _parse_zone_rankings(zone_rankings_raw, zone_name_map)
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


def _parse_report_rankings(rankings_blob) -> list[dict]:
    """Extract per-character parse entries from a WCL report rankings response.

    rankings_blob is the value of reportData.report.rankings — a JSON scalar.
    WCL returns this as a raw JSON string (not a parsed dict), so we json.loads()
    it first if needed.

    WCL actual shape (flat list):
      {"data": [{"name": "...", "spec": "...", "rankPercent": 94.0, "amount": 12345}, ...]}

    Older/spec assumed shape (roles-nested):
      {"data": {"roles": {"tanks":   {"characters": [...]},
                          "healers": {"characters": [...]},
                          "dps":     {"characters": [...]}}}}

    Returns list of dicts with: name, spec, percentile, amount.
    """
    entries = []
    if not rankings_blob:
        return entries
    if isinstance(rankings_blob, str):
        try:
            rankings_blob = json.loads(rankings_blob)
        except (json.JSONDecodeError, ValueError):
            return entries
    if not isinstance(rankings_blob, dict):
        return entries

    data = rankings_blob.get("data") or {}

    if isinstance(data, list):
        char_list = data
    else:
        roles = data.get("roles") or {}
        char_list = []
        for role_data in roles.values():
            if isinstance(role_data, dict):
                char_list.extend(role_data.get("characters") or [])

    for char_entry in char_list:
        if not isinstance(char_entry, dict):
            continue
        name = char_entry.get("name") or ""
        spec = char_entry.get("spec") or None
        percentile = char_entry.get("rankPercent")
        amount = char_entry.get("amount")
        if name and percentile is not None:
            entries.append({
                "name": name,
                "spec": spec,
                "percentile": float(percentile),
                "amount": float(amount) if amount is not None else None,
            })
    return entries


async def sync_report_parses(
    pool: asyncpg.Pool,
    wcl_client: WarcraftLogsClient,
    report_codes: list[str],
    zone_name_map: dict[int, str],
) -> dict:
    """Fetch per-player parse rankings from WCL report logs and store granularly.

    For each report:
      - Reads encounter_ids, encounter_map, zone_id, zone_name, raid_date from raid_reports
      - Calls rankings(encounterID) for each unique encounter
      - Matches character names to guild wow_characters (in_guild=TRUE)
      - Upserts into character_report_parses

    Returns stats: reports_processed, encounters_queried, parse_records, errors.
    """
    stats = {
        "reports_processed": 0,
        "encounters_queried": 0,
        "parse_records": 0,
        "errors": 0,
    }

    # Build character name→id lookup (case-insensitive)
    async with pool.acquire() as conn:
        char_rows = await conn.fetch(
            """SELECT id, LOWER(character_name) AS name_lower
               FROM guild_identity.wow_characters
               WHERE in_guild = TRUE AND removed_at IS NULL"""
        )
    char_lookup: dict[str, int] = {
        row["name_lower"]: row["id"] for row in char_rows
    }

    for report_code in report_codes:
        async with pool.acquire() as conn:
            report_row = await conn.fetchrow(
                """SELECT encounter_ids, encounter_map, zone_id, zone_name, raid_date
                   FROM guild_identity.raid_reports
                   WHERE report_code = $1""",
                report_code,
            )
        if not report_row:
            logger.warning("sync_report_parses: report %s not found in DB", report_code)
            stats["errors"] += 1
            continue

        encounter_ids = report_row["encounter_ids"] or []
        if not encounter_ids:
            logger.debug("sync_report_parses: report %s has no encounter_ids — skipping", report_code)
            continue

        # encounter_map stored as JSONB with string keys {"123": "Boss Name"}
        # asyncpg decodes JSONB automatically, but handle string fallback defensively
        _raw_enc_map = report_row["encounter_map"] or {}
        if isinstance(_raw_enc_map, str):
            try:
                _raw_enc_map = json.loads(_raw_enc_map)
            except (json.JSONDecodeError, ValueError):
                _raw_enc_map = {}
        encounter_map_raw = _raw_enc_map
        zone_id = report_row["zone_id"] or 0
        zone_name = report_row["zone_name"] or zone_name_map.get(zone_id, "")
        raid_date = report_row["raid_date"]

        for encounter_id in encounter_ids:
            encounter_name = (
                encounter_map_raw.get(str(encounter_id))
                or encounter_map_raw.get(encounter_id)
                or f"Encounter {encounter_id}"
            )

            try:
                result = await wcl_client.get_report_rankings(report_code, encounter_id)
                rankings_blob = (
                    result.get("reportData", {})
                          .get("report", {})
                          .get("rankings")
                )
                if not rankings_blob:
                    stats["encounters_queried"] += 1
                    await asyncio.sleep(0.3)
                    continue

                entries = _parse_report_rankings(rankings_blob)
                if entries:
                    logger.info(
                        "sync_report_parses: report=%s enc=%d got %d entries, sample names: %s",
                        report_code, encounter_id, len(entries),
                        [e["name"] for e in entries[:5]],
                    )
                stats["encounters_queried"] += 1

                async with pool.acquire() as conn:
                    for entry in entries:
                        char_id = char_lookup.get(entry["name"].lower())
                        if char_id is None:
                            continue  # Not a tracked guild character
                        await conn.execute(
                            """INSERT INTO guild_identity.character_report_parses
                                   (character_id, report_code, encounter_id,
                                    encounter_name, zone_id, zone_name,
                                    difficulty, spec, percentile, amount,
                                    raid_date, last_synced)
                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                                       $11, NOW())
                               ON CONFLICT (character_id, report_code, encounter_id)
                               DO UPDATE SET
                                   percentile     = GREATEST(EXCLUDED.percentile,
                                                             character_report_parses.percentile),
                                   spec           = EXCLUDED.spec,
                                   amount         = EXCLUDED.amount,
                                   last_synced    = NOW()""",
                            char_id,
                            report_code,
                            encounter_id,
                            encounter_name,
                            zone_id,
                            zone_name,
                            3,  # WCL difficulty: 3=normal (guild raids normal)
                            entry["spec"],
                            entry["percentile"],
                            entry["amount"],
                            raid_date,
                        )
                        stats["parse_records"] += 1

            except Exception as exc:
                logger.warning(
                    "sync_report_parses: error on report %s encounter %s: %s",
                    report_code, encounter_id, exc,
                )
                stats["errors"] += 1

            await asyncio.sleep(0.3)  # Space requests between encounter calls

        stats["reports_processed"] += 1

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
