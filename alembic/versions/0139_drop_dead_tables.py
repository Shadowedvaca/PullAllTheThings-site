"""chore: drop two confirmed-dead tables

Tables dropped:
  common.guild_members — legacy, replaced by guild_identity.players
  common.characters    — legacy, replaced by guild_identity.wow_characters

Note: patt.alembic_version is the active Alembic version table for this project
(configured in alembic.ini) — NOT an orphan. Do not drop it.

Note: enrichment.item_set_members was incorrectly identified as dead. It is
actively used by sp_update_item_categories, sp_rebuild_item_seasons, and
sp_rebuild_all. Do NOT drop it.

Revision ID: 0139
Revises: 0138
"""

revision = "0139"
down_revision = "0138"

from alembic import op


def upgrade():
    op.execute("DROP TABLE IF EXISTS common.guild_members")
    op.execute("DROP TABLE IF EXISTS common.characters")


def downgrade():
    # guild_members / characters are legacy — not worth restoring
    pass
