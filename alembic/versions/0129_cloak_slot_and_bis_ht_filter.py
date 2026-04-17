"""fix: CLOAK inventory_type → back slot; u.gg BIS visible when no hero talent selected

Revision ID: 0129
Revises: 0128
Create Date: 2026-04-17

Two fixes:
1. sp_rebuild_items: Blizzard API uses inventory_type 'CLOAK', not 'BACK'.
   The CASE had WHEN 'BACK' but all 295 cloaks in landing use CLOAK, so they
   all fell through to 'other'.  Add WHEN 'CLOAK' THEN 'back'.
   Requires a full Enrich & Classify run after deploy to populate back items.

2. BIS hero_talent filter (Python-only): gear_plan_service query changed from
   (vbr.hero_talent_id = $2 OR vbr.hero_talent_id IS NULL)
   to
   ($2::int IS NULL OR vbr.hero_talent_id = $2 OR vbr.hero_talent_id IS NULL)
   so that u.gg entries (which always carry a specific hero_talent_id) are
   visible when the plan has no hero talent selected yet.
"""
from alembic import op

revision = "0129"
down_revision = "0128"
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
                quality,
                playable_class_ids,
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
                    WHEN 'CLOAK'      THEN 'back'
                    WHEN 'CHEST'      THEN 'chest'
                    WHEN 'ROBE'       THEN 'chest'
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
                bi.payload -> 'quality' ->> 'type',
                CASE
                    WHEN jsonb_array_length(
                             COALESCE(
                                 bi.payload -> 'preview_item' -> 'requirements'
                                           -> 'playable_classes' -> 'links',
                                 '[]'::jsonb
                             )
                         ) = 0
                    THEN NULL
                    ELSE ARRAY(
                        SELECT (cls ->> 'id')::int
                          FROM jsonb_array_elements(
                               bi.payload -> 'preview_item' -> 'requirements'
                                          -> 'playable_classes' -> 'links'
                          ) AS cls
                    )
                END,
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
        $$
    """)


def downgrade():
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
                quality,
                playable_class_ids,
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
                    WHEN 'ROBE'       THEN 'chest'
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
                bi.payload -> 'quality' ->> 'type',
                CASE
                    WHEN jsonb_array_length(
                             COALESCE(
                                 bi.payload -> 'preview_item' -> 'requirements'
                                           -> 'playable_classes' -> 'links',
                                 '[]'::jsonb
                             )
                         ) = 0
                    THEN NULL
                    ELSE ARRAY(
                        SELECT (cls ->> 'id')::int
                          FROM jsonb_array_elements(
                               bi.payload -> 'preview_item' -> 'requirements'
                                          -> 'playable_classes' -> 'links'
                          ) AS cls
                    )
                END,
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
        $$
    """)
