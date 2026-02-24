"""Discord role sync — keeps player ranks in sync with Discord roles.

Discord is the source of truth. When Mike promotes someone in Discord,
the next sync picks it up and updates their platform rank.
"""

import logging
from datetime import datetime, timezone

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sv_common.db.models import DiscordConfig, DiscordUser, GuildRank, Player

logger = logging.getLogger(__name__)


async def sync_discord_roles(
    bot: discord.Client,
    session_factory: async_sessionmaker,
    guild_discord_id: str,
) -> dict:
    """Sync Discord roles to platform ranks for all guild members.

    Algorithm:
    1. Fetch all members from the Discord guild
    2. For each member, find or create a DiscordUser record
    3. Update DiscordUser fields (username, display_name, is_present, last_sync)
    4. If a Player is linked to the DiscordUser, update their guild_rank_id
       if the best matching rank has changed

    Returns a summary dict: {updated, created, skipped, errors}
    """
    guild = bot.get_guild(int(guild_discord_id))
    if guild is None:
        try:
            guild = await bot.fetch_guild(int(guild_discord_id))
        except discord.NotFound:
            logger.error("Discord guild %s not found", guild_discord_id)
            return {"updated": 0, "created": 0, "skipped": 0, "errors": 1}

    stats = {"updated": 0, "created": 0, "skipped": 0, "errors": 0}

    async with session_factory() as db:
        # Load all platform ranks that have a discord_role_id mapping
        ranks_result = await db.execute(
            select(GuildRank).where(GuildRank.discord_role_id.isnot(None))
        )
        mapped_ranks: list[GuildRank] = list(ranks_result.scalars().all())
        role_id_to_rank: dict[str, GuildRank] = {
            r.discord_role_id: r for r in mapped_ranks if r.discord_role_id
        }

        try:
            members = guild.members or await guild.fetch_members(limit=None).flatten()
        except Exception:
            members = [m async for m in guild.fetch_members(limit=None)]

        for discord_member in members:
            if discord_member.bot:
                continue

            discord_id = str(discord_member.id)
            discord_role_ids = {str(r.id) for r in discord_member.roles}

            # Find the highest-level matching rank
            matching_ranks = [
                rank for role_id, rank in role_id_to_rank.items()
                if role_id in discord_role_ids
            ]
            best_rank = max(matching_ranks, key=lambda r: r.level) if matching_ranks else None

            try:
                # Find or create DiscordUser
                du_result = await db.execute(
                    select(DiscordUser).where(DiscordUser.discord_id == discord_id)
                )
                discord_user = du_result.scalar_one_or_none()

                if discord_user is None:
                    # New Discord member — create DiscordUser record
                    discord_user = DiscordUser(
                        discord_id=discord_id,
                        username=str(discord_member),
                        display_name=discord_member.display_name,
                        is_present=True,
                        last_sync=datetime.now(timezone.utc),
                    )
                    db.add(discord_user)
                    await db.flush()
                    stats["created"] += 1
                    logger.info(
                        "Created DiscordUser for discord_id=%s",
                        discord_id,
                    )
                else:
                    # Update existing DiscordUser presence info
                    discord_user.username = str(discord_member)
                    discord_user.display_name = discord_member.display_name
                    discord_user.is_present = True
                    discord_user.last_sync = datetime.now(timezone.utc)

                # Update linked Player's rank if applicable
                if discord_user.id and best_rank is not None:
                    player_result = await db.execute(
                        select(Player).where(Player.discord_user_id == discord_user.id)
                    )
                    player = player_result.scalar_one_or_none()

                    if player is not None and player.guild_rank_id != best_rank.id:
                        old_rank_id = player.guild_rank_id
                        player.guild_rank_id = best_rank.id
                        player.guild_rank_source = "discord_sync"
                        stats["updated"] += 1
                        logger.info(
                            "Updated rank for discord_id=%s: rank_id %s → %s",
                            discord_id,
                            old_rank_id,
                            best_rank.id,
                        )
                    else:
                        stats["skipped"] += 1
                else:
                    stats["skipped"] += 1

            except Exception as exc:
                logger.error("Error syncing discord_id=%s: %s", discord_id, exc)
                stats["errors"] += 1

        # Update last_role_sync_at in discord_config
        config_result = await db.execute(
            select(DiscordConfig).where(DiscordConfig.guild_discord_id == guild_discord_id)
        )
        config = config_result.scalar_one_or_none()
        if config:
            config.last_role_sync_at = datetime.now(timezone.utc)

        await db.commit()

    logger.info("Role sync complete: %s", stats)
    return stats
