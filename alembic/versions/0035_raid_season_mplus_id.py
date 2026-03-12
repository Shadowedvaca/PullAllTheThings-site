"""Phase 4.3 follow-up: add blizzard_mplus_season_id to patt.raid_seasons

Revision ID: 0035
Revises: 0034
Create Date: 2026-03-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "raid_seasons",
        sa.Column("blizzard_mplus_season_id", sa.Integer, nullable=True),
        schema="patt",
    )


def downgrade():
    op.drop_column("raid_seasons", "blizzard_mplus_season_id", schema="patt")
