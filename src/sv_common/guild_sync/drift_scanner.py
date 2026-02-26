"""
Drift scanner — detects data that *was* correct and is now *wrong*.

Drift is the gap between what the database believes and what the source of
truth (guild notes, Discord roles, game state) now says.  These rules fire
rarely (a handful of times per month) and are narrowly targeted.

Coverage gaps (unmatched characters, unmatched Discord users) are NOT drift.
They belong on the coverage dashboard.

Rules run in this scanner:
  1. note_mismatch           — guild note changed, link is now wrong (auto-mitigated)
  2. link_contradicts_note   — existing link disagrees with note (flag only)
  3. duplicate_discord_link  — duplicate or stale Discord ↔ Player links (flag only)
"""

import logging

import asyncpg

from .integrity_checker import (
    detect_duplicate_discord_links,
    detect_link_note_contradictions,
    detect_note_mismatch,
)
from .mitigations import run_auto_mitigations

logger = logging.getLogger(__name__)

# Issue types that belong to the drift detection concept (used for UI grouping)
DRIFT_RULE_TYPES = frozenset(
    ["note_mismatch", "link_contradicts_note", "duplicate_discord", "stale_discord_link"]
)


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
        # Rule 1: note_mismatch — creates 'warning' issues (auto-mitigated below)
        note_mismatch_new = await detect_note_mismatch(conn)

        # Rule 2: link_contradicts_note — creates 'info' issues (manual review)
        contradiction_new = await detect_link_note_contradictions(conn)

        # Rule 3: duplicate / stale discord links — creates 'error'/'info' issues
        discord_new = await detect_duplicate_discord_links(conn)

    # Run auto-mitigations (processes all pending auto-mitigate issues, including note_mismatch)
    mitigation_stats = await run_auto_mitigations(pool)

    total_new = note_mismatch_new + contradiction_new + discord_new

    logger.info(
        "Drift scan complete: %d note_mismatch, %d link_contradicts_note, "
        "%d discord issues — %d auto-mitigated",
        note_mismatch_new, contradiction_new, discord_new,
        mitigation_stats.get("resolved", 0),
    )

    return {
        "note_mismatch": {
            "detected": note_mismatch_new,
            "mitigated": mitigation_stats.get("resolved", 0),
        },
        "link_contradicts_note": {"detected": contradiction_new},
        "duplicate_discord": {"detected": discord_new},
        "total_new": total_new,
        "auto_mitigated": mitigation_stats.get("resolved", 0),
        "mitigation_stats": mitigation_stats,
    }
