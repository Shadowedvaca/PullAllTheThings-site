"""
Base types for the matching rule system.

RuleResult    — what one rule produced in one pass
MatchingContext — shared state loaded once, refreshed between passes
MatchingRule  — Protocol defining the interface every rule must implement
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Protocol

if TYPE_CHECKING:
    import asyncpg


@dataclass
class RuleResult:
    """What a single rule produced in one pass."""

    rule_name: str
    players_created: int = 0
    chars_linked: int = 0
    discord_linked: int = 0
    stubs_created: int = 0   # players created without a Discord match
    skipped: int = 0          # chars with no note and no Discord match
    details: list[str] = field(default_factory=list)

    @property
    def changed_anything(self) -> bool:
        return (
            self.players_created > 0
            or self.chars_linked > 0
            or self.discord_linked > 0
        )


@dataclass
class MatchingContext:
    """Shared state for a matching run.  Loaded once, refreshed between passes."""

    # All active characters not yet linked to a player (shrinks between passes)
    unlinked_chars: list[dict]

    # All Discord users who have a guild role (keyed for fast lookup by rules)
    all_discord: list[dict]

    # discord_user_id → player_id cache (populated from DB; grows as rules create players)
    discord_player_cache: dict[int, int]

    # Characters grouped by guild-note key  (note_key → [char, ...])
    note_groups: dict[str, list[dict]]

    # Characters with no guild note (fall back to name matching)
    no_note_chars: list[dict]

    # Optional rank filter applied when loading unlinked_chars
    min_rank_level: Optional[int] = None


class MatchingRule(Protocol):
    """Interface every matching rule must satisfy."""

    name: str           # short identifier used in results & logs
    description: str    # human-readable summary
    link_source: str    # value stamped on player_characters.link_source
    order: int          # lower = runs first within a pass

    async def run(
        self,
        conn: "asyncpg.Connection",
        context: MatchingContext,
    ) -> RuleResult:
        """
        Execute this rule.  Read from context for shared state; write
        back to context.discord_player_cache when new players are created.

        Return a RuleResult describing what was found / created.
        """
        ...
