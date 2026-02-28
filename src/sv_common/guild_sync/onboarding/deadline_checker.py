"""
Deadline checker for onboarding sessions.

Runs after every Blizzard sync (and on a 30-min interval) to:
  1. Re-attempt verification for all pending_verification sessions
  2. Escalate overdue sessions to the #audit-channel
  3. Resume sessions stuck in awaiting_dm when DMs are re-enabled

Called by GuildSyncScheduler.run_onboarding_check().
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import discord

from .provisioner import AutoProvisioner

logger = logging.getLogger(__name__)

PATT_GOLD = 0xD4A84B


class OnboardingDeadlineChecker:
    """Checks for overdue onboarding sessions and re-runs verification."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        bot: Optional[discord.Client] = None,
        audit_channel_id: Optional[int] = None,
    ):
        self.db_pool = db_pool
        self.bot = bot
        self.audit_channel_id = audit_channel_id

    async def run(self) -> dict:
        """
        Main entry point. Returns summary stats.
        Also called as check_pending() for scheduler compatibility.
        """
        stats = {
            "verified": 0,
            "escalated": 0,
            "provisioned": 0,
            "still_pending": 0,
            "dm_resumed": 0,
        }

        # 1. Retry verification for pending_verification sessions
        async with self.db_pool.acquire() as conn:
            sessions = await conn.fetch(
                """SELECT id, discord_id, reported_main_name, deadline_at,
                          escalated_at, verification_attempts
                   FROM guild_identity.onboarding_sessions
                   WHERE state = 'pending_verification'
                   ORDER BY created_at ASC""",
            )

        now = datetime.now(timezone.utc)

        for session in sessions:
            verified = await self._retry_verification(session["id"], session["reported_main_name"])
            if verified:
                stats["verified"] += 1
                stats["provisioned"] += 1
                continue

            # Check deadline
            deadline = session["deadline_at"]
            if deadline and deadline < now and not session["escalated_at"]:
                await self._escalate(session)
                stats["escalated"] += 1
            else:
                stats["still_pending"] += 1

        # 2. Resume awaiting_dm sessions if DMs are now enabled
        resumed = await self._resume_awaiting_dm_sessions()
        stats["dm_resumed"] = resumed

        if stats["escalated"] > 0 or resumed > 0:
            logger.info(
                "Onboarding check: verified=%d provisioned=%d escalated=%d pending=%d resumed=%d",
                stats["verified"], stats["provisioned"],
                stats["escalated"], stats["still_pending"], resumed,
            )

        return stats

    # Alias so scheduler can call check_pending() or run()
    async def check_pending(self) -> dict:
        return await self.run()

    async def _resume_awaiting_dm_sessions(self) -> int:
        """
        If DMs are now enabled, start conversations for sessions stuck in awaiting_dm.
        Returns the number of sessions where DM was attempted.
        """
        from sv_common.discord.dm import is_onboarding_dm_enabled
        if not await is_onboarding_dm_enabled(self.db_pool):
            return 0  # Still disabled, skip

        async with self.db_pool.acquire() as conn:
            awaiting = await conn.fetch(
                """SELECT id, discord_id FROM guild_identity.onboarding_sessions
                   WHERE state = 'awaiting_dm' AND dm_sent_at IS NULL
                   ORDER BY created_at ASC LIMIT 10""",
            )

        resumed = 0
        for session in awaiting:
            member = await self._find_discord_member(session["discord_id"])
            if not member:
                continue
            from .conversation import OnboardingConversation
            conv = OnboardingConversation(self.bot, member, self.db_pool)
            conv.session_id = session["id"]
            # Fire-and-forget: _send_welcome blocks waiting for a user reply
            # (up to 5 min timeout), so it must not be awaited here.
            asyncio.create_task(self._dm_and_log(conv, session["discord_id"]))
            resumed += 1

        return resumed

    async def _dm_and_log(self, conv, discord_id: str) -> None:
        """Background task: send the welcome DM and log any failures."""
        try:
            await conv._send_welcome()
        except Exception as e:
            logger.warning("Failed to resume DM for discord_id=%s: %s", discord_id, e)

    async def _find_discord_member(self, discord_id: str) -> Optional[discord.Member]:
        """Find a Discord Member object by discord_id string."""
        if not self.bot:
            return None
        for guild in self.bot.guilds:
            member = guild.get_member(int(discord_id))
            if member:
                return member
        return None

    async def _retry_verification(self, session_id: int, main_name: Optional[str]) -> bool:
        """
        Re-run character match for this session using the player model.
        Returns True if the session was verified (and provisioned).
        """
        if not main_name:
            return False

        async with self.db_pool.acquire() as conn:
            # Re-check state (might have changed since we loaded)
            state = await conn.fetchval(
                "SELECT state FROM guild_identity.onboarding_sessions WHERE id = $1",
                session_id,
            )
            if state != "pending_verification":
                return False

            char = await conn.fetchrow(
                """SELECT id, character_name, realm_slug
                   FROM guild_identity.wow_characters
                   WHERE LOWER(character_name) = $1 AND removed_at IS NULL""",
                main_name.lower(),
            )
            if not char:
                await conn.execute(
                    """UPDATE guild_identity.onboarding_sessions SET
                        verification_attempts = verification_attempts + 1,
                        last_verification_at = NOW(),
                        updated_at = NOW()
                       WHERE id = $1""",
                    session_id,
                )
                return False

            # Load session to get discord_id and reported alts
            session = await conn.fetchrow(
                "SELECT * FROM guild_identity.onboarding_sessions WHERE id = $1",
                session_id,
            )

            # Check if character already belongs to a player
            existing_pc = await conn.fetchrow(
                """SELECT pc.player_id FROM guild_identity.player_characters pc
                   WHERE pc.character_id = $1""",
                char["id"],
            )

            # Get discord_users.id for this session
            du_row = await conn.fetchrow(
                "SELECT id FROM guild_identity.discord_users WHERE discord_id = $1",
                session["discord_id"],
            )
            du_id = du_row["id"] if du_row else None

            if existing_pc:
                player_id = existing_pc["player_id"]
                if du_id:
                    await conn.execute(
                        """UPDATE guild_identity.players SET discord_user_id = $1, updated_at = NOW()
                           WHERE id = $2 AND discord_user_id IS NULL""",
                        du_id, player_id,
                    )
            else:
                # Create new player
                char_name = char["character_name"]
                player_id = await conn.fetchval(
                    """INSERT INTO guild_identity.players (display_name, discord_user_id)
                       VALUES ($1, $2) RETURNING id""",
                    char_name, du_id,
                )
                await conn.execute(
                    """INSERT INTO guild_identity.player_characters (player_id, character_id)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    player_id, char["id"],
                )

            # Link reported alts
            for alt_name in (session["reported_alt_names"] or []):
                alt_char = await conn.fetchrow(
                    """SELECT id FROM guild_identity.wow_characters
                       WHERE LOWER(character_name) = $1
                         AND removed_at IS NULL
                         AND id NOT IN (
                             SELECT character_id FROM guild_identity.player_characters
                         )""",
                    alt_name.lower(),
                )
                if alt_char:
                    await conn.execute(
                        """INSERT INTO guild_identity.player_characters (player_id, character_id)
                           VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                        player_id, alt_char["id"],
                    )

            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'verified',
                    verified_at = NOW(),
                    verified_player_id = $2,
                    verification_attempts = verification_attempts + 1,
                    last_verification_at = NOW(),
                    updated_at = NOW()
                   WHERE id = $1""",
                session_id, player_id,
            )

        # Provision (outside the connection — provisioner opens its own)
        await self._provision(session_id, player_id)
        return True

    async def _provision(self, session_id: int, player_id: int):
        """Run auto-provisioner for a verified session."""
        provisioner = AutoProvisioner(self.db_pool, self.bot)
        result = await provisioner.provision_player(
            player_id,
            silent=False,
            onboarding_session_id=session_id,
        )

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'provisioned',
                    website_invite_sent = $2,
                    website_invite_code = $3,
                    roster_entries_created = $4,
                    discord_role_assigned = $5,
                    completed_at = NOW(),
                    updated_at = NOW()
                   WHERE id = $1""",
                session_id,
                result["invite_code"] is not None,
                result["invite_code"],
                result["characters_linked"] > 0,
                result["discord_role_assigned"],
            )
        logger.info("Deadline check provisioned player=%d session=%d", player_id, session_id)

    async def _escalate(self, session: asyncpg.Record):
        """Mark session as escalated and post to audit channel."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    escalated_at = NOW(), updated_at = NOW()
                   WHERE id = $1""",
                session["id"],
            )

        logger.warning(
            "Onboarding deadline passed: session=%d discord_id=%s attempts=%d",
            session["id"], session["discord_id"], session["verification_attempts"],
        )

        if self.bot and self.audit_channel_id:
            await self._post_escalation_embed(session)

    async def _post_escalation_embed(self, session: asyncpg.Record):
        """Post an escalation notice to #audit-channel."""
        channel = self.bot.get_channel(self.audit_channel_id)
        if not channel:
            return

        try:
            user_display = f"<@{session['discord_id']}>"
            main_name = session["reported_main_name"] or "*(not provided)*"
            attempts = session["verification_attempts"]

            embed = discord.Embed(
                title="⚠️ Onboarding Unresolved",
                description=(
                    f"**Member:** {user_display}\n"
                    f"**Reported main:** {main_name}\n"
                    f"**Verification attempts:** {attempts}\n\n"
                    "This member completed the onboarding DM but their character "
                    "couldn't be matched in the roster after 24 hours.\n\n"
                    "**Actions:**\n"
                    "• `/onboard-resolve` — manually provision them\n"
                    "• `/onboard-dismiss` — close without provisioning\n"
                    "• `/onboard-retry` — trigger another verification attempt"
                ),
                color=0xFBBF24,  # warning yellow
            )
            embed.set_footer(text=f"Session ID: {session['id']}")
            await channel.send(embed=embed)
        except Exception as e:
            logger.warning("Failed to post escalation embed: %s", e)
