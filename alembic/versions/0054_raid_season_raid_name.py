"""add current_raid_name to patt.raid_seasons

Revision ID: 0054
Revises: 0053
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raid_seasons",
        sa.Column("current_raid_name", sa.String(100), nullable=True),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_column("raid_seasons", "current_raid_name", schema="patt")
