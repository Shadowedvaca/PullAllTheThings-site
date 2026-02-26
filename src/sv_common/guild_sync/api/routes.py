"""
FastAPI routes for the guild identity & sync system.

Mounted at /api/guild-sync/ and /api/identity/ on the main FastAPI app.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

guild_sync_router = APIRouter(prefix="/api/guild-sync", tags=["Guild Sync"])
identity_router = APIRouter(prefix="/api/identity", tags=["Identity"])


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class AddonUploadRequest(BaseModel):
    characters: list[dict]
    addon_version: str = "1.0"
    uploaded_by: str = "unknown"


class ManualLinkRequest(BaseModel):
    wow_character_id: int
    discord_user_id: int
    confirmed_by: str = "manual"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

async def get_db_pool(request: Request) -> asyncpg.Pool:
    """Retrieve the asyncpg pool stored on app state."""
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if pool is None:
        raise HTTPException(503, "Guild sync database pool not initialised")
    return pool


async def get_sync_scheduler(request: Request):
    """Retrieve the GuildSyncScheduler stored on app state."""
    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    if scheduler is None:
        raise HTTPException(503, "Guild sync scheduler not initialised")
    return scheduler


async def verify_addon_key(x_api_key: str = Header(None)):
    """Simple API key auth for addon uploads."""
    from patt.config import get_settings
    api_key = get_settings().patt_api_key
    if not api_key:
        raise HTTPException(500, "PATT_API_KEY not configured")
    if x_api_key != api_key:
        raise HTTPException(401, "Invalid API key")


# ---------------------------------------------------------------------------
# Guild Sync Routes
# ---------------------------------------------------------------------------

@guild_sync_router.post("/blizzard/trigger")
async def trigger_blizzard_sync(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """
    Manually trigger a full Blizzard API sync.
    Works with or without the scheduler — falls back to running directly
    if the scheduler is not initialised (no audit channel configured).
    """
    import asyncio
    import os

    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    if scheduler is not None:
        asyncio.create_task(scheduler.run_blizzard_sync())
        return {"ok": True, "status": "sync_triggered", "mode": "scheduler"}

    # Scheduler not available — run directly
    async def _run_direct():
        try:
            from sv_common.guild_sync.blizzard_client import BlizzardClient
            from sv_common.guild_sync.db_sync import sync_blizzard_roster
            from sv_common.guild_sync.sync_logger import SyncLogEntry

            client = BlizzardClient(
                client_id=os.environ["BLIZZARD_CLIENT_ID"],
                client_secret=os.environ["BLIZZARD_CLIENT_SECRET"],
            )
            await client.initialize()
            try:
                async with SyncLogEntry(pool, "blizzard_api") as log:
                    characters = await client.sync_full_roster()
                    stats = await sync_blizzard_roster(pool, characters)
                    log.stats = stats
                    logger.info("Direct Blizzard sync complete: %s", stats)
            finally:
                await client.close()
        except Exception as e:
            logger.error("Direct Blizzard sync failed: %s", e)

    asyncio.create_task(_run_direct())
    return {"ok": True, "status": "sync_triggered", "mode": "direct"}


@guild_sync_router.post("/discord/trigger")
async def trigger_discord_sync(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """
    Manually trigger a Discord member sync.
    Works without the full scheduler — only needs the running bot.
    """
    from sv_common.discord.bot import get_bot
    from sv_common.guild_sync.discord_sync import sync_discord_members
    from patt.config import get_settings
    import asyncio

    bot = get_bot()
    if not bot or bot.is_closed():
        raise HTTPException(503, "Discord bot is not running")

    settings = get_settings()
    if not settings.discord_guild_id:
        raise HTTPException(503, "DISCORD_GUILD_ID not configured")

    guild = bot.get_guild(int(settings.discord_guild_id))
    if not guild:
        raise HTTPException(503, "Bot cannot see the Discord guild — check DISCORD_GUILD_ID")

    async def _run():
        try:
            stats = await sync_discord_members(pool, guild)
            logger.info("Manual Discord sync complete: %s", stats)
        except Exception as e:
            logger.error("Manual Discord sync failed: %s", e)

    asyncio.create_task(_run())
    return {"ok": True, "status": "discord_sync_triggered", "guild": guild.name}


@guild_sync_router.post("/addon-upload", dependencies=[Depends(verify_addon_key)])
async def addon_upload(
    request: Request,
    payload: AddonUploadRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """
    Receive guild roster data from the WoW addon companion app.

    The companion app watches SavedVariables and POSTs here when new data
    is detected. Works with or without the full scheduler running.
    """
    if not payload.characters:
        raise HTTPException(400, "No character data provided")

    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)

    import asyncio
    if scheduler is not None:
        asyncio.create_task(scheduler.run_addon_sync(payload.characters))
    else:
        # Scheduler not running (no audit channel configured) — process directly
        asyncio.create_task(_process_addon_direct(pool, payload.characters))

    return {
        "ok": True,
        "status": "processing",
        "characters_received": len(payload.characters),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _process_addon_direct(pool: asyncpg.Pool, characters: list[dict]):
    """Process addon upload without a running scheduler (no Discord audit posts)."""
    try:
        from sv_common.guild_sync.db_sync import sync_addon_data
        from sv_common.guild_sync.integrity_checker import run_integrity_check
        from sv_common.guild_sync.mitigations import run_auto_mitigations
        from sv_common.guild_sync.sync_logger import SyncLogEntry
        async with SyncLogEntry(pool, "addon_upload") as log:
            stats = await sync_addon_data(pool, characters)
            log.stats = {"found": stats["processed"], "updated": stats["updated"]}
            await run_integrity_check(pool)
            await run_auto_mitigations(pool)
        logger.info("Addon upload processed: %s characters", len(characters))
    except Exception as e:
        logger.error("Addon upload processing failed: %s", e)


@guild_sync_router.get("/addon-upload/status")
async def addon_upload_status(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Get the timestamp of the last addon upload."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT completed_at FROM guild_identity.sync_log
               WHERE source = 'addon_upload' AND status = 'success'
               ORDER BY completed_at DESC LIMIT 1"""
        )
    return {"ok": True, "last_upload": row["completed_at"].isoformat() if row else None}


@guild_sync_router.get("/status")
async def sync_status(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Overall sync status — last successful run time for each source."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT ON (source) source, completed_at, status, error_message
               FROM guild_identity.sync_log
               WHERE status IN ('success', 'partial')
               ORDER BY source, completed_at DESC"""
        )
    result = {}
    for row in rows:
        result[row["source"]] = {
            "last_sync": row["completed_at"].isoformat() if row["completed_at"] else None,
            "status": row["status"],
        }
    return {"ok": True, "sources": result}


@guild_sync_router.post("/report/trigger")
async def trigger_report(
    scheduler=Depends(get_sync_scheduler),
):
    """Force a full integrity report to #audit-channel."""
    import asyncio
    asyncio.create_task(scheduler.trigger_full_report())
    return {"ok": True, "status": "report_triggered"}


@guild_sync_router.post("/crafting/trigger")
async def trigger_crafting_sync(
    request: Request,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Manually trigger a crafting professions sync (admin only)."""
    import asyncio
    import os

    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    if scheduler is not None:
        asyncio.create_task(scheduler.run_crafting_sync(force=True))
        return {"ok": True, "status": "crafting_sync_triggered", "mode": "scheduler"}

    # Scheduler not available — run directly
    async def _run_direct():
        try:
            from sv_common.guild_sync.blizzard_client import BlizzardClient
            from sv_common.guild_sync.crafting_sync import run_crafting_sync

            client = BlizzardClient(
                client_id=os.environ["BLIZZARD_CLIENT_ID"],
                client_secret=os.environ["BLIZZARD_CLIENT_SECRET"],
            )
            await client.initialize()
            try:
                stats = await run_crafting_sync(pool, client, force=True)
                logger.info("Direct crafting sync complete: %s", stats)
            finally:
                await client.close()
        except Exception as e:
            logger.error("Direct crafting sync failed: %s", e)

    asyncio.create_task(_run_direct())
    return {"ok": True, "status": "crafting_sync_triggered", "mode": "direct"}


# ---------------------------------------------------------------------------
# Matching trigger (synchronous — returns full per-rule results)
# ---------------------------------------------------------------------------

@guild_sync_router.post("/matching/trigger")
async def trigger_matching_sync(
    request: Request,
    min_rank_level: int | None = None,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """
    Run the iterative matching engine and return per-rule results.

    Unlike /api/identity/run-matching (which fires a background task),
    this endpoint awaits the full run and returns the structured results
    so the admin UI can display a per-rule breakdown.
    """
    from sv_common.guild_sync.identity_engine import run_matching

    try:
        stats = await run_matching(pool, min_rank_level=min_rank_level)
        logger.info(
            "Matching trigger complete (min_rank=%s): %d passes, converged=%s, "
            "%d players created, %d chars linked",
            min_rank_level,
            stats.get("passes", 1),
            stats.get("converged", True),
            stats.get("players_created", 0),
            stats.get("chars_linked", 0),
        )
        return {"ok": True, "data": stats}
    except Exception as e:
        logger.error("Matching trigger failed: %s", e)
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Identity Routes
# ---------------------------------------------------------------------------

@identity_router.post("/run-matching")
async def run_identity_matching(
    request: Request,
    min_rank_level: int | None = None,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """
    Run the identity matching engine to auto-link unlinked characters to players.

    Optional query param: min_rank_level (int) — restrict to characters at or above
    this guild rank level (e.g. 4 = Officers+). Omit to process all ranks.
    """
    from sv_common.guild_sync.identity_engine import run_matching
    import asyncio

    async def _run():
        try:
            stats = await run_matching(pool, min_rank_level=min_rank_level)
            logger.info("Manual identity matching complete (min_rank=%s): %s", min_rank_level, stats)
        except Exception as e:
            logger.error("Manual identity matching failed: %s", e)

    asyncio.create_task(_run())
    return {
        "ok": True,
        "status": "matching_started",
        "min_rank_level": min_rank_level,
    }


@identity_router.get("/players")
async def list_players(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """List all known players with their linked characters and Discord accounts."""
    import json
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.display_name, p.is_active,
                      du.username AS discord_username,
                      du.highest_guild_role,
                      COALESCE(json_agg(DISTINCT jsonb_build_object(
                          'id', wc.id,
                          'character_name', wc.character_name,
                          'realm_slug', wc.realm_slug,
                          'is_main', (wc.id = p.main_character_id)
                      )) FILTER (WHERE wc.id IS NOT NULL), '[]') AS characters
               FROM guild_identity.players p
               LEFT JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               LEFT JOIN guild_identity.player_characters pc ON pc.player_id = p.id
               LEFT JOIN guild_identity.wow_characters wc
                   ON wc.id = pc.character_id AND wc.removed_at IS NULL
               WHERE p.is_active = TRUE
               GROUP BY p.id, du.username, du.highest_guild_role
               ORDER BY p.display_name"""
        )
    players = []
    for row in rows:
        players.append({
            "id": row["id"],
            "display_name": row["display_name"],
            "discord_username": row["discord_username"],
            "highest_guild_role": row["highest_guild_role"],
            "characters": json.loads(row["characters"]) if isinstance(row["characters"], str) else row["characters"],
        })
    return {"ok": True, "players": players}


@identity_router.get("/orphans/wow")
async def orphan_wow_characters(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """WoW characters in the guild with no player link."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT wc.id, wc.character_name, wc.realm_slug,
                      wc.level, wc.item_level,
                      gr.name AS guild_rank_name,
                      cl.name AS class_name
               FROM guild_identity.wow_characters wc
               LEFT JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
               LEFT JOIN guild_identity.classes cl ON cl.id = wc.class_id
               WHERE wc.removed_at IS NULL
                 AND wc.id NOT IN (
                     SELECT character_id FROM guild_identity.player_characters
                 )
               ORDER BY gr.level DESC NULLS LAST, wc.character_name"""
        )
    return {"ok": True, "orphans": [dict(r) for r in rows]}


@identity_router.get("/orphans/discord")
async def orphan_discord_users(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Discord users with guild roles but no player link."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT du.id, du.discord_id, du.username, du.display_name,
                      du.highest_guild_role
               FROM guild_identity.discord_users du
               WHERE du.is_present = TRUE
                 AND du.highest_guild_role IS NOT NULL
                 AND du.id NOT IN (
                     SELECT discord_user_id FROM guild_identity.players
                     WHERE discord_user_id IS NOT NULL
                 )
               ORDER BY du.highest_guild_role, du.username"""
        )
    return {"ok": True, "orphans": [dict(r) for r in rows]}


@identity_router.get("/mismatches")
async def role_mismatches(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Role mismatches and other open audit issues."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, issue_type, severity, summary, details, created_at
               FROM guild_identity.audit_issues
               WHERE resolved_at IS NULL
                 AND issue_type IN ('role_mismatch', 'no_guild_role')
               ORDER BY severity DESC, created_at"""
        )
    return {"ok": True, "mismatches": [dict(r) for r in rows]}


@identity_router.post("/link")
async def create_manual_link(
    req: ManualLinkRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Manually link a WoW character to a Discord user, creating a player if needed."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify the WoW character exists
            char = await conn.fetchrow(
                """SELECT id, character_name
                   FROM guild_identity.wow_characters WHERE id = $1""",
                req.wow_character_id,
            )
            if not char:
                raise HTTPException(404, f"WoW character {req.wow_character_id} not found")

            # Verify the Discord user exists
            du = await conn.fetchrow(
                """SELECT id, username, display_name
                   FROM guild_identity.discord_users WHERE id = $1""",
                req.discord_user_id,
            )
            if not du:
                raise HTTPException(404, f"Discord user {req.discord_user_id} not found")

            # Find or create a player for this Discord user
            player_id = await conn.fetchval(
                """SELECT id FROM guild_identity.players WHERE discord_user_id = $1""",
                du["id"],
            )

            if not player_id:
                # Check if the character already belongs to a player
                player_id = await conn.fetchval(
                    """SELECT player_id FROM guild_identity.player_characters WHERE character_id = $1""",
                    char["id"],
                )

            if not player_id:
                # Create a new player
                display = du["display_name"] or du["username"]
                player_id = await conn.fetchval(
                    """INSERT INTO guild_identity.players (display_name, discord_user_id)
                       VALUES ($1, $2) RETURNING id""",
                    display, du["id"],
                )
            else:
                # Ensure this player's discord_user_id is set
                await conn.execute(
                    """UPDATE guild_identity.players SET discord_user_id = $1
                       WHERE id = $2 AND discord_user_id IS NULL""",
                    du["id"], player_id,
                )

            # Link the character
            await conn.execute(
                """INSERT INTO guild_identity.player_characters (player_id, character_id)
                   VALUES ($1, $2)
                   ON CONFLICT (character_id) DO UPDATE SET player_id = $1""",
                player_id, char["id"],
            )

    return {"ok": True, "status": "linked", "player_id": player_id}


@identity_router.delete("/character-link/{player_character_id}")
async def remove_character_link(
    player_character_id: int,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Remove a character from a player (delete a player_characters entry)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, player_id, character_id
               FROM guild_identity.player_characters WHERE id = $1""",
            player_character_id,
        )
        if not row:
            raise HTTPException(404, f"Character link {player_character_id} not found")

        await conn.execute(
            "DELETE FROM guild_identity.player_characters WHERE id = $1",
            player_character_id,
        )

    return {"ok": True, "status": "removed", "character_id": row["character_id"]}


# ---------------------------------------------------------------------------
# Discord Channel Routes
# ---------------------------------------------------------------------------

@guild_sync_router.get("/channels")
async def list_channels(pool: asyncpg.Pool = Depends(get_db_pool)):
    """Return all synced Discord channels, ordered by category then position."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT discord_channel_id, name, channel_type, category_name,
                      position, is_nsfw, is_public, visible_role_names, synced_at
               FROM guild_identity.discord_channels
               ORDER BY
                   COALESCE(category_name, name),
                   CASE channel_type WHEN 'category' THEN 0 ELSE 1 END,
                   position"""
        )
    return {"ok": True, "data": [dict(r) for r in rows]}


@guild_sync_router.post("/channels/sync")
async def trigger_channel_sync(request: Request, pool: asyncpg.Pool = Depends(get_db_pool)):
    """Manually re-sync Discord channels from the live server."""
    try:
        from sv_common.discord.bot import get_bot
        from sv_common.discord.channel_sync import sync_channels
        from patt.config import get_settings

        bot = get_bot()
        settings = get_settings()

        if not bot or bot.is_closed():
            raise HTTPException(503, "Discord bot is not running")
        if not settings.discord_guild_id:
            raise HTTPException(503, "DISCORD_GUILD_ID not configured")

        guild = bot.get_guild(int(settings.discord_guild_id))
        if not guild:
            raise HTTPException(503, "Guild not found — bot may not have joined yet")

        count = await sync_channels(pool, guild)
        return {"ok": True, "channels_synced": count}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Channel sync failed: %s", exc)
        raise HTTPException(500, f"Channel sync failed: {exc}")
