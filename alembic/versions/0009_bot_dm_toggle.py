"""phase 2.6: add bot_dm_enabled to discord_config

Revision ID: 0009
Revises: 0008
Create Date: 2026-02-23

Changes:
1. Add bot_dm_enabled (BOOLEAN, default FALSE) to common.discord_config
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discord_config",
        sa.Column(
            "bot_dm_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("discord_config", "bot_dm_enabled", schema="common")
