"""chore: drop three confirmed-dead tables

Tables dropped:
  common.guild_members        — legacy, replaced by guild_identity.players
  common.characters           — legacy, replaced by guild_identity.wow_characters
  enrichment.item_set_members — scaffolded but never referenced in any code

Note: patt.alembic_version is the active Alembic version table for this project
(configured in alembic.ini) — NOT an orphan. Do not drop it.

Revision ID: 0139
Revises: 0138
"""

revision = "0139"
down_revision = "0138"

from alembic import op


def upgrade():
    op.execute("DROP TABLE IF EXISTS common.guild_members")
    op.execute("DROP TABLE IF EXISTS common.characters")
    op.execute("DROP TABLE IF EXISTS enrichment.item_set_members")


def downgrade():
    # guild_members / characters are legacy — not worth restoring
    # item_set_members had no data or dependents
    pass
