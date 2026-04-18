"""fix: tier_set_ids on raid_seasons + ROBE inventory type maps to chest

Revision ID: 0125
Revises: 0124
Create Date: 2026-04-17

Two fixes:

Bug 1 — No way to distinguish current-season tier sets from old-expansion sets:
  item_set_members spans all 942+ Blizzard sets across all expansions.
  sp_update_item_categories and sp_rebuild_item_seasons had no mechanism to
  limit tier classification to the current season's sets — any cloth chest item
  in any set from any expansion matched because a current cloth chest token
  EXISTS.  Result: 3688 tier items instead of ~65 (13 classes × 5 slots).
  Fix: add tier_set_ids INTEGER[] to patt.raid_seasons (same pattern as
  current_raid_ids); filter item_set_members joins by the active season's
  tier_set_ids.  Midnight S1 seeds: {1978..1990} — 13 class tier sets.

Bug 2 — ROBE inventory type maps to 'other' instead of 'chest':
  sp_rebuild_items handles CHEST but not ROBE.  Cloth robes and some mail
  chest items use inventory_type='ROBE' in the Blizzard API (e.g. Voidbreaker's
  Robe, Abyssal Immolator's Dreadrobe).  Those items get slot_type='other',
  which excludes them from tier classification and the chest slot drawer.
  Fix: add WHEN 'ROBE' THEN 'chest' to the inventory_type CASE in
  sp_rebuild_items.
"""
from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def upgrade():
    # ── Add tier_set_ids to patt.raid_seasons ────────────────────────────────
    op.execute("""
        ALTER TABLE patt.raid_seasons
        ADD COLUMN tier_set_ids INTEGER[] NOT NULL DEFAULT '{}'
    """)

    # Seed Midnight S1 — 13 class tier sets (set IDs 1978–1990)
    op.execute("""
        UPDATE patt.raid_seasons
           SET tier_set_ids = '{1978,1979,1980,1981,1982,1983,1984,1985,1986,1987,1988,1989,1990}'
         WHERE is_active = TRUE
    """)

    # ── Fix sp_rebuild_items — add ROBE → chest ───────────────────────────────
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
            LEFT JOIN landing.blizzard_item_icons lii
                ON lii.blizzard_item_id = bi.blizzard_item_id;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $proc$
    """)

    # ── Fix sp_update_item_categories — filter by tier_set_ids ───────────────
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

            -- 1. Tier: item is in a current-season tier set (tier_set_ids) +
            --    a slot-specific token exists for this slot+armor_type +
            --    no direct raid/dungeon source.
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

            SELECT count(*) INTO v_unclassed
              FROM enrichment.items WHERE item_category = 'unclassified';

            RAISE NOTICE
                'sp_update_item_categories: crafted=%, tier=%, catalyst=%, '
                'raid=%, dungeon=%, world_boss=%, unclassified=%',
                v_crafted, v_tier, v_catalyst, v_raid, v_dungeon, v_world_boss, v_unclassed;
        END;
        $$
    """)

    # ── Fix sp_rebuild_item_seasons — filter step 3 by tier_set_ids ──────────
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_seasons()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_raid     BIGINT;
            v_dungeon  BIGINT;
            v_tier     BIGINT;
            v_catalyst BIGINT;
            v_crafted  BIGINT;
        BEGIN
            TRUNCATE enrichment.item_seasons;

            -- 1. Raid items
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs
                ON eis.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE eis.instance_type = 'raid'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_raid = ROW_COUNT;

            -- 2. Dungeon items
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs
                ON eis.blizzard_instance_id = ANY(rs.current_instance_ids)
             WHERE eis.instance_type = 'dungeon'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;

            -- 3. Tier pieces: in a current-season tier set + slot-specific token
            --    drops from a current-season raid boss + no direct drop source.
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN enrichment.item_set_members ism
                ON ism.blizzard_item_id = ei.blizzard_item_id
              JOIN enrichment.tier_tokens tt
                ON tt.target_slot = ei.slot_type
               AND (ei.armor_type = tt.armor_type OR tt.armor_type = 'any')
              JOIN enrichment.item_sources es
                ON es.blizzard_item_id = tt.blizzard_item_id
               AND NOT es.is_junk
              JOIN patt.raid_seasons rs
                ON es.blizzard_instance_id = ANY(rs.current_raid_ids)
               AND ism.set_id = ANY(rs.tier_set_ids)
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               AND NOT EXISTS (
                     SELECT 1 FROM enrichment.item_sources es2
                      WHERE es2.blizzard_item_id = ei.blizzard_item_id
                        AND es2.instance_type IN ('raid', 'dungeon')
                        AND NOT es2.is_junk
                   )
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 4. Catalyst pieces
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 5. Crafted items
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ir.blizzard_item_id, rs.id
              FROM enrichment.item_recipes ir
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_crafted = ROW_COUNT;

            RAISE NOTICE
                'sp_rebuild_item_seasons: raid=%, dungeon=%, tier=%, catalyst=%, crafted=%',
                v_raid, v_dungeon, v_tier, v_catalyst, v_crafted;
        END;
        $$
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
               AND EXISTS (SELECT 1 FROM enrichment.item_set_members ism WHERE ism.blizzard_item_id = ei.blizzard_item_id)
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
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_seasons()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_raid BIGINT; v_dungeon BIGINT; v_tier BIGINT;
            v_catalyst BIGINT; v_crafted BIGINT;
        BEGIN
            TRUNCATE enrichment.item_seasons;
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs ON eis.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE eis.instance_type = 'raid'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_raid = ROW_COUNT;
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs ON eis.blizzard_instance_id = ANY(rs.current_instance_ids)
             WHERE eis.instance_type = 'dungeon'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN enrichment.item_set_members ism ON ism.blizzard_item_id = ei.blizzard_item_id
              JOIN enrichment.tier_tokens tt
                ON tt.target_slot = ei.slot_type
               AND (ei.armor_type = tt.armor_type OR tt.armor_type = 'any')
              JOIN enrichment.item_sources es
                ON es.blizzard_item_id = tt.blizzard_item_id AND NOT es.is_junk
              JOIN patt.raid_seasons rs ON es.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE ei.slot_type IN ('head','shoulder','chest','hands','legs')
               AND ei.armor_type IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM enrichment.item_sources es2
                                WHERE es2.blizzard_item_id = ei.blizzard_item_id
                                  AND es2.instance_type IN ('raid','dungeon') AND NOT es2.is_junk)
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_tier = ROW_COUNT;
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
             WHERE ei.slot_type IN ('back','wrist','waist','feet') AND ei.quality_track = 'C'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ir.blizzard_item_id, rs.id
              FROM enrichment.item_recipes ir
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_crafted = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_seasons: raid=%, dungeon=%, tier=%, catalyst=%, crafted=%',
                v_raid, v_dungeon, v_tier, v_catalyst, v_crafted;
        END;
        $$
    """)

    op.execute("""
        ALTER TABLE patt.raid_seasons DROP COLUMN tier_set_ids
    """)
