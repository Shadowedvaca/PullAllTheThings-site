"""feat: store crafters corner channel in crafting_sync_config

Replaces the PATT_CRAFTERS_CORNER_CHANNEL_ID env var with a DB column
so the channel can be configured via an admin dropdown and is never
buried in environment secrets.

Revision ID: 0021
Revises: 0020
Create Date: 2026-02-25
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE guild_identity.crafting_sync_config
        ADD COLUMN crafters_corner_channel_id VARCHAR(25)
        """
    )


def downgrade():
    op.execute(
        "ALTER TABLE guild_identity.crafting_sync_config DROP COLUMN crafters_corner_channel_id"
    )
