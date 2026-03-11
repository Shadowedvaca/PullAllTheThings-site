"""Shared helpers for the Settings nav (screen_permissions-driven sidebar)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import Player, ScreenPermission


async def load_nav_items(db: AsyncSession, player: Player | None) -> list[ScreenPermission]:
    """Return ScreenPermission rows the given player is allowed to see.

    Any logged-in player sees items where min_rank_level <= their rank level.
    If the player has no rank, they are treated as level 0 (sees nothing below
    my_profile, which has min_rank_level=1 — but see NOTE below).

    NOTE: My Profile (min_rank_level=1) is visible to any authenticated player
    regardless of rank because logging in implies at least being a registered
    user. We enforce this by treating no-rank as level 1 for nav purposes.
    """
    if player is None:
        return []

    rank_level = player.guild_rank.level if player.guild_rank else 1

    result = await db.execute(
        select(ScreenPermission)
        .where(ScreenPermission.min_rank_level <= rank_level)
        .order_by(ScreenPermission.category_order, ScreenPermission.nav_order)
    )
    return list(result.scalars().all())


async def get_min_rank_for_screen(db: AsyncSession, screen_key: str) -> int:
    """Look up the minimum rank level for a given screen. Defaults to 4 (Officer)."""
    result = await db.execute(
        select(ScreenPermission.min_rank_level).where(
            ScreenPermission.screen_key == screen_key
        )
    )
    level = result.scalar_one_or_none()
    return level if level is not None else 4
