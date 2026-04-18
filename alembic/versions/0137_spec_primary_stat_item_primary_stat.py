"""feat: primary_stat on ref.specializations + sp_rebuild_items extracts it from payload

Two changes:
  1. ref.specializations gains primary_stat VARCHAR(3) ('int'/'agi'/'str'),
     seeded for all 40 current specs + Demon Hunter Devourer.
  2. enrichment.sp_rebuild_items() now extracts primary_stat from the Blizzard
     preview_item.stats payload: prefers a non-negated INTELLECT/AGILITY/STRENGTH
     entry, falls back to any listed primary stat (handles items fetched from
     a non-matching character's perspective).

Python changes (alongside this migration):
  gear_plan_service.py:
  - SPEC_PRIMARY_STAT dict removed; fetched from ref.specializations via DB.
  - get_available_items() reads s.primary_stat from ref.specializations.
  - Primary stat filter now applies to ALL slots (not just weapon slots) —
    items with NULL primary_stat always pass through (universal items).

After deploying, run Enrich & Classify to backfill primary_stat on existing items.

Revision ID: 0137
Revises: 0136
"""

revision = "0137"
down_revision = "0136"

from alembic import op


def upgrade():
    # ── 1. Add primary_stat to ref.specializations ────────────────────────────
    op.execute("""
        ALTER TABLE ref.specializations
        ADD COLUMN primary_stat VARCHAR(3)
            CHECK (primary_stat IN ('int', 'agi', 'str'))
    """)

    op.execute("""
        UPDATE ref.specializations s
           SET primary_stat = v.ps
          FROM (VALUES
            ('Death Knight', 'Blood',         'str'),
            ('Death Knight', 'Frost',         'str'),
            ('Death Knight', 'Unholy',        'str'),
            ('Demon Hunter', 'Devourer',      'agi'),
            ('Demon Hunter', 'Havoc',         'agi'),
            ('Demon Hunter', 'Vengeance',     'agi'),
            ('Druid',        'Balance',       'int'),
            ('Druid',        'Feral',         'agi'),
            ('Druid',        'Guardian',      'agi'),
            ('Druid',        'Restoration',   'int'),
            ('Evoker',       'Augmentation',  'int'),
            ('Evoker',       'Devastation',   'int'),
            ('Evoker',       'Preservation',  'int'),
            ('Hunter',       'Beast Mastery', 'agi'),
            ('Hunter',       'Marksmanship',  'agi'),
            ('Hunter',       'Survival',      'agi'),
            ('Mage',         'Arcane',        'int'),
            ('Mage',         'Fire',          'int'),
            ('Mage',         'Frost',         'int'),
            ('Monk',         'Brewmaster',    'agi'),
            ('Monk',         'Mistweaver',    'int'),
            ('Monk',         'Windwalker',    'agi'),
            ('Paladin',      'Holy',          'int'),
            ('Paladin',      'Protection',    'str'),
            ('Paladin',      'Retribution',   'str'),
            ('Priest',       'Discipline',    'int'),
            ('Priest',       'Holy',          'int'),
            ('Priest',       'Shadow',        'int'),
            ('Rogue',        'Assassination', 'agi'),
            ('Rogue',        'Outlaw',        'agi'),
            ('Rogue',        'Subtlety',      'agi'),
            ('Shaman',       'Elemental',     'int'),
            ('Shaman',       'Enhancement',   'agi'),
            ('Shaman',       'Restoration',   'int'),
            ('Warlock',      'Affliction',    'int'),
            ('Warlock',      'Demonology',    'int'),
            ('Warlock',      'Destruction',   'int'),
            ('Warrior',      'Arms',          'str'),
            ('Warrior',      'Fury',          'str'),
            ('Warrior',      'Protection',    'str')
          ) AS v(class_name, spec_name, ps)
          JOIN ref.classes c ON c.name = v.class_name
         WHERE s.class_id = c.id AND s.name = v.spec_name
    """)

    # ── 2. Update sp_rebuild_items() to extract primary_stat from payload ─────
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
                -- primary_stat: non-negated INT/AGI/STR first, any listed as fallback
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


def downgrade():
    # Restore sp_rebuild_items without primary_stat extraction
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

    op.execute("ALTER TABLE ref.specializations DROP COLUMN IF EXISTS primary_stat")
