"""chore: drop four confirmed-dead tables

Tables dropped:
  patt.alembic_version       — orphan; Alembic version lives in public.alembic_version
  common.guild_members       — legacy, replaced by guild_identity.players
  common.characters          — legacy, replaced by guild_identity.wow_characters
  enrichment.item_set_members — scaffolded but never referenced in any code

Revision ID: 0139
Revises: 0138
"""

revision = "0139"
down_revision = "0138"

from alembic import op


def upgrade():
    op.execute("DROP TABLE IF EXISTS patt.alembic_version")
    op.execute("DROP TABLE IF EXISTS common.guild_members")
    op.execute("DROP TABLE IF EXISTS common.characters")
    op.execute("DROP TABLE IF EXISTS enrichment.item_set_members")


def downgrade():
    # guild_members / characters are legacy — not worth restoring
    # item_set_members had no data or dependents
    pass
