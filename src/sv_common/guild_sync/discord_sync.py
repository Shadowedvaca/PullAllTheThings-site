"""
Discord server member and role synchronization.

Integrates with the existing Phase 2 Discord bot to:
- Pull the full member list with roles
- Determine each member's highest guild-relevant role
- Listen for real-time join/leave/role-change events
- Write data to guild_identity.discord_members

Guild-relevant Discord roles (in priority order):
  GM > Officer > Veteran > Member > Initiate

Members without ANY of these roles are tracked but flagged differently —
they might be guests, bots, or people who haven't been assigned a role yet.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import discord

logger = logging.getLogger(__name__)

# Discord role names → normalized names, in priority order (highest first)
# The role name in Discord should match these exactly (case-insensitive matching applied)
GUILD_ROLE_PRIORITY = ["GM", "Officer", "Veteran", "Member", "Initiate"]

# Map Discord role to equivalent in-game rank name for comparison
DISCORD_TO_INGAME_RANK = {
    "GM": "Guild Leader",
    "Officer": "Officer",
    "Veteran": "Veteran",
    "Member": "Member",
    "Initiate": "Initiate",
}


def get_highest_guild_role(member: discord.Member) -> Optional[str]:
    """
    Determine a Discord member's highest guild-relevant role.

    Returns the role name (e.g., "Officer") or None if they have no guild roles.
    """
    member_role_names = [r.name for r in member.roles]

    for role_name in GUILD_ROLE_PRIORITY:
        for mr in member_role_names:
            if mr.lower() == role_name.lower():
                return role_name

    return None


def get_all_guild_roles(member: discord.Member) -> list[str]:
    """Get all guild-relevant roles a member has."""
    result = []
    member_role_names = [r.name.lower() for r in member.roles]

    for role_name in GUILD_ROLE_PRIORITY:
        if role_name.lower() in member_role_names:
            result.append(role_name)

    return result


async def sync_discord_members(
    pool: asyncpg.Pool,
    guild: discord.Guild,
) -> dict:
    """
    Full sync of all Discord server members into the database.

    Called periodically (every 15-30 min) or on demand.
    """
    now = datetime.now(timezone.utc)
    stats = {"found": 0, "updated": 0, "new": 0, "departed": 0}

    current_ids = set()

    async with pool.acquire() as conn:
        async with conn.transaction():

            # Iterate all members (requires GUILD_MEMBERS intent)
            async for member in guild.fetch_members(limit=None):
                if member.bot:
                    continue

                stats["found"] += 1
                discord_id = str(member.id)
                current_ids.add(discord_id)

                highest_role = get_highest_guild_role(member)
                all_roles = get_all_guild_roles(member)
                display = member.nick or member.display_name

                existing = await conn.fetchrow(
                    """SELECT id, highest_guild_role, is_present
                       FROM guild_identity.discord_members
                       WHERE discord_id = $1""",
                    discord_id,
                )

                if existing:
                    await conn.execute(
                        """UPDATE guild_identity.discord_members SET
                            username = $2,
                            display_name = $3,
                            highest_guild_role = $4,
                            all_guild_roles = $5,
                            last_sync = $6,
                            is_present = TRUE,
                            removed_at = NULL
                           WHERE discord_id = $1""",
                        discord_id,
                        member.name,  # New Discord username (no discriminator)
                        display,
                        highest_role,
                        all_roles,
                        now,
                    )
                    stats["updated"] += 1
                else:
                    await conn.execute(
                        """INSERT INTO guild_identity.discord_members
                           (discord_id, username, display_name, highest_guild_role,
                            all_guild_roles, joined_server_at, last_sync, is_present)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)""",
                        discord_id,
                        member.name,
                        display,
                        highest_role,
                        all_roles,
                        member.joined_at,
                        now,
                    )
                    stats["new"] += 1

            # Mark members who left
            all_present = await conn.fetch(
                """SELECT id, discord_id FROM guild_identity.discord_members
                   WHERE is_present = TRUE"""
            )

            for row in all_present:
                if row["discord_id"] not in current_ids:
                    await conn.execute(
                        """UPDATE guild_identity.discord_members SET
                            is_present = FALSE, removed_at = $2
                           WHERE id = $1""",
                        row["id"], now,
                    )
                    stats["departed"] += 1

    logger.info(
        "Discord sync: %d found, %d updated, %d new, %d departed",
        stats["found"], stats["updated"], stats["new"], stats["departed"],
    )
    return stats


# ---------------------------------------------------------------------------
# Real-time event handlers (register with existing bot)
# ---------------------------------------------------------------------------

async def on_member_join(pool: asyncpg.Pool, member: discord.Member):
    """Handle a new member joining the Discord server."""
    if member.bot:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username, display_name, joined_server_at, last_sync, is_present)
               VALUES ($1, $2, $3, $4, NOW(), TRUE)
               ON CONFLICT (discord_id) DO UPDATE SET
                 is_present = TRUE, removed_at = NULL, last_sync = NOW()""",
            str(member.id), member.name, member.nick or member.display_name,
            member.joined_at,
        )
    logger.info("Discord member joined: %s (%s)", member.name, member.id)


async def on_member_remove(pool: asyncpg.Pool, member: discord.Member):
    """Handle a member leaving the Discord server."""
    if member.bot:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE guild_identity.discord_members SET
                is_present = FALSE, removed_at = NOW()
               WHERE discord_id = $1""",
            str(member.id),
        )
    logger.info("Discord member left: %s (%s)", member.name, member.id)


async def on_member_update(pool: asyncpg.Pool, before: discord.Member, after: discord.Member):
    """Handle role changes or nickname changes."""
    if after.bot:
        return

    # Check if guild-relevant roles changed
    old_roles = get_all_guild_roles(before)
    new_roles = get_all_guild_roles(after)

    if old_roles != new_roles or before.nick != after.nick:
        highest = get_highest_guild_role(after)
        display = after.nick or after.display_name

        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.discord_members SET
                    username = $2, display_name = $3,
                    highest_guild_role = $4, all_guild_roles = $5,
                    last_sync = NOW()
                   WHERE discord_id = $1""",
                str(after.id), after.name, display, highest, new_roles,
            )

        if old_roles != new_roles:
            logger.info(
                "Discord role change for %s: %s → %s",
                after.name, old_roles, new_roles,
            )
