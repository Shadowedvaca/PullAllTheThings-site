"""
API routes for the Crafting Corner.

Mounted at /api/crafting/ on the main FastAPI app.
Public read endpoints + auth-required write endpoints.
"""

import logging
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from sv_common.guild_sync import crafting_service

logger = logging.getLogger(__name__)

crafting_router = APIRouter(prefix="/api/crafting", tags=["Crafting Corner"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def get_db_pool(request: Request) -> asyncpg.Pool:
    """Retrieve the asyncpg pool stored on app state."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if pool is None:
        raise HTTPException(503, "Database pool not available")
    return pool


async def _get_current_player_id(request: Request) -> Optional[int]:
    """Extract player_id from JWT cookie if present. Returns None if not logged in."""
    from patt.deps import get_page_member
    from sv_common.db.engine import get_session_factory
    from patt.config import get_settings

    settings = get_settings()
    factory = get_session_factory(settings.database_url)
    async with factory() as session:
        player = await get_page_member(request, session)
    return player.id if player else None


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class GuildOrderRequest(BaseModel):
    recipe_id: int
    message: str = ""


class CraftingPreferenceRequest(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Public Read Endpoints
# ---------------------------------------------------------------------------


@crafting_router.get("/professions")
async def list_professions(pool: asyncpg.Pool = Depends(get_db_pool)):
    """All professions that have at least one recipe, sorted alphabetically."""
    data = await crafting_service.get_profession_list(pool)
    return {"ok": True, "data": data}


@crafting_router.get("/expansions/{profession_id}")
async def list_expansions(
    profession_id: int,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """All expansion tiers for a profession, newest first."""
    data = await crafting_service.get_expansion_list(pool, profession_id)
    return {"ok": True, "data": data}


@crafting_router.get("/recipes/{profession_id}/{tier_id}")
async def list_recipes(
    profession_id: int,
    tier_id: int,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """All recipes for a profession+tier, alphabetical, with crafter counts."""
    data = await crafting_service.get_recipes_for_filter(pool, profession_id, tier_id)
    return {"ok": True, "data": data}


@crafting_router.get("/recipe/{recipe_id}/crafters")
async def get_crafters(
    recipe_id: int,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Crafters for a recipe, grouped by guild rank tier."""
    data = await crafting_service.get_recipe_crafters(pool, recipe_id)
    if data["recipe"] is None:
        raise HTTPException(404, "Recipe not found")
    return {"ok": True, "data": data}


@crafting_router.get("/search")
async def search_recipes(
    q: str = "",
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Full-text search across all recipes."""
    if len(q) < 2:
        return {"ok": True, "data": []}
    data = await crafting_service.search_recipes(pool, q)
    return {"ok": True, "data": data}


@crafting_router.get("/sync-status")
async def sync_status(pool: asyncpg.Pool = Depends(get_db_pool)):
    """Sync status: season name, last/next sync, cadence."""
    data = await crafting_service.get_sync_status(pool)
    return {"ok": True, "data": data}


# ---------------------------------------------------------------------------
# Auth-Required Endpoints
# ---------------------------------------------------------------------------


@crafting_router.post("/guild-order")
async def post_guild_order(
    request: Request,
    order: GuildOrderRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """
    Post a guild crafting order to the #crafters-corner Discord channel.

    Requirements:
    - User must be logged in (JWT cookie)
    - Recipe must exist
    """
    player_id = await _get_current_player_id(request)
    if player_id is None:
        raise HTTPException(401, "Login required to place a guild order")

    # Get requester's discord_id
    async with pool.acquire() as conn:
        requester = await conn.fetchrow(
            """SELECT p.id, du.discord_id, du.username
               FROM guild_identity.players p
               LEFT JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               WHERE p.id = $1""",
            player_id,
        )

    if not requester or not requester["discord_id"]:
        raise HTTPException(
            400,
            "You need a linked Discord account to place a guild order. "
            "Contact an officer to link your accounts.",
        )

    # Get recipe + crafter info
    crafter_data = await crafting_service.get_recipe_crafters(pool, order.recipe_id)
    if crafter_data["recipe"] is None:
        raise HTTPException(404, "Recipe not found")

    recipe = crafter_data["recipe"]
    all_crafters = [
        c for group in crafter_data["rank_groups"] for c in group["crafters"]
    ]

    # Post to Discord
    try:
        from sv_common.discord.bot import get_bot
        from patt.config import get_settings
        import discord

        bot = get_bot()
        settings = get_settings()
        channel_id = getattr(settings, "patt_crafters_corner_channel_id", None)

        if not bot or not channel_id:
            logger.warning("Discord bot not available or PATT_CRAFTERS_CORNER_CHANNEL_ID not set")
            return {"ok": True, "status": "queued", "note": "Discord not configured"}

        channel = bot.get_channel(int(channel_id))
        if not channel:
            logger.warning("Could not find #crafters-corner channel (id=%s)", channel_id)
            return {"ok": True, "status": "queued", "note": "Channel not found"}

        embed = discord.Embed(
            title=f"\U0001f528 Guild Order: {recipe['name']}",
            url=recipe["wowhead_url"],
            description=f"Requested by <@{requester['discord_id']}>",
            color=0xD4A84B,
        )

        if order.message:
            embed.add_field(name="Note", value=order.message, inline=False)

        crafter_names = ", ".join(c["character_name"] for c in all_crafters) or "None found"
        embed.add_field(name="Known Crafters", value=crafter_names, inline=False)
        embed.set_footer(text="View recipe on Wowhead \u2191 \u2022 Crafting Corner on pullallthethings.com")

        opted_in_mentions = " ".join(
            f"<@{c['player_discord_id']}>"
            for c in all_crafters
            if c.get("crafting_notifications_enabled") and c.get("player_discord_id")
        )
        content = opted_in_mentions if opted_in_mentions else None

        import asyncio
        asyncio.create_task(channel.send(content=content, embed=embed))

    except Exception as exc:
        logger.error("Guild order Discord post failed: %s", exc)
        # Don't fail the request — order was placed, Discord posting failed
        return {"ok": True, "status": "posted", "discord_note": "Discord post failed"}

    return {"ok": True, "status": "posted"}


@crafting_router.get("/preferences")
async def get_preferences(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get the logged-in user's crafting notification preference."""
    player_id = await _get_current_player_id(request)
    if player_id is None:
        raise HTTPException(401, "Login required")
    enabled = await crafting_service.get_player_crafting_preference(pool, player_id)
    return {"ok": True, "data": {"crafting_notifications_enabled": enabled}}


@crafting_router.post("/preferences")
async def update_preferences(
    request: Request,
    pref: CraftingPreferenceRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Update the logged-in user's crafting notification preference."""
    player_id = await _get_current_player_id(request)
    if player_id is None:
        raise HTTPException(401, "Login required")
    success = await crafting_service.set_player_crafting_preference(
        pool, player_id, pref.enabled
    )
    if not success:
        raise HTTPException(404, "Player not found")
    return {"ok": True, "data": {"crafting_notifications_enabled": pref.enabled}}


# ---------------------------------------------------------------------------
# Admin Endpoints
# ---------------------------------------------------------------------------


@crafting_router.get("/admin/config")
async def get_admin_config(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get full crafting sync config (admin only)."""
    player_id = await _get_current_player_id(request)
    if player_id is None:
        raise HTTPException(401, "Login required")

    data = await crafting_service.get_full_config(pool)
    return {"ok": True, "data": data}
