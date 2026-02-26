"""feat: link attribution — add link_source and confidence to player_characters

Revision ID: 0026
Revises: 0025
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add attribution columns
    op.add_column(
        "player_characters",
        sa.Column(
            "link_source",
            sa.String(30),
            nullable=False,
            server_default="unknown",
        ),
        schema="guild_identity",
    )
    op.add_column(
        "player_characters",
        sa.Column(
            "confidence",
            sa.String(15),
            nullable=False,
            server_default="unknown",
        ),
        schema="guild_identity",
    )

    # Backfill: stub players (discord_user_id IS NULL) get confidence = 'low'
    op.execute(
        """UPDATE guild_identity.player_characters pc
           SET confidence = 'low'
           FROM guild_identity.players p
           WHERE pc.player_id = p.id
             AND p.discord_user_id IS NULL"""
    )


def downgrade() -> None:
    op.drop_column("player_characters", "confidence", schema="guild_identity")
    op.drop_column("player_characters", "link_source", schema="guild_identity")
