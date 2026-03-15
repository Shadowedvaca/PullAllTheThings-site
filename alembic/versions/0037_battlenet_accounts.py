"""Phase 4.4.1: Battle.net OAuth account linking — battlenet_accounts table

Revision ID: 0037
Revises: 0036
Create Date: 2026-03-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "battlenet_accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "player_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.players.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("bnet_id", sa.String(50), nullable=False, unique=True),
        sa.Column("battletag", sa.String(100), nullable=False),
        sa.Column("access_token_encrypted", sa.Text, nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text),
        sa.Column("token_expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "linked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_refreshed", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_character_sync", sa.TIMESTAMP(timezone=True)),
        schema="guild_identity",
    )
    op.execute(
        "CREATE INDEX idx_bnet_player ON guild_identity.battlenet_accounts(player_id)"
    )


def downgrade():
    op.drop_table("battlenet_accounts", schema="guild_identity")
