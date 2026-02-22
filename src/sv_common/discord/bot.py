"""PATT-Bot Discord client.

Provides the bot instance used throughout the application.
The bot is started as a background task during FastAPI lifespan.
"""

import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# Intents: members required for roster sync; message_content not needed
intents = discord.Intents.default()
intents.members = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info("PATT-Bot connected as %s (id=%s)", bot.user, bot.user.id)


async def start_bot(token: str) -> None:
    """Start the bot. Intended to be run as an asyncio background task."""
    await bot.start(token)


async def stop_bot() -> None:
    """Gracefully close the bot connection."""
    if not bot.is_closed():
        await bot.close()


def get_bot() -> commands.Bot:
    """Return the global bot instance."""
    return bot
