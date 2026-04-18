"""fix: restore enrichment.item_set_members dropped incorrectly in 0139

Migration 0139 incorrectly identified enrichment.item_set_members as a dead
table. It is actively used by sp_update_item_categories, sp_rebuild_item_seasons,
and sp_rebuild_all stored procedures. This migration recreates the table and
index if they were dropped.

Revision ID: 0140
Revises: 0139
"""

revision = "0140"
down_revision = "0139"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS enrichment.item_set_members (
            set_id           INTEGER NOT NULL,
            set_name         TEXT,
            blizzard_item_id INTEGER NOT NULL,
            PRIMARY KEY (set_id, blizzard_item_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_item_set_members_bid
            ON enrichment.item_set_members (blizzard_item_id)
    """)


def downgrade():
    pass
