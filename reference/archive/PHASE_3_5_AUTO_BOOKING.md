# Phase 3.5 — Auto-Booking Scheduler

## Goal

15 minutes after a raid event starts, automatically create next week's Raid-Helper event so the
signup sheet is live before players log off for the night. Posts a Discord announcement.

---

## Logic

A background task polls every 5 minutes. For each check cycle:

### Step 1 — Find Events in Booking Window

```sql
SELECT re.*, rev.*
FROM patt.raid_events re
JOIN patt.recurring_events rev ON re.recurring_event_id = rev.id
WHERE re.start_time_utc <= NOW() - INTERVAL '10 minutes'
  AND re.start_time_utc >= NOW() - INTERVAL '20 minutes'
  AND re.recurring_event_id IS NOT NULL
  AND re.auto_booked = FALSE
```

This targets events that started 10–20 minutes ago and haven't been booked yet.
The 10-minute lower bound gives raiders time to get in and confirm the event is happening.
The 20-minute upper bound prevents re-processing old events after a scheduler restart.

### Step 2 — Check for Duplicate

For each found event, check if next week's event already exists:

```sql
SELECT id FROM patt.raid_events
WHERE recurring_event_id = $1
  AND event_date = $2  -- source_event.event_date + 7 days
```

If a row exists → skip (already booked, possibly manually). Log and continue.

### Step 3 — Create Next Week's Event

1. Call `raid_helper_service.create_event()` with:
   - `start_time_utc` = source event's `start_time_utc + 7 days`
   - `title`, `duration_minutes`, `channel_id`, `template_id` from `recurring_event`
   - Default signups (same auto-invite logic as Phase 3.4)

2. On success:
   - INSERT `patt.raid_events` with `auto_booked = TRUE`, `recurring_event_id` set
   - Batch-INSERT `patt.raid_attendance` rows (`source = 'auto'`)

3. Mark source event `auto_booked = TRUE` (prevents re-triggering even if scheduler restarts)

### Step 4 — Discord Announcement

Post to `common.discord_config.raid_channel_id`:

```
📅 Next week's raid has been posted! Sign up here:
{event_url}
```

Uses the existing `sv_common.discord.channels.post_message()` helper.

---

## New Module: `src/patt/services/raid_booking_service.py`

```python
"""
Auto-booking service: fires 10–20 minutes after a recurring raid event starts,
creates next week's Raid-Helper event, and posts a Discord announcement.
"""
import asyncio
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
            await book_next_occurrence(conn, config, dict(event))


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
            f"Auto-booking skipped: event already exists for "
            f"recurring_event_id={source_event['recurring_event_id']} on {next_date}"
        )
        # Still mark source as booked to prevent re-checking
        await _mark_booked(conn, source_event["id"])
        return None

    # Build player signups
    signups = await _build_signups(conn, source_event["recurring_event_id"])

    # Call Raid-Helper API
    try:
        result = await create_event(
            config=dict(config),
            title=source_event["label"],
            event_type=source_event["event_type"],
            start_time_utc=next_start_utc,
            duration_minutes=source_event["default_duration_minutes"],
            channel_id=source_event["discord_channel_id"] or config["raid_channel_id"],
            description=f"Auto-scheduled raid. Sign up below!",
            template_id=source_event["raid_helper_template_id"] or "wowretail2",
            signups=signups,
        )
    except RaidHelperError as e:
        logger.error(f"Auto-booking failed (Raid-Helper API error): {e}")
        return None

    # Insert patt.raid_events row
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
            $1, $2, $3, $3 + ($4 * interval '1 minute'),
            $5, $6, $7, TRUE, $8
        )
        RETURNING id
        """,
        source_event["label"],
        next_date,
        next_start_utc,
        source_event["default_duration_minutes"],
        result["event_id"],
        source_event["discord_channel_id"],
        source_event["recurring_event_id"],
        result["payload"],  # stored as JSONB for debugging
    )

    # Batch-insert attendance rows
    await _insert_attendance(conn, new_event_id, signups)

    # Mark source event as booked
    await _mark_booked(conn, source_event["id"])

    # Post Discord announcement
    await _post_announcement(config, result["event_url"])

    logger.info(
        f"Auto-booked next occurrence: raid_event_id={new_event_id}, "
        f"rh_event_id={result['event_id']}, date={next_date}"
    )
    return result["event_id"]


async def _build_signups(conn: asyncpg.Connection, recurring_event_id: int) -> list[dict]:
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
        (event_id, s["player_id"], s["status"] in ("accepted",), "auto")
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
    # Use the existing bot channel posting helper
    from sv_common.discord.channels import post_to_channel
    message = f"📅 Next week's raid has been posted! Sign up here:\n{event_url}"
    try:
        await post_to_channel(channel_id, message)
    except Exception as e:
        logger.error(f"Auto-booking: failed to post Discord announcement: {e}")
```

---

## Background Loop (`src/patt/app.py`)

Add to lifespan startup (after bot and DB pool are initialized):

```python
async def _auto_book_loop(pool: asyncpg.Pool) -> None:
    """Background loop: checks every 5 minutes for events to auto-book."""
    from patt.services.raid_booking_service import check_and_auto_book, POLL_INTERVAL_SECONDS
    logger.info("Auto-booking scheduler started")
    while True:
        try:
            await check_and_auto_book(pool)
        except Exception as e:
            logger.error(f"Auto-booking loop error: {e}", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

# In lifespan startup, after pool is created:
asyncio.create_task(_auto_book_loop(pool))
```

**Skip condition:** If Raid-Helper is not configured, `check_and_auto_book` returns early without
error. The loop continues polling — config can be added at runtime without restart.

---

## Edge Cases

| Case | Handling |
|------|---------|
| Raid-Helper API down | Log error, skip booking, try again next poll (event stays `auto_booked=FALSE` until window passes) |
| Event already manually booked | `book_next_occurrence` finds existing row, marks source `auto_booked=TRUE`, skips |
| No Raid-Helper config | `check_and_auto_book` returns early, no error |
| Scheduler restarts during booking window | Event re-found on next poll; duplicate check prevents double-booking |
| Booking window passes without success | After 20 min, event falls out of query window — manual creation needed |
| No current season | `season_id` subquery returns NULL — acceptable (nullable FK) |
| Player has no Discord linked | `discord_id` is NULL — signup created in DB but Raid-Helper signup skipped for that player |

---

## Manual Test Procedure

To test without waiting for a real raid event:

```sql
-- Create a test event with start_time_utc = 12 minutes ago
INSERT INTO patt.raid_events (title, event_date, start_time_utc, end_time_utc, recurring_event_id, auto_booked)
VALUES (
    'TEST Auto-Book',
    CURRENT_DATE,
    NOW() - INTERVAL '12 minutes',
    NOW() + INTERVAL '108 minutes',
    1,  -- a valid recurring_event_id
    FALSE
);
```

Then either:
- Wait for next scheduler poll (up to 5 minutes), OR
- Call `check_and_auto_book(pool)` directly from a test script

Verify:
- New `patt.raid_events` row created with `event_date = test_date + 7`
- New row has `auto_booked = TRUE`
- Source row has `auto_booked = TRUE`
- `patt.raid_attendance` rows created for new event
- Discord announcement posted (if `raid_channel_id` configured)

---

## Files Created/Modified

| File | Action |
|------|--------|
| `src/patt/services/raid_booking_service.py` | NEW |
| `src/patt/app.py` | ADD `_auto_book_loop` task in lifespan |

---

## Verification Checklist

- [ ] Auto-book loop starts on app startup (check logs: "Auto-booking scheduler started")
- [ ] Test event created in booking window → next week's event created in Raid-Helper
- [ ] `patt.raid_events` row inserted with `auto_booked=TRUE`
- [ ] Source event marked `auto_booked=TRUE` after booking
- [ ] `patt.raid_attendance` rows created for new event
- [ ] Discord announcement posted to `raid_channel_id`
- [ ] Running test twice with same source event → no duplicate booking
- [ ] App starts cleanly if Raid-Helper not configured (no crash)
- [ ] Loop survives transient errors and continues polling
