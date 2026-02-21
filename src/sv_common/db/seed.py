"""Seed data loader — inserts default guild ranks on first startup."""

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuildRank

logger = logging.getLogger(__name__)

RANKS_FILE = Path(__file__).parent.parent.parent.parent / "data" / "seed" / "ranks.json"


async def seed_ranks(session: AsyncSession) -> None:
    """Insert default guild ranks if the table is empty."""
    result = await session.execute(select(GuildRank).limit(1))
    if result.scalar_one_or_none() is not None:
        return

    with open(RANKS_FILE) as f:
        ranks_data = json.load(f)

    for rank_data in ranks_data:
        rank = GuildRank(
            name=rank_data["name"],
            level=rank_data["level"],
            description=rank_data.get("description"),
        )
        session.add(rank)

    await session.commit()
    logger.info("Seeded %d guild ranks", len(ranks_data))
