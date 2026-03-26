"""Add blizzard_character_id to wow_characters and character_name_history table

Revision ID: 0061
Revises: 0060
Create Date: 2026-03-26

Enables stable character tracking through renames and transfers.
blizzard_character_id is the Blizzard API's numeric ID for a character,
which survives name changes. character_name_history records old names
so historical WCL parse data can still be attributed correctly.
"""
from alembic import op
import sqlalchemy as sa

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add stable Blizzard character ID to wow_characters (nullable for backfill)
    op.add_column(
        "wow_characters",
        sa.Column("blizzard_character_id", sa.BigInteger(), nullable=True),
        schema="guild_identity",
    )
    op.create_index(
        "idx_wc_blizzard_id",
        "wow_characters",
        ["blizzard_character_id"],
        unique=True,
        schema="guild_identity",
        postgresql_where=sa.text("blizzard_character_id IS NOT NULL"),
    )

    # 2. Create character_name_history table for rename tracking
    op.create_table(
        "character_name_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wow_character_id",
            sa.Integer(),
            sa.ForeignKey("guild_identity.wow_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("character_name", sa.String(50), nullable=False),
        sa.Column("realm_slug", sa.String(50), nullable=False),
        sa.Column(
            "recorded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="guild_identity",
    )
    op.create_index(
        "idx_cnh_character",
        "character_name_history",
        ["wow_character_id"],
        schema="guild_identity",
    )
    # Index for name lookups (WCL parse historical name resolution)
    op.create_index(
        "idx_cnh_name_realm",
        "character_name_history",
        ["character_name", "realm_slug"],
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_index("idx_cnh_name_realm", "character_name_history", schema="guild_identity")
    op.drop_index("idx_cnh_character", "character_name_history", schema="guild_identity")
    op.drop_table("character_name_history", schema="guild_identity")
    op.drop_index("idx_wc_blizzard_id", "wow_characters", schema="guild_identity")
    op.drop_column("wow_characters", "blizzard_character_id", schema="guild_identity")
