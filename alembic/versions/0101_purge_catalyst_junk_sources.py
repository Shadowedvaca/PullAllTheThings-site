"""Purge junk raid/world_boss source rows for catalyst items.

Catalyst items (wow_items.quality_track = 'C') are Revival Catalyst
conversion pieces — back, wrist, waist, feet tier slots.  They are never
dropped by raid bosses.  enrich_catalyst_tier_items() previously inserted
per-boss item_sources rows for them (copying from same-slot raid drops),
which caused them to appear in the Raid section of slot drawers for every
class with boss names as their source.

This migration deletes those rows.  The corrected enrich_catalyst_tier_items()
(from this same release) will insert a single instance_type='catalyst' row
per item on the next run of Sync Loot Tables.

Revision ID: 0101
Revises: 0100
"""

from alembic import op

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DELETE FROM guild_identity.item_sources
         WHERE instance_type IN ('raid', 'world_boss')
           AND item_id IN (
               SELECT id FROM guild_identity.wow_items
                WHERE quality_track = 'C'
           )
    """)


def downgrade():
    # Rows are not restored on downgrade — re-run enrich_catalyst_tier_items()
    # (Sync Loot Tables in admin) with the old code to repopulate if needed.
    pass
