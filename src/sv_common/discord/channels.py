"""Discord channel posting utilities for PATT-Bot."""

import logging
from typing import Optional

import discord

logger = logging.getLogger(__name__)


async def post_embed_to_channel(
    bot: discord.Client,
    channel_id: str,
    embed: discord.Embed,
) -> Optional[str]:
    """Post an embed to a Discord channel. Returns the message ID or None on failure."""
    try:
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        msg = await channel.send(embed=embed)
        return str(msg.id)
    except Exception as exc:
        logger.error("Failed to post embed to channel %s: %s", channel_id, exc)
        return None


async def post_text_to_channel(
    bot: discord.Client,
    channel_id: str,
    content: str,
) -> Optional[str]:
    """Post plain text to a Discord channel. Returns the message ID or None on failure."""
    try:
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        msg = await channel.send(content=content)
        return str(msg.id)
    except Exception as exc:
        logger.error("Failed to post text to channel %s: %s", channel_id, exc)
        return None
