"""feat: player_action_log — self-service character claim/unclaim history

Revision ID: 0027
Revises: 0026
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_action_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "player_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Denormalized so the log row survives character deletion
        sa.Column("character_name", sa.String(50), nullable=True),
        sa.Column("realm_slug", sa.String(50), nullable=True),
        sa.Column("details", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        schema="guild_identity",
    )
    op.create_index(
        "ix_player_action_log_player_id",
        "player_action_log",
        ["player_id"],
        schema="guild_identity",
    )
    op.create_index(
        "ix_player_action_log_created_at",
        "player_action_log",
        ["created_at"],
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_table("player_action_log", schema="guild_identity")
