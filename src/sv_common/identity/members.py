"""Player management service functions.

Replaces the old guild_members service — now operates on guild_identity.players.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sv_common.db.models import GuildRank, Player


async def get_all_players(db: AsyncSession) -> list[Player]:
    result = await db.execute(
        select(Player).order_by(Player.display_name)
    )
    return list(result.scalars().all())


async def get_player_by_id(db: AsyncSession, player_id: int) -> Player | None:
    result = await db.execute(select(Player).where(Player.id == player_id))
    return result.scalar_one_or_none()


async def get_player_by_user_id(db: AsyncSession, user_id: int) -> Player | None:
    result = await db.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.website_user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_player_by_discord_id(db: AsyncSession, discord_id: str) -> Player | None:
    from sv_common.db.models import DiscordUser
    result = await db.execute(
        select(Player)
        .join(DiscordUser, Player.discord_user_id == DiscordUser.id)
        .where(DiscordUser.discord_id == discord_id)
    )
    return result.scalar_one_or_none()


async def get_players_by_min_rank(
    db: AsyncSession, min_level: int
) -> list[Player]:
    result = await db.execute(
        select(Player)
        .join(GuildRank, Player.guild_rank_id == GuildRank.id)
        .where(GuildRank.level >= min_level)
    )
    return list(result.scalars().all())


async def create_player(
    db: AsyncSession,
    display_name: str,
    guild_rank_id: int | None = None,
    notes: str | None = None,
) -> Player:
    if guild_rank_id is None:
        result = await db.execute(select(GuildRank).where(GuildRank.level == 1))
        rank = result.scalar_one_or_none()
        if rank is None:
            raise ValueError("No Initiate rank (level 1) found — run seed first")
        guild_rank_id = rank.id

    player = Player(
        display_name=display_name,
        guild_rank_id=guild_rank_id,
        guild_rank_source="admin_override",
        notes=notes,
    )
    db.add(player)
    await db.flush()
    await db.refresh(player)
    return player


async def update_player(db: AsyncSession, player_id: int, **kwargs) -> Player:
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if player is None:
        raise ValueError(f"Player {player_id} not found")
    allowed = {
        "display_name",
        "guild_rank_id",
        "guild_rank_source",
        "main_character_id",
        "main_spec_id",
        "offspec_character_id",
        "offspec_spec_id",
        "is_active",
        "notes",
    }
    for key, value in kwargs.items():
        if key in allowed:
            setattr(player, key, value)
    await db.flush()
    await db.refresh(player)
    return player


async def link_user_to_player(
    db: AsyncSession, player_id: int, user_id: int
) -> Player:
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if player is None:
        raise ValueError(f"Player {player_id} not found")
    player.website_user_id = user_id
    await db.flush()
    await db.refresh(player)
    return player


async def get_eligible_voters(
    db: AsyncSession, min_rank_level: int
) -> list[Player]:
    """Return players with website accounts at or above min_rank_level."""
    result = await db.execute(
        select(Player)
        .join(GuildRank, Player.guild_rank_id == GuildRank.id)
        .where(GuildRank.level >= min_rank_level)
        .where(Player.website_user_id.is_not(None))
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Backward-compat aliases (used in many call sites; renamed gradually)
# ---------------------------------------------------------------------------

get_all_members = get_all_players
get_member_by_id = get_player_by_id
create_member = create_player
update_member = update_player
