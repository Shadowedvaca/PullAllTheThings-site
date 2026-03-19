"""add in_guild to wow_characters

Revision ID: 0052
Revises: 0051
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wow_characters",
        sa.Column("in_guild", sa.Boolean(), nullable=False, server_default="true"),
        schema="guild_identity",
    )
    # Index for the common filter pattern
    op.create_index(
        "ix_wow_characters_in_guild",
        "wow_characters",
        ["in_guild"],
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_index("ix_wow_characters_in_guild", table_name="wow_characters", schema="guild_identity")
    op.drop_column("wow_characters", "in_guild", schema="guild_identity")
