"""PATT-Bot Discord client.

Provides the bot instance used throughout the application.
The bot is started as a background task during FastAPI lifespan.
"""

import asyncio
import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# Intents: members required for roster sync; message_content not needed
intents = discord.Intents.default()
intents.members = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

# db_pool is set by the FastAPI lifespan after startup
_db_pool = None


def set_db_pool(pool):
    """Called from FastAPI lifespan to give the bot access to the DB pool."""
    global _db_pool
    _db_pool = pool


@bot.event
async def on_ready():
    logger.info("PATT-Bot connected as %s (id=%s)", bot.user, bot.user.id)

    # Register onboarding slash commands
    if _db_pool is not None:
        try:
            from sv_common.guild_sync.onboarding.commands import register_onboarding_commands
            register_onboarding_commands(bot.tree, _db_pool)
            await bot.tree.sync()
            logger.info("Onboarding slash commands registered")
        except Exception as e:
            logger.warning("Failed to register onboarding commands: %s", e)


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return

    pool = _db_pool
    if pool is None:
        logger.warning("on_member_join: db_pool not set, skipping sync for %s", member.name)
        return

    # Record/update the new member in discord_users
    try:
        from sv_common.guild_sync.discord_sync import on_member_join as sync_member_join
        await sync_member_join(pool, member)
    except Exception as e:
        logger.warning("on_member_join discord_sync failed for %s: %s", member.name, e)

    # Start onboarding conversation (gated by bot_dm_enabled internally)
    try:
        from sv_common.guild_sync.onboarding.conversation import OnboardingConversation
        conv = OnboardingConversation(bot, member, pool)
        asyncio.create_task(conv.start())
    except Exception as e:
        logger.warning("on_member_join onboarding start failed for %s: %s", member.name, e)


@bot.event
async def on_member_remove(member: discord.Member):
    if member.bot:
        return

    pool = _db_pool
    if pool is None:
        return

    try:
        from sv_common.guild_sync.discord_sync import on_member_remove as sync_member_remove
        await sync_member_remove(pool, member)
    except Exception as e:
        logger.warning("on_member_remove sync failed for %s: %s", member.name, e)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.bot:
        return

    pool = _db_pool
    if pool is None:
        return

    try:
        from sv_common.guild_sync.discord_sync import on_member_update as sync_member_update
        await sync_member_update(pool, before, after)
    except Exception as e:
        logger.warning("on_member_update sync failed for %s: %s", after.name, e)


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
