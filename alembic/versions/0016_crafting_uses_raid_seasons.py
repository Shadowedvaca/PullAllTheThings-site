"""use raid_seasons for crafting sync cadence

Revision ID: 0016
Revises: 0015
Create Date: 2026-02-25

Phase 2.8 (revised) — Crafting Corner season integration
- Add is_new_expansion to patt.raid_seasons (replaces crafting_sync_config.is_first_season)
- Drop redundant season fields from guild_identity.crafting_sync_config:
    expansion_name, season_number, season_start_date, is_first_season
  These are now sourced directly from patt.raid_seasons.
- Keep cadence_override_until for admin manual overrides.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_new_expansion to patt.raid_seasons
    op.add_column(
        "raid_seasons",
        sa.Column(
            "is_new_expansion",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        schema="patt",
    )

    # Drop redundant season fields from crafting_sync_config
    op.drop_column("crafting_sync_config", "expansion_name", schema="guild_identity")
    op.drop_column("crafting_sync_config", "season_number", schema="guild_identity")
    op.drop_column("crafting_sync_config", "season_start_date", schema="guild_identity")
    op.drop_column("crafting_sync_config", "is_first_season", schema="guild_identity")


def downgrade() -> None:
    op.add_column(
        "crafting_sync_config",
        sa.Column("is_first_season", sa.Boolean(), server_default="false", nullable=False),
        schema="guild_identity",
    )
    op.add_column(
        "crafting_sync_config",
        sa.Column("season_start_date", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "crafting_sync_config",
        sa.Column("season_number", sa.Integer(), nullable=True),
        schema="guild_identity",
    )
    op.add_column(
        "crafting_sync_config",
        sa.Column("expansion_name", sa.String(50), nullable=True),
        schema="guild_identity",
    )
    op.drop_column("raid_seasons", "is_new_expansion", schema="patt")
