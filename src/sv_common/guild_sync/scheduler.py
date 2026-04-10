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
    sync_boss_counts_from_journal,
    create_weekly_snapshot,
    update_last_progression_sync,
)
from .equipment_sync import load_characters_for_equipment_sync, sync_equipment
from .raiderio_client import RaiderIOClient
from .reporter import send_new_issues_report, send_sync_summary, send_error
from .sync_logger import SyncLogEntry

logger = logging.getLogger(__name__)


def _build_digest_embeds(errors: list[dict]) -> list[discord.Embed]:
    """Build a list of Discord embeds for the weekly error digest.

    One header embed + one embed per issue_type group.
    """
    from datetime import datetime, timezone
    from sv_common.guild_sync.reporter import ISSUE_EMOJI, ISSUE_TYPE_NAMES, SEVERITY_COLORS
    from sv_common.config_cache import get_accent_color_int

    # Group by issue_type
    grouped: dict[str, list[dict]] = {}
    for err in errors:
        grouped.setdefault(err["issue_type"], []).append(err)

    # Determine overall worst severity
    sev_order = {"info": 0, "warning": 1, "critical": 2}
    worst = max(errors, key=lambda e: sev_order.get(e["severity"], 0))["severity"]

    header = discord.Embed(
        title="📋 Weekly Error Digest",
        description=(
            f"**{len(errors)} open issue{'s' if len(errors) != 1 else ''}** "
            f"across {len(grouped)} type{'s' if len(grouped) != 1 else ''}.\n"
            f"Manage at **Admin → Error Routing**."
        ),
        color=SEVERITY_COLORS.get(worst, get_accent_color_int()),
        timestamp=datetime.now(timezone.utc),
    )
    embeds = [header]

    for issue_type, group in grouped.items():
        emoji = ISSUE_EMOJI.get(issue_type, "🔴")
        label = ISSUE_TYPE_NAMES.get(issue_type, issue_type.replace("_", " ").title())
        worst_sev = max(group, key=lambda e: sev_order.get(e["severity"], 0))["severity"]
        color = SEVERITY_COLORS.get(worst_sev, 0x3498DB)

        lines = []
        for err in group[:15]:  # cap at 15 per type
            identifier = f" `{err['identifier']}`" if err["identifier"] else ""
            count = f" · {err['occurrence_count']}×" if err["occurrence_count"] > 1 else ""
            first = err["first_occurred_at"]
            age = f"first seen <t:{int(first.timestamp())}:R>"
            lines.append(f"•{identifier} — {err['summary'][:80]}{count} ({age})")

        if len(group) > 15:
            lines.append(f"*...and {len(group) - 15} more*")

        desc = "\n".join(lines)
        if len(desc) > 4000:
            desc = desc[:3990] + "\n*...truncated*"

        embeds.append(discord.Embed(
            title=f"{emoji} {label} ({len(group)})",
            description=desc,
            color=color,
        ))

    return embeds


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

        # Warcraft Logs sync: daily at 5 AM UTC (independent of Blizzard pipeline)
        self.scheduler.add_job(
            self.run_wcl_sync,
            CronTrigger(hour=5, minute=0),
            id="wcl_sync",
            name="Warcraft Logs Parse & Report Sync",
            misfire_grace_time=3600,
        )

        # AH price sync: every hour at :15 past the hour
        self.scheduler.add_job(
            self.run_ah_sync,
            CronTrigger(minute=15),
            id="ah_sync",
            name="Auction House Price Sync",
            misfire_grace_time=3600,
        )

        # Voice attendance post-processing: every 30 minutes
        # Picks up events that ended ≥30 min ago and haven't been processed yet
        self.scheduler.add_job(
            self.run_attendance_processing,
            IntervalTrigger(minutes=30),
            id="attendance_processing",
            name="Voice Attendance Post-Processing",
            misfire_grace_time=3600,
        )

        # Weekly error digest: Sunday 8:00 AM UTC
        self.scheduler.add_job(
            self.run_weekly_error_digest,
            CronTrigger(day_of_week="sun", hour=8, minute=0),
            id="weekly_error_digest",
            replace_existing=True,
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
          8. [NEW] sync_equipment()     — per-slot gear + quality tracks (last-login filtered)
          9. send_sync_summary()        — Discord report if notable
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
                    # M+ season ID comes from raid_seasons (single source of truth).
                    mplus_season_id = None
                    current_raid_ids: list[int] = []
                    async with self.db_pool.acquire() as _conn:
                        _season_row = await _conn.fetchrow(
                            """SELECT blizzard_mplus_season_id, current_raid_ids
                               FROM patt.raid_seasons
                               WHERE is_active = TRUE ORDER BY start_date DESC LIMIT 1"""
                        )
                        if _season_row:
                            if _season_row["blizzard_mplus_season_id"]:
                                mplus_season_id = _season_row["blizzard_mplus_season_id"]
                            if _season_row["current_raid_ids"]:
                                current_raid_ids = list(_season_row["current_raid_ids"])

                    # Update boss counts from Journal API (authoritative, not player-dependent).
                    # Runs every sync to catch progressive releases without requiring a migration.
                    if current_raid_ids:
                        try:
                            journal_stats = await sync_boss_counts_from_journal(
                                self.db_pool, self.blizzard_client, current_raid_ids
                            )
                            logger.info("Journal boss count sync: %s", journal_stats)
                        except Exception as journal_exc:
                            logger.warning(
                                "Journal boss count sync failed (non-fatal): %s", journal_exc,
                                exc_info=True,
                            )

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
                        realm_slug = self.blizzard_client.realm_slug
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

                # Step 8: Equipment sync (full per-slot gear) — non-fatal
                equipment_stats: dict = {}
                try:
                    equipment_chars, equipment_total = await load_characters_for_equipment_sync(
                        self.db_pool
                    )
                    logger.info(
                        "Equipment sync: %d of %d characters to sync",
                        len(equipment_chars), equipment_total,
                    )
                    if equipment_chars:
                        equipment_stats = await sync_equipment(
                            self.db_pool, self.blizzard_client, equipment_chars
                        )
                except Exception as equip_exc:
                    logger.error("Equipment sync failed: %s", equip_exc, exc_info=True)

                # Surface gear plan auto-setup failures to Discord if any occurred
                plan_errs = equipment_stats.get("gear_plan_setup_errors", 0)
                if plan_errs > 0:
                    from sv_common.errors import report_error
                    from guild_portal.services.error_routing import maybe_notify_discord
                    _plan_err_result = await report_error(
                        self.db_pool,
                        "gear_plan_auto_setup_failed",
                        "warning",
                        f"Gear plan auto-setup failed for {plan_errs} character(s) during equipment sync.",
                        "equipment_sync",
                        details={"failed_count": plan_errs},
                    )
                    await maybe_notify_discord(
                        self.db_pool, self.discord_bot, self.audit_channel_id,
                        "gear_plan_auto_setup_failed", "warning",
                        f"Gear plan auto-setup failed for {plan_errs} character(s) during equipment sync. Check Admin → Error Log for details.",
                        _plan_err_result["is_first_occurrence"],
                    )

                # Step 9: Retry onboarding verifications (was step 8)
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
            from sv_common.errors import report_error
            from guild_portal.services.error_routing import maybe_notify_discord
            result = await report_error(
                self.db_pool,
                "blizzard_sync_failed",
                "critical",
                str(exc),
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "blizzard_sync_failed", "critical",
                f"The Blizzard API sync pipeline encountered an unexpected error: {exc}",
                result["is_first_occurrence"],
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
            from sv_common.errors import report_error
            from guild_portal.services.error_routing import maybe_notify_discord
            result = await report_error(
                self.db_pool,
                "discord_sync_failed",
                "critical",
                str(exc),
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "discord_sync_failed", "critical",
                f"The Discord member sync pipeline encountered an unexpected error: {exc}",
                result["is_first_occurrence"],
            )

    async def run_addon_sync(self, addon_data: list[dict]):
        """Process addon upload and run downstream pipeline.

        Pipeline:
          1. sync_addon_data()          — write guild/officer notes to DB
          2. run_integrity_check()      — detect orphans and other issues
          3. run_drift_scan()           — detect duplicate links + auto-fix
          4. send_sync_summary()        — Discord report if notable

        Note: run_matching() is NOT called here. Use POST /api/identity/run-matching
        to trigger the matching engine as an admin action.
        """
        channel = self._get_audit_channel()

        try:
            async with SyncLogEntry(self.db_pool, "addon_upload") as log:
                start = time.time()

                # Step 1: Write guild/officer notes to DB
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
            from sv_common.errors import report_error
            from guild_portal.services.error_routing import maybe_notify_discord
            result = await report_error(
                self.db_pool,
                "crafting_sync_failed",
                "critical",
                str(exc),
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "crafting_sync_failed", "critical",
                str(exc),
                result["is_first_occurrence"],
            )

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
        from sv_common.errors import report_error, resolve_issue
        from guild_portal.services.error_routing import maybe_notify_discord

        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT player_id, battletag FROM guild_identity.battlenet_accounts"
                )

            if not rows:
                logger.info("Battle.net character refresh: no linked accounts")
                return

            refreshed = 0
            new_chars = 0
            errors = 0
            skipped = 0

            for row in rows:
                player_id = row["player_id"]
                battletag = row["battletag"] or f"player#{player_id}"

                try:
                    access_token = await get_valid_access_token(self.db_pool, player_id)
                    if access_token is None:
                        # Token expiry is expected (Blizzard tokens last 24h, no refresh tokens).
                        # The player will re-sync via the Refresh Characters button when they visit.
                        logger.info(
                            "Battle.net refresh: token expired for %s — skipping until player re-links",
                            battletag,
                        )
                        skipped += 1
                        continue

                    stats = await sync_bnet_characters(self.db_pool, player_id, access_token)
                    refreshed += 1
                    new_chars += stats.get("new_characters", 0)

                    # Clear any open errors for this player on success
                    await resolve_issue(self.db_pool, "bnet_token_expired", identifier=battletag)
                    await resolve_issue(self.db_pool, "bnet_sync_error", identifier=battletag)

                except Exception as exc:
                    logger.error(
                        "Battle.net character refresh failed for %s: %s",
                        battletag, exc, exc_info=True,
                    )
                    errors += 1
                    result = await report_error(
                        self.db_pool,
                        "bnet_sync_error",
                        "warning",
                        f"Battle.net character sync failed for {battletag}: {exc}",
                        "scheduler",
                        details={"player_id": player_id, "battletag": battletag, "error": str(exc)},
                        identifier=battletag,
                    )
                    await maybe_notify_discord(
                        self.db_pool, self.discord_bot, self.audit_channel_id,
                        "bnet_sync_error", "warning",
                        f"Battle.net sync failed for **{battletag}**: {exc}",
                        result["is_first_occurrence"],
                    )

            logger.info(
                "Battle.net character refresh complete: refreshed=%d new_chars=%d skipped=%d errors=%d",
                refreshed, new_chars, skipped, errors,
            )

        except Exception as exc:
            logger.error("Battle.net character refresh job failed: %s", exc, exc_info=True)
            result = await report_error(
                self.db_pool,
                "bnet_sync_error",
                "critical",
                f"Battle.net character refresh job crashed: {exc}",
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "bnet_sync_error", "critical",
                f"Battle.net character refresh job crashed: {exc}",
                result["is_first_occurrence"],
            )

    async def run_wcl_sync(self):
        """Warcraft Logs sync pipeline. Runs daily at 5 AM UTC.

        Pipeline:
          1. Load wcl_config from guild_identity.wcl_config
          2. If not configured or sync disabled, skip
          3. Decrypt credentials, initialize WarcraftLogsClient
          4. Sync guild reports (last 25)
          5. Sync character parses for active characters
          6. Update wcl_config timestamps
        """
        from .wcl_sync import load_wcl_config, sync_guild_reports, sync_character_parses, sync_report_parses
        from .warcraftlogs_client import WarcraftLogsClient, WarcraftLogsError

        config = await load_wcl_config(self.db_pool)
        if not config:
            logger.info("WCL sync: no config row found — skipping")
            return
        if not config.get("is_configured") or not config.get("sync_enabled"):
            logger.info("WCL sync: not configured or disabled — skipping")
            return

        client_id = config.get("client_id") or ""
        encrypted_secret = config.get("client_secret_encrypted") or ""
        guild_name = config.get("wcl_guild_name") or ""
        server_slug = config.get("wcl_server_slug") or ""
        region = config.get("wcl_server_region") or "us"
        config_id = config["id"]

        if not client_id or not encrypted_secret or not guild_name:
            logger.warning("WCL sync: incomplete config — skipping")
            return

        # Decrypt client secret
        try:
            jwt_secret = os.environ.get("JWT_SECRET_KEY", "")
            from sv_common.crypto import decrypt_secret
            client_secret = decrypt_secret(encrypted_secret, jwt_secret)
        except Exception as exc:
            logger.error("WCL sync: failed to decrypt client secret: %s", exc)
            return

        wcl_client = WarcraftLogsClient(client_id, client_secret)
        try:
            await wcl_client.initialize()

            report_stats = await sync_guild_reports(
                self.db_pool, wcl_client, guild_name, server_slug, region
            )
            logger.info("WCL report sync complete: %s", report_stats)

            # Sync character parses — use currently active characters
            async with self.db_pool.acquire() as conn:
                char_rows = await conn.fetch(
                    """SELECT id, character_name AS name
                       FROM guild_identity.wow_characters
                       WHERE removed_at IS NULL AND in_guild = TRUE
                       ORDER BY character_name"""
                )
            characters = [dict(r) for r in char_rows]

            parse_stats = await sync_character_parses(
                self.db_pool, wcl_client, characters, server_slug, region
            )
            logger.info("WCL parse sync complete: %s", parse_stats)

            # Step 3: Report-based parse sync — covers all raid attendees,
            # not just characters with public WCL profiles.
            # Fetch zone name map once, then pull current-tier reports.
            try:
                zone_name_map = await wcl_client.get_world_zones()
            except Exception:
                zone_name_map = {}

            # Derive current WCL zone IDs from current raid season's boss names
            site_cfg = get_site_config()
            current_raid_ids: list[int] = []
            if site_cfg:
                async with self.db_pool.acquire() as conn:
                    season_row = await conn.fetchrow(
                        """SELECT current_raid_ids
                           FROM patt.raid_seasons
                           WHERE is_active = TRUE
                           LIMIT 1"""
                    )
                if season_row and season_row["current_raid_ids"]:
                    current_raid_ids = season_row["current_raid_ids"]

            # Find WCL zone IDs that correspond to the current raid tier
            current_wcl_zone_ids: list[int] = []
            if current_raid_ids:
                async with self.db_pool.acquire() as conn:
                    zone_rows = await conn.fetch(
                        """SELECT DISTINCT rr.zone_id
                           FROM guild_identity.raid_reports rr
                           WHERE rr.zone_id IS NOT NULL
                             AND array_length(rr.encounter_ids, 1) > 0
                             AND LOWER(rr.zone_name) IN (
                                 SELECT DISTINCT LOWER(crp.boss_name)
                                 FROM guild_identity.character_raid_progress crp
                                 WHERE crp.raid_id = ANY($1)
                             )""",
                        current_raid_ids,
                    )
                current_wcl_zone_ids = [r["zone_id"] for r in zone_rows]

            # If zone derivation failed, just pull all recent reports
            # that have encounter data (better than nothing)
            zone_filter_sql = (
                "AND zone_id = ANY($2)" if current_wcl_zone_ids else ""
            )
            zone_params: list = [20]
            if current_wcl_zone_ids:
                zone_params = [current_wcl_zone_ids, 20]

            async with self.db_pool.acquire() as conn:
                if current_wcl_zone_ids:
                    report_rows = await conn.fetch(
                        """SELECT report_code
                           FROM guild_identity.raid_reports
                           WHERE array_length(encounter_ids, 1) > 0
                             AND zone_id = ANY($1)
                           ORDER BY raid_date DESC
                           LIMIT $2""",
                        current_wcl_zone_ids,
                        20,
                    )
                else:
                    report_rows = await conn.fetch(
                        """SELECT report_code
                           FROM guild_identity.raid_reports
                           WHERE array_length(encounter_ids, 1) > 0
                           ORDER BY raid_date DESC
                           LIMIT $1""",
                        20,
                    )
            report_codes = [r["report_code"] for r in report_rows]

            if report_codes:
                report_parse_stats = await sync_report_parses(
                    self.db_pool, wcl_client, report_codes, zone_name_map
                )
                logger.info("WCL report parse sync: %s", report_parse_stats)
            else:
                logger.info("WCL report parse sync: no eligible reports found")

            # Update sync status
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE guild_identity.wcl_config
                       SET last_sync = NOW(), last_sync_status = 'success',
                           last_sync_error = NULL, updated_at = NOW()
                       WHERE id = $1""",
                    config_id,
                )

        except WarcraftLogsError as exc:
            logger.error("WCL sync failed (WCL API error): %s", exc, exc_info=True)
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE guild_identity.wcl_config
                       SET last_sync = NOW(), last_sync_status = 'error',
                           last_sync_error = $1, updated_at = NOW()
                       WHERE id = $2""",
                    str(exc)[:500],
                    config_id,
                )
            from sv_common.errors import report_error
            from guild_portal.services.error_routing import maybe_notify_discord
            result = await report_error(
                self.db_pool,
                "wcl_sync_failed",
                "critical",
                str(exc),
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "wcl_sync_failed", "critical",
                str(exc),
                result["is_first_occurrence"],
            )
        except Exception as exc:
            logger.error("WCL sync pipeline failed: %s", exc, exc_info=True)
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE guild_identity.wcl_config
                       SET last_sync = NOW(), last_sync_status = 'error',
                           last_sync_error = $1, updated_at = NOW()
                       WHERE id = $2""",
                    str(exc)[:500],
                    config_id,
                )
            from sv_common.errors import report_error
            from guild_portal.services.error_routing import maybe_notify_discord
            result = await report_error(
                self.db_pool,
                "wcl_sync_failed",
                "critical",
                str(exc),
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "wcl_sync_failed", "critical",
                str(exc),
                result["is_first_occurrence"],
            )
        finally:
            await wcl_client.close()

    async def run_ah_sync(self):
        """Auction House price sync pipeline. Runs every hour at :15.

        Pipeline:
          1. Load active_connected_realm_ids from site_config (resolve if empty)
          2. Call sync_ah_prices() — fetch commodities (realm_id=0) + per-realm auctions
          3. Daily cleanup of old price history (runs on the first call each day)
        """
        from .ah_sync import sync_ah_prices, cleanup_old_prices, get_active_connected_realm_ids

        try:
            # Load connected realm IDs from site_config
            connected_realm_ids: list[int] = []
            home_realm_slug: str | None = None

            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT connected_realm_id, home_realm_slug, active_connected_realm_ids FROM common.site_config LIMIT 1"
                )
            if row:
                home_realm_slug = row["home_realm_slug"]
                stored_ids = row["active_connected_realm_ids"] or []
                connected_realm_ids = list(stored_ids)

            # Resolve the home realm ID if not yet stored
            if not connected_realm_ids and home_realm_slug:
                logger.info("AH sync: resolving connected realm IDs for slug '%s'", home_realm_slug)
                connected_realm_ids = await get_active_connected_realm_ids(
                    self.db_pool, self.blizzard_client
                )
                if not connected_realm_ids:
                    # Fall back to just the home realm
                    crid = await self.blizzard_client.get_connected_realm_id(home_realm_slug)
                    if crid:
                        connected_realm_ids = [crid]

                if connected_realm_ids:
                    async with self.db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE common.site_config SET connected_realm_id = $1, active_connected_realm_ids = $2",
                            connected_realm_ids[0],
                            connected_realm_ids,
                        )
                    logger.info("AH sync: cached %d active realm(s): %s", len(connected_realm_ids), connected_realm_ids)

            # Daily refresh of active realm list (runs on the first sync after midnight)
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            if now_utc.hour == 0 and home_realm_slug:
                try:
                    refreshed_ids = await get_active_connected_realm_ids(
                        self.db_pool, self.blizzard_client
                    )
                    if refreshed_ids:
                        connected_realm_ids = refreshed_ids
                        async with self.db_pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE common.site_config SET active_connected_realm_ids = $1",
                                connected_realm_ids,
                            )
                        logger.info("AH sync: refreshed active realm list: %s", connected_realm_ids)
                except Exception as refresh_exc:
                    logger.warning("AH sync: realm list refresh failed (non-fatal): %s", refresh_exc)

            if not connected_realm_ids:
                logger.warning("AH sync: no connected_realm_ids available — skipping")
                return

            stats = await sync_ah_prices(self.db_pool, self.blizzard_client, connected_realm_ids)
            logger.info("AH sync complete: %s", stats)

            # Run cleanup once per day (on the first sync after midnight)
            if now_utc.hour == 0:
                cleanup_stats = await cleanup_old_prices(self.db_pool)
                logger.info("AH cleanup: %s", cleanup_stats)

        except Exception as exc:
            logger.error("AH sync failed: %s", exc, exc_info=True)
            from sv_common.errors import report_error
            from guild_portal.services.error_routing import maybe_notify_discord
            result = await report_error(
                self.db_pool,
                "ah_sync_failed",
                "critical",
                str(exc),
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "ah_sync_failed", "critical",
                str(exc),
                result["is_first_occurrence"],
            )

    async def run_attendance_processing(self):
        """Voice attendance post-processing pipeline. Runs every 30 minutes.

        Picks up events that ended ≥30 minutes ago, have voice_tracking_enabled,
        and have not yet been processed. Runs both WCL and voice passes.

        Also snapshots signup data for events that have started but not yet
        been snapshotted (was_available + raid_helper_status).
        """
        from .attendance_processor import (
            get_unprocessed_events,
            get_unsnapshotted_events,
            process_event,
            snapshot_event_signups,
        )

        try:
            # Check if feature is enabled
            async with self.db_pool.acquire() as conn:
                enabled = await conn.fetchval(
                    "SELECT attendance_feature_enabled FROM common.discord_config LIMIT 1"
                )
            if not enabled:
                return

            # --- Signup snapshot pass (runs at event start) ---
            unsnapshotted = await get_unsnapshotted_events(self.db_pool)
            for event in unsnapshotted:
                logger.info(
                    "Signup snapshot: event %d (%s) started %s",
                    event["id"],
                    event["title"],
                    event["start_time_utc"],
                )
                await snapshot_event_signups(self.db_pool, event["id"])

            # --- Attendance processing pass (runs 30 min after event end) ---
            events = await get_unprocessed_events(self.db_pool)
            if not events:
                return

            audit_channel = self._get_audit_channel()
            for event in events:
                logger.info(
                    "Attendance processing: event %d (%s) ended %s",
                    event["id"],
                    event["title"],
                    event["end_time_utc"],
                )
                await process_event(self.db_pool, event["id"], audit_channel)

        except Exception as exc:
            logger.error("Attendance processing failed: %s", exc, exc_info=True)
            from sv_common.errors import report_error
            from guild_portal.services.error_routing import maybe_notify_discord
            result = await report_error(
                self.db_pool,
                "attendance_sync_failed",
                "critical",
                str(exc),
                "scheduler",
                details={"error": str(exc)},
            )
            await maybe_notify_discord(
                self.db_pool, self.discord_bot, self.audit_channel_id,
                "attendance_sync_failed", "critical",
                str(exc),
                result["is_first_occurrence"],
            )

    async def run_weekly_error_digest(self):
        """Post a grouped summary of all open errors to the audit channel.

        Runs Sunday 8:00 AM UTC. Silent if no open errors.
        """
        from sv_common.errors import get_unresolved

        audit_channel = self._get_audit_channel()
        if audit_channel is None:
            logger.warning("Weekly error digest: audit channel not available")
            return

        try:
            errors = await get_unresolved(self.db_pool, limit=200)
        except Exception as exc:
            logger.error("Weekly error digest: failed to fetch errors: %s", exc)
            return

        if not errors:
            logger.info("Weekly error digest: no open errors")
            return

        embeds = _build_digest_embeds(errors)
        try:
            for i in range(0, len(embeds), 10):
                await audit_channel.send(embeds=embeds[i:i + 10])
        except Exception as exc:
            logger.error("Weekly error digest: failed to post to Discord: %s", exc)

    async def trigger_full_report(self):
        """Manual trigger: send a full report of ALL unresolved issues."""
        channel = self._get_audit_channel()
        if channel:
            await send_new_issues_report(self.db_pool, channel, force_full=True)
