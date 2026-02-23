"""add preferred_role to guild_members

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-23

Adds a nullable `preferred_role` field to common.guild_members.
Officers can set this to override the in-game role shown on the roster.
Values: tank | healer | melee_dps | ranged_dps  (or NULL = use main char role)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guild_members",
        sa.Column("preferred_role", sa.String(20), nullable=True),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("guild_members", "preferred_role", schema="common")
