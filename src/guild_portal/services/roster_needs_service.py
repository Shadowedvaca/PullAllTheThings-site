"""Shared roster composition rules used by recruiting and the spec wheel."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ROLE_TARGETS = {
    "Tank": 2,
    "Healer": 4,
    "Melee DPS": 7,
    "Ranged DPS": 7,
}
DPS_TOTAL_TARGET = 14


def calculate_open_role_needs(current_counts: dict[str, int]) -> dict[str, int]:
    """Return role deficits while keeping the combined DPS target at 14."""
    effective_targets = dict(ROLE_TARGETS)
    melee = current_counts.get("Melee DPS", 0)
    ranged = current_counts.get("Ranged DPS", 0)
    if melee > ROLE_TARGETS["Melee DPS"]:
        effective_targets["Ranged DPS"] = max(0, DPS_TOTAL_TARGET - melee)
    elif ranged > ROLE_TARGETS["Ranged DPS"]:
        effective_targets["Melee DPS"] = max(0, DPS_TOTAL_TARGET - ranged)

    return {
        role: target - current_counts.get(role, 0)
        for role, target in effective_targets.items()
        if current_counts.get(role, 0) < target
    }


async def get_open_role_needs(db: AsyncSession) -> dict[str, int]:
    """Count established members' active in-guild mains against role targets."""
    rows = await db.execute(
        text(
            """
            SELECT r.name AS role_name, COUNT(p.id) AS cnt
            FROM guild_identity.players p
            JOIN guild_identity.wow_characters wc ON wc.id = p.main_character_id
            JOIN ref.specializations s
              ON s.id = COALESCE(p.main_spec_id, wc.active_spec_id)
            JOIN guild_identity.roles r ON r.id = s.default_role_id
            JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
            WHERE p.is_active = TRUE
              AND p.on_raid_hiatus IS NOT TRUE
              AND gr.level > 1
              AND wc.in_guild = TRUE
            GROUP BY r.name
            """
        )
    )
    return calculate_open_role_needs(
        {row.role_name: int(row.cnt) for row in rows}
    )


async def get_represented_main_spec_ids(db: AsyncSession) -> set[int]:
    """Return main specs represented by established active roster members."""
    rows = await db.execute(
        text(
            """
            SELECT DISTINCT COALESCE(p.main_spec_id, wc.active_spec_id) AS spec_id
            FROM guild_identity.players p
            JOIN guild_identity.wow_characters wc ON wc.id = p.main_character_id
            JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
            WHERE p.is_active = TRUE
              AND p.on_raid_hiatus IS NOT TRUE
              AND gr.level > 1
              AND wc.in_guild = TRUE
              AND COALESCE(p.main_spec_id, wc.active_spec_id) IS NOT NULL
            """
        )
    )
    return {int(row.spec_id) for row in rows}
