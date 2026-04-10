"""Drop crafted_m_ilvl_threshold from site_config.

Replaced by _CRAFTED_TRACK_IDS bonus-ID discovery in quality_track.py.
The ilvl threshold was a manual fallback for detecting crafted item quality;
the bonus-ID map derives the track directly from game data without hardcoding
ilvl ranges that change every season.

Revision ID: 0084
Revises: 0083
"""

from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("site_config", "crafted_m_ilvl_threshold", schema="common")


def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column(
        "site_config",
        sa.Column("crafted_m_ilvl_threshold", sa.Integer(), nullable=True),
        schema="common",
    )
