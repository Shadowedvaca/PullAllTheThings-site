"""Add is_deleted soft-delete flag to patt.raid_events

Revision ID: 0062
Revises: 0061
Create Date: 2026-03-26

Allows raid events to be soft-deleted so they are excluded from
attendance views and exports without losing the underlying data.
"""
from alembic import op
import sqlalchemy as sa

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raid_events",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        schema="patt",
    )


def downgrade() -> None:
    op.drop_column("raid_events", "is_deleted", schema="patt")
