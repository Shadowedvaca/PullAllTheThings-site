"""
Auto-booking service: fires 10–20 minutes after a recurring raid event starts,
creates next week's Raid-Helper event, and posts a Discord announcement.
"""
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from patt.services.raid_helper_service import create_event, RaidHelperError

logger = logging.getLogger(__name__)

BOOKING_WINDOW_MIN = 10   # minutes after start_time_utc
BOOKING_WINDOW_MAX = 20   # minutes after start_time_utc
POLL_INTERVAL_SECONDS = 300  # 5 minutes


async def check_and_auto_book(pool: asyncpg.Pool) -> None:
    """
    Find raid events in the booking window and create next week's event if needed.
    Called every POLL_INTERVAL_SECONDS by the background loop.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=BOOKING_WINDOW_MAX)
    window_end = now - timedelta(minutes=BOOKING_WINDOW_MIN)

    async with pool.acquire() as conn:
        # Load Raid-Helper config
        config = await conn.fetchrow(
            "SELECT * FROM common.discord_config LIMIT 1"
        )
        if not config or not config["raid_helper_api_key"]:
            logger.debug("Auto-booking skipped: Raid-Helper not configured")
            return

        # Find events in booking window
        events = await conn.fetch(
            """
            SELECT re.*, rev.label, rev.default_start_time, rev.default_duration_minutes,
                   rev.discord_channel_id, rev.raid_helper_template_id, rev.event_type
            FROM patt.raid_events re
            JOIN patt.recurring_events rev ON re.recurring_event_id = rev.id
            WHERE re.start_time_utc BETWEEN $1 AND $2
              AND re.recurring_event_id IS NOT NULL
              AND re.auto_booked = FALSE
            """,
            window_start,
            window_end,
        )

        for event in events:
            await book_next_occurrence(conn, dict(config), dict(event))


async def book_next_occurrence(
    conn: asyncpg.Connection,
    config: dict,
    source_event: dict,
) -> str | None:
    """
    Create next week's Raid-Helper event based on the source recurring event.
    Returns the Raid-Helper event ID on success, None on failure.
    """
    next_date = source_event["event_date"] + timedelta(days=7)
    next_start_utc = source_event["start_time_utc"] + timedelta(days=7)

    # Check for duplicate
    existing = await conn.fetchrow(
        """
        SELECT id FROM patt.raid_events
        WHERE recurring_event_id = $1 AND event_date = $2
        """,
        source_event["recurring_event_id"],
        next_date,
    )
    if existing:
        logger.info(
            "Auto-booking skipped: event already exists for "
            "recurring_event_id=%s on %s",
            source_event["recurring_event_id"],
            next_date,
        )
        # Still mark source as booked to prevent re-checking
        await _mark_booked(conn, source_event["id"])
        return None

    # Build player signups
    signups = await _build_signups(conn)

    # Call Raid-Helper API
    try:
        result = await create_event(
            config=config,
            title=source_event["label"],
            event_type=source_event["event_type"],
            start_time_utc=next_start_utc,
            duration_minutes=source_event["default_duration_minutes"],
            channel_id=source_event["discord_channel_id"] or config.get("raid_channel_id") or "",
            description="Auto-scheduled raid. Sign up below!",
            template_id=source_event["raid_helper_template_id"] or "wowretail2",
            signups=signups,
        )
    except RaidHelperError as e:
        logger.error("Auto-booking failed (Raid-Helper API error): %s", e)
        return None

    # Insert patt.raid_events row
    next_end_utc = next_start_utc + timedelta(minutes=source_event["default_duration_minutes"])
    new_event_id = await conn.fetchval(
        """
        INSERT INTO patt.raid_events
            (season_id, title, event_date, start_time_utc, end_time_utc,
             raid_helper_event_id, discord_channel_id, recurring_event_id,
             auto_booked, raid_helper_payload)
        VALUES (
            (SELECT id FROM patt.raid_seasons
             WHERE start_date <= CURRENT_DATE AND is_active = TRUE
             ORDER BY start_date DESC LIMIT 1),
            $1, $2, $3, $4,
            $5, $6, $7, TRUE, $8
        )
        RETURNING id
        """,
        source_event["label"],
        next_date,
        next_start_utc,
        next_end_utc,
        result["event_id"],
        source_event["discord_channel_id"],
        source_event["recurring_event_id"],
        result["payload"],
    )

    # Batch-insert attendance rows
    await _insert_attendance(conn, new_event_id, signups)

    # Mark source event as booked
    await _mark_booked(conn, source_event["id"])

    # Post Discord announcement
    await _post_announcement(config, result["event_url"])

    logger.info(
        "Auto-booked next occurrence: raid_event_id=%s, rh_event_id=%s, date=%s",
        new_event_id,
        result["event_id"],
        next_date,
    )
    return result["event_id"]


async def _build_signups(conn: asyncpg.Connection) -> list[dict]:
    """Build signup list using auto-invite rules."""
    players = await conn.fetch(
        """
        SELECT p.id, p.auto_invite_events,
               gr.level as rank_level,
               du.discord_id,
               s.name as spec_name, c.name as class_name
        FROM guild_identity.players p
        JOIN common.guild_ranks gr ON p.guild_rank_id = gr.id
        LEFT JOIN guild_identity.discord_users du ON p.discord_user_id = du.id
        LEFT JOIN guild_identity.wow_characters wc ON p.main_character_id = wc.id
        LEFT JOIN guild_identity.specializations s ON p.main_spec_id = s.id
        LEFT JOIN guild_identity.classes c ON s.class_id = c.id
        WHERE p.is_active = TRUE AND p.main_character_id IS NOT NULL
        """
    )

    signups = []
    for p in players:
        if p["rank_level"] >= 2 and p["auto_invite_events"]:
            status = "accepted"
        elif p["rank_level"] >= 2:
            status = "tentative"
        else:
            status = "bench"

        signups.append({
            "player_id": p["id"],
            "discord_id": p["discord_id"],
            "status": status,
            "class_name": p["class_name"],
            "spec_name": p["spec_name"],
        })

    return signups


async def _insert_attendance(
    conn: asyncpg.Connection, event_id: int, signups: list[dict]
) -> None:
    """Batch-insert raid_attendance rows for all non-skip players."""
    rows = [
        (event_id, s["player_id"], s["status"] == "accepted", "auto")
        for s in signups
        if s["status"] != "skip"
    ]
    await conn.executemany(
        """
        INSERT INTO patt.raid_attendance (event_id, player_id, signed_up, source)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (event_id, player_id) DO NOTHING
        """,
        rows,
    )


async def _mark_booked(conn: asyncpg.Connection, event_id: int) -> None:
    await conn.execute(
        "UPDATE patt.raid_events SET auto_booked = TRUE WHERE id = $1", event_id
    )


async def _post_announcement(config: dict, event_url: str) -> None:
    """Post Discord announcement to raid channel."""
    channel_id = config.get("raid_channel_id")
    if not channel_id:
        logger.warning("Auto-booking: no raid_channel_id configured, skipping announcement")
        return
    from sv_common.discord.bot import get_bot
    from sv_common.discord.channels import post_text_to_channel
    bot = get_bot()
    if bot is None:
        logger.warning("Auto-booking: bot not available, skipping announcement")
        return
    message = f"📅 Next week's raid has been posted! Sign up here:\n{event_url}"
    try:
        await post_text_to_channel(bot, channel_id, message)
    except Exception as e:
        logger.error("Auto-booking: failed to post Discord announcement: %s", e)
