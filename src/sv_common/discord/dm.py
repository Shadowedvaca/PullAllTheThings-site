"""Discord DM dispatch — send messages directly to guild members."""

import logging

import discord

logger = logging.getLogger(__name__)

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
