"""Add kill boolean to character_parses

Revision ID: 0059
Revises: 0058
Create Date: 2026-03-23

"""
from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "character_parses",
        sa.Column("kill", sa.Boolean(), nullable=True),
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_column("character_parses", "kill", schema="guild_identity")
