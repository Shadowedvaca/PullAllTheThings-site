"""add wow_rank_index to guild_ranks

Revision ID: 0010
Revises: 0009
Create Date: 2026-02-24

Changes:
1. Add wow_rank_index (INTEGER, nullable, unique) to common.guild_ranks
   — Maps the WoW guild rank integer (from Blizzard API) to a platform rank.
   — Replaces the hardcoded RANK_NAME_MAP in blizzard_client.py.
2. Seed initial values based on PATT's WoW rank structure:
   WoW 0=Guild Leader, 1=Officer, 2=Veteran, 3=Member, 4=Initiate
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guild_ranks",
        sa.Column("wow_rank_index", sa.Integer(), nullable=True),
        schema="common",
    )
    op.create_unique_constraint(
        "uq_guild_ranks_wow_rank_index",
        "guild_ranks",
        ["wow_rank_index"],
        schema="common",
    )

    # Seed initial values: WoW rank integer → platform rank name
    # (standard WoW ordering: 0=GL, lower index = more access)
    # asyncpg requires one statement per op.execute() call
    op.execute("UPDATE common.guild_ranks SET wow_rank_index = 0 WHERE name = 'Guild Leader'")
    op.execute("UPDATE common.guild_ranks SET wow_rank_index = 1 WHERE name = 'Officer'")
    op.execute("UPDATE common.guild_ranks SET wow_rank_index = 2 WHERE name = 'Veteran'")
    op.execute("UPDATE common.guild_ranks SET wow_rank_index = 3 WHERE name = 'Member'")
    op.execute("UPDATE common.guild_ranks SET wow_rank_index = 4 WHERE name = 'Initiate'")


def downgrade() -> None:
    op.drop_constraint(
        "uq_guild_ranks_wow_rank_index", "guild_ranks", schema="common"
    )
    op.drop_column("guild_ranks", "wow_rank_index", schema="common")
