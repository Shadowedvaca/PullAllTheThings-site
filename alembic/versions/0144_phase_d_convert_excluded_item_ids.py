"""feat: Phase D — convert gear_plan_slots.excluded_item_ids from wow_items.id to blizzard_item_id

excluded_item_ids stores integer IDs that were previously wow_items PKs.
This migration converts them to blizzard_item_id values so Python code
can manage exclusions without reading guild_identity.wow_items.

Revision ID: 0144
Revises: 0143
"""

revision = "0144"
down_revision = "0143"

from alembic import op


def upgrade():
    # Convert excluded_item_ids from wow_items.id values to blizzard_item_id values.
    # Rows where excluded_item_ids is empty or NULL are left unchanged.
    # Any excluded IDs with no matching wow_items row (orphans) are dropped silently.
    op.execute("""
        UPDATE guild_identity.gear_plan_slots gps
           SET excluded_item_ids = ARRAY(
               SELECT wi.blizzard_item_id
                 FROM unnest(gps.excluded_item_ids) AS eid
                 JOIN guild_identity.wow_items wi ON wi.id = eid
                WHERE wi.blizzard_item_id IS NOT NULL
           )
         WHERE array_length(excluded_item_ids, 1) > 0
    """)


def downgrade():
    # Convert blizzard_item_id values back to wow_items.id values.
    op.execute("""
        UPDATE guild_identity.gear_plan_slots gps
           SET excluded_item_ids = ARRAY(
               SELECT wi.id
                 FROM unnest(gps.excluded_item_ids) AS bid
                 JOIN guild_identity.wow_items wi ON wi.blizzard_item_id = bid
           )
         WHERE array_length(excluded_item_ids, 1) > 0
    """)
