"""feat: add on_raid_hiatus flag to players

Revision ID: 0030
Revises: 0029
Create Date: 2026-02-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column(
            "on_raid_hiatus",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_column("players", "on_raid_hiatus", schema="guild_identity")
