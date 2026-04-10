"""Add crafted_m_ilvl_threshold to site_config.

Revision ID: 0083
Revises: 0082
"""

from alembic import op
import sqlalchemy as sa

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "site_config",
        sa.Column("crafted_m_ilvl_threshold", sa.Integer(), nullable=True),
        schema="common",
    )


def downgrade() -> None:
    op.drop_column("site_config", "crafted_m_ilvl_threshold", schema="common")
