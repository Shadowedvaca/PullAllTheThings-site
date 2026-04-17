"""feat: persist icon URLs in landing to survive enrichment rebuilds

Revision ID: 0122
Revises: 0121
Create Date: 2026-04-17

sp_rebuild_items() truncates enrichment.items CASCADE on every run, wiping
all icon_url values fetched by enrich-and-classify step 4.  Step 4 then
re-fetches ~7000+ Blizzard media API calls from scratch every time.

Fix: add landing.blizzard_item_icons as a permanent icon URL cache.
  - sp_rebuild_items() LEFT JOINs this table and reads icon_url directly.
  - Step 4 (Python) writes to landing.blizzard_item_icons (persistent) and
    enrichment.items (current run), and only fetches items not already cached.
  - Future Enrich & Classify step 4 fetches only genuinely new items.

Upgrade seeds landing.blizzard_item_icons from enrichment.items immediately
so any icon URLs already fetched in the current session are preserved.
"""
from alembic import op

revision = "0122"
down_revision = "0121"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE landing.blizzard_item_icons (
            blizzard_item_id  INTEGER PRIMARY KEY,
            icon_url          TEXT NOT NULL,
            fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Seed from enrichment.items — preserves icon URLs already fetched this session
    op.execute("""
        INSERT INTO landing.blizzard_item_icons (blizzard_item_id, icon_url)
        SELECT blizzard_item_id, icon_url
          FROM enrichment.items
         WHERE icon_url IS NOT NULL
        ON CONFLICT (blizzard_item_id) DO NOTHING
    """)

    # Update sp_rebuild_items to read icon_url from landing cache
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_items()
        LANGUAGE plpgsql AS $proc$
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
                bi.blizzard_item_id,
                COALESCE(NULLIF(trim(bi.payload->>'name'), ''), 'Unknown Item'),
                lii.icon_url,
                CASE bi.payload->'inventory_type'->>'type'
                    WHEN 'HEAD'       THEN 'head'
                    WHEN 'NECK'       THEN 'neck'
                    WHEN 'SHOULDER'   THEN 'shoulder'
                    WHEN 'BACK'       THEN 'back'
                    WHEN 'CHEST'      THEN 'chest'
                    WHEN 'WAIST'      THEN 'waist'
                    WHEN 'LEGS'       THEN 'legs'
                    WHEN 'FEET'       THEN 'feet'
                    WHEN 'WRIST'      THEN 'wrist'
                    WHEN 'HAND'       THEN 'hands'
                    WHEN 'FINGER'     THEN 'finger'
                    WHEN 'TRINKET'    THEN 'trinket'
                    WHEN 'WEAPON'     THEN 'one_hand'
                    WHEN 'TWOHWEAPON' THEN 'two_hand'
                    WHEN 'RANGED'     THEN 'ranged'
                    WHEN 'OFFHAND'    THEN 'off_hand'
                    WHEN 'HOLDABLE'   THEN 'off_hand'
                    WHEN 'SHIELD'     THEN 'off_hand'
                    ELSE 'other'
                END,
                CASE WHEN (bi.payload->'item_class'->>'id')::int = 4
                     THEN CASE bi.payload->'item_subclass'->>'name'
                              WHEN 'Cloth'   THEN 'cloth'
                              WHEN 'Leather' THEN 'leather'
                              WHEN 'Mail'    THEN 'mail'
                              WHEN 'Plate'   THEN 'plate'
                              ELSE NULL
                          END
                     ELSE NULL
                END,
                'unclassified',
                NULL::varchar(1),
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id)
                    blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            LEFT JOIN landing.blizzard_item_icons lii
                ON lii.blizzard_item_id = bi.blizzard_item_id;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $proc$
    """)


def downgrade():
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_items()
        LANGUAGE plpgsql AS $proc$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.items CASCADE;

            INSERT INTO enrichment.items (
                blizzard_item_id, name, icon_url, slot_type, armor_type,
                item_category, quality_track, enriched_at
            )
            SELECT
                bi.blizzard_item_id,
                COALESCE(NULLIF(trim(bi.payload->>'name'), ''), 'Unknown Item'),
                NULL::text,
                CASE bi.payload->'inventory_type'->>'type'
                    WHEN 'HEAD'       THEN 'head'
                    WHEN 'NECK'       THEN 'neck'
                    WHEN 'SHOULDER'   THEN 'shoulder'
                    WHEN 'BACK'       THEN 'back'
                    WHEN 'CHEST'      THEN 'chest'
                    WHEN 'WAIST'      THEN 'waist'
                    WHEN 'LEGS'       THEN 'legs'
                    WHEN 'FEET'       THEN 'feet'
                    WHEN 'WRIST'      THEN 'wrist'
                    WHEN 'HAND'       THEN 'hands'
                    WHEN 'FINGER'     THEN 'finger'
                    WHEN 'TRINKET'    THEN 'trinket'
                    WHEN 'WEAPON'     THEN 'one_hand'
                    WHEN 'TWOHWEAPON' THEN 'two_hand'
                    WHEN 'RANGED'     THEN 'ranged'
                    WHEN 'OFFHAND'    THEN 'off_hand'
                    WHEN 'HOLDABLE'   THEN 'off_hand'
                    WHEN 'SHIELD'     THEN 'off_hand'
                    ELSE 'other'
                END,
                CASE WHEN (bi.payload->'item_class'->>'id')::int = 4
                     THEN CASE bi.payload->'item_subclass'->>'name'
                              WHEN 'Cloth'   THEN 'cloth'
                              WHEN 'Leather' THEN 'leather'
                              WHEN 'Mail'    THEN 'mail'
                              WHEN 'Plate'   THEN 'plate'
                              ELSE NULL
                          END
                     ELSE NULL
                END,
                'unclassified',
                NULL::varchar(1),
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id)
                    blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $proc$
    """)

    op.execute("DROP TABLE IF EXISTS landing.blizzard_item_icons")
