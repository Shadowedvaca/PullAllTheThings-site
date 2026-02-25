"""feat: move raid defaults and audit channel into discord_config

Adds configurable fields to common.discord_config so timezone, default
start time, default duration, and audit channel are set via the admin UI
rather than hardcoded in templates or buried in environment variables.

Revision ID: 0022
Revises: 0021
Create Date: 2026-02-25
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE common.discord_config
            ADD COLUMN IF NOT EXISTS audit_channel_id VARCHAR(25),
            ADD COLUMN IF NOT EXISTS raid_event_timezone VARCHAR(50)
                DEFAULT 'America/New_York',
            ADD COLUMN IF NOT EXISTS raid_default_start_time VARCHAR(5)
                DEFAULT '21:00',
            ADD COLUMN IF NOT EXISTS raid_default_duration_minutes INTEGER
                DEFAULT 120
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE common.discord_config
            DROP COLUMN IF EXISTS audit_channel_id,
            DROP COLUMN IF EXISTS raid_event_timezone,
            DROP COLUMN IF EXISTS raid_default_start_time,
            DROP COLUMN IF EXISTS raid_default_duration_minutes
        """
    )
