"""Guild rank management service functions."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildRank, Player
from sqlalchemy.orm import selectinload


async def get_all_ranks(db: AsyncSession) -> list[GuildRank]:
    result = await db.execute(select(GuildRank).order_by(GuildRank.level))
    return list(result.scalars().all())


async def get_rank_by_level(db: AsyncSession, level: int) -> GuildRank | None:
    result = await db.execute(select(GuildRank).where(GuildRank.level == level))
    return result.scalar_one_or_none()


async def get_rank_by_name(db: AsyncSession, name: str) -> GuildRank | None:
    result = await db.execute(select(GuildRank).where(GuildRank.name == name))
    return result.scalar_one_or_none()


async def create_rank(
    db: AsyncSession,
    name: str,
    level: int,
    description: str | None = None,
    discord_role_id: str | None = None,
    scheduling_weight: int = 0,
) -> GuildRank:
    rank = GuildRank(
        name=name,
        level=level,
        description=description,
        discord_role_id=discord_role_id,
        scheduling_weight=scheduling_weight,
    )
    db.add(rank)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(f"Rank with that name or level already exists") from exc
    await db.refresh(rank)
    return rank


async def update_rank(db: AsyncSession, rank_id: int, **kwargs) -> GuildRank:
    result = await db.execute(select(GuildRank).where(GuildRank.id == rank_id))
    rank = result.scalar_one_or_none()
    if rank is None:
        raise ValueError(f"Rank {rank_id} not found")
    allowed = {"name", "level", "description", "discord_role_id", "scheduling_weight"}
    for key, value in kwargs.items():
        if key in allowed:
            setattr(rank, key, value)
    await db.flush()
    await db.refresh(rank)
    return rank


async def delete_rank(db: AsyncSession, rank_id: int) -> bool:
    result = await db.execute(select(GuildRank).where(GuildRank.id == rank_id))
    rank = result.scalar_one_or_none()
    if rank is None:
        return False
    await db.delete(rank)
    await db.flush()
    return True


async def member_meets_rank_requirement(
    db: AsyncSession, member_id: int, required_level: int
) -> bool:
    """Return True if the player's rank level >= required_level."""
    result = await db.execute(
        select(Player).options(selectinload(Player.guild_rank)).where(Player.id == member_id)
    )
    player = result.scalar_one_or_none()
    if player is None:
        return False
    if player.guild_rank is None:
        return False
    return player.guild_rank.level >= required_level
