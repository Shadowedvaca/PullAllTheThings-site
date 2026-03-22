"""drop current_wcl_zone_ids from patt.raid_seasons — derived at query time

Revision ID: 0058
Revises: 0057
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("raid_seasons", "current_wcl_zone_ids", schema="patt")


def downgrade() -> None:
    op.add_column(
        "raid_seasons",
        sa.Column("current_wcl_zone_ids", ARRAY(sa.Integer), nullable=True),
        schema="patt",
    )
