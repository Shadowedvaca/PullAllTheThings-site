"""add current_wcl_zone_ids to patt.raid_seasons

Revision ID: 0057
Revises: 0056
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raid_seasons",
        sa.Column(
            "current_wcl_zone_ids",
            ARRAY(sa.Integer),
            nullable=True,
            server_default=None,
        ),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_column("raid_seasons", "current_wcl_zone_ids", schema="patt")
