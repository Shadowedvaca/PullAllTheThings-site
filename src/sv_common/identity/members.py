"""Guild member management service functions."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildMember, GuildRank


async def get_all_members(db: AsyncSession) -> list[GuildMember]:
    result = await db.execute(
        select(GuildMember).order_by(GuildMember.discord_username)
    )
    return list(result.scalars().all())


async def get_member_by_id(db: AsyncSession, member_id: int) -> GuildMember | None:
    result = await db.execute(select(GuildMember).where(GuildMember.id == member_id))
    return result.scalar_one_or_none()


async def get_member_by_discord_id(
    db: AsyncSession, discord_id: str
) -> GuildMember | None:
    result = await db.execute(
        select(GuildMember).where(GuildMember.discord_id == discord_id)
    )
    return result.scalar_one_or_none()


async def get_member_by_discord_username(
    db: AsyncSession, username: str
) -> GuildMember | None:
    result = await db.execute(
        select(GuildMember).where(GuildMember.discord_username == username)
    )
    return result.scalar_one_or_none()


async def get_members_by_min_rank(
    db: AsyncSession, min_level: int
) -> list[GuildMember]:
    result = await db.execute(
        select(GuildMember)
        .join(GuildRank, GuildMember.rank_id == GuildRank.id)
        .where(GuildRank.level >= min_level)
    )
    return list(result.scalars().all())


async def create_member(
    db: AsyncSession,
    discord_username: str,
    discord_id: str | None = None,
    display_name: str | None = None,
    rank_id: int | None = None,
) -> GuildMember:
    if rank_id is None:
        result = await db.execute(select(GuildRank).where(GuildRank.level == 1))
        rank = result.scalar_one_or_none()
        if rank is None:
            raise ValueError("No Initiate rank (level 1) found — run seed first")
        rank_id = rank.id

    member = GuildMember(
        discord_username=discord_username,
        discord_id=discord_id,
        display_name=display_name,
        rank_id=rank_id,
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)
    return member


async def update_member(db: AsyncSession, member_id: int, **kwargs) -> GuildMember:
    result = await db.execute(select(GuildMember).where(GuildMember.id == member_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise ValueError(f"Member {member_id} not found")
    allowed = {
        "discord_username",
        "discord_id",
        "display_name",
        "rank_id",
        "rank_source",
        "registered_at",
        "last_seen_at",
    }
    for key, value in kwargs.items():
        if key in allowed:
            setattr(member, key, value)
    await db.flush()
    await db.refresh(member)
    return member


async def link_user_to_member(
    db: AsyncSession, member_id: int, user_id: int
) -> GuildMember:
    result = await db.execute(select(GuildMember).where(GuildMember.id == member_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise ValueError(f"Member {member_id} not found")
    member.user_id = user_id
    await db.flush()
    await db.refresh(member)
    return member


async def get_eligible_voters(
    db: AsyncSession, min_rank_level: int
) -> list[GuildMember]:
    """Return registered members (linked user account) at or above min_rank_level."""
    result = await db.execute(
        select(GuildMember)
        .join(GuildRank, GuildMember.rank_id == GuildRank.id)
        .where(GuildRank.level >= min_rank_level)
        .where(GuildMember.user_id.is_not(None))
    )
    return list(result.scalars().all())
