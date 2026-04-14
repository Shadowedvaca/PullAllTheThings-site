"""Add 'catalyst' to item_sources.instance_type CHECK constraint.

Revival Catalyst source rows (instance_type='catalyst') were being rejected
by the existing CHECK constraint which only allowed 'raid', 'dungeon', and
'world_boss'.  This caused all 21 catalyst-slot source inserts to fail silently
with a DB error rather than a unique-conflict no-op.

Revision ID: 0103
Revises: 0102
"""

from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE guild_identity.item_sources
          DROP CONSTRAINT item_sources_instance_type_check,
          ADD CONSTRAINT item_sources_instance_type_check
            CHECK (instance_type IN ('raid', 'dungeon', 'world_boss', 'catalyst'))
    """)


def downgrade():
    op.execute("""
        ALTER TABLE guild_identity.item_sources
          DROP CONSTRAINT item_sources_instance_type_check,
          ADD CONSTRAINT item_sources_instance_type_check
            CHECK (instance_type IN ('raid', 'dungeon', 'world_boss'))
    """)
