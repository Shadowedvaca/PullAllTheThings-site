"""
Drift scanner — detects data that *was* correct and is now *wrong*.

Drift is the gap between what the database believes and what the source of
truth (Discord roles, game state) now says.  These rules fire rarely
(a handful of times per month) and are narrowly targeted.

Note-based rules (note_mismatch, link_contradicts_note) have been retired.
Character ownership is now established via Battle.net OAuth, not guild notes.

Rules run in this scanner:
  1. duplicate_discord_link  — duplicate or stale Discord ↔ Player links (flag only)
"""

import logging

import asyncpg

from .integrity_checker import detect_duplicate_discord_links
from .mitigations import run_auto_mitigations

logger = logging.getLogger(__name__)

# Issue types that belong to the drift detection concept (used for UI grouping)
DRIFT_RULE_TYPES = frozenset(["duplicate_discord", "stale_discord_link"])


async def run_drift_scan(pool: asyncpg.Pool) -> dict:
    """
    Run all drift detection rules.  Auto-mitigate where configured.

    Called by:
    - Scheduler after every guild sync (Blizzard, addon, Discord)
    - Admin "Run Drift Scan" button
    - POST /admin/drift/scan

    Returns a summary dict with per-rule findings and mitigation counts.
    """
    async with pool.acquire() as conn:
        # Duplicate / stale discord links — creates 'error'/'info' issues
        discord_new = await detect_duplicate_discord_links(conn)

    # Run auto-mitigations (processes all pending auto-mitigate issues)
    mitigation_stats = await run_auto_mitigations(pool)

    logger.info(
        "Drift scan complete: %d discord issues — %d auto-mitigated",
        discord_new,
        mitigation_stats.get("resolved", 0),
    )

    return {
        "duplicate_discord": {"detected": discord_new},
        "total_new": discord_new,
        "auto_mitigated": mitigation_stats.get("resolved", 0),
        "mitigation_stats": mitigation_stats,
    }
