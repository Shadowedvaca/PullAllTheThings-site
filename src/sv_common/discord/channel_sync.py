"""Discord channel sync.

Scrapes all channels from the Discord server into guild_identity.discord_channels.
Captures name, ID, type, category, position, NSFW flag, and role visibility.

Role visibility:
  is_public=True  → @everyone can view the channel
  is_public=False → @everyone is denied; visible_role_names lists which roles
                    have an explicit VIEW_CHANNEL grant
"""

import logging
from typing import TYPE_CHECKING

import asyncpg
import discord

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CHANNEL_TYPE_MAP = {
    discord.ChannelType.text:         "text",
    discord.ChannelType.voice:        "voice",
    discord.ChannelType.category:     "category",
    discord.ChannelType.forum:        "forum",
    discord.ChannelType.news:         "announcement",
    discord.ChannelType.stage_voice:  "stage",
}


async def sync_channels(pool: asyncpg.Pool, guild: discord.Guild) -> int:
    """Upsert all guild channels into discord_channels. Returns count synced."""
    rows = []

    for channel in guild.channels:
        type_str = _CHANNEL_TYPE_MAP.get(channel.type, str(channel.type).split(".")[-1])

        category = getattr(channel, "category", None)

        # Determine visibility: check @everyone's effective VIEW_CHANNEL permission
        everyone_perms = channel.permissions_for(guild.default_role)
        is_public = everyone_perms.view_channel

        visible_role_names = None
        if not is_public:
            # Collect roles that have explicit view access
            visible_role_names = [
                role.name
                for role in guild.roles
                if role != guild.default_role
                and channel.permissions_for(role).view_channel
            ]

        is_nsfw = getattr(channel, "is_nsfw", lambda: False)
        if callable(is_nsfw):
            is_nsfw = is_nsfw()

        rows.append((
            str(channel.id),
            channel.name,
            type_str,
            category.name if category else None,
            str(category.id) if category else None,
            channel.position,
            bool(is_nsfw),
            is_public,
            visible_role_names,
        ))

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO guild_identity.discord_channels
                (discord_channel_id, name, channel_type, category_name, category_id,
                 position, is_nsfw, is_public, visible_role_names, synced_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            ON CONFLICT (discord_channel_id) DO UPDATE SET
                name               = EXCLUDED.name,
                channel_type       = EXCLUDED.channel_type,
                category_name      = EXCLUDED.category_name,
                category_id        = EXCLUDED.category_id,
                position           = EXCLUDED.position,
                is_nsfw            = EXCLUDED.is_nsfw,
                is_public          = EXCLUDED.is_public,
                visible_role_names = EXCLUDED.visible_role_names,
                synced_at          = NOW()
            """,
            rows,
        )

        # Remove channels that were deleted from Discord
        current_ids = [r[0] for r in rows]
        await conn.execute(
            """
            DELETE FROM guild_identity.discord_channels
            WHERE discord_channel_id != ALL($1::varchar[])
            """,
            current_ids,
        )

    logger.info("Discord channel sync complete: %d channels", len(rows))
    return len(rows)
