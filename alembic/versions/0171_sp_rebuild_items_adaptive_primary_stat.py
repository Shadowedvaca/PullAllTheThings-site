"""fix: sp_rebuild_items — NULL primary_stat when item has multiple primary stats

Items with more than one primary stat type (e.g. both INTELLECT and STRENGTH)
are adaptive — their stats change per equipping spec.  Storing whichever stat
appears first in the payload is wrong.  Set primary_stat = NULL for those items
so all eligible specs can see them; the armor_type filter handles cross-armor
contamination.  Items with exactly one primary stat type keep that value.

Revision ID: 0171
Revises: 0170
Create Date: 2026-04-22
"""
from alembic import op

revision = "0171"
down_revision = "0170"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
                weapon_subtype,
                primary_stat,
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
                    WHEN 'HEAD'            THEN 'head'
                    WHEN 'NECK'            THEN 'neck'
                    WHEN 'SHOULDER'        THEN 'shoulder'
                    WHEN 'BACK'            THEN 'back'
                    WHEN 'CLOAK'           THEN 'back'
                    WHEN 'CHEST'           THEN 'chest'
                    WHEN 'ROBE'            THEN 'chest'
                    WHEN 'WAIST'           THEN 'waist'
                    WHEN 'LEGS'            THEN 'legs'
                    WHEN 'FEET'            THEN 'feet'
                    WHEN 'WRIST'           THEN 'wrist'
                    WHEN 'HAND'            THEN 'hands'
                    WHEN 'FINGER'          THEN 'finger'
                    WHEN 'TRINKET'         THEN 'trinket'
                    WHEN 'WEAPON'          THEN 'one_hand'
                    WHEN 'WEAPONMAINHAND'  THEN 'one_hand'
                    WHEN 'WEAPONOFFHAND'   THEN 'one_hand'
                    WHEN 'TWOHWEAPON'      THEN 'two_hand'
                    WHEN 'RANGED'          THEN 'ranged'
                    WHEN 'RANGEDRIGHT'     THEN 'ranged'
                    WHEN 'OFFHAND'         THEN 'off_hand'
                    WHEN 'HOLDABLE'        THEN 'off_hand'
                    WHEN 'SHIELD'          THEN 'off_hand'
                    ELSE 'other'
                END,
                -- armor_type: only for armor class (id=4)
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
                -- weapon_subtype: weapons (class 2) + shields (class 4, SHIELD)
                CASE
                    WHEN (bi.payload->'item_class'->>'id')::int = 2 THEN
                        CASE bi.payload->'item_subclass'->>'name'
                            WHEN 'Axe'  THEN
                                CASE bi.payload->'inventory_type'->>'type'
                                    WHEN 'TWOHWEAPON' THEN 'Two-Handed Axe'
                                    ELSE 'One-Handed Axe'
                                END
                            WHEN 'Mace' THEN
                                CASE bi.payload->'inventory_type'->>'type'
                                    WHEN 'TWOHWEAPON' THEN 'Two-Handed Mace'
                                    ELSE 'One-Handed Mace'
                                END
                            WHEN 'Sword' THEN
                                CASE bi.payload->'inventory_type'->>'type'
                                    WHEN 'TWOHWEAPON' THEN 'Two-Handed Sword'
                                    ELSE 'One-Handed Sword'
                                END
                            ELSE bi.payload->'item_subclass'->>'name'
                        END
                    WHEN (bi.payload->'item_class'->>'id')::int = 4
                     AND bi.payload->'inventory_type'->>'type' = 'SHIELD'
                        THEN 'Shield'
                    ELSE NULL
                END,
                -- primary_stat: NULL if item has multiple primary stat types (adaptive);
                -- single primary stat type kept as-is
                CASE
                    WHEN (
                        SELECT count(DISTINCT stat->'type'->>'type')
                          FROM jsonb_array_elements(
                                   COALESCE(bi.payload->'preview_item'->'stats', '[]'::jsonb)
                               ) AS stat
                         WHERE stat->'type'->>'type' IN ('INTELLECT', 'AGILITY', 'STRENGTH')
                    ) > 1 THEN NULL
                    ELSE (
                        SELECT CASE stat->'type'->>'type'
                                   WHEN 'INTELLECT' THEN 'int'
                                   WHEN 'AGILITY'   THEN 'agi'
                                   WHEN 'STRENGTH'  THEN 'str'
                               END
                          FROM jsonb_array_elements(
                                   COALESCE(bi.payload->'preview_item'->'stats', '[]'::jsonb)
                               ) AS stat
                         WHERE stat->'type'->>'type' IN ('INTELLECT', 'AGILITY', 'STRENGTH')
                         LIMIT 1
                    )
                END,
                'unclassified',
                qt.quality_track,
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
                ON lii.blizzard_item_id = bi.blizzard_item_id
            LEFT JOIN landing.blizzard_item_quality_tracks qt
                ON qt.blizzard_item_id = bi.blizzard_item_id;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $$
    """)


def downgrade() -> None:
    pass
