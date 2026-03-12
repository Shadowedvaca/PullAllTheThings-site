"""Phase 4.1: Add encrypted credential columns for setup wizard

Revision ID: 0033
Revises: 0032
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "discord_config",
        sa.Column("bot_token_encrypted", sa.Text(), nullable=True),
        schema="common",
    )
    op.add_column(
        "site_config",
        sa.Column("blizzard_client_id", sa.String(100), nullable=True),
        schema="common",
    )
    op.add_column(
        "site_config",
        sa.Column("blizzard_client_secret_encrypted", sa.Text(), nullable=True),
        schema="common",
    )


def downgrade():
    op.drop_column("site_config", "blizzard_client_secret_encrypted", schema="common")
    op.drop_column("site_config", "blizzard_client_id", schema="common")
    op.drop_column("discord_config", "bot_token_encrypted", schema="common")
