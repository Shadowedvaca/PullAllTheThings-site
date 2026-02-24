"""
Scheduler for periodic guild sync operations.

Uses APScheduler to run:
- Blizzard API sync: every 6 hours (4x/day)
- Discord member sync: every 15 minutes
- Matching engine: after each Blizzard or addon sync
- Integrity check: after matching
- Report: after integrity check (only if new issues)

The Discord bot also handles real-time events (joins, leaves, role changes)
which don't need scheduling.
"""

import logging
import os
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

import asyncpg
import discord

from .blizzard_client import BlizzardClient
from .db_sync import sync_blizzard_roster, sync_addon_data
from .discord_sync import sync_discord_members
from .identity_engine import run_matching
from .integrity_checker import run_integrity_check
from .reporter import send_new_issues_report, send_sync_summary
from .sync_logger import SyncLogEntry

logger = logging.getLogger(__name__)


class GuildSyncScheduler:
    """Manages all scheduled guild sync tasks."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        discord_bot: discord.Client,
        audit_channel_id: int,
    ):
        self.db_pool = db_pool
        self.discord_bot = discord_bot
        self.audit_channel_id = audit_channel_id

        self.blizzard_client = BlizzardClient(
            client_id=os.environ["BLIZZARD_CLIENT_ID"],
            client_secret=os.environ["BLIZZARD_CLIENT_SECRET"],
            realm_slug=os.environ.get("PATT_GUILD_REALM_SLUG", "senjin"),
            guild_slug=os.environ.get("PATT_GUILD_NAME_SLUG", "pull-all-the-things"),
        )

        self.scheduler = AsyncIOScheduler()

    async def start(self):
        """Initialize clients and start the scheduler."""
        await self.blizzard_client.initialize()

        # Blizzard sync: 4x/day (every 6 hours, offset to avoid midnight)
        self.scheduler.add_job(
            self.run_blizzard_sync,
            CronTrigger(hour="1,7,13,19", minute=0),
            id="blizzard_sync",
            name="Blizzard API Guild Roster Sync",
            misfire_grace_time=3600,
        )

        # Discord member sync: every 15 minutes
        self.scheduler.add_job(
            self.run_discord_sync,
            IntervalTrigger(minutes=15),
            id="discord_sync",
            name="Discord Member Sync",
            misfire_grace_time=300,
        )

        # Onboarding deadline check: every 30 minutes
        self.scheduler.add_job(
            self.run_onboarding_check,
            IntervalTrigger(minutes=30),
            id="onboarding_check",
            name="Onboarding Deadline & Verification Check",
            misfire_grace_time=300,
        )

        self.scheduler.start()
        logger.info("Guild sync scheduler started")

    async def stop(self):
        """Shut down scheduler and clients."""
        self.scheduler.shutdown()
        await self.blizzard_client.close()

    def _get_audit_channel(self) -> discord.TextChannel:
        """Get the #audit-channel from the bot."""
        return self.discord_bot.get_channel(self.audit_channel_id)

    async def run_blizzard_sync(self):
        """Full Blizzard API sync pipeline."""
        channel = self._get_audit_channel()

        async with SyncLogEntry(self.db_pool, "blizzard_api") as log:
            start = time.time()

            # Step 1: Fetch and store roster
            characters = await self.blizzard_client.sync_full_roster()
            sync_stats = await sync_blizzard_roster(self.db_pool, characters)
            log.stats = sync_stats

            # Step 2: Run matching engine
            await run_matching(self.db_pool)

            # Step 3: Run integrity check
            integrity_stats = await run_integrity_check(self.db_pool)

            # Step 4: Report new issues
            if channel and integrity_stats.get("total_new", 0) > 0:
                await send_new_issues_report(self.db_pool, channel)

            # Step 5: Retry onboarding verifications (new roster data may unlock matches)
            await self.run_onboarding_check()

            duration = time.time() - start

            # Send sync summary if notable
            if channel:
                combined_stats = {**sync_stats, **integrity_stats}
                await send_sync_summary(channel, "Blizzard API", combined_stats, duration)

    async def run_discord_sync(self):
        """Discord member sync pipeline."""
        async with SyncLogEntry(self.db_pool, "discord_bot") as log:
            # Find the guild that contains our audit channel
            guild = None
            audit_channel = self.discord_bot.get_channel(self.audit_channel_id)
            if audit_channel:
                guild = audit_channel.guild

            if not guild:
                logger.error("Could not find Discord guild with audit channel")
                return

            sync_stats = await sync_discord_members(self.db_pool, guild)
            log.stats = sync_stats

    async def run_addon_sync(self, addon_data: list[dict]):
        """Process addon upload and run downstream pipeline."""
        channel = self._get_audit_channel()

        async with SyncLogEntry(self.db_pool, "addon_upload") as log:
            start = time.time()

            addon_stats = await sync_addon_data(self.db_pool, addon_data)
            log.stats = {"found": addon_stats["processed"], "updated": addon_stats["updated"]}

            # Re-run matching (addon notes might reveal new links)
            await run_matching(self.db_pool)

            # Re-run integrity check
            integrity_stats = await run_integrity_check(self.db_pool)

            duration = time.time() - start

            if channel and integrity_stats.get("total_new", 0) > 0:
                await send_new_issues_report(self.db_pool, channel)

            if channel:
                combined_stats = {**addon_stats, **integrity_stats}
                await send_sync_summary(channel, "WoW Addon Upload", combined_stats, duration)

    async def run_onboarding_check(self):
        """Dormant — Phase 2.6 onboarding not yet updated for Phase 2.7 schema."""
        pass

    async def trigger_full_report(self):
        """Manual trigger: send a full report of ALL unresolved issues."""
        channel = self._get_audit_channel()
        if channel:
            await send_new_issues_report(self.db_pool, channel, force_full=True)
