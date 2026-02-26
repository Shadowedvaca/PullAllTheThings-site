"""
Rule 1: Note Group Rule

Groups unlinked characters by guild-note key and creates one player per
group.  If a Discord user is found for the key, that player is linked to
Discord.  If not, a stub player is created (can be linked manually later).

This is a direct extraction of the note-group processing loop that
previously lived in identity_engine.run_matching().
"""

import logging

from .base import MatchingContext, RuleResult

logger = logging.getLogger(__name__)


class NoteGroupRule:
    name = "note_group"
    description = "Group characters by guild note key, find Discord user for each group"
    link_source = "note_key"  # or "note_key_stub" for stubs — attribution derives the right value
    order = 10

    async def run(self, conn, context: MatchingContext) -> RuleResult:
        from sv_common.guild_sync.identity_engine import (
            _create_player_group,
            _find_discord_for_key,
        )

        result = RuleResult(rule_name=self.name)
        stats: dict = {
            "players_created": 0,
            "chars_linked": 0,
            "discord_linked": 0,
            "no_discord_match": 0,
            "skipped": 0,
        }

        for note_key, chars in context.note_groups.items():
            discord_user, match_type = _find_discord_for_key(
                note_key, context.all_discord
            )
            await _create_player_group(
                conn,
                chars,
                discord_user,
                note_key,
                context.discord_player_cache,
                stats,
                match_type=match_type,
                from_note=True,
            )

        result.players_created = stats["players_created"]
        result.chars_linked = stats["chars_linked"]
        result.discord_linked = stats["discord_linked"]
        result.stubs_created = stats["no_discord_match"]

        if result.changed_anything:
            logger.debug(
                "[note_group] %d players, %d chars linked (%d with Discord, %d stubs)",
                result.players_created,
                result.chars_linked,
                result.discord_linked,
                result.stubs_created,
            )

        return result
