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
    discord_member_id: int
    confirmed_by: str = "manual"


class LinkConfirmRequest(BaseModel):
    link_id: int
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
    scheduler=Depends(get_sync_scheduler),
):
    """Manually trigger a full Blizzard API sync."""
    import asyncio
    asyncio.create_task(scheduler.run_blizzard_sync())
    return {"ok": True, "status": "sync_triggered"}


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
        from sv_common.guild_sync.db_sync import sync_addon_data
        from sv_common.guild_sync.matching import run_matching
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
        from sv_common.guild_sync.matching import run_matching
        from sv_common.guild_sync.scheduler import SyncLogEntry
        async with SyncLogEntry(pool, "addon_upload") as log:
            stats = await sync_addon_data(pool, characters)
            log.stats = {"found": stats["processed"], "updated": stats["updated"]}
            await run_matching(pool)
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


# ---------------------------------------------------------------------------
# Identity Routes
# ---------------------------------------------------------------------------

@identity_router.get("/persons")
async def list_persons(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """List all known persons with their linked characters and Discord accounts."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.display_name, p.is_active,
                      COALESCE(json_agg(DISTINCT jsonb_build_object(
                          'id', wc.id, 'character_name', wc.character_name,
                          'realm_slug', wc.realm_slug, 'character_class', wc.character_class,
                          'guild_rank_name', wc.guild_rank_name, 'is_main', wc.is_main
                      )) FILTER (WHERE wc.id IS NOT NULL), '[]') AS characters,
                      COALESCE(json_agg(DISTINCT jsonb_build_object(
                          'id', dm.id, 'username', dm.username,
                          'display_name', dm.display_name,
                          'highest_guild_role', dm.highest_guild_role
                      )) FILTER (WHERE dm.id IS NOT NULL), '[]') AS discord_accounts
               FROM guild_identity.persons p
               LEFT JOIN guild_identity.wow_characters wc
                   ON wc.person_id = p.id AND wc.removed_at IS NULL
               LEFT JOIN guild_identity.discord_members dm
                   ON dm.person_id = p.id AND dm.is_present = TRUE
               WHERE p.is_active = TRUE
               GROUP BY p.id
               ORDER BY p.display_name"""
        )
    import json
    persons = []
    for row in rows:
        persons.append({
            "id": row["id"],
            "display_name": row["display_name"],
            "characters": json.loads(row["characters"]) if isinstance(row["characters"], str) else row["characters"],
            "discord_accounts": json.loads(row["discord_accounts"]) if isinstance(row["discord_accounts"], str) else row["discord_accounts"],
        })
    return {"ok": True, "persons": persons}


@identity_router.get("/orphans/wow")
async def orphan_wow_characters(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """WoW characters in the guild with no Discord link."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, character_name, realm_slug, character_class,
                      guild_rank_name, active_spec, level, item_level
               FROM guild_identity.wow_characters
               WHERE person_id IS NULL AND removed_at IS NULL
               ORDER BY guild_rank, character_name"""
        )
    return {"ok": True, "orphans": [dict(r) for r in rows]}


@identity_router.get("/orphans/discord")
async def orphan_discord_members(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Discord members with guild roles but no WoW character link."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, discord_id, username, display_name, highest_guild_role
               FROM guild_identity.discord_members
               WHERE person_id IS NULL
                 AND is_present = TRUE
                 AND highest_guild_role IS NOT NULL
               ORDER BY highest_guild_role, username"""
        )
    return {"ok": True, "orphans": [dict(r) for r in rows]}


@identity_router.get("/mismatches")
async def role_mismatches(
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Role mismatches and other open audit issues."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, issue_type, severity, summary, details,
                      first_detected, last_detected
               FROM guild_identity.audit_issues
               WHERE resolved_at IS NULL
                 AND issue_type IN ('role_mismatch', 'no_guild_role')
               ORDER BY severity DESC, first_detected"""
        )
    return {"ok": True, "mismatches": [dict(r) for r in rows]}


@identity_router.post("/link")
async def create_manual_link(
    req: ManualLinkRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Manually link a WoW character to a Discord member."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify both exist
            char = await conn.fetchrow(
                "SELECT id, character_name, person_id FROM guild_identity.wow_characters WHERE id = $1",
                req.wow_character_id,
            )
            dm = await conn.fetchrow(
                "SELECT id, username, person_id FROM guild_identity.discord_members WHERE id = $1",
                req.discord_member_id,
            )

            if not char:
                raise HTTPException(404, f"WoW character {req.wow_character_id} not found")
            if not dm:
                raise HTTPException(404, f"Discord member {req.discord_member_id} not found")

            # Use existing person or create new one
            if dm["person_id"]:
                person_id = dm["person_id"]
            elif char["person_id"]:
                person_id = char["person_id"]
            else:
                person_id = await conn.fetchval(
                    "INSERT INTO guild_identity.persons (display_name) VALUES ($1) RETURNING id",
                    dm["username"],
                )

            # Link character
            await conn.execute(
                "UPDATE guild_identity.wow_characters SET person_id = $1 WHERE id = $2",
                person_id, char["id"],
            )
            await conn.execute(
                """INSERT INTO guild_identity.identity_links
                   (person_id, wow_character_id, link_source, confidence, is_confirmed, confirmed_by, confirmed_at)
                   VALUES ($1, $2, 'manual', 'high', TRUE, $3, NOW())
                   ON CONFLICT (wow_character_id) DO UPDATE
                   SET person_id = $1, link_source = 'manual', confidence = 'high',
                       is_confirmed = TRUE, confirmed_by = $3, confirmed_at = NOW()""",
                person_id, char["id"], req.confirmed_by,
            )

            # Link Discord member
            await conn.execute(
                "UPDATE guild_identity.discord_members SET person_id = $1 WHERE id = $2",
                person_id, dm["id"],
            )
            await conn.execute(
                """INSERT INTO guild_identity.identity_links
                   (person_id, discord_member_id, link_source, confidence, is_confirmed, confirmed_by, confirmed_at)
                   VALUES ($1, $2, 'manual', 'high', TRUE, $3, NOW())
                   ON CONFLICT (discord_member_id) DO UPDATE
                   SET person_id = $1, link_source = 'manual', confidence = 'high',
                       is_confirmed = TRUE, confirmed_by = $3, confirmed_at = NOW()""",
                person_id, dm["id"], req.confirmed_by,
            )

    return {"ok": True, "status": "linked", "person_id": person_id}


@identity_router.post("/confirm")
async def confirm_link(
    req: LinkConfirmRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Confirm an auto-suggested link."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, is_confirmed FROM guild_identity.identity_links WHERE id = $1",
            req.link_id,
        )
        if not row:
            raise HTTPException(404, f"Link {req.link_id} not found")

        await conn.execute(
            """UPDATE guild_identity.identity_links
               SET is_confirmed = TRUE, confirmed_by = $2, confirmed_at = NOW()
               WHERE id = $1""",
            req.link_id, req.confirmed_by,
        )
    return {"ok": True, "status": "confirmed"}


@identity_router.delete("/link/{link_id}")
async def remove_link(
    link_id: int,
    pool: asyncpg.Pool = Depends(get_db_pool),
):
    """Remove an incorrect identity link."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, person_id, wow_character_id, discord_member_id FROM guild_identity.identity_links WHERE id = $1",
            link_id,
        )
        if not row:
            raise HTTPException(404, f"Link {link_id} not found")

        # Clear person_id from the linked entity
        if row["wow_character_id"]:
            await conn.execute(
                "UPDATE guild_identity.wow_characters SET person_id = NULL WHERE id = $1",
                row["wow_character_id"],
            )
        if row["discord_member_id"]:
            await conn.execute(
                "UPDATE guild_identity.discord_members SET person_id = NULL WHERE id = $1",
                row["discord_member_id"],
            )

        await conn.execute("DELETE FROM guild_identity.identity_links WHERE id = $1", link_id)

    return {"ok": True, "status": "removed"}
