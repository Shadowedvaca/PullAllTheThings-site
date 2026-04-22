"""feat: enrichment.items primary_stat → primary_stats TEXT[]

Replace the single primary_stat VARCHAR(3) with a primary_stats TEXT[] array
collecting all distinct primary stat types found in the item payload.  An item
with only INTELLECT gets '{int}'; one with both INTELLECT and STRENGTH gets
'{int,str}'; NULL means no primary stat found.

This lets gear_plan_service filter correctly: spec.primary_stat = ANY(primary_stats)
passes adaptive items (multiple stats) for ALL eligible specs rather than
mis-assigning them based on which character happened to fetch the payload.

viz.slot_items rebuilt to expose primary_stats instead of primary_stat.

Revision ID: 0172
Revises: 0171
Create Date: 2026-04-22
"""
from alembic import op

revision = "0172"
down_revision = "0171"
branch_labels = None
depends_on = None

_VIEW_SQL = """
    CREATE VIEW viz.slot_items AS
    SELECT
        i.blizzard_item_id,
        i.name,
        i.icon_url,
        i.slot_type,
        i.armor_type,
        i.weapon_subtype,
        i.primary_stats,
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
        i.playable_class_ids,
        CASE
            WHEN i.slot_type IN ('two_hand', 'ranged') THEN 'main_hand_2h'
            WHEN i.slot_type = 'one_hand'              THEN 'main_hand_1h'
            WHEN i.slot_type = 'off_hand'              THEN 'off_hand'
            ELSE NULL
        END AS weapon_plan_slot
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
"""


def upgrade() -> None:
    # ── 1. Schema change on enrichment.items ──────────────────────────────────
    op.execute("DROP VIEW IF EXISTS viz.slot_items")
    op.execute("ALTER TABLE enrichment.items ADD COLUMN primary_stats TEXT[]")
    op.execute("ALTER TABLE enrichment.items DROP COLUMN IF EXISTS primary_stat")

    # ── 2. Update sp_rebuild_items ─────────────────────────────────────────────
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
                primary_stats,
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
                -- primary_stats: array of all distinct primary stat types in payload;
                -- NULL when none found
                NULLIF(
                    ARRAY(
                        SELECT DISTINCT CASE stat->'type'->>'type'
                                            WHEN 'INTELLECT' THEN 'int'
                                            WHEN 'AGILITY'   THEN 'agi'
                                            WHEN 'STRENGTH'  THEN 'str'
                                        END
                          FROM jsonb_array_elements(
                                   COALESCE(bi.payload->'preview_item'->'stats', '[]'::jsonb)
                               ) AS stat
                         WHERE stat->'type'->>'type' IN ('INTELLECT', 'AGILITY', 'STRENGTH')
                    ),
                    ARRAY[]::TEXT[]
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

    # ── 3. Rebuild viz.slot_items with primary_stats ───────────────────────────
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS viz.slot_items")
    op.execute("ALTER TABLE enrichment.items ADD COLUMN primary_stat VARCHAR(3)")
    op.execute("ALTER TABLE enrichment.items DROP COLUMN IF EXISTS primary_stats")
    # Restore view with primary_stat (single value)
    op.execute("""
        CREATE VIEW viz.slot_items AS
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
            i.playable_class_ids,
            CASE
                WHEN i.slot_type IN ('two_hand', 'ranged') THEN 'main_hand_2h'
                WHEN i.slot_type = 'one_hand'              THEN 'main_hand_1h'
                WHEN i.slot_type = 'off_hand'              THEN 'off_hand'
                ELSE NULL
            END AS weapon_plan_slot
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
