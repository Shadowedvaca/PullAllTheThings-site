"""Add guild_identity.raid_boss_counts — static boss count per raid/difficulty

Revision ID: 0078
Revises: 0077
Create Date: 2026-04-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use IF NOT EXISTS — prod DB already has this table from the old 0066 migration
    # that was renumbered to 0078 during the gear-plan merge.
    op.execute("""
        CREATE TABLE IF NOT EXISTS guild_identity.raid_boss_counts (
            raid_id    INTEGER NOT NULL,
            difficulty VARCHAR(20) NOT NULL,
            boss_count INTEGER NOT NULL,
            PRIMARY KEY (raid_id, difficulty)
        )
    """)


def downgrade() -> None:
    op.drop_table("raid_boss_counts", schema="guild_identity")
