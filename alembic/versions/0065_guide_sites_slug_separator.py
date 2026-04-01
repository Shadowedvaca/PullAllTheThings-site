"""add slug_separator to common.guide_sites for u.gg underscore support

Revision ID: 0065
Revises: 0064
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guide_sites",
        sa.Column(
            "slug_separator",
            sa.String(1),
            nullable=False,
            server_default="-",
        ),
        schema="common",
    )
    # u.gg (id=3) uses underscores for multi-word class/spec names
    op.execute("UPDATE common.guide_sites SET slug_separator = '_' WHERE id = 3")


def downgrade() -> None:
    op.drop_column("guide_sites", "slug_separator", schema="common")
