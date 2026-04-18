"""feat: playable_class_ids + quality on enrichment.items; filter tier by class

Revision ID: 0126
Revises: 0125
Create Date: 2026-04-17

Two fixes for gear plan slot drawer:

1. Tier items not filtered by character class:
   enrichment.items had no class restriction column.  All 13 tier sets for a
   given armor type appeared in the drawer instead of only the character's
   class set (e.g. Warrior saw all 3 plate tier sets).
   Fix: add playable_class_ids INTEGER[] populated from
   preview_item.requirements.playable_classes.links.  viz.slot_items exposes
   the column; gear_plan_service.py filters tier items by character class_id.

2. Non-epic crafted items surfacing in slot drawers:
   sp_update_item_categories classified all crafted items regardless of
   quality — 70 RARE and 17 UNCOMMON items were classified as 'crafted'.
   Fix: add quality VARCHAR(20) populated from payload->'quality'->>'type'.
   sp_update_item_categories now requires quality = 'EPIC' for crafted
   classification.  Non-epic crafted items remain 'unclassified'.
"""
from alembic import op

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE enrichment.items ADD COLUMN playable_class_ids INTEGER[]")
    op.execute("ALTER TABLE enrichment.items ADD COLUMN quality VARCHAR(20)")

    # ── sp_rebuild_items: populate both new columns ───────────────────────────
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
        $proc$
    """)

    # ── sp_update_item_categories: require EPIC for crafted ───────────────────
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_update_item_categories()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_crafted    BIGINT;
            v_tier       BIGINT;
            v_catalyst   BIGINT;
            v_raid       BIGINT;
            v_dungeon    BIGINT;
            v_world_boss BIGINT;
            v_unclassed  BIGINT;
        BEGIN
            UPDATE enrichment.items SET item_category = 'unclassified'
             WHERE item_category != 'crafted';

            SELECT count(*) INTO v_crafted
              FROM enrichment.items WHERE item_category = 'crafted';

            -- 1. Tier
            UPDATE enrichment.items ei
               SET item_category = 'tier'
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_set_members ism
                      JOIN patt.raid_seasons rs
                        ON ism.set_id = ANY(rs.tier_set_ids)
                       AND rs.is_active = TRUE
                      WHERE ism.blizzard_item_id = ei.blizzard_item_id
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.tier_tokens tt
                      WHERE tt.target_slot = ei.slot_type
                        AND (ei.armor_type = tt.armor_type OR tt.armor_type = 'any')
                   )
               AND NOT EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type IN ('raid', 'dungeon')
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 2. Catalyst
            UPDATE enrichment.items ei
               SET item_category = 'catalyst'
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
               AND ei.item_category = 'unclassified';
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 3. Raid
            UPDATE enrichment.items ei
               SET item_category = 'raid'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'raid'
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_raid = ROW_COUNT;

            -- 4. Dungeon
            UPDATE enrichment.items ei
               SET item_category = 'dungeon'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'dungeon'
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;

            -- 5. World boss
            UPDATE enrichment.items ei
               SET item_category = 'world_boss'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'world_boss'
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_world_boss = ROW_COUNT;

            -- 6. Crafted: epic only
            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE ei.item_category = 'unclassified'
               AND ei.quality = 'EPIC'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_crafted = ROW_COUNT;

            SELECT count(*) INTO v_unclassed
              FROM enrichment.items WHERE item_category = 'unclassified';

            RAISE NOTICE
                'sp_update_item_categories: crafted=%, tier=%, catalyst=%, '
                'raid=%, dungeon=%, world_boss=%, unclassified=%',
                v_crafted, v_tier, v_catalyst, v_raid, v_dungeon, v_world_boss, v_unclassed;
        END;
        $$
    """)

    # ── viz.slot_items: expose playable_class_ids (appended — PG requires new
    #    columns at end of list for CREATE OR REPLACE VIEW) ────────────────────
    op.execute("""
        CREATE OR REPLACE VIEW viz.slot_items AS
        SELECT i.blizzard_item_id,
               i.name,
               i.icon_url,
               i.slot_type,
               i.armor_type,
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
          LEFT JOIN enrichment.item_sources s ON s.blizzard_item_id = i.blizzard_item_id
         WHERE NOT COALESCE(s.is_junk, FALSE)
    """)


def downgrade():
    op.execute("""
        CREATE OR REPLACE VIEW viz.slot_items AS
        SELECT i.blizzard_item_id,
               i.name,
               i.icon_url,
               i.slot_type,
               i.armor_type,
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
               s.is_junk
          FROM enrichment.items i
          JOIN enrichment.item_seasons ise ON ise.blizzard_item_id = i.blizzard_item_id
          JOIN patt.raid_seasons rs        ON rs.id = ise.season_id AND rs.is_active = TRUE
          LEFT JOIN enrichment.item_sources s ON s.blizzard_item_id = i.blizzard_item_id
         WHERE NOT COALESCE(s.is_junk, FALSE)
    """)

    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_update_item_categories()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_crafted BIGINT; v_tier BIGINT; v_catalyst BIGINT;
            v_raid BIGINT; v_dungeon BIGINT; v_world_boss BIGINT; v_unclassed BIGINT;
        BEGIN
            UPDATE enrichment.items SET item_category = 'unclassified' WHERE item_category != 'crafted';
            SELECT count(*) INTO v_crafted FROM enrichment.items WHERE item_category = 'crafted';
            UPDATE enrichment.items ei SET item_category = 'tier'
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL AND ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_set_members ism
                            JOIN patt.raid_seasons rs ON ism.set_id = ANY(rs.tier_set_ids) AND rs.is_active = TRUE
                            WHERE ism.blizzard_item_id = ei.blizzard_item_id)
               AND EXISTS (SELECT 1 FROM enrichment.tier_tokens tt
                            WHERE tt.target_slot = ei.slot_type
                              AND (ei.armor_type = tt.armor_type OR tt.armor_type = 'any'))
               AND NOT EXISTS (SELECT 1 FROM enrichment.item_sources s
                                WHERE s.blizzard_item_id = ei.blizzard_item_id
                                  AND s.instance_type IN ('raid', 'dungeon') AND NOT s.is_junk);
            GET DIAGNOSTICS v_tier = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'catalyst'
             WHERE ei.slot_type IN ('back','wrist','waist','feet') AND ei.quality_track = 'C'
               AND ei.item_category = 'unclassified';
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'raid'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_sources s WHERE s.blizzard_item_id = ei.blizzard_item_id AND s.instance_type = 'raid' AND NOT s.is_junk);
            GET DIAGNOSTICS v_raid = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'dungeon'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_sources s WHERE s.blizzard_item_id = ei.blizzard_item_id AND s.instance_type = 'dungeon' AND NOT s.is_junk);
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'world_boss'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_sources s WHERE s.blizzard_item_id = ei.blizzard_item_id AND s.instance_type = 'world_boss' AND NOT s.is_junk);
            GET DIAGNOSTICS v_world_boss = ROW_COUNT;
            SELECT count(*) INTO v_unclassed FROM enrichment.items WHERE item_category = 'unclassified';
            RAISE NOTICE 'sp_update_item_categories: crafted=%, tier=%, catalyst=%, raid=%, dungeon=%, world_boss=%, unclassified=%',
                v_crafted, v_tier, v_catalyst, v_raid, v_dungeon, v_world_boss, v_unclassed;
        END;
        $$
    """)

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
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id)
                    blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            LEFT JOIN landing.blizzard_item_icons lii ON lii.blizzard_item_id = bi.blizzard_item_id;
            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $proc$
    """)

    op.execute("ALTER TABLE enrichment.items DROP COLUMN quality")
    op.execute("ALTER TABLE enrichment.items DROP COLUMN playable_class_ids")
