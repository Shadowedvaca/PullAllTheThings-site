"""Discord DM dispatch — send messages directly to guild members."""

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


async def is_bot_dm_enabled(pool: "asyncpg.Pool") -> bool:
    """
    Check whether the master DM switch is on.

    Reads common.discord_config.bot_dm_enabled.
    Returns False if not configured or if the flag is off.
    """
    async with pool.acquire() as conn:
        enabled = await conn.fetchval(
            "SELECT bot_dm_enabled FROM common.discord_config LIMIT 1"
        )
        return bool(enabled)


async def is_invite_dm_enabled(pool: "asyncpg.Pool") -> bool:
    """
    Check whether invite-code DMs are enabled.

    Requires both bot_dm_enabled AND feature_invite_dm to be TRUE.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT bot_dm_enabled, feature_invite_dm FROM common.discord_config LIMIT 1"
        )
        if not row:
            return False
        return bool(row["bot_dm_enabled"]) and bool(row["feature_invite_dm"])


async def is_onboarding_dm_enabled(pool: "asyncpg.Pool") -> bool:
    """
    Check whether onboarding conversation DMs are enabled.

    Requires both bot_dm_enabled AND feature_onboarding_dm to be TRUE.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT bot_dm_enabled, feature_onboarding_dm FROM common.discord_config LIMIT 1"
        )
        if not row:
            return False
        return bool(row["bot_dm_enabled"]) and bool(row["feature_onboarding_dm"])


_REGISTRATION_TEMPLATE = """\
Hey! You've been invited to register on the Pull All The Things guild platform.

Your registration code: **{code}**
Register here: {url}

This code expires in 72 hours. If you have any questions, ask Trog!
"""


async def send_registration_dm(
    bot: discord.Client,
    discord_id: str,
    invite_code: str,
    register_url: str,
) -> bool:
    """Send a registration DM to a Discord user.

    Returns True if sent successfully, False if DM failed
    (e.g. user has DMs disabled, or bot can't find the user).
    """
    try:
        user = await bot.fetch_user(int(discord_id))
        message = _REGISTRATION_TEMPLATE.format(code=invite_code, url=register_url)
        await user.send(message)
        logger.info("Registration DM sent to discord_id=%s", discord_id)
        return True
    except discord.Forbidden:
        logger.warning("DM forbidden for discord_id=%s (DMs disabled?)", discord_id)
        return False
    except discord.NotFound:
        logger.warning("User not found for discord_id=%s", discord_id)
        return False
    except Exception as exc:
        logger.error("Failed to send DM to discord_id=%s: %s", discord_id, exc)
        return False


# Alias used by admin_pages.py and other callers
send_invite_dm = send_registration_dm
