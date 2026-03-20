"""replace current_raid_name with current_raid_ids array on patt.raid_seasons

Revision ID: 0055
Revises: 0054
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("raid_seasons", "current_raid_name", schema="patt")
    op.add_column(
        "raid_seasons",
        sa.Column(
            "current_raid_ids",
            ARRAY(sa.Integer),
            nullable=True,
            server_default=None,
        ),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_column("raid_seasons", "current_raid_ids", schema="patt")
    op.add_column(
        "raid_seasons",
        sa.Column("current_raid_name", sa.String(100), nullable=True),
        schema="patt",
    )
