"""feat: Phase A — add blizzard_item_id to item_sources and tier_token_attrs + backfill

Adds a plain INTEGER blizzard_item_id column (no FK constraint) to the two
tables that were missing it. character_equipment and gear_plan_slots already
have this column from earlier migrations.

Also backfills gear_plan_slots.blizzard_item_id for any rows where
desired_item_id is set but blizzard_item_id is NULL (should be 0 rows on a
current install, but safe to run regardless).

This is Phase A of the wow_items retirement plan
(reference/gear-plan-1.0-wow_items-fix.md). No code changes; additive only.

Revision ID: 0141
Revises: 0140
"""

revision = "0141"
down_revision = "0140"

from alembic import op


def upgrade():
    # 1. item_sources: add blizzard_item_id and backfill from wow_items
    op.execute("""
        ALTER TABLE guild_identity.item_sources
            ADD COLUMN IF NOT EXISTS blizzard_item_id INTEGER
    """)
    op.execute("""
        UPDATE guild_identity.item_sources s
           SET blizzard_item_id = wi.blizzard_item_id
          FROM guild_identity.wow_items wi
         WHERE wi.id = s.item_id
           AND s.blizzard_item_id IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_item_sources_blizzard_item_id
            ON guild_identity.item_sources (blizzard_item_id)
    """)

    # 2. character_equipment: blizzard_item_id already exists — no action needed.

    # 3. gear_plan_slots: blizzard_item_id already exists — backfill any gaps
    #    where desired_item_id is set but blizzard_item_id is NULL.
    op.execute("""
        UPDATE guild_identity.gear_plan_slots gps
           SET blizzard_item_id = wi.blizzard_item_id
          FROM guild_identity.wow_items wi
         WHERE wi.id = gps.desired_item_id
           AND gps.desired_item_id IS NOT NULL
           AND gps.blizzard_item_id IS NULL
    """)

    # 4. tier_token_attrs: add blizzard_item_id and backfill from wow_items
    op.execute("""
        ALTER TABLE guild_identity.tier_token_attrs
            ADD COLUMN IF NOT EXISTS blizzard_item_id INTEGER
    """)
    op.execute("""
        UPDATE guild_identity.tier_token_attrs t
           SET blizzard_item_id = wi.blizzard_item_id
          FROM guild_identity.wow_items wi
         WHERE wi.id = t.token_item_id
           AND t.blizzard_item_id IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tier_token_attrs_blizzard_item_id
            ON guild_identity.tier_token_attrs (blizzard_item_id)
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS guild_identity.ix_tier_token_attrs_blizzard_item_id")
    op.execute("ALTER TABLE guild_identity.tier_token_attrs DROP COLUMN IF EXISTS blizzard_item_id")
    op.execute("DROP INDEX IF EXISTS guild_identity.ix_item_sources_blizzard_item_id")
    op.execute("ALTER TABLE guild_identity.item_sources DROP COLUMN IF EXISTS blizzard_item_id")
