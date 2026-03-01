"""PATT-Bot Mito slash command — /mito posts a random Mito quote + title."""

import logging

import asyncpg
import discord
from discord import app_commands

logger = logging.getLogger(__name__)

PATT_GOLD = 0xD4A84B


def register_mito_commands(tree: app_commands.CommandTree, db_pool: asyncpg.Pool) -> None:
    """Register the /mito slash command on the given command tree."""

    @tree.command(name="mito", description="Hear some wisdom from Mito")
    async def mito(interaction: discord.Interaction):
        try:
            async with db_pool.acquire() as conn:
                quote = await conn.fetchval(
                    "SELECT quote FROM patt.mito_quotes ORDER BY RANDOM() LIMIT 1"
                )
                title = await conn.fetchval(
                    "SELECT title FROM patt.mito_titles ORDER BY RANDOM() LIMIT 1"
                )
        except Exception:
            logger.warning("Failed to fetch Mito content", exc_info=True)
            await interaction.response.send_message(
                "Mito is unavailable for wisdom right now.", ephemeral=True
            )
            return

        if not quote and not title:
            await interaction.response.send_message(
                "Mito hasn't shared any wisdom yet.", ephemeral=True
            )
            return

        embed = discord.Embed(
            description=f'*"{quote}"*' if quote else "",
            color=PATT_GOLD,
        )
        embed.set_author(name=f"Mito, {title}" if title else "Mito")
        embed.set_footer(text="Pull All The Things • Sen'jin")
        await interaction.response.send_message(embed=embed)
