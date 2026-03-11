"""Guild Bot Guild Quote slash command — /quote posts a random guild quote."""

import logging

import asyncpg
import discord
from discord import app_commands

from sv_common.config_cache import get_accent_color_int, get_guild_name, is_guild_quotes_enabled

logger = logging.getLogger(__name__)


def register_guild_quote_commands(tree: app_commands.CommandTree, db_pool: asyncpg.Pool) -> None:
    """Register the /quote slash command on the given command tree.

    The command is only registered when enable_guild_quotes is TRUE in site_config.
    """
    if not is_guild_quotes_enabled():
        logger.info("Guild Quotes feature is disabled — /quote command not registered")
        return

    @tree.command(name="quote", description="Hear a random guild quote")
    async def guild_quote(interaction: discord.Interaction):
        try:
            async with db_pool.acquire() as conn:
                quote = await conn.fetchval(
                    "SELECT quote FROM patt.guild_quotes ORDER BY RANDOM() LIMIT 1"
                )
                title = await conn.fetchval(
                    "SELECT title FROM patt.guild_quote_titles ORDER BY RANDOM() LIMIT 1"
                )
        except Exception:
            logger.warning("Failed to fetch guild quote content", exc_info=True)
            await interaction.response.send_message(
                "No quotes available right now.", ephemeral=True
            )
            return

        if not quote and not title:
            await interaction.response.send_message(
                "No quotes have been added yet.", ephemeral=True
            )
            return

        embed = discord.Embed(
            description=f'*"{quote}"*' if quote else "",
            color=get_accent_color_int(),
        )
        embed.set_author(name=f"{get_guild_name()}{f', {title}' if title else ''}")
        embed.set_footer(text=get_guild_name())
        await interaction.response.send_message(embed=embed)
