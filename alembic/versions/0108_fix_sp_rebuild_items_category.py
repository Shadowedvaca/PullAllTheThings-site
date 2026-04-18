"""Fix sp_rebuild_items() to use 'unclassified' instead of 'unknown'.

Migration 0107 updated the item_category CHECK constraint to replace 'unknown'
with 'unclassified', but sp_rebuild_items() (defined in 0105) still hardcoded
'unknown' as the initial item_category on insert.  This caused sp_rebuild_all()
to fail with a CHECK constraint violation on the first step.

Revision ID: 0108
Revises: 0107
"""

from alembic import op

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_items()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.items CASCADE;

            INSERT INTO enrichment.items (
                blizzard_item_id,
                name,
                icon_url,
                slot_type,
                armor_type,
                item_category,
                quality_track,
                enriched_at
            )
            SELECT
                wi.blizzard_item_id,
                COALESCE(NULLIF(trim(wi.name), ''), 'Unknown Item'),
                wi.icon_url,
                wi.slot_type,
                LOWER(wi.armor_type),
                'unclassified',
                wi.quality_track,
                NOW()
            FROM guild_identity.wow_items wi
            WHERE wi.blizzard_item_id IS NOT NULL;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $$
    """)


def downgrade():
    # Restore the 0105 version that used 'unknown'
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_items()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.items CASCADE;

            INSERT INTO enrichment.items (
                blizzard_item_id,
                name,
                icon_url,
                slot_type,
                armor_type,
                item_category,
                quality_track,
                enriched_at
            )
            SELECT
                wi.blizzard_item_id,
                COALESCE(NULLIF(trim(wi.name), ''), 'Unknown Item'),
                wi.icon_url,
                wi.slot_type,
                LOWER(wi.armor_type),
                'unknown',
                wi.quality_track,
                NOW()
            FROM guild_identity.wow_items wi
            WHERE wi.blizzard_item_id IS NOT NULL;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $$
    """)
