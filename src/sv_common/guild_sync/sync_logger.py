"""Helper for recording sync operations in the sync_log table."""

import logging
import time
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


class SyncLogEntry:
    """Context manager that logs a sync operation to guild_identity.sync_log."""

    def __init__(self, pool: asyncpg.Pool, source: str):
        self.pool = pool
        self.source = source
        self.log_id = None
        self.start_time = None
        self.stats = {}

    async def __aenter__(self):
        self.start_time = time.time()
        async with self.pool.acquire() as conn:
            self.log_id = await conn.fetchval(
                """INSERT INTO guild_identity.sync_log (source, status)
                   VALUES ($1, 'running') RETURNING id""",
                self.source,
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        status = "error" if exc_type else "success"
        error_msg = str(exc_val) if exc_val else None

        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.sync_log SET
                    status = $2,
                    characters_found = $3,
                    characters_updated = $4,
                    characters_new = $5,
                    characters_removed = $6,
                    error_message = $7,
                    duration_seconds = $8,
                    completed_at = $9
                   WHERE id = $1""",
                self.log_id,
                status,
                self.stats.get("found"),
                self.stats.get("updated"),
                self.stats.get("new"),
                self.stats.get("removed"),
                error_msg,
                duration,
                datetime.now(timezone.utc),
            )

        if exc_type:
            logger.error("Sync %s failed after %.1fs: %s", self.source, duration, exc_val)
        else:
            logger.info("Sync %s completed in %.1fs", self.source, duration)

        return False  # Don't suppress exceptions
