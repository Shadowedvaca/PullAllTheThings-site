"""Guild Bot Discord client.

Provides the bot instance used throughout the application.
The bot is started as a background task during FastAPI lifespan.
"""

import asyncio
import logging

import discord
from discord.ext import commands

from sv_common.config_cache import get_accent_color_int, get_guild_name

logger = logging.getLogger(__name__)

# Intents: members required for roster sync; voice_states for attendance tracking
intents = discord.Intents.default()
intents.members = True
intents.message_content = False
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# db_pool is set by the FastAPI lifespan after startup
_db_pool = None


def set_db_pool(pool):
    """Called from FastAPI lifespan to give the bot access to the DB pool."""
    global _db_pool
    _db_pool = pool


@bot.event
async def on_ready():
    logger.info("Guild Bot connected as %s (id=%s)", bot.user, bot.user.id)

    from guild_portal.config import get_settings
    settings = get_settings()
    discord_guild = None
    guild_id_str = settings.discord_guild_id
    # DB value (set via Admin → Bot Settings) takes precedence over env var
    if _db_pool is not None:
        try:
            async with _db_pool.acquire() as _conn:
                db_guild_id = await _conn.fetchval(
                    "SELECT guild_discord_id FROM common.discord_config LIMIT 1"
                )
            if db_guild_id:
                guild_id_str = db_guild_id
        except Exception:
            pass
    if guild_id_str:
        discord_guild = bot.get_guild(int(guild_id_str))

    # Register slash commands — guild-scoped so they appear instantly
    if _db_pool is not None:
        try:
            from sv_common.guild_sync.onboarding.commands import register_onboarding_commands
            register_onboarding_commands(bot.tree, _db_pool)
            from guild_portal.bot.guild_quote_commands import _async_register_guild_quote_commands
            await _async_register_guild_quote_commands(bot.tree, _db_pool)
            if discord_guild:
                bot.tree.copy_global_to(guild=discord_guild)
                await bot.tree.sync(guild=discord_guild)
                logger.info("Slash commands synced to guild %s", discord_guild.name)
            else:
                await bot.tree.sync()
                logger.info("Slash commands synced globally (guild ID not configured)")
        except Exception as e:
            logger.warning("Failed to register slash commands: %s", e)

    # Sync Discord channel list to DB
    if _db_pool is not None and discord_guild is not None:
        try:
            from sv_common.discord.channel_sync import sync_channels
            await sync_channels(_db_pool, discord_guild)
        except Exception as e:
            logger.warning("Channel sync on_ready failed: %s", e)

    # Register VoiceAttendanceCog if attendance tracking is enabled
    if _db_pool is not None:
        try:
            async with _db_pool.acquire() as _conn:
                _att_enabled = await _conn.fetchval(
                    "SELECT attendance_feature_enabled FROM common.discord_config LIMIT 1"
                )
            if _att_enabled:
                from sv_common.discord.voice_attendance import VoiceAttendanceCog
                if not bot.cogs.get("VoiceAttendanceCog"):
                    await bot.add_cog(VoiceAttendanceCog(bot, _db_pool))
                    logger.info("VoiceAttendanceCog loaded — attendance tracking active")
            else:
                logger.debug("Voice attendance tracking disabled — cog not loaded")
        except Exception as e:
            logger.warning("Failed to load VoiceAttendanceCog: %s", e)


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

    # Start onboarding conversation (gated by enable_onboarding flag and bot_dm_enabled)
    try:
        from sv_common.config_cache import is_onboarding_enabled
        if is_onboarding_enabled():
            from sv_common.guild_sync.onboarding.conversation import OnboardingConversation
            conv = OnboardingConversation(bot, member, pool)
            asyncio.create_task(conv.start())
        else:
            logger.debug("Onboarding disabled — skipping for %s", member.name)
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


@bot.event
async def on_message(message: discord.Message):
    """Respond to DMs with a help message listing available commands.

    Suppressed when the user is mid-onboarding conversation — their reply
    belongs to the wait_for in the conversation flow, not to this handler.
    """
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return

    # Don't fire while the user is actively answering onboarding questions
    _ACTIVE_ONBOARDING_STATES = {"asked_in_guild", "asked_main", "asked_alts"}
    if _db_pool is not None:
        try:
            async with _db_pool.acquire() as conn:
                state = await conn.fetchval(
                    "SELECT state FROM guild_identity.onboarding_sessions WHERE discord_id = $1",
                    str(message.author.id),
                )
            if state in _ACTIVE_ONBOARDING_STATES:
                return
        except Exception:
            pass  # DB not available — fall through and show help

    embed = discord.Embed(
        title=f"{get_guild_name()} Bot",
        description="Here's what I can do for you:",
        color=get_accent_color_int(),
    )
    embed.add_field(
        name="/get-account",
        value="Get your website invite code or log in link.",
        inline=False,
    )
    embed.add_field(
        name="/resetpassword",
        value="Reset your website password. A temporary password will be DMed to you.",
        inline=False,
    )
    embed.set_footer(text=get_guild_name())
    await message.channel.send(embed=embed)


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


async def sync_quote_commands_from_admin() -> None:
    """Re-register and sync quote commands — called from the admin API endpoint.

    Resolves the guild from DB/settings (same logic as on_ready) so the sync
    is always guild-scoped when a guild ID is configured.
    """
    if _db_pool is None:
        raise RuntimeError("DB pool not available — bot not started")

    from guild_portal.config import get_settings
    settings = get_settings()
    guild_id_str = settings.discord_guild_id
    try:
        async with _db_pool.acquire() as _conn:
            db_guild_id = await _conn.fetchval(
                "SELECT guild_discord_id FROM common.discord_config LIMIT 1"
            )
        if db_guild_id:
            guild_id_str = db_guild_id
    except Exception:
        pass

    discord_guild = None
    if guild_id_str:
        discord_guild = bot.get_guild(int(guild_id_str))

    from guild_portal.bot.guild_quote_commands import sync_quote_commands
    await sync_quote_commands(bot.tree, _db_pool, discord_guild)
