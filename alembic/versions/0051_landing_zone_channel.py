"""add landing_zone_channel_id to discord_config

Revision ID: 0051
Revises: 0050
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discord_config",
        sa.Column("landing_zone_channel_id", sa.String(25), nullable=True),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("discord_config", "landing_zone_channel_id", schema="common")
