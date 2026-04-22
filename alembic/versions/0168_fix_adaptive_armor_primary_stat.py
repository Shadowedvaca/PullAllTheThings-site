"""fix: sp_rebuild_items nullifies primary_stat for unrestricted non-weapon items

Seasonal Midnight M+ gear is "adaptive" — stats adjust to the equipping spec.
When the Blizzard item API is called in the context of an INT character, adaptive
plate/mail/leather items land with primary_stat='int' even though a STR Death
Knight should see them too.

Fix: after the INSERT, any non-weapon item (slot_type NOT IN weapon types) with
no class restriction (playable_class_ids IS NULL) gets primary_stat set to NULL.
NULL passes the _filter_by_primary_stat check in gear_plan_service, so the item
becomes visible to all primary-stat groups.  The armor_type filter already prevents
cross-armor contamination (plate items don't show for leather specs, etc.).

Revision ID: 0168
Revises: 0167
Create Date: 2026-04-22
"""
from alembic import op

revision = "0168"
down_revision = "0167"
branch_labels = None
depends_on = None

_WEAPON_SLOT_TYPES = ("'one_hand'", "'two_hand'", "'ranged'", "'off_hand'")


def upgrade() -> None:
    # ── 1. Fix existing data immediately ──────────────────────────────────────
    op.execute(f"""
        UPDATE enrichment.items
           SET primary_stat = NULL
         WHERE primary_stat IS NOT NULL
           AND slot_type NOT IN ({', '.join(_WEAPON_SLOT_TYPES)})
           AND playable_class_ids IS NULL
    """)

    # ── 2. Update sp_rebuild_items to apply the same correction on rebuild ────
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
                -- primary_stat from payload; nullified below for adaptive (unrestricted) non-weapons
                COALESCE(
                    (SELECT CASE stat->'type'->>'type'
                                WHEN 'INTELLECT' THEN 'int'
                                WHEN 'AGILITY'   THEN 'agi'
                                WHEN 'STRENGTH'  THEN 'str'
                            END
                       FROM jsonb_array_elements(
                                COALESCE(bi.payload->'preview_item'->'stats', '[]'::jsonb)
                            ) AS stat
                      WHERE stat->'type'->>'type' IN ('INTELLECT', 'AGILITY', 'STRENGTH')
                        AND (stat->>'is_negated') IS DISTINCT FROM 'true'
                      LIMIT 1),
                    (SELECT CASE stat->'type'->>'type'
                                WHEN 'INTELLECT' THEN 'int'
                                WHEN 'AGILITY'   THEN 'agi'
                                WHEN 'STRENGTH'  THEN 'str'
                            END
                       FROM jsonb_array_elements(
                                COALESCE(bi.payload->'preview_item'->'stats', '[]'::jsonb)
                            ) AS stat
                      WHERE stat->'type'->>'type' IN ('INTELLECT', 'AGILITY', 'STRENGTH')
                      LIMIT 1)
                ),
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

            -- Adaptive (unrestricted) non-weapon items were fetched from a single
            -- character context, so the payload primary_stat reflects that class,
            -- not the item's true adaptive nature.  NULL lets all specs see them;
            -- the armor_type filter prevents cross-armor contamination.
            UPDATE enrichment.items
               SET primary_stat = NULL
             WHERE primary_stat IS NOT NULL
               AND slot_type NOT IN ('one_hand', 'two_hand', 'ranged', 'off_hand')
               AND playable_class_ids IS NULL;

            RAISE NOTICE 'sp_rebuild_items: adaptive armor primary_stat nullified';
        END;
        $$
    """)


def downgrade() -> None:
    # Restore proc without the adaptive-armor correction (primary_stat comes
    # straight from payload); also revert the immediate data fix is impractical
    # since we don't know what values existed before.
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
                COALESCE(
                    (SELECT CASE stat->'type'->>'type'
                                WHEN 'INTELLECT' THEN 'int'
                                WHEN 'AGILITY'   THEN 'agi'
                                WHEN 'STRENGTH'  THEN 'str'
                            END
                       FROM jsonb_array_elements(
                                COALESCE(bi.payload->'preview_item'->'stats', '[]'::jsonb)
                            ) AS stat
                      WHERE stat->'type'->>'type' IN ('INTELLECT', 'AGILITY', 'STRENGTH')
                        AND (stat->>'is_negated') IS DISTINCT FROM 'true'
                      LIMIT 1),
                    (SELECT CASE stat->'type'->>'type'
                                WHEN 'INTELLECT' THEN 'int'
                                WHEN 'AGILITY'   THEN 'agi'
                                WHEN 'STRENGTH'  THEN 'str'
                            END
                       FROM jsonb_array_elements(
                                COALESCE(bi.payload->'preview_item'->'stats', '[]'::jsonb)
                            ) AS stat
                      WHERE stat->'type'->>'type' IN ('INTELLECT', 'AGILITY', 'STRENGTH')
                      LIMIT 1)
                ),
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
