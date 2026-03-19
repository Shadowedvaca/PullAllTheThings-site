"""
Attendance processor: Two-pass reconciliation of voice + WCL data.

Pass 1 — WCL Reconciliation:
  Reads raid_reports.attendees for the event date, resolves character names to
  player_ids, and upserts raid_attendance with source='wcl'.

Pass 2 — Discord Voice Processing:
  Reads voice_attendance_log, reconstructs presence spans, computes timing flags,
  and upserts raid_attendance with voice timing data.

Pass 3 — Habitual Behavior Check:
  Scans recent events for repeated joined_late / left_early patterns. Posts an
  officer alert to the audit channel if the threshold is met.

The processor runs 30 minutes after each raid's end_time_utc via the scheduler,
or can be triggered manually via the admin API.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import discord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pass 1 — WCL Reconciliation
# ---------------------------------------------------------------------------


async def process_wcl_pass(pool: asyncpg.Pool, event_id: int) -> dict:
    """
    Read raid_reports for the event date, resolve attendees to player_ids,
    upsert raid_attendance rows with source='wcl'.

    Returns stats dict: {"matched": N, "unmatched": M, "skipped": 0 if no report}
    """
    async with pool.acquire() as conn:
        event = await conn.fetchrow(
            "SELECT id, event_date, log_url, start_time_utc FROM patt.raid_events WHERE id = $1",
            event_id,
        )
        if not event:
            logger.warning("process_wcl_pass: event %d not found", event_id)
            return {"matched": 0, "unmatched": 0, "skipped": True}

        event_date = event["event_date"]
        log_url = event["log_url"] or ""

        # Find matching WCL reports: prefer by report code from log_url, else by date
        report_code = _extract_wcl_code(log_url)
        if report_code:
            reports = await conn.fetch(
                """
                SELECT id, attendees FROM guild_identity.raid_reports
                WHERE report_code = $1
                """,
                report_code,
            )
        else:
            reports = await conn.fetch(
                """
                SELECT id, attendees FROM guild_identity.raid_reports
                WHERE raid_date::date = $1::date
                """,
                event_date,
            )

        if not reports:
            logger.debug("process_wcl_pass: no WCL reports for event %d (date=%s)", event_id, event_date)
            return {"matched": 0, "unmatched": 0, "skipped": True}

        # Collect all attendee names across all reports
        attendee_names: set[str] = set()
        for report in reports:
            attendees = report["attendees"] or []
            for a in attendees:
                name = (a.get("name") or "").strip()
                if name:
                    attendee_names.add(name.lower())

        if not attendee_names:
            return {"matched": 0, "unmatched": 0, "skipped": False}

        # Resolve character names → player_ids
        name_list = list(attendee_names)
        char_rows = await conn.fetch(
            """
            SELECT wc.character_name, pc.player_id
            FROM guild_identity.wow_characters wc
            JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
            WHERE LOWER(wc.character_name) = ANY($1::text[])
              AND wc.removed_at IS NULL AND wc.in_guild = TRUE
            """,
            name_list,
        )
        name_to_player: dict[str, int] = {}
        for row in char_rows:
            name_to_player[row["character_name"].lower()] = row["player_id"]

        matched = 0
        unmatched = 0
        for name in attendee_names:
            player_id = name_to_player.get(name)
            if player_id is None:
                logger.debug("WCL attendee '%s' not found in wow_characters (event %d)", name, event_id)
                unmatched += 1
                continue

            await conn.execute(
                """
                INSERT INTO patt.raid_attendance (event_id, player_id, attended, source)
                VALUES ($1, $2, TRUE, 'wcl')
                ON CONFLICT (event_id, player_id) DO UPDATE
                  SET attended = TRUE,
                      source = CASE
                        WHEN raid_attendance.source = 'voice' THEN 'wcl+voice'
                        WHEN raid_attendance.source = 'raid_helper' THEN 'raid_helper+wcl'
                        WHEN raid_attendance.source LIKE '%wcl%' THEN raid_attendance.source
                        ELSE 'wcl'
                      END
                """,
                event_id,
                player_id,
            )
            matched += 1

        logger.info(
            "WCL pass event %d: %d matched, %d unmatched",
            event_id, matched, unmatched,
        )
        return {"matched": matched, "unmatched": unmatched, "skipped": False}


def _extract_wcl_code(log_url: str) -> str | None:
    """Extract WCL report code from a URL like https://www.warcraftlogs.com/reports/ABCDEF."""
    if not log_url:
        return None
    parts = log_url.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if part == "reports" and i + 1 < len(parts):
            code = parts[i + 1].split("?")[0].split("#")[0]
            if code:
                return code
    return None


# ---------------------------------------------------------------------------
# Pass 2 — Discord Voice Processing
# ---------------------------------------------------------------------------


async def process_voice_pass(pool: asyncpg.Pool, event_id: int) -> dict:
    """
    Read voice_attendance_log, reconstruct presence spans, compute timing flags,
    and upsert raid_attendance with voice timing data.

    Returns stats dict: {"processed": N, "unlinked": M, "attended_voice": K}
    """
    async with pool.acquire() as conn:
        event = await conn.fetchrow(
            """
            SELECT id, start_time_utc, end_time_utc
            FROM patt.raid_events WHERE id = $1
            """,
            event_id,
        )
        if not event:
            return {"processed": 0, "unlinked": 0, "attended_voice": 0}

        config = await conn.fetchrow(
            """
            SELECT attendance_min_pct, attendance_late_grace_min, attendance_early_leave_min
            FROM common.discord_config LIMIT 1
            """
        )
        if not config:
            return {"processed": 0, "unlinked": 0, "attended_voice": 0}

        min_pct = config["attendance_min_pct"]
        late_grace = timedelta(minutes=config["attendance_late_grace_min"])
        early_grace = timedelta(minutes=config["attendance_early_leave_min"])

        start_utc: datetime = event["start_time_utc"]
        end_utc: datetime = event["end_time_utc"]
        effective_start = start_utc + late_grace
        effective_end = end_utc - early_grace

        if effective_start >= effective_end:
            # Degenerate window (very short raid) — no voice attendance possible
            return {"processed": 0, "unlinked": 0, "attended_voice": 0}

        effective_window = (effective_end - effective_start).total_seconds()

        # Load all log entries for this event, ordered by user then time
        log_rows = await conn.fetch(
            """
            SELECT discord_user_id, action, occurred_at
            FROM patt.voice_attendance_log
            WHERE event_id = $1
            ORDER BY discord_user_id, occurred_at
            """,
            event_id,
        )

        # Group by discord_user_id
        by_user: dict[str, list] = {}
        for row in log_rows:
            uid = row["discord_user_id"]
            by_user.setdefault(uid, []).append(row)

        # Build discord_id → player_id mapping
        all_discord_ids = list(by_user.keys())
        if not all_discord_ids:
            return {"processed": 0, "unlinked": 0, "attended_voice": 0}

        du_rows = await conn.fetch(
            """
            SELECT du.discord_id, p.id AS player_id
            FROM guild_identity.discord_users du
            JOIN guild_identity.players p ON p.id = du.player_id
            WHERE du.discord_id = ANY($1::text[])
            """,
            all_discord_ids,
        )
        discord_to_player: dict[str, int] = {r["discord_id"]: r["player_id"] for r in du_rows}

        processed = 0
        unlinked = 0
        attended_voice = 0

        for discord_uid, entries in by_user.items():
            player_id = discord_to_player.get(discord_uid)
            if player_id is None:
                logger.debug(
                    "Voice pass event %d: unlinked Discord user %s", event_id, discord_uid
                )
                unlinked += 1
                continue

            # Reconstruct spans
            spans = _reconstruct_spans(entries, end_utc)

            if not spans:
                continue

            # Compute raw timing flags (before grace clipping)
            first_join = min(s[0] for s in spans)
            last_leave = max(s[1] for s in spans)
            joined_late = first_join > (start_utc + late_grace)
            left_early = last_leave < (end_utc - early_grace)

            # Clip spans to effective window and sum
            total_present_secs = 0.0
            for span_start, span_end in spans:
                clipped_start = max(span_start, effective_start)
                clipped_end = min(span_end, effective_end)
                if clipped_end > clipped_start:
                    total_present_secs += (clipped_end - clipped_start).total_seconds()

            minutes_present = int(total_present_secs / 60)
            presence_pct = (total_present_secs / effective_window) * 100.0
            voice_attended = presence_pct >= min_pct

            if voice_attended:
                attended_voice += 1

            await conn.execute(
                """
                INSERT INTO patt.raid_attendance
                    (event_id, player_id, attended, source,
                     minutes_present, first_join_at, last_leave_at, joined_late, left_early)
                VALUES ($1, $2, $3, 'voice', $4, $5, $6, $7, $8)
                ON CONFLICT (event_id, player_id) DO UPDATE
                  SET attended      = GREATEST(raid_attendance.attended, $3),
                      source        = CASE
                        WHEN raid_attendance.source = 'wcl'          THEN 'wcl+voice'
                        WHEN raid_attendance.source = 'raid_helper'  THEN 'raid_helper+voice'
                        WHEN raid_attendance.source LIKE '%+voice'   THEN raid_attendance.source
                        ELSE 'voice'
                      END,
                      minutes_present = $4,
                      first_join_at   = $5,
                      last_leave_at   = $6,
                      joined_late     = $7,
                      left_early      = $8
                """,
                event_id,
                player_id,
                voice_attended,
                minutes_present,
                first_join,
                last_leave,
                joined_late,
                left_early,
            )
            processed += 1

    logger.info(
        "Voice pass event %d: %d processed (%d attended), %d unlinked",
        event_id, processed, attended_voice, unlinked,
    )
    return {"processed": processed, "unlinked": unlinked, "attended_voice": attended_voice}


def _reconstruct_spans(
    entries: list, end_utc: datetime
) -> list[tuple[datetime, datetime]]:
    """
    Pair join→leave events into (start, end) spans.
    If the last action is 'join' (still in VC at raid end), closes it at end_utc.
    """
    spans = []
    pending_join: datetime | None = None

    for entry in entries:
        action = entry["action"]
        occurred = entry["occurred_at"]

        if action == "join":
            if pending_join is None:
                pending_join = occurred
            # else: duplicate join (DC scenario) — ignore, keep original join
        elif action == "leave":
            if pending_join is not None:
                spans.append((pending_join, occurred))
                pending_join = None
            # else: leave with no open join — ignore

    if pending_join is not None:
        # Still in VC at raid end — close the span
        spans.append((pending_join, end_utc))

    return spans


# ---------------------------------------------------------------------------
# Pass 3 — Habitual Behavior Check
# ---------------------------------------------------------------------------


async def check_habitual_patterns(
    pool: asyncpg.Pool,
    event_id: int,
    audit_channel: discord.TextChannel | None,
) -> None:
    """
    After both passes complete for an event, scan the roster for habitual
    late/early patterns and post an officer alert if the threshold is met.
    """
    async with pool.acquire() as conn:
        config = await conn.fetchrow(
            """
            SELECT attendance_habitual_window, attendance_habitual_threshold
            FROM common.discord_config LIMIT 1
            """
        )
        if not config:
            return

        window = config["attendance_habitual_window"]
        threshold = config["attendance_habitual_threshold"]

        event = await conn.fetchrow(
            "SELECT event_date FROM patt.raid_events WHERE id = $1", event_id
        )
        if not event:
            return

        # For each player who attended today's event (and has voice data),
        # count joined_late / left_early in the last N events
        today_players = await conn.fetch(
            """
            SELECT DISTINCT player_id
            FROM patt.raid_attendance
            WHERE event_id = $1 AND joined_late IS NOT NULL
            """,
            event_id,
        )

        habitual_late: list[dict] = []
        habitual_early: list[dict] = []

        for row in today_players:
            player_id = row["player_id"]

            # Fetch last N events (including this one) with voice data for this player
            recent = await conn.fetch(
                """
                SELECT ra.joined_late, ra.left_early, re.event_date
                FROM patt.raid_attendance ra
                JOIN patt.raid_events re ON re.id = ra.event_id
                WHERE ra.player_id = $1
                  AND ra.joined_late IS NOT NULL
                  AND re.attendance_processed_at IS NOT NULL
                ORDER BY re.start_time_utc DESC
                LIMIT $2
                """,
                player_id,
                window,
            )

            late_count = sum(1 for r in recent if r["joined_late"])
            early_count = sum(1 for r in recent if r["left_early"])

            player_name = await conn.fetchval(
                "SELECT display_name FROM guild_identity.players WHERE id = $1", player_id
            )

            if late_count >= threshold:
                habitual_late.append({
                    "name": player_name or f"Player#{player_id}",
                    "count": late_count,
                    "window": len(recent),
                    "dates": [r["event_date"].strftime("%b %d").replace(" 0", " ") for r in recent if r["joined_late"]],
                })

            if early_count >= threshold:
                habitual_early.append({
                    "name": player_name or f"Player#{player_id}",
                    "count": early_count,
                    "window": len(recent),
                    "dates": [r["event_date"].strftime("%b %d").replace(" 0", " ") for r in recent if r["left_early"]],
                })

    if not habitual_late and not habitual_early:
        return

    if audit_channel is None:
        logger.info("Habitual patterns found but no audit channel — skipping Discord alert")
        return

    event_date_str = event["event_date"].strftime("%b %d").replace(" 0", " ")
    lines = [f"**Habitual Attendance Patterns — {event_date_str}**\n"]

    if habitual_late:
        lines.append("**Joined Late:**")
        for p in habitual_late:
            dates_str = " / ".join(p["dates"])
            lines.append(f"  \u2022 {p['name']} \u2014 late {p['count']}/{p['window']} recent raids ({dates_str})")

    if habitual_early:
        lines.append("\n**Left Early:**")
        for p in habitual_early:
            dates_str = " / ".join(p["dates"])
            lines.append(f"  \u2022 {p['name']} \u2014 early {p['count']}/{p['window']} recent raids ({dates_str})")

    description = "\n".join(lines)
    try:
        embed = discord.Embed(
            title="\u23f0 Habitual Attendance Alert",
            description=description,
            color=0xf59e0b,
        )
        await audit_channel.send(embed=embed)
    except Exception as exc:
        logger.warning("Failed to send habitual attendance alert: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def process_event(
    pool: asyncpg.Pool,
    event_id: int,
    audit_channel: discord.TextChannel | None = None,
) -> dict:
    """
    Run all three passes for a raid event and mark it as processed.

    Returns a combined stats dict.
    """
    logger.info("Processing attendance for event %d", event_id)

    wcl_stats = await process_wcl_pass(pool, event_id)
    voice_stats = await process_voice_pass(pool, event_id)
    await check_habitual_patterns(pool, event_id, audit_channel)

    # Mark the event as processed
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE patt.raid_events SET attendance_processed_at = NOW() WHERE id = $1",
            event_id,
        )

    result = {
        "event_id": event_id,
        "wcl": wcl_stats,
        "voice": voice_stats,
    }
    logger.info("Attendance processing complete for event %d: %s", event_id, result)
    return result


async def get_unprocessed_events(pool: asyncpg.Pool) -> list[dict]:
    """
    Return events that ended more than 30 minutes ago, have not been processed,
    and have voice tracking enabled.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, end_time_utc
            FROM patt.raid_events
            WHERE voice_tracking_enabled = TRUE
              AND attendance_processed_at IS NULL
              AND end_time_utc < NOW() - INTERVAL '30 minutes'
            ORDER BY end_time_utc
            """
        )
        return [dict(r) for r in rows]


async def get_attendance_status(pool: asyncpg.Pool, player_id: int, trailing: int = 8) -> dict:
    """
    Return attendance summary for a player over the last N processed events.
    Used by the Player Manager for the attendance dot badge.

    Returns: {"status": "good"|"at_risk"|"concern"|"new"|"none", "summary": "6/8 raids"}
    """
    async with pool.acquire() as conn:
        # Check if feature is enabled
        enabled = await conn.fetchval(
            "SELECT attendance_feature_enabled FROM common.discord_config LIMIT 1"
        )
        if not enabled:
            return {"status": "none", "summary": ""}

        trailing_events = await conn.fetchval(
            "SELECT attendance_trailing_events FROM common.discord_config LIMIT 1"
        ) or trailing

        min_pct = await conn.fetchval(
            "SELECT attendance_min_pct FROM common.discord_config LIMIT 1"
        ) or 75

        rows = await conn.fetch(
            """
            SELECT ra.attended, ra.noted_absence
            FROM patt.raid_attendance ra
            JOIN patt.raid_events re ON re.id = ra.event_id
            WHERE ra.player_id = $1
              AND re.attendance_processed_at IS NOT NULL
            ORDER BY re.start_time_utc DESC
            LIMIT $2
            """,
            player_id,
            trailing_events,
        )

        if len(rows) < 3:
            return {"status": "new", "summary": f"{len(rows)} events"}

        attended = sum(1 for r in rows if r["attended"] or r["noted_absence"])
        total = len(rows)
        pct = (attended / total) * 100 if total > 0 else 0

        summary = f"{attended}/{total} raids"
        if pct >= min_pct:
            status = "good"
        elif pct >= 50:
            status = "at_risk"
        else:
            status = "concern"

        return {"status": status, "summary": summary}
