"""
Discord server member and role synchronization.

Writes to guild_identity.discord_users (renamed from discord_members).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import discord

logger = logging.getLogger(__name__)

GUILD_ROLE_PRIORITY = ["GM", "Officer", "Veteran", "Member", "Initiate"]

DISCORD_TO_INGAME_RANK = {
    "GM": "Guild Leader",
    "Officer": "Officer",
    "Veteran": "Veteran",
    "Member": "Member",
    "Initiate": "Initiate",
}


def get_highest_guild_role(member: discord.Member) -> Optional[str]:
    member_role_names = [r.name for r in member.roles]
    for role_name in GUILD_ROLE_PRIORITY:
        for mr in member_role_names:
            if mr.lower() == role_name.lower():
                return role_name
    return None


def get_all_guild_roles(member: discord.Member) -> list[str]:
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
    """Full sync of all Discord server members into guild_identity.discord_users."""
    now = datetime.now(timezone.utc)
    stats = {"found": 0, "updated": 0, "new": 0, "departed": 0}

    current_ids = set()

    async with pool.acquire() as conn:
        async with conn.transaction():

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
                       FROM guild_identity.discord_users
                       WHERE discord_id = $1""",
                    discord_id,
                )

                if existing:
                    await conn.execute(
                        """UPDATE guild_identity.discord_users SET
                            username = $2,
                            display_name = $3,
                            highest_guild_role = $4,
                            all_guild_roles = $5,
                            last_sync = $6,
                            is_present = TRUE,
                            removed_at = NULL
                           WHERE discord_id = $1""",
                        discord_id,
                        member.name,
                        display,
                        highest_role,
                        all_roles,
                        now,
                    )
                    stats["updated"] += 1
                else:
                    await conn.execute(
                        """INSERT INTO guild_identity.discord_users
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
                """SELECT id, discord_id FROM guild_identity.discord_users
                   WHERE is_present = TRUE"""
            )

            for row in all_present:
                if row["discord_id"] not in current_ids:
                    await conn.execute(
                        """UPDATE guild_identity.discord_users SET
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


async def reconcile_player_ranks(
    pool: asyncpg.Pool,
    guild: Optional[discord.Guild],
) -> dict:
    """Scan all active players and ensure guild_rank_id reflects the correct rank.

    Priority:
      1. Highest guild_rank_id across all linked wow_characters (primary source of truth)
      2. discord_user.highest_guild_role name lookup (fallback — only if no characters)

    If the DB rank is wrong, it is updated and logged to player_action_log.
    If a Discord user is linked, their Discord roles are also corrected via the bot.

    Players with guild_rank_source = 'admin_override' are never touched.
    """
    stats = {"checked": 0, "db_updated": 0, "discord_updated": 0, "skipped": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    # Build a map of all guild role IDs so we can strip non-guild roles safely
    all_guild_role_ids: set[str] = set()

    async with pool.acquire() as conn:
        # Load all guild ranks
        ranks = await conn.fetch(
            "SELECT id, name, level, discord_role_id FROM common.guild_ranks ORDER BY level DESC"
        )
        rank_by_id: dict[int, dict] = {r["id"]: dict(r) for r in ranks}
        rank_by_name: dict[str, dict] = {r["name"].lower(): dict(r) for r in ranks}
        all_guild_role_ids = {r["discord_role_id"] for r in ranks if r["discord_role_id"]}

        # Load all active players with their best character rank and discord info in one query
        players = await conn.fetch(
            """
            SELECT
                p.id,
                p.display_name,
                p.guild_rank_id,
                p.guild_rank_source,
                du.discord_id,
                du.highest_guild_role,
                (
                    SELECT gr.id
                    FROM guild_identity.player_characters pc
                    JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
                    JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
                    WHERE pc.player_id = p.id
                    ORDER BY gr.level DESC
                    LIMIT 1
                ) AS best_char_rank_id
            FROM guild_identity.players p
            LEFT JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
            WHERE p.is_active = TRUE
            """
        )

        for row in players:
            stats["checked"] += 1
            player_id = row["id"]
            current_rank_id = row["guild_rank_id"]
            current_source = row["guild_rank_source"]

            # Never touch admin overrides
            if current_source == "admin_override":
                stats["skipped"] += 1
                continue

            # Determine correct rank using priority rules
            correct_rank: Optional[dict] = None
            correct_source: Optional[str] = None

            if row["best_char_rank_id"]:
                correct_rank = rank_by_id.get(row["best_char_rank_id"])
                correct_source = "wow_character"
            elif row["highest_guild_role"]:
                correct_rank = rank_by_name.get(row["highest_guild_role"].lower())
                correct_source = "discord_sync"

            if correct_rank is None:
                stats["skipped"] += 1
                continue

            correct_rank_id = correct_rank["id"]

            # --- Fix DB rank if wrong ---
            if current_rank_id != correct_rank_id:
                old_rank = rank_by_id.get(current_rank_id, {})

                await conn.execute(
                    """
                    UPDATE guild_identity.players
                    SET guild_rank_id = $2, guild_rank_source = $3, updated_at = $4
                    WHERE id = $1
                    """,
                    player_id, correct_rank_id, correct_source, now,
                )

                await conn.execute(
                    """
                    INSERT INTO guild_identity.player_action_log
                        (player_id, action, details, created_at)
                    VALUES ($1, 'rank_auto_corrected', $2::json, $3)
                    """,
                    player_id,
                    json.dumps({
                        "old_rank_id": current_rank_id,
                        "old_rank_name": old_rank.get("name"),
                        "new_rank_id": correct_rank_id,
                        "new_rank_name": correct_rank["name"],
                        "source": correct_source,
                    }),
                    now,
                )

                stats["db_updated"] += 1
                logger.info(
                    "Rank auto-corrected for player %d (%s): %s → %s (source: %s)",
                    player_id, row["display_name"],
                    old_rank.get("name"), correct_rank["name"], correct_source,
                )

            # --- Fix Discord role if wrong ---
            if not row["discord_id"] or not guild:
                continue

            target_discord_role_id = correct_rank.get("discord_role_id")
            if not target_discord_role_id:
                continue

            try:
                discord_member = guild.get_member(int(row["discord_id"]))
                if discord_member is None:
                    try:
                        discord_member = await guild.fetch_member(int(row["discord_id"]))
                    except discord.NotFound:
                        logger.warning(
                            "Discord member not found for player %d (%s)",
                            player_id, row["display_name"],
                        )
                        continue

                # Find which guild roles the member currently has
                member_guild_roles = [
                    r for r in discord_member.roles
                    if str(r.id) in all_guild_role_ids
                ]
                member_guild_role_ids = {str(r.id) for r in member_guild_roles}

                if member_guild_role_ids == {target_discord_role_id}:
                    continue  # Already correct

                # Remove wrong guild roles
                roles_to_remove = [r for r in member_guild_roles if str(r.id) != target_discord_role_id]
                if roles_to_remove:
                    await discord_member.remove_roles(*roles_to_remove, reason="Rank auto-correction")

                # Add correct guild role if missing
                if target_discord_role_id not in member_guild_role_ids:
                    target_role = guild.get_role(int(target_discord_role_id))
                    if target_role:
                        await discord_member.add_roles(target_role, reason="Rank auto-correction")

                stats["discord_updated"] += 1
                logger.info(
                    "Discord role corrected for player %d (%s): set to %s",
                    player_id, row["display_name"], correct_rank["name"],
                )

            except discord.Forbidden:
                logger.error(
                    "Missing permissions to update Discord roles for player %d", player_id
                )
                stats["errors"] += 1
            except Exception as exc:
                logger.error(
                    "Discord role update failed for player %d: %s", player_id, exc
                )
                stats["errors"] += 1

    logger.info(
        "Rank reconciliation: %d checked, %d DB updated, %d Discord updated, %d skipped, %d errors",
        stats["checked"], stats["db_updated"], stats["discord_updated"],
        stats["skipped"], stats["errors"],
    )
    return stats


async def on_member_join(pool: asyncpg.Pool, member: discord.Member):
    """Handle a new member joining the Discord server."""
    if member.bot:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.discord_users
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
            """UPDATE guild_identity.discord_users SET
                is_present = FALSE, removed_at = NOW()
               WHERE discord_id = $1""",
            str(member.id),
        )
    logger.info("Discord member left: %s (%s)", member.name, member.id)


async def on_member_update(pool: asyncpg.Pool, before: discord.Member, after: discord.Member):
    """Handle role changes or nickname changes."""
    if after.bot:
        return

    old_roles = get_all_guild_roles(before)
    new_roles = get_all_guild_roles(after)

    if old_roles != new_roles or before.nick != after.nick:
        highest = get_highest_guild_role(after)
        display = after.nick or after.display_name

        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.discord_users SET
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
