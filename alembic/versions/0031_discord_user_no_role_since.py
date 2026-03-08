"""Add no_guild_role_since to discord_users

Tracks when a Discord member's last guild role was removed.
Used by the roleless-member prune job to kick members who have
had no guild role for more than the configured threshold.

Revision ID: 0031
Revises: 0030
Create Date: 2026-03-08
"""

from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "discord_users",
        sa.Column("no_guild_role_since", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="guild_identity",
    )
    # Backfill: any currently-present member with no guild role gets stamped now
    op.execute(
        """
        UPDATE guild_identity.discord_users
        SET no_guild_role_since = NOW()
        WHERE is_present = TRUE AND highest_guild_role IS NULL
        """
    )


def downgrade():
    op.drop_column("discord_users", "no_guild_role_since", schema="guild_identity")
