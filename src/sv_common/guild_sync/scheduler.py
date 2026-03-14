"""
Scheduler for periodic guild sync operations.

Uses APScheduler to run:
- Blizzard API sync: every 6 hours (4x/day)
- Discord member sync: every 15 minutes
- Integrity check + auto-mitigations: after each sync
- Report: after integrity check (only if new issues)

run_matching() is available as an admin-triggered action only
(via POST /api/identity/run-matching). It is NOT called automatically.

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

from sv_common.config_cache import get_site_config
from .blizzard_client import BlizzardClient, get_rank_name_map
from .db_sync import sync_blizzard_roster, sync_addon_data
from .discord_sync import sync_discord_members, reconcile_player_ranks, prune_roleless_members, purge_fully_departed_players
from .drift_scanner import run_drift_scan
from .integrity_checker import run_integrity_check
from .progression_sync import (
    load_characters_for_progression_sync,
    sync_raid_progress,
    sync_mythic_plus,
    sync_achievements,
    sync_raiderio_profiles,
    create_weekly_snapshot,
    update_last_progression_sync,
)
from .raiderio_client import RaiderIOClient
from .reporter import send_new_issues_report, send_sync_summary, send_error
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

        _cfg = get_site_config()
        realm_slug = (
            _cfg.get("home_realm_slug")
            or os.environ.get("GUILD_REALM_SLUG", "senjin")
        )
        guild_slug = (
            _cfg.get("guild_name_slug")
            or os.environ.get("GUILD_NAME_SLUG", "pull-all-the-things")
        )
        self.blizzard_client = BlizzardClient(
            client_id=os.environ["BLIZZARD_CLIENT_ID"],
            client_secret=os.environ["BLIZZARD_CLIENT_SECRET"],
            realm_slug=realm_slug,
            guild_slug=guild_slug,
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

        # Crafting sync: runs daily at 3 AM, checks cadence internally
        self.scheduler.add_job(
            self.run_crafting_sync,
            CronTrigger(hour=3, minute=0),
            id="crafting_sync",
            name="Crafting Professions Sync",
            misfire_grace_time=3600,
        )

        # Battle.net character refresh: daily at 3:15 AM (after nightly Blizzard sync)
        self.scheduler.add_job(
            self.run_bnet_character_refresh,
            CronTrigger(hour=3, minute=15),
            id="bnet_character_refresh",
            name="Battle.net Character List Refresh",
            misfire_grace_time=3600,
        )

        # Roleless member prune: weekly on Sunday at 4 AM
        self.scheduler.add_job(
            self.run_roleless_prune,
            CronTrigger(day_of_week="sun", hour=4, minute=0),
            id="roleless_prune",
            name="Roleless Discord Member Prune",
            misfire_grace_time=3600,
        )

        # Weekly progression sweep: Sunday at 4:30 AM (after roleless prune)
        # Creates snapshots then syncs achievements for all characters (force_full)
        self.scheduler.add_job(
            self.run_weekly_progression_sweep,
            CronTrigger(day_of_week="sun", hour=4, minute=30),
            id="weekly_progression_sweep",
            name="Weekly Progression Snapshot & Achievement Sync",
            misfire_grace_time=3600,
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
        """Full Blizzard API sync pipeline.

        Pipeline:
          1. sync_blizzard_roster()     — update characters from Blizzard API
          2. run_integrity_check()      — detect orphans, role mismatches, stale chars
          3. run_drift_scan()           — detect note mismatches + link contradictions + auto-fix
          4. reconcile_player_ranks()   — fix DB ranks + Discord roles (chars-first, discord fallback)
          5. purge_fully_departed_players() — remove ghosts with no chars + no Discord
          6. [NEW] sync_raid_progress() — boss kill counts (last-login filtered)
          7. [NEW] sync_mythic_plus()   — M+ ratings (last-login filtered)
          8. send_sync_summary()        — Discord report if notable
        """
        channel = self._get_audit_channel()
        guild = channel.guild if channel else None

        try:
            async with SyncLogEntry(self.db_pool, "blizzard_api") as log:
                start = time.time()

                # Step 1: Fetch and store roster
                rank_map = await get_rank_name_map(self.db_pool)
                characters = await self.blizzard_client.sync_full_roster(rank_map=rank_map)
                sync_stats = await sync_blizzard_roster(self.db_pool, characters)
                log.stats = sync_stats

                # Step 2: Run integrity check (orphans, role mismatches, stale chars)
                integrity_stats = await run_integrity_check(self.db_pool)

                # Step 3: Drift scan + auto-mitigations
                drift_stats = await run_drift_scan(self.db_pool)

                # Step 4: Reconcile player ranks (character ranks may have changed)
                reconcile_stats = await reconcile_player_ranks(self.db_pool, guild)
                if channel and reconcile_stats.get("errors", 0) > 0:
                    await send_error(
                        channel,
                        "Rank Reconciliation Errors (Blizzard Sync)",
                        f"{reconcile_stats['errors']} Discord role update(s) failed.\n"
                        "Check that the bot has **Manage Roles** permission and its role "
                        "is above all guild rank roles in the server role list.",
                    )

                # Step 5: Purge fully-departed players (no chars + no Discord presence)
                purge_stats = await purge_fully_departed_players(self.db_pool)
                if channel and purge_stats.get("purged", 0) > 0:
                    names = ", ".join(purge_stats["names"])
                    await channel.send(embed=discord.Embed(
                        title="🗑️ Departed Players Removed",
                        description=(
                            f"**{purge_stats['purged']}** player record(s) purged "
                            f"(no characters, no Discord):\n{names}"
                        ),
                        color=0x888888,
                    ))

                # Step 6: Report new issues
                total_new = integrity_stats.get("total_new", 0) + drift_stats.get("total_new", 0)
                if channel and total_new > 0:
                    await send_new_issues_report(self.db_pool, channel)

                # Step 7: Progression sync (raid + M+) — skip unchanged characters
                try:
                    # Prefer M+ season ID from the active raid season row; fall back
                    # to site_config.current_mplus_season_id if not set on the season.
                    mplus_season_id = None
                    async with self.db_pool.acquire() as _conn:
                        _season_row = await _conn.fetchrow(
                            """SELECT blizzard_mplus_season_id FROM patt.raid_seasons
                               WHERE is_active = TRUE ORDER BY start_date DESC LIMIT 1"""
                        )
                        if _season_row and _season_row["blizzard_mplus_season_id"]:
                            mplus_season_id = _season_row["blizzard_mplus_season_id"]
                    if mplus_season_id is None:
                        cfg = get_site_config()
                        mplus_season_id = cfg.get("current_mplus_season_id")
                    progression_chars, total_chars = await load_characters_for_progression_sync(
                        self.db_pool
                    )
                    skipped_count = total_chars - len(progression_chars)
                    logger.info(
                        "Progression sync: %d of %d characters (skipped %d — no login change)",
                        len(progression_chars), total_chars, skipped_count,
                    )

                    if progression_chars:
                        raid_stats = await sync_raid_progress(
                            self.db_pool, self.blizzard_client, progression_chars
                        )
                        mplus_stats = await sync_mythic_plus(
                            self.db_pool, self.blizzard_client, progression_chars,
                            season_id=mplus_season_id,
                        )

                        # Raider.IO sync — runs after Blizzard M+, non-fatal
                        realm_slug = (
                            _cfg.get("home_realm_slug")
                            or os.environ.get("GUILD_REALM_SLUG", "senjin")
                        )
                        rio_client = RaiderIOClient(region="us")
                        await rio_client.initialize()
                        try:
                            rio_stats = await sync_raiderio_profiles(
                                self.db_pool, rio_client,
                                progression_chars, realm_slug,
                            )
                            logger.info("Raider.IO sync complete: %s", rio_stats)
                        except Exception as rio_exc:
                            logger.warning(
                                "Raider.IO sync failed (non-fatal): %s", rio_exc,
                                exc_info=True,
                            )
                        finally:
                            await rio_client.close()

                        synced_ids = [c["id"] for c in progression_chars]
                        await update_last_progression_sync(self.db_pool, synced_ids)
                        logger.info(
                            "Progression sync complete — raid: %s, M+: %s",
                            raid_stats, mplus_stats,
                        )
                except Exception as prog_exc:
                    logger.error("Progression sync failed: %s", prog_exc, exc_info=True)

                # Step 8: Retry onboarding verifications
                await self.run_onboarding_check()

                duration = time.time() - start

                # Send sync summary if notable
                if channel:
                    combined_stats = {
                        **sync_stats, **integrity_stats,
                        "drift": drift_stats, "rank_reconcile": reconcile_stats,
                    }
                    await send_sync_summary(channel, "Blizzard API", combined_stats, duration)

        except Exception as exc:
            logger.error("Blizzard sync pipeline failed: %s", exc, exc_info=True)
            if channel:
                await send_error(
                    channel,
                    "Blizzard Sync Failed",
                    f"The Blizzard API sync pipeline encountered an unexpected error:\n```{exc}```",
                )

    async def run_discord_sync(self):
        """Discord member sync pipeline.

        Pipeline:
          1. sync_discord_members()     — update discord_users table
          2. run_integrity_check()      — detect new issues (especially role_mismatch)
          3. run_drift_scan()           — detect note mismatches + stale links + auto-fix
          4. reconcile_player_ranks()   — fix DB ranks + Discord roles (chars-first, discord fallback)
        """
        audit_channel = self.discord_bot.get_channel(self.audit_channel_id)
        guild = audit_channel.guild if audit_channel else None

        if not guild:
            logger.error("Could not find Discord guild with audit channel")
            return

        try:
            async with SyncLogEntry(self.db_pool, "discord_bot") as log:
                sync_stats = await sync_discord_members(self.db_pool, guild)
                log.stats = sync_stats

                await run_integrity_check(self.db_pool)
                await run_drift_scan(self.db_pool)

                reconcile_stats = await reconcile_player_ranks(self.db_pool, guild)
                if audit_channel and reconcile_stats.get("errors", 0) > 0:
                    await send_error(
                        audit_channel,
                        "Rank Reconciliation Errors (Discord Sync)",
                        f"{reconcile_stats['errors']} Discord role update(s) failed.\n"
                        "Check that the bot has **Manage Roles** permission and its role "
                        "is above all guild rank roles in the server role list.",
                    )

                purge_stats = await purge_fully_departed_players(self.db_pool)
                if audit_channel and purge_stats.get("purged", 0) > 0:
                    names = ", ".join(purge_stats["names"])
                    await audit_channel.send(embed=discord.Embed(
                        title="🗑️ Departed Players Removed",
                        description=(
                            f"**{purge_stats['purged']}** player record(s) purged "
                            f"(no characters, no Discord):\n{names}"
                        ),
                        color=0x888888,
                    ))

        except Exception as exc:
            logger.error("Discord sync pipeline failed: %s", exc, exc_info=True)
            if audit_channel:
                await send_error(
                    audit_channel,
                    "Discord Sync Failed",
                    f"The Discord member sync pipeline encountered an unexpected error:\n```{exc}```",
                )

    async def run_addon_sync(self, addon_data: list[dict]):
        """Process addon upload and run downstream pipeline.

        Pipeline:
          1. sync_addon_data()          — write notes, log note_mismatch issues
          2. run_integrity_check()      — detect orphans and other issues
          3. run_drift_scan()           — detect note mismatches + link contradictions + auto-fix
          4. send_sync_summary()        — Discord report if notable

        Note: run_matching() is NOT called here. Use POST /api/identity/run-matching
        to trigger the matching engine as an admin action.
        """
        channel = self._get_audit_channel()

        try:
            async with SyncLogEntry(self.db_pool, "addon_upload") as log:
                start = time.time()

                # Step 1: Write notes, log note_mismatch issues for changed notes
                addon_stats = await sync_addon_data(self.db_pool, addon_data)
                log.stats = {"found": addon_stats["processed"], "updated": addon_stats["updated"]}

                # Step 2: Detect all other issue types
                integrity_stats = await run_integrity_check(self.db_pool)

                # Step 3: Drift scan + auto-mitigations
                drift_stats = await run_drift_scan(self.db_pool)

                duration = time.time() - start

                total_new = integrity_stats.get("total_new", 0) + drift_stats.get("total_new", 0)
                if channel and total_new > 0:
                    await send_new_issues_report(self.db_pool, channel)

                if channel:
                    combined_stats = {**addon_stats, **integrity_stats, "drift": drift_stats}
                    await send_sync_summary(channel, "WoW Addon Upload", combined_stats, duration)

        except Exception as exc:
            logger.error("Addon sync pipeline failed: %s", exc, exc_info=True)
            if channel:
                await send_error(
                    channel,
                    "Addon Upload Sync Failed",
                    f"The WoW addon upload pipeline encountered an unexpected error:\n```{exc}```",
                )

    async def run_onboarding_check(self):
        """Run onboarding deadline checks and resume stalled sessions."""
        from .onboarding.deadline_checker import OnboardingDeadlineChecker
        checker = OnboardingDeadlineChecker(
            self.db_pool,
            self.discord_bot,
            self.audit_channel_id,
        )
        await checker.check_pending()

    async def run_crafting_sync(self, force: bool = False):
        """Run the crafting professions sync."""
        from .crafting_sync import run_crafting_sync
        try:
            stats = await run_crafting_sync(self.db_pool, self.blizzard_client, force=force)
            logger.info("Crafting sync complete: %s", stats)
        except Exception as exc:
            logger.error("Crafting sync failed: %s", exc, exc_info=True)

    async def run_roleless_prune(self):
        """Prune Discord members with no guild role for 30+ days and no linked characters."""
        channel = self._get_audit_channel()
        guild = channel.guild if channel else None
        try:
            stats = await prune_roleless_members(self.db_pool, guild)
            logger.info("Roleless prune complete: %s", stats)
            if channel and stats.get("errors", 0) > 0:
                await send_error(
                    channel,
                    "Roleless Member Prune Errors",
                    f"{stats['errors']} member(s) could not be kicked.\n"
                    "Check that the bot has **Kick Members** permission.",
                )
            if channel and stats.get("pruned", 0) > 0:
                embed = discord.Embed(
                    title="🧹 Roleless Member Prune Complete",
                    description=(
                        f"**{stats['pruned']}** member(s) removed after 30+ days without a guild role.\n"
                        f"**{stats.get('skipped_has_chars', 0)}** skipped (had characters linked)."
                    ),
                    color=0xFFA500,
                )
                await channel.send(embed=embed)
        except Exception as exc:
            logger.error("Roleless prune failed: %s", exc, exc_info=True)
            if channel:
                await send_error(
                    channel,
                    "Roleless Member Prune Failed",
                    f"The roleless member prune encountered an unexpected error:\n```{exc}```",
                )

    async def run_weekly_progression_sweep(self):
        """Weekly full progression sweep: snapshots + achievement sync (all characters).

        Runs Sunday at 4:30 AM UTC. Uses force_full=True so every character gets
        achievement data refreshed regardless of last_login_timestamp.
        """
        try:
            snapshot_count = await create_weekly_snapshot(self.db_pool)
            logger.info("Weekly snapshot: %d characters snapshotted", snapshot_count)

            all_chars, _ = await load_characters_for_progression_sync(
                self.db_pool, force_full=True
            )
            ach_stats = await sync_achievements(
                self.db_pool, self.blizzard_client, all_chars, force_full=True
            )
            logger.info("Weekly achievement sync complete: %s", ach_stats)
        except Exception as exc:
            logger.error("Weekly progression sweep failed: %s", exc, exc_info=True)

    async def run_bnet_character_refresh(self):
        """Daily refresh of Battle.net character lists for all linked players.

        Runs at 3:15 AM UTC, after nightly Blizzard sync and crafting sync.
        Fetches character lists from the Battle.net profile API for every player
        with a linked Battle.net account, refreshing tokens as needed.
        """
        from .bnet_character_sync import get_valid_access_token, sync_bnet_characters

        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT player_id FROM guild_identity.battlenet_accounts"
                )
            player_ids = [r["player_id"] for r in rows]

            if not player_ids:
                logger.info("Battle.net character refresh: no linked accounts")
                return

            refreshed = 0
            tokens_refreshed = 0
            new_chars = 0
            errors = 0

            for player_id in player_ids:
                try:
                    access_token = await get_valid_access_token(self.db_pool, player_id)
                    if access_token is None:
                        logger.warning(
                            "Battle.net refresh: no valid token for player %s — skipping",
                            player_id,
                        )
                        continue
                    stats = await sync_bnet_characters(self.db_pool, player_id, access_token)
                    refreshed += 1
                    new_chars += stats.get("new_characters", 0)
                except Exception as exc:
                    logger.error(
                        "Battle.net character refresh failed for player %s: %s",
                        player_id, exc, exc_info=True,
                    )
                    errors += 1

            logger.info(
                "Battle.net character refresh complete: players=%d new_chars=%d errors=%d",
                refreshed, new_chars, errors,
            )
        except Exception as exc:
            logger.error("Battle.net character refresh job failed: %s", exc, exc_info=True)

    async def trigger_full_report(self):
        """Manual trigger: send a full report of ALL unresolved issues."""
        channel = self._get_audit_channel()
        if channel:
            await send_new_issues_report(self.db_pool, channel, force_full=True)
