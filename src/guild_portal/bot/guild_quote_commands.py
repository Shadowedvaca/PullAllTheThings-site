"""Guild Bot Guild Quote slash commands — Phase 4.8 Quotes 2.0.

Registers one slash command per active quote subject plus a random /quote command.
Commands are re-registered via sync_quote_commands() when the admin changes subjects.
"""

import logging

import asyncpg
import discord
from discord import app_commands

from sv_common.config_cache import (
    get_accent_color_int,
    get_guild_name,
    get_realm_display_name,
    is_guild_quotes_enabled,
)

logger = logging.getLogger(__name__)

# Reserved Discord slash command names that cannot be used as slugs
_RESERVED_SLUGS = {
    "quote", "help", "me", "info", "ping", "stats", "settings",
    "admin", "mod", "bot", "server", "role", "user",
}


def _register_subject_command(
    tree: app_commands.CommandTree,
    db_pool: asyncpg.Pool,
    subject_id: int,
    command_slug: str,
    display_name: str,
) -> None:
    """Register a single slash command for one quote subject."""

    @tree.command(name=command_slug, description=f"Hear a quote from {display_name}")
    async def subject_quote(interaction: discord.Interaction):
        await _send_subject_quote(interaction, db_pool, subject_id, display_name)

    subject_quote.__name__ = command_slug  # keep logging readable


async def _send_subject_quote(
    interaction: discord.Interaction,
    db_pool: asyncpg.Pool,
    subject_id: int,
    display_name: str,
) -> None:
    """Fetch and send a random quote for a specific subject."""
    try:
        async with db_pool.acquire() as conn:
            quote = await conn.fetchval(
                "SELECT quote FROM patt.guild_quotes "
                "WHERE subject_id = $1 ORDER BY RANDOM() LIMIT 1",
                subject_id,
            )
            title = await conn.fetchval(
                "SELECT title FROM patt.guild_quote_titles "
                "WHERE subject_id = $1 ORDER BY RANDOM() LIMIT 1",
                subject_id,
            )
    except Exception:
        logger.warning("Failed to fetch quote for subject_id=%s", subject_id, exc_info=True)
        await interaction.response.send_message(
            "Could not fetch a quote right now.", ephemeral=True
        )
        return

    if not quote:
        await interaction.response.send_message(
            f"No quotes have been added for {display_name} yet.", ephemeral=True
        )
        return

    embed = discord.Embed(
        description=f'*"{quote}"*',
        color=get_accent_color_int(),
    )
    author_name = display_name
    if title:
        author_name = f"{display_name}, {title}"
    embed.set_author(name=author_name)
    realm = get_realm_display_name()
    footer = get_guild_name()
    if realm:
        footer = f"{get_guild_name()} \u2022 {realm}"
    embed.set_footer(text=footer)
    await interaction.response.send_message(embed=embed)


def register_guild_quote_commands(
    tree: app_commands.CommandTree, db_pool: asyncpg.Pool
) -> None:
    """Synchronous registration wrapper called from on_ready.

    Schedules the async registration as a coroutine to be awaited by the caller.
    Because on_ready is async and already awaits this block, we use a helper that
    returns a coroutine that on_ready will await via asyncio.ensure_future or direct await.
    """
    import asyncio

    if not is_guild_quotes_enabled():
        logger.info("Guild Quotes feature is disabled — quote commands not registered")
        return

    # Schedule async registration; on_ready calls this synchronously so we
    # create a task to run after the event loop is running.
    asyncio.ensure_future(_async_register_guild_quote_commands(tree, db_pool))


async def _async_register_guild_quote_commands(
    tree: app_commands.CommandTree, db_pool: asyncpg.Pool
) -> None:
    """Async implementation: register /quote + per-subject commands from DB."""
    if not is_guild_quotes_enabled():
        return

    try:
        async with db_pool.acquire() as conn:
            subjects = await conn.fetch(
                "SELECT id, command_slug, display_name "
                "FROM patt.quote_subjects WHERE active = TRUE"
            )
    except Exception:
        logger.warning("Failed to load quote subjects for command registration", exc_info=True)
        subjects = []

    # Register /quote — picks a random active subject
    @tree.command(name="quote", description="Hear a random guild quote")
    async def random_quote(interaction: discord.Interaction):
        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, display_name FROM patt.quote_subjects "
                    "WHERE active = TRUE ORDER BY RANDOM() LIMIT 1"
                )
        except Exception:
            logger.warning("Failed to pick random quote subject", exc_info=True)
            row = None

        if not row:
            await interaction.response.send_message(
                "No quotes available right now.", ephemeral=True
            )
            return

        await _send_subject_quote(interaction, db_pool, row["id"], row["display_name"])

    # Register one command per active subject (skip if slug conflicts with /quote or reserved)
    for subj in subjects:
        slug = subj["command_slug"]
        if slug == "quote" or slug in _RESERVED_SLUGS:
            logger.warning("Skipping reserved/conflicting slug '%s' for subject registration", slug)
            continue
        try:
            _register_subject_command(
                tree, db_pool, subj["id"], slug, subj["display_name"]
            )
            logger.debug("Registered /%s for subject '%s'", slug, subj["display_name"])
        except Exception:
            logger.warning("Failed to register command /%s", slug, exc_info=True)

    logger.info(
        "Guild quote commands registered: /quote + %d subject command(s)",
        len([s for s in subjects if s["command_slug"] not in _RESERVED_SLUGS and s["command_slug"] != "quote"]),
    )


async def sync_quote_commands(
    tree: app_commands.CommandTree,
    db_pool: asyncpg.Pool,
    discord_guild: discord.Guild | None,
) -> None:
    """Re-register all quote commands from DB and sync to Discord.

    Called from the admin 'Sync Bot Commands' endpoint.
    Removes all existing subject-named commands first to avoid duplicates.
    """
    if not is_guild_quotes_enabled():
        return

    # Remove existing subject commands (everything except built-in bot commands)
    # We do this by clearing commands registered by this module, identified by
    # fetching current slugs from DB (active + inactive) and removing those from tree.
    try:
        async with db_pool.acquire() as conn:
            all_slugs = await conn.fetch(
                "SELECT command_slug FROM patt.quote_subjects"
            )
    except Exception:
        logger.warning("Failed to fetch slugs for sync cleanup", exc_info=True)
        all_slugs = []

    for row in all_slugs:
        slug = row["command_slug"]
        # Remove from global tree
        tree.remove_command(slug)

    # Also remove /quote to re-register fresh
    tree.remove_command("quote")

    # Re-register
    await _async_register_guild_quote_commands(tree, db_pool)

    # Sync to Discord
    if discord_guild:
        tree.copy_global_to(guild=discord_guild)
        await tree.sync(guild=discord_guild)
        logger.info("Quote commands synced to guild %s", discord_guild.name)
    else:
        await tree.sync()
        logger.info("Quote commands synced globally (no guild configured)")
