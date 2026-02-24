"""
Auto-provisioner for verified guild members.

Given a player_id from guild_identity.players, this module:
  1. Looks up the player's Discord account and linked characters
  2. Determines their rank from highest-ranked character
  3. Assigns the appropriate Discord role (skipped when silent=True)
  4. Generates a website invite code (skipped when silent=True)
  5. DMs the invite code to the member (skipped when silent=True or DM gate OFF)

silent=True is used for retroactive provisioning of existing members —
full roster sync without sending any messages.
"""

import logging
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
import discord

logger = logging.getLogger(__name__)

# guild rank name → Discord role name (must match actual Discord role names)
RANK_TO_DISCORD_ROLE = {
    "Guild Leader": "Guild Leader",
    "Officer":      "Officer",
    "Veteran":      "Veteran",
    "Member":       "Member",
    "Initiate":     "Initiate",
}

DEFAULT_RANK_NAME = "Initiate"


class AutoProvisioner:
    """Handles automatic provisioning of verified guild members."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        bot: Optional[discord.Client] = None,
    ):
        self.db_pool = db_pool
        self.bot = bot

    async def provision_player(
        self,
        player_id: int,
        silent: bool = False,
        onboarding_session_id: Optional[int] = None,
    ) -> dict:
        """
        Provision a player across all platform systems.

        Returns a summary dict of what was done.
        silent=True skips Discord role assignment, invite codes, and DMs.
        """
        result = {
            "player_id": player_id,
            "discord_role_assigned": False,
            "invite_code": None,
            "characters_linked": 0,
            "errors": [],
        }

        async with self.db_pool.acquire() as conn:
            # Get player with discord info
            player = await conn.fetchrow(
                """SELECT p.id, p.display_name, p.discord_user_id,
                          du.discord_id
                   FROM guild_identity.players p
                   LEFT JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
                   WHERE p.id = $1""",
                player_id,
            )
            if not player:
                result["errors"].append("Player not found")
                return result

            discord_id = player["discord_id"]

            # Count linked characters
            char_count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.player_characters WHERE player_id = $1",
                player_id,
            )
            result["characters_linked"] = char_count or 0

            # Get highest rank from linked characters
            rank_row = await conn.fetchrow(
                """SELECT gr.name as rank_name
                   FROM guild_identity.player_characters pc
                   JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
                   JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
                   WHERE pc.player_id = $1 AND wc.removed_at IS NULL
                   ORDER BY gr.level DESC LIMIT 1""",
                player_id,
            )
            rank_name = rank_row["rank_name"] if rank_row else DEFAULT_RANK_NAME

        # Assign Discord role (requires live bot, skipped in silent mode)
        if not silent and self.bot and discord_id:
            result["discord_role_assigned"] = await self._assign_discord_role(
                discord_id, rank_name
            )

        # Generate invite + send DM (skipped in silent mode, also checks DM gate)
        if not silent and discord_id:
            invite_code = await self._create_invite(player_id, onboarding_session_id)
            result["invite_code"] = invite_code
            if invite_code and self.bot:
                await self._send_invite_dm(discord_id, invite_code)

        logger.info(
            "Provisioned player=%d | chars=%d | role_assigned=%s | silent=%s",
            player_id,
            result["characters_linked"],
            result["discord_role_assigned"],
            silent,
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _assign_discord_role(
        self,
        discord_id: str,
        rank_name: Optional[str],
    ) -> bool:
        """Assign the appropriate guild role in Discord."""
        if not self.bot:
            return False
        try:
            target_role_name = RANK_TO_DISCORD_ROLE.get(rank_name or "", DEFAULT_RANK_NAME)
            for guild in self.bot.guilds:
                member = guild.get_member(int(discord_id))
                if not member:
                    continue
                role = discord.utils.get(guild.roles, name=target_role_name)
                if role and role not in member.roles:
                    await member.add_roles(
                        role,
                        reason=f"Auto-provisioned via onboarding (rank: {rank_name})",
                    )
                return True
        except Exception as e:
            logger.warning("Discord role assign failed for %s: %s", discord_id, e)
        return False

    async def _create_invite(
        self,
        player_id: int,
        onboarding_session_id: Optional[int],
    ) -> Optional[str]:
        """Generate a single-use website invite code."""
        try:
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            expires_at = datetime.now(timezone.utc) + timedelta(days=7)
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO common.invite_codes
                       (code, player_id, generated_by, onboarding_session_id, expires_at)
                       VALUES ($1, $2, 'auto_onboarding', $3, $4)""",
                    code,
                    player_id,
                    onboarding_session_id,
                    expires_at,
                )
            return code
        except Exception as e:
            logger.error("Failed to create invite code for player %d: %s", player_id, e)
            return None

    async def _send_invite_dm(self, discord_id: str, invite_code: str) -> None:
        """DM the invite code and welcome message to the member."""
        from sv_common.discord.dm import is_bot_dm_enabled
        if not await is_bot_dm_enabled(self.db_pool):
            logger.info(
                "Bot DM disabled — invite code %s created but not sent to %s",
                invite_code, discord_id,
            )
            return

        if not self.bot:
            return
        try:
            user = await self.bot.fetch_user(int(discord_id))
            embed = discord.Embed(
                title="You're officially set up! 🎉",
                description=(
                    f"**Your invite code:** `{invite_code}`\n"
                    f"**Sign up here:** https://pullallthethings.com/register\n\n"
                    "Your characters have been pre-loaded — log in and confirm "
                    "everything looks right. You can mark your main and add any "
                    "characters we might have missed."
                ),
                color=0x4ADE80,
            )
            embed.add_field(
                name="📅 Raid Schedule",
                value="Fridays & Saturdays at 6 PM PST / 9 PM EST",
                inline=False,
            )
            embed.set_footer(text="Pull All The Things • Welcome!")
            dm = await user.create_dm()
            await dm.send(embed=embed)
        except Exception as e:
            logger.warning("Could not DM invite to %s: %s", discord_id, e)
