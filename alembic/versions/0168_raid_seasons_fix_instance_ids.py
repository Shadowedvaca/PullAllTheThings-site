"""raid_seasons: add missing Midnight S1 dungeon instance IDs 1298 and 1309

Operation: Floodgate (1298) and The Blinding Vale (1309) were omitted from
current_instance_ids when the active season was configured. Items from those
two dungeons had no item_seasons entries and were invisible in viz.slot_items.

Revision ID: 0168
Revises: 0167
Create Date: 2026-04-22
"""
from alembic import op

revision = "0168"
down_revision = "0167"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE patt.raid_seasons
           SET current_instance_ids = array(
               SELECT DISTINCT unnest(current_instance_ids || ARRAY[1298, 1309])
               ORDER BY 1
           )
         WHERE is_active = true
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE patt.raid_seasons
           SET current_instance_ids = array(
               SELECT unnest(current_instance_ids)
               EXCEPT
               SELECT unnest(ARRAY[1298, 1309])
               ORDER BY 1
           )
         WHERE is_active = true
    """)
