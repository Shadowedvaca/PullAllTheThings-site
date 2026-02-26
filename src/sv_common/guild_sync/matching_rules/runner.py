"""
Iterative matching rule runner.

Executes all registered rules in order.  If any rule produced changes,
the whole pass is repeated (context is refreshed from the DB first).
Stops when a full pass produces zero new links, or when max_passes is
reached.

A guild of 50-80 characters typically converges in 2-3 passes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

import asyncpg

from .base import MatchingContext, RuleResult
from . import get_registered_rules

logger = logging.getLogger(__name__)


async def build_context(
    pool: asyncpg.Pool,
    min_rank_level: Optional[int] = None,
) -> MatchingContext:
    """
    Load a fresh MatchingContext from the database.

    Called once before the first pass and again after each pass that
    produced changes, so the next pass sees the updated state.
    """
    from sv_common.guild_sync.identity_engine import _extract_note_key

    async with pool.acquire() as conn:
        # --- Unlinked characters ---
        if min_rank_level is not None:
            rows = await conn.fetch(
                """SELECT wc.id, wc.character_name, wc.guild_note, wc.officer_note,
                          wc.guild_rank_id
                   FROM guild_identity.wow_characters wc
                   JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
                   WHERE wc.removed_at IS NULL
                     AND gr.level >= $1
                     AND wc.id NOT IN (
                         SELECT character_id FROM guild_identity.player_characters
                     )""",
                min_rank_level,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, character_name, guild_note, officer_note, guild_rank_id
                   FROM guild_identity.wow_characters
                   WHERE removed_at IS NULL
                     AND id NOT IN (
                         SELECT character_id FROM guild_identity.player_characters
                     )"""
            )
        unlinked_chars = [dict(r) for r in rows]

        # --- Discord guild members ---
        discord_rows = await conn.fetch(
            """SELECT du.id, du.discord_id, du.username, du.display_name,
                      p.id AS player_id
               FROM guild_identity.discord_users du
               LEFT JOIN guild_identity.players p ON p.discord_user_id = du.id
               WHERE du.is_present = TRUE
                 AND du.highest_guild_role IS NOT NULL"""
        )
        all_discord = [dict(r) for r in discord_rows]

    # --- Build discord_player_cache from current DB state ---
    discord_player_cache: dict[int, int] = {}
    for du in all_discord:
        if du["player_id"]:
            discord_player_cache[du["id"]] = du["player_id"]

    # --- Group chars by note key ---
    note_groups: dict[str, list[dict]] = defaultdict(list)
    no_note_chars: list[dict] = []
    for char in unlinked_chars:
        key = _extract_note_key(char)
        if key:
            note_groups[key].append(char)
        else:
            no_note_chars.append(char)

    return MatchingContext(
        unlinked_chars=unlinked_chars,
        all_discord=all_discord,
        discord_player_cache=discord_player_cache,
        note_groups=dict(note_groups),
        no_note_chars=no_note_chars,
        min_rank_level=min_rank_level,
    )


async def run_matching_rules(
    pool: asyncpg.Pool,
    min_rank_level: Optional[int] = None,
    max_passes: int = 5,
) -> dict:
    """
    Execute all registered rules iteratively until convergence.

    Returns a combined stats dict compatible with callers that consumed
    the old run_matching() flat format, plus new per-rule and per-pass
    breakdowns.
    """
    rules = get_registered_rules()
    context = await build_context(pool, min_rank_level)

    # (pass_number, RuleResult) pairs — one entry per rule per pass
    pass_results: list[tuple[int, RuleResult]] = []
    pass_number = 0
    pass_changed = False  # track whether the LAST completed pass changed anything

    while pass_number < max_passes:
        pass_number += 1
        pass_changed = False

        for rule in rules:
            async with pool.acquire() as conn:
                result = await rule.run(conn, context)
                pass_results.append((pass_number, result))
                if result.changed_anything:
                    pass_changed = True

        if not pass_changed:
            break

        # Refresh state so next pass sees newly created players / links
        context = await build_context(pool, min_rank_level)

    # --- Aggregate totals ---
    all_results = [r for _, r in pass_results]
    totals = {
        "players_created": sum(r.players_created for r in all_results),
        "chars_linked": sum(r.chars_linked for r in all_results),
        "discord_linked": sum(r.discord_linked for r in all_results),
        "no_discord_match": sum(r.stubs_created for r in all_results),
        "skipped": sum(r.skipped for r in all_results),
    }

    converged = (not pass_changed) or (pass_number < max_passes)

    logger.info(
        "Matching complete: %d pass(es), converged=%s — "
        "%d players created, %d chars linked, %d with Discord, "
        "%d stubs, %d skipped",
        pass_number,
        converged,
        totals["players_created"],
        totals["chars_linked"],
        totals["discord_linked"],
        totals["no_discord_match"],
        totals["skipped"],
    )

    return {
        # New structured format
        "passes": pass_number,
        "converged": converged,
        "results": [
            {
                "pass": pn,
                "rule": r.rule_name,
                "players_created": r.players_created,
                "chars_linked": r.chars_linked,
                "discord_linked": r.discord_linked,
                "stubs_created": r.stubs_created,
                "skipped": r.skipped,
                "details": r.details,
            }
            for pn, r in pass_results
        ],
        "totals": totals,
        # Backward-compatible flat keys (same shape as old run_matching() return)
        **totals,
    }
