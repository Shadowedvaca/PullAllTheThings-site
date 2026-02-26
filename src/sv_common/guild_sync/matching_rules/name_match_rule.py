"""
Rule 2: Name Match Rule

For characters that have no guild note, attempts to match by character
name against Discord usernames / display names.  Only creates a player
when a Discord match is found — name-only characters without a Discord
match are left unlinked (the operator should add a guild note or link
manually).

This is a direct extraction of the no-note fallback that previously
lived in identity_engine.run_matching().
"""

import logging

from .base import MatchingContext, RuleResult

logger = logging.getLogger(__name__)


class NameMatchRule:
    name = "name_match"
    description = "Match characters (no guild note) by character name to Discord username"
    link_source = "exact_name"  # or "fuzzy_name" — attribution derives the right value
    order = 20

    async def run(self, conn, context: MatchingContext) -> RuleResult:
        from sv_common.guild_sync.identity_engine import (
            _create_player_group,
            _find_discord_for_key,
            normalize_name,
        )

        result = RuleResult(rule_name=self.name)
        stats: dict = {
            "players_created": 0,
            "chars_linked": 0,
            "discord_linked": 0,
            "no_discord_match": 0,
            "skipped": 0,
        }

        for char in context.no_note_chars:
            char_norm = normalize_name(char["character_name"])
            discord_user, match_type = _find_discord_for_key(
                char_norm, context.all_discord
            )
            if discord_user:
                await _create_player_group(
                    conn,
                    [char],
                    discord_user,
                    char_norm,
                    context.discord_player_cache,
                    stats,
                    match_type=match_type,
                    from_note=False,
                )
            else:
                stats["skipped"] += 1

        result.players_created = stats["players_created"]
        result.chars_linked = stats["chars_linked"]
        result.discord_linked = stats["discord_linked"]
        result.skipped = stats["skipped"]

        if result.changed_anything:
            logger.debug(
                "[name_match] %d players, %d chars linked (%d with Discord)",
                result.players_created,
                result.chars_linked,
                result.discord_linked,
            )

        return result
