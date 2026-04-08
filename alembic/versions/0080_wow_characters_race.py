"""feat: add race column to guild_identity.wow_characters

Revision ID: 0080
Revises: 0079
Create Date: 2026-04-07

Adds a race VARCHAR(40) column so the BNet sync can store each character's
playable race. Used by the new /my-characters-new page to display race info
in the persistent character header.
"""

from alembic import op
import sqlalchemy as sa

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wow_characters",
        sa.Column("race", sa.String(40), nullable=True),
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_column("wow_characters", "race", schema="guild_identity")
