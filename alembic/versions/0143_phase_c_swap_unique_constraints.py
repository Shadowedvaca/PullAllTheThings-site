"""feat: Phase C — swap unique constraints on item_sources + item_recipe_links to blizzard_item_id

Drops the old FK-based unique constraints and replaces them with constraints
keyed on blizzard_item_id. This enables Phase C code to INSERT directly using
blizzard_item_id without needing the wow_items integer PK first.

Also backfills any item_sources rows where blizzard_item_id is NULL (can occur
if Sync Loot Tables was run on dev between the Phase A migration and this one).

Revision ID: 0143
Revises: 0142
"""

revision = "0143"
down_revision = "0142"

from alembic import op


def upgrade():
    # Safety backfill: any item_sources rows written by _sync_encounter after
    # Phase A (which did not include blizzard_item_id in its INSERT) will have
    # NULL. Re-derive from wow_items before swapping the unique constraint.
    op.execute("""
        UPDATE guild_identity.item_sources s
           SET blizzard_item_id = wi.blizzard_item_id
          FROM guild_identity.wow_items wi
         WHERE wi.id = s.item_id
           AND s.blizzard_item_id IS NULL
    """)

    # item_sources: swap unique from (item_id, instance_type, encounter_name)
    # to (blizzard_item_id, instance_type, encounter_name).
    op.execute("""
        ALTER TABLE guild_identity.item_sources
            DROP CONSTRAINT IF EXISTS uq_item_source
    """)
    op.execute("""
        ALTER TABLE guild_identity.item_sources
            ADD CONSTRAINT uq_item_source_bid
            UNIQUE (blizzard_item_id, instance_type, encounter_name)
    """)

    # item_recipe_links: swap unique from (item_id, recipe_id) to
    # (blizzard_item_id, recipe_id). All existing rows have blizzard_item_id
    # populated (Phase H migration 0132 backfill).
    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
            DROP CONSTRAINT IF EXISTS uq_item_recipe
    """)
    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
            ADD CONSTRAINT uq_item_recipe_bid
            UNIQUE (blizzard_item_id, recipe_id)
    """)


def downgrade():
    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
            DROP CONSTRAINT IF EXISTS uq_item_recipe_bid
    """)
    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
            ADD CONSTRAINT uq_item_recipe UNIQUE (item_id, recipe_id)
    """)
    op.execute("""
        ALTER TABLE guild_identity.item_sources
            DROP CONSTRAINT IF EXISTS uq_item_source_bid
    """)
    op.execute("""
        ALTER TABLE guild_identity.item_sources
            ADD CONSTRAINT uq_item_source UNIQUE (item_id, instance_type, encounter_name)
    """)
