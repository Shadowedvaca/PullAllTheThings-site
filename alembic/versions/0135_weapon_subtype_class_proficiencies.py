"""feat: weapon_subtype on enrichment.items + ref.class_weapon_proficiencies

Adds weapon_subtype to enrichment.items (derived from Blizzard item_subclass
+ inventory_type) and creates ref.class_weapon_proficiencies to filter
weapon/off-hand items in the gear plan drawer by class.

Changes:
  enrichment.items — new column: weapon_subtype VARCHAR(30)
  sp_rebuild_items() — populates weapon_subtype from payload
  ref.class_weapon_proficiencies (blizzard_class_id, weapon_subtype) — new table,
    seeded from ClassWeaponMapping.csv

Revision ID: 0135
Revises: 0134
"""

revision = "0135"
down_revision = "0134"

from alembic import op


def upgrade():
    # ── 1. Add weapon_subtype to enrichment.items ─────────────────────────────
    op.execute("ALTER TABLE enrichment.items ADD COLUMN weapon_subtype VARCHAR(30)")

    # ── 2. Update sp_rebuild_items() to populate weapon_subtype ───────────────
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

    # ── 3. Create ref.class_weapon_proficiencies ──────────────────────────────
    op.execute("""
        CREATE TABLE ref.class_weapon_proficiencies (
            blizzard_class_id  INTEGER     NOT NULL,
            weapon_subtype     VARCHAR(30) NOT NULL,
            PRIMARY KEY (blizzard_class_id, weapon_subtype)
        )
    """)

    # Seed from ClassWeaponMapping.csv (reference/ClassWeaponMapping.csv)
    # blizzard_class_id: 1=Warrior 2=Paladin 3=Hunter 4=Rogue 5=Priest
    #                    6=Death Knight 7=Shaman 8=Mage 9=Warlock 10=Monk
    #                    11=Druid 12=Demon Hunter 13=Evoker
    op.execute("""
        INSERT INTO ref.class_weapon_proficiencies (blizzard_class_id, weapon_subtype) VALUES
        -- Bow: Hunter, Rogue, Warrior
        (3,  'Bow'), (4,  'Bow'), (1,  'Bow'),
        -- Crossbow: Hunter, Rogue, Warrior
        (3,  'Crossbow'), (4,  'Crossbow'), (1,  'Crossbow'),
        -- Dagger: Druid, Evoker, Mage, Priest, Rogue, Shaman, Warlock, Warrior
        (11, 'Dagger'), (13, 'Dagger'), (8,  'Dagger'), (5,  'Dagger'),
        (4,  'Dagger'), (7,  'Dagger'), (9,  'Dagger'), (1,  'Dagger'),
        -- Fist Weapon: Demon Hunter, Druid, Evoker, Hunter, Monk, Rogue, Shaman, Warrior
        (12, 'Fist Weapon'), (11, 'Fist Weapon'), (13, 'Fist Weapon'), (3,  'Fist Weapon'),
        (10, 'Fist Weapon'), (4,  'Fist Weapon'), (7,  'Fist Weapon'), (1,  'Fist Weapon'),
        -- Gun: Hunter, Rogue, Warrior
        (3,  'Gun'), (4,  'Gun'), (1,  'Gun'),
        -- One-Handed Axe: Death Knight, Demon Hunter, Evoker, Hunter, Monk, Paladin, Rogue, Shaman, Warrior
        (6,  'One-Handed Axe'), (12, 'One-Handed Axe'), (13, 'One-Handed Axe'), (3,  'One-Handed Axe'),
        (10, 'One-Handed Axe'), (2,  'One-Handed Axe'), (4,  'One-Handed Axe'), (7,  'One-Handed Axe'),
        (1,  'One-Handed Axe'),
        -- One-Handed Mace: Death Knight, Druid, Evoker, Monk, Paladin, Priest, Rogue, Shaman, Warrior
        (6,  'One-Handed Mace'), (11, 'One-Handed Mace'), (13, 'One-Handed Mace'), (10, 'One-Handed Mace'),
        (2,  'One-Handed Mace'), (5,  'One-Handed Mace'), (4,  'One-Handed Mace'), (7,  'One-Handed Mace'),
        (1,  'One-Handed Mace'),
        -- One-Handed Sword: Death Knight, Demon Hunter, Evoker, Hunter, Mage, Monk, Paladin, Rogue, Warlock, Warrior
        (6,  'One-Handed Sword'), (12, 'One-Handed Sword'), (13, 'One-Handed Sword'), (3,  'One-Handed Sword'),
        (8,  'One-Handed Sword'), (10, 'One-Handed Sword'), (2,  'One-Handed Sword'), (4,  'One-Handed Sword'),
        (9,  'One-Handed Sword'), (1,  'One-Handed Sword'),
        -- Polearm: Death Knight, Druid, Evoker, Hunter, Monk, Paladin, Warrior
        (6,  'Polearm'), (11, 'Polearm'), (13, 'Polearm'), (3,  'Polearm'),
        (10, 'Polearm'), (2,  'Polearm'), (1,  'Polearm'),
        -- Shield: Paladin, Shaman, Warrior
        (2,  'Shield'), (7,  'Shield'), (1,  'Shield'),
        -- Staff: Druid, Evoker, Hunter, Mage, Monk, Priest, Shaman, Warlock, Warrior
        (11, 'Staff'), (13, 'Staff'), (3,  'Staff'), (8,  'Staff'),
        (10, 'Staff'), (5,  'Staff'), (7,  'Staff'), (9,  'Staff'), (1,  'Staff'),
        -- Two-Handed Axe: Death Knight, Evoker, Hunter, Paladin, Shaman, Warrior
        (6,  'Two-Handed Axe'), (13, 'Two-Handed Axe'), (3,  'Two-Handed Axe'),
        (2,  'Two-Handed Axe'), (7,  'Two-Handed Axe'), (1,  'Two-Handed Axe'),
        -- Two-Handed Mace: Death Knight, Druid, Evoker, Paladin, Shaman, Warrior
        (6,  'Two-Handed Mace'), (11, 'Two-Handed Mace'), (13, 'Two-Handed Mace'),
        (2,  'Two-Handed Mace'), (7,  'Two-Handed Mace'), (1,  'Two-Handed Mace'),
        -- Two-Handed Sword: Death Knight, Evoker, Hunter, Paladin, Warrior
        (6,  'Two-Handed Sword'), (13, 'Two-Handed Sword'), (3,  'Two-Handed Sword'),
        (2,  'Two-Handed Sword'), (1,  'Two-Handed Sword'),
        -- Wand: Mage, Priest, Warlock
        (8,  'Wand'), (5,  'Wand'), (9,  'Wand'),
        -- Warglaives: Demon Hunter only
        (12, 'Warglaives')
    """)


    # ── 4. Add weapon_subtype to viz.slot_items ───────────────────────────────
    op.execute("""
        CREATE OR REPLACE VIEW viz.slot_items AS
        SELECT
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.slot_type,
            i.armor_type,
            i.weapon_subtype,
            i.primary_stat,
            i.item_category,
            i.tier_set_suffix,
            i.quality_track,
            s.id               AS source_id,
            s.instance_type,
            s.encounter_name,
            s.instance_name,
            s.blizzard_instance_id,
            s.blizzard_encounter_id,
            s.quality_tracks,
            s.is_junk,
            i.playable_class_ids
        FROM enrichment.items i
        JOIN enrichment.item_seasons ise ON ise.blizzard_item_id = i.blizzard_item_id
        JOIN patt.raid_seasons rs        ON rs.id = ise.season_id AND rs.is_active = TRUE
        LEFT JOIN enrichment.item_sources s
               ON s.blizzard_item_id = i.blizzard_item_id
              AND (
                      s.instance_type = 'world_boss'
                  OR (s.instance_type = 'dungeon' AND s.blizzard_instance_id = ANY(rs.current_instance_ids))
                  OR (s.instance_type = 'raid'    AND s.blizzard_instance_id = ANY(rs.current_raid_ids))
              )
        WHERE NOT COALESCE(s.is_junk, FALSE)
          AND (i.item_category != 'crafted' OR i.quality = 'EPIC')
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS ref.class_weapon_proficiencies")

    # Restore viz.slot_items without weapon_subtype
    op.execute("""
        CREATE OR REPLACE VIEW viz.slot_items AS
        SELECT
            i.blizzard_item_id, i.name, i.icon_url, i.slot_type, i.armor_type,
            i.primary_stat, i.item_category, i.tier_set_suffix, i.quality_track,
            s.id AS source_id, s.instance_type, s.encounter_name, s.instance_name,
            s.blizzard_instance_id, s.blizzard_encounter_id, s.quality_tracks,
            s.is_junk, i.playable_class_ids
        FROM enrichment.items i
        JOIN enrichment.item_seasons ise ON ise.blizzard_item_id = i.blizzard_item_id
        JOIN patt.raid_seasons rs ON rs.id = ise.season_id AND rs.is_active = TRUE
        LEFT JOIN enrichment.item_sources s
               ON s.blizzard_item_id = i.blizzard_item_id
              AND (
                      s.instance_type = 'world_boss'
                  OR (s.instance_type = 'dungeon' AND s.blizzard_instance_id = ANY(rs.current_instance_ids))
                  OR (s.instance_type = 'raid'    AND s.blizzard_instance_id = ANY(rs.current_raid_ids))
              )
        WHERE NOT COALESCE(s.is_junk, FALSE)
          AND (i.item_category != 'crafted' OR i.quality = 'EPIC')
    """)

    op.execute("ALTER TABLE enrichment.items DROP COLUMN IF EXISTS weapon_subtype")

    # Restore sp_rebuild_items without weapon_subtype
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_items()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.items CASCADE;

            INSERT INTO enrichment.items (
                blizzard_item_id, name, icon_url, slot_type, armor_type,
                item_category, quality_track, quality, playable_class_ids, enriched_at
            )
            SELECT
                bi.blizzard_item_id,
                COALESCE(NULLIF(trim(bi.payload->>'name'), ''), 'Unknown Item'),
                lii.icon_url,
                CASE bi.payload->'inventory_type'->>'type'
                    WHEN 'HEAD'       THEN 'head'   WHEN 'NECK'      THEN 'neck'
                    WHEN 'SHOULDER'   THEN 'shoulder' WHEN 'BACK'    THEN 'back'
                    WHEN 'CLOAK'      THEN 'back'   WHEN 'CHEST'     THEN 'chest'
                    WHEN 'ROBE'       THEN 'chest'  WHEN 'WAIST'     THEN 'waist'
                    WHEN 'LEGS'       THEN 'legs'   WHEN 'FEET'      THEN 'feet'
                    WHEN 'WRIST'      THEN 'wrist'  WHEN 'HAND'      THEN 'hands'
                    WHEN 'FINGER'     THEN 'finger' WHEN 'TRINKET'   THEN 'trinket'
                    WHEN 'WEAPON'     THEN 'one_hand' WHEN 'TWOHWEAPON' THEN 'two_hand'
                    WHEN 'RANGED'     THEN 'ranged' WHEN 'OFFHAND'   THEN 'off_hand'
                    WHEN 'HOLDABLE'   THEN 'off_hand' WHEN 'SHIELD'  THEN 'off_hand'
                    ELSE 'other'
                END,
                CASE WHEN (bi.payload->'item_class'->>'id')::int = 4
                     THEN CASE bi.payload->'item_subclass'->>'name'
                              WHEN 'Cloth' THEN 'cloth' WHEN 'Leather' THEN 'leather'
                              WHEN 'Mail'  THEN 'mail'  WHEN 'Plate'   THEN 'plate'
                              ELSE NULL END
                     ELSE NULL END,
                'unclassified', qt.quality_track,
                bi.payload -> 'quality' ->> 'type',
                CASE WHEN jsonb_array_length(COALESCE(
                         bi.payload->'preview_item'->'requirements'->'playable_classes'->'links',
                         '[]'::jsonb)) = 0 THEN NULL
                     ELSE ARRAY(SELECT (cls->>'id')::int FROM jsonb_array_elements(
                          bi.payload->'preview_item'->'requirements'->'playable_classes'->'links') AS cls)
                END,
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id) blizzard_item_id, payload
                FROM landing.blizzard_items ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            LEFT JOIN landing.blizzard_item_icons lii ON lii.blizzard_item_id = bi.blizzard_item_id
            LEFT JOIN landing.blizzard_item_quality_tracks qt ON qt.blizzard_item_id = bi.blizzard_item_id;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $$
    """)
