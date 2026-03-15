"""
Rules registry for the data quality engine.

Each rule defines a detectable issue type with optional auto-mitigation.
Detection functions live in integrity_checker.py.
Mitigation functions live in mitigations.py.

Usage:
    from sv_common.guild_sync.rules import RULES
    for rule in RULES.values():
        print(rule.name, rule.auto_mitigate)
"""

from dataclasses import dataclass
from typing import Optional, Callable


@dataclass
class RuleDefinition:
    issue_type: str
    name: str
    description: str
    severity: str           # 'info', 'warning', 'error'
    auto_mitigate: bool
    mitigate_fn: Optional[Callable]  # async fn(pool, issue_row) -> bool


# Lazy wrappers to break the circular import:
# rules.py → mitigations.py → integrity_checker.py  (no cycle)
# The wrapper bodies are NOT executed at import time.

async def _wrap_orphan_wow(pool, issue_row) -> bool:
    from .mitigations import mitigate_orphan_wow
    return await mitigate_orphan_wow(pool, issue_row)


async def _wrap_orphan_discord(pool, issue_row) -> bool:
    from .mitigations import mitigate_orphan_discord
    return await mitigate_orphan_discord(pool, issue_row)


async def _wrap_role_mismatch(pool, issue_row) -> bool:
    from .mitigations import mitigate_role_mismatch
    return await mitigate_role_mismatch(pool, issue_row)


RULES: dict[str, RuleDefinition] = {
    "orphan_wow": RuleDefinition(
        issue_type="orphan_wow",
        name="Unlinked WoW Character",
        description=(
            "WoW character in the guild has no player record. "
            "Member should connect Battle.net or add the character manually from Settings."
        ),
        severity="warning",
        auto_mitigate=False,
        mitigate_fn=_wrap_orphan_wow,
    ),
    "orphan_discord": RuleDefinition(
        issue_type="orphan_discord",
        name="Unlinked Discord Member",
        description=(
            "Discord member has a guild role but no player record. "
            "Member should register on the website and connect Battle.net."
        ),
        severity="warning",
        auto_mitigate=False,
        mitigate_fn=_wrap_orphan_discord,
    ),
    "role_mismatch": RuleDefinition(
        issue_type="role_mismatch",
        name="Role Mismatch",
        description=(
            "Player's in-game rank doesn't match their Discord role. "
            "Discord bot action required to correct."
        ),
        severity="warning",
        auto_mitigate=False,
        mitigate_fn=_wrap_role_mismatch,
    ),
    "stale_character": RuleDefinition(
        issue_type="stale_character",
        name="Stale Character",
        description=(
            "WoW character hasn't logged in for more than 30 days. "
            "Informational only — resolves automatically when the character logs in."
        ),
        severity="info",
        auto_mitigate=False,
        mitigate_fn=None,
    ),
    "duplicate_discord": RuleDefinition(
        issue_type="duplicate_discord",
        name="Duplicate Discord Link",
        description=(
            "Two active players point to the same Discord account — an impossible state. "
            "Manual admin action required to determine the correct player record."
        ),
        severity="error",
        auto_mitigate=False,
        mitigate_fn=None,
    ),
    "stale_discord_link": RuleDefinition(
        issue_type="stale_discord_link",
        name="Stale Discord Link",
        description=(
            "A player is linked to a Discord account that is no longer in the server. "
            "Informational — the person may return. Resolves automatically if they rejoin."
        ),
        severity="info",
        auto_mitigate=False,
        mitigate_fn=None,
    ),
    "main_char_not_linked": RuleDefinition(
        issue_type="main_char_not_linked",
        name="Main/Offspec Pointer Orphaned",
        description=(
            "A player's main_character_id or offspec_character_id points to a character "
            "that is not in their player_characters bridge table. "
            "This should not be possible under normal operation. Manual admin action required."
        ),
        severity="error",
        auto_mitigate=False,
        mitigate_fn=None,
    ),
}
