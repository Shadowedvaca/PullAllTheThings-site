"""
Auto-provisioner for verified guild members.

Given a person_id from guild_identity, this module:
  1. Finds or creates a common.guild_member record
  2. Links their Discord account
  3. Creates common.characters for all their WoW characters
  4. Syncs their rank from the highest in-game rank
  5. Assigns the appropriate Discord role  (skipped when silent=True)
  6. Generates a website invite + sends DM  (skipped when silent=True)

silent=True is used for retroactive provisioning of existing members —
full roster sync without sending any messages.
"""

import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
import discord

logger = logging.getLogger(__name__)

# guild_identity.role_category → common.characters.role
ROLE_MAP = {
    "Tank":   "tank",
    "Healer": "healer",
    "Melee":  "melee_dps",
    "Ranged": "ranged_dps",
}

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

    async def provision_person(
        self,
        person_id: int,
        silent: bool = False,
        onboarding_session_id: Optional[int] = None,
    ) -> dict:
        """
        Provision a person across all platform systems.

        Returns a summary dict of what was done.
        silent=True skips Discord role assignment, invite codes, and DMs.
        """
        result = {
            "person_id": person_id,
            "guild_member_id": None,
            "discord_linked": False,
            "characters_created": 0,
            "characters_skipped": 0,
            "discord_role_assigned": False,
            "invite_code": None,
            "errors": [],
        }

        async with self.db_pool.acquire() as conn:
            # Load person's Discord + WoW data from guild_identity
            discord_member = await conn.fetchrow(
                """SELECT id, discord_id, username, display_name
                   FROM guild_identity.discord_members
                   WHERE person_id = $1 AND is_present = TRUE
                   LIMIT 1""",
                person_id,
            )
            wow_chars = await conn.fetch(
                """SELECT character_name, realm_name, character_class,
                          active_spec, role_category, is_main,
                          guild_rank, guild_rank_name, level
                   FROM guild_identity.wow_characters
                   WHERE person_id = $1 AND removed_at IS NULL
                   ORDER BY guild_rank ASC, is_main DESC NULLS LAST""",
                person_id,
            )

            if not discord_member and not wow_chars:
                result["errors"].append("No Discord member or characters found")
                return result

            discord_id = discord_member["discord_id"] if discord_member else None

            # Find or create common.guild_member
            member_id = await self._find_or_create_guild_member(
                conn, discord_id, discord_member, wow_chars
            )
            result["guild_member_id"] = member_id

            # Link Discord account if not already linked
            if discord_id:
                existing_discord = await conn.fetchval(
                    "SELECT discord_id FROM common.guild_members WHERE id = $1",
                    member_id,
                )
                if not existing_discord:
                    await conn.execute(
                        """UPDATE common.guild_members
                           SET discord_id = $1, discord_username = $2, updated_at = NOW()
                           WHERE id = $3""",
                        discord_id,
                        discord_member["username"],
                        member_id,
                    )
                    result["discord_linked"] = True

            # Create common.characters for each WoW character
            created, skipped = await self._sync_characters(conn, member_id, wow_chars)
            result["characters_created"] = created
            result["characters_skipped"] = skipped

            # Sync rank from highest in-game rank
            if wow_chars:
                await self._sync_rank(conn, member_id, wow_chars[0]["guild_rank_name"])

        # Assign Discord role (requires live bot, skipped in silent mode)
        if not silent and self.bot and discord_id:
            rank_name = wow_chars[0]["guild_rank_name"] if wow_chars else None
            result["discord_role_assigned"] = await self._assign_discord_role(
                discord_id, rank_name
            )

        # Generate invite + send DM (skipped in silent mode)
        if not silent and discord_id:
            invite_code = await self._create_invite(member_id, onboarding_session_id)
            result["invite_code"] = invite_code
            if invite_code and self.bot:
                await self._send_invite_dm(discord_id, invite_code)

        logger.info(
            "Provisioned person=%d → member=%d | chars +%d =%d | silent=%s",
            person_id,
            member_id,
            created,
            skipped,
            silent,
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _find_or_create_guild_member(
        self,
        conn: asyncpg.Connection,
        discord_id: Optional[str],
        discord_member,
        wow_chars,
    ) -> int:
        """Return existing guild_member id, or create one."""
        # Try to find by discord_id first
        if discord_id:
            existing = await conn.fetchval(
                "SELECT id FROM common.guild_members WHERE discord_id = $1",
                discord_id,
            )
            if existing:
                return existing

        # Determine display name
        display_name = None
        if discord_member:
            display_name = discord_member["display_name"] or discord_member["username"]

        # Determine rank
        rank_name = wow_chars[0]["guild_rank_name"] if wow_chars else DEFAULT_RANK_NAME
        rank_id = await conn.fetchval(
            "SELECT id FROM common.guild_ranks WHERE name = $1", rank_name
        )
        if not rank_id:
            rank_id = await conn.fetchval(
                "SELECT id FROM common.guild_ranks ORDER BY level LIMIT 1"
            )

        discord_username = (
            discord_member["username"] if discord_member else display_name or "unknown"
        )

        member_id = await conn.fetchval(
            """INSERT INTO common.guild_members
               (discord_id, discord_username, display_name, rank_id, rank_source)
               VALUES ($1, $2, $3, $4, 'discord_sync')
               RETURNING id""",
            discord_id,
            discord_username,
            display_name,
            rank_id,
        )
        return member_id

    async def _sync_characters(
        self,
        conn: asyncpg.Connection,
        member_id: int,
        wow_chars,
    ) -> tuple[int, int]:
        """Create common.characters entries for all wow_chars not already present."""
        created = skipped = 0

        for wc in wow_chars:
            realm = wc["realm_name"] or ""
            if not realm:
                # Skip chars with no realm data — can't create a valid entry
                skipped += 1
                continue

            # Check if character already exists
            existing_id = await conn.fetchval(
                """SELECT id FROM common.characters
                   WHERE LOWER(name) = LOWER($1) AND LOWER(realm) = LOWER($2)""",
                wc["character_name"],
                realm,
            )
            if existing_id:
                # Link to this member if currently unlinked
                await conn.execute(
                    """UPDATE common.characters SET member_id = $1
                       WHERE id = $2 AND member_id IS NULL""",
                    member_id,
                    existing_id,
                )
                skipped += 1
                continue

            role = ROLE_MAP.get(wc["role_category"] or "", "melee_dps")
            main_alt = "main" if wc["is_main"] else "alt"

            await conn.execute(
                """INSERT INTO common.characters
                   (member_id, name, realm, class, spec, role, main_alt)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (name, realm) DO UPDATE
                       SET member_id = EXCLUDED.member_id""",
                member_id,
                wc["character_name"],
                realm,
                wc["character_class"] or "",
                wc["active_spec"] or "",
                role,
                main_alt,
            )
            created += 1

        return created, skipped

    async def _sync_rank(
        self,
        conn: asyncpg.Connection,
        member_id: int,
        rank_name: Optional[str],
    ) -> None:
        """Update guild_member rank from in-game data."""
        if not rank_name:
            return
        rank_id = await conn.fetchval(
            "SELECT id FROM common.guild_ranks WHERE name = $1", rank_name
        )
        if rank_id:
            await conn.execute(
                """UPDATE common.guild_members
                   SET rank_id = $1, rank_source = 'discord_sync', updated_at = NOW()
                   WHERE id = $2""",
                rank_id,
                member_id,
            )

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
        member_id: int,
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
                       (code, member_id, expires_at, generated_by, onboarding_session_id)
                       VALUES ($1, $2, $3, 'auto_onboarding', $4)""",
                    code,
                    member_id,
                    expires_at,
                    onboarding_session_id,
                )
            return code
        except Exception as e:
            logger.error("Failed to create invite code for member %d: %s", member_id, e)
            return None

    async def _send_invite_dm(self, discord_id: str, invite_code: str) -> None:
        """DM the invite code and welcome message to the member."""
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
