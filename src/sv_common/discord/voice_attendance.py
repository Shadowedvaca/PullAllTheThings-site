"""
Voice channel attendance tracking for scheduled raid events.

Listens to Discord on_voice_state_update events. During an active raid window,
logs join/leave actions to patt.voice_attendance_log.

Active window detection: loads today's raid events at startup and after midnight.
Only logs events for the configured voice channel (discord_config.raid_voice_channel_id
or per-event override stored on patt.raid_events.voice_channel_id).
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)


class VoiceAttendanceCog(commands.Cog):
    """Tracks voice channel presence for scheduled raid events."""

    def __init__(self, bot: commands.Bot, db_pool):
        self.bot = bot
        self.db_pool = db_pool
        # Cache of today's raid events: list of dicts with keys:
        #   id, start_time_utc, end_time_utc, voice_channel_id, voice_tracking_enabled
        self._today_events: list[dict] = []
        self._default_voice_channel_id: str | None = None
        self._cache_date: datetime | None = None

    async def cog_load(self):
        """Called when the cog is added to the bot."""
        self._refresh_cache.start()

    async def cog_unload(self):
        self._refresh_cache.cancel()

    @tasks.loop(minutes=30)
    async def _refresh_cache(self):
        """Refresh the raid event cache every 30 minutes."""
        await self._load_today_events()

    @_refresh_cache.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()
        await self._load_today_events()

    async def _load_today_events(self):
        """Load today's raid events and default voice channel from DB."""
        try:
            now = datetime.now(timezone.utc)
            async with self.db_pool.acquire() as conn:
                # Load default voice channel from discord_config
                cfg_row = await conn.fetchrow(
                    "SELECT raid_voice_channel_id FROM common.discord_config LIMIT 1"
                )
                if cfg_row:
                    self._default_voice_channel_id = cfg_row["raid_voice_channel_id"]

                # Load events that start within the next 24 hours or are currently active
                window_start = now - timedelta(hours=12)
                window_end = now + timedelta(hours=12)
                rows = await conn.fetch(
                    """
                    SELECT id, start_time_utc, end_time_utc,
                           voice_channel_id, voice_tracking_enabled
                    FROM patt.raid_events
                    WHERE voice_tracking_enabled = TRUE
                      AND start_time_utc >= $1
                      AND start_time_utc <= $2
                    ORDER BY start_time_utc
                    """,
                    window_start,
                    window_end,
                )
                self._today_events = [dict(r) for r in rows]
                self._cache_date = now.date()
                logger.debug(
                    "Voice attendance cache refreshed: %d events", len(self._today_events)
                )
        except Exception as exc:
            logger.warning("Failed to load raid events for voice attendance: %s", exc)

    def _find_active_event(self, now: datetime) -> dict | None:
        """Return the raid event active at the given time, or None."""
        for event in self._today_events:
            if event["start_time_utc"] <= now <= event["end_time_utc"]:
                return event
        return None

    def _effective_channel(self, event: dict) -> str | None:
        """Return the voice channel ID to watch for this event."""
        return event.get("voice_channel_id") or self._default_voice_channel_id

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Log voice join/leave events during active raid windows."""
        if member.bot:
            return

        now = datetime.now(timezone.utc)

        # Determine join or leave
        joined_channel = after.channel
        left_channel = before.channel

        # Collect actions: (channel_id, action)
        actions = []
        if left_channel and (joined_channel is None or joined_channel.id != left_channel.id):
            actions.append((str(left_channel.id), "leave"))
        if joined_channel and (left_channel is None or joined_channel.id != left_channel.id):
            actions.append((str(joined_channel.id), "join"))

        if not actions:
            return

        # Refresh cache if stale (crossed midnight)
        if self._cache_date != now.date():
            asyncio.create_task(self._load_today_events())

        active_event = self._find_active_event(now)
        if active_event is None:
            return

        effective_channel = self._effective_channel(active_event)
        if not effective_channel:
            return

        discord_user_id = str(member.id)
        event_id = active_event["id"]

        for channel_id, action in actions:
            if channel_id != effective_channel:
                continue
            try:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO patt.voice_attendance_log
                            (event_id, discord_user_id, channel_id, action, occurred_at)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        event_id,
                        discord_user_id,
                        channel_id,
                        action,
                        now,
                    )
                logger.debug(
                    "Voice attendance: %s %s channel %s event %d",
                    member.display_name,
                    action,
                    channel_id,
                    event_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to log voice %s for %s: %s", action, member.display_name, exc
                )
