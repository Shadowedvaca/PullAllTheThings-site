"""feat: discord_channels reference table

Stores all text, voice, and category channels scraped from the Discord
server by the bot. Provides a reference for channel IDs when configuring
bot features, and shows role-visibility restrictions.

Revision ID: 0020
Revises: 0019
Create Date: 2026-02-25
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE guild_identity.discord_channels (
            id                  SERIAL PRIMARY KEY,
            discord_channel_id  VARCHAR(25) NOT NULL UNIQUE,
            name                VARCHAR(100) NOT NULL,
            channel_type        VARCHAR(20) NOT NULL,   -- text, voice, category, forum, announcement, stage
            category_name       VARCHAR(100),
            category_id         VARCHAR(25),
            position            INTEGER DEFAULT 0,
            is_nsfw             BOOLEAN DEFAULT FALSE,
            is_public           BOOLEAN DEFAULT TRUE,   -- FALSE = @everyone denied view_channel
            visible_role_names  TEXT[],                 -- roles with view access when not public
            synced_at           TIMESTAMPTZ DEFAULT NOW(),
            created_at          TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE guild_identity.discord_channels")
