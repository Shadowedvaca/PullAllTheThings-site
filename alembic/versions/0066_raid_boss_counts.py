"""Add guild_identity.raid_boss_counts — static boss count per raid/difficulty

Revision ID: 0066
Revises: 0065
Create Date: 2026-04-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raid_boss_counts",
        sa.Column("raid_id", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=False),
        sa.Column("boss_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("raid_id", "difficulty"),
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_table("raid_boss_counts", schema="guild_identity")
