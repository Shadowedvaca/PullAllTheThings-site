"""fix enrichment pipeline: correct sp_rebuild_all step order, consolidate crafted classification

Revision ID: 0118
Revises: 0117
Create Date: 2026-04-16

Fixes three bugs in the enrichment rebuild pipeline:

Issue 1 — Wrong step order in sp_rebuild_all:
  sp_update_item_categories checks enrichment.item_seasons for tier, catalyst, raid,
  dungeon, and world_boss classification, but sp_rebuild_item_seasons ran AFTER it
  (and started with TRUNCATE). On a fresh rebuild item_seasons was empty → those 5
  categories all stayed unclassified.  Fix: run sp_rebuild_item_seasons before
  sp_update_item_categories.

Issue 2 — Double crafted classification:
  sp_rebuild_item_recipes promoted unclassified→crafted in enrichment.items, then
  sp_update_item_categories reset everything to unclassified before re-classifying,
  making the promotion wasted work. Fix: sp_update_item_categories now skips the
  reset for items already classified as 'crafted' (which sp_rebuild_item_recipes
  set), and drops its own redundant crafted step. sp_rebuild_item_recipes remains
  the single owner of crafted classification and continues to work correctly for
  the incremental crafting-sync path.

Issue 3 (no SQL change — handled in bis_routes.py):
  Fill Landing only fetched items found in journal encounter loot tables. Crafted
  items were never fetched, so sp_rebuild_items (which reads landing.blizzard_items)
  could never populate them into enrichment.items, causing sp_rebuild_item_recipes
  to return 0 for all crafted items.  Fix: bis_routes.py now also queries
  item_recipe_links and adds those blizzard_item_ids to the Fill Landing fetch.
"""
from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None


def upgrade():
    # ── Fix sp_update_item_categories ─────────────────────────────────────────
    # Changes vs 0109:
    #   1. Reset only resets non-crafted items; crafted items are already correct
    #      because sp_rebuild_item_recipes (which runs before this in sp_rebuild_all)
    #      owns crafted classification.
    #   2. Crafted step removed — no longer needed here.
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
            -- Reset non-crafted items only.  Crafted classification is owned by
            -- sp_rebuild_item_recipes, which runs before this in sp_rebuild_all.
            UPDATE enrichment.items SET item_category = 'unclassified'
             WHERE item_category != 'crafted';

            -- Count crafted so the NOTICE stays informative
            SELECT count(*) INTO v_crafted
              FROM enrichment.items WHERE item_category = 'crafted';

            -- 1. Tier: strict slot match + in season + NO direct raid/dungeon source.
            UPDATE enrichment.items ei
               SET item_category = 'tier'
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM guild_identity.tier_token_attrs tta
                      WHERE tta.target_slot = ei.slot_type
                        AND (ei.armor_type = tta.armor_type OR tta.armor_type = 'any')
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   )
               AND NOT EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type IN ('raid', 'dungeon')
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 2. Catalyst: quality_track='C' in catalyst slots, in season
            UPDATE enrichment.items ei
               SET item_category = 'catalyst'
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 3. Raid: non-junk raid source, in season
            UPDATE enrichment.items ei
               SET item_category = 'raid'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'raid'
                        AND NOT s.is_junk
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_raid = ROW_COUNT;

            -- 4. Dungeon: non-junk dungeon source, in season
            UPDATE enrichment.items ei
               SET item_category = 'dungeon'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'dungeon'
                        AND NOT s.is_junk
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;

            -- 5. World boss: non-junk world_boss source, in season
            UPDATE enrichment.items ei
               SET item_category = 'world_boss'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'world_boss'
                        AND NOT s.is_junk
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
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

    # ── Fix sp_rebuild_all: correct step order ────────────────────────────────
    # Correct order (was: sources → recipes → categories → seasons → junk):
    #   1. items
    #   2. sources
    #   3. recipes        — promotes crafted in enrichment.items
    #   4. item_seasons   — needs item_recipes (crafted step); needed BY categories
    #   5. categories     — uses item_seasons for tier/catalyst/raid/dungeon/world_boss
    #   6. junk sources
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_all()
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE NOTICE 'sp_rebuild_all: starting enrichment rebuild';

            -- 1. Items first (all other enrichment tables FK to this)
            CALL enrichment.sp_rebuild_items();

            -- 2. Sources (needed for category classification + junk flagging)
            CALL enrichment.sp_rebuild_item_sources();

            -- 3. Recipes (promotes unclassified→crafted; required by sp_rebuild_item_seasons)
            CALL enrichment.sp_rebuild_item_recipes();

            -- 4. Item seasons (requires item_recipes for crafted step;
            --    required by sp_update_item_categories for all non-crafted steps)
            CALL enrichment.sp_rebuild_item_seasons();

            -- 5. Classify item categories (requires item_seasons)
            CALL enrichment.sp_update_item_categories();

            -- 6. Flag junk sources (requires item_category)
            CALL enrichment.sp_flag_junk_sources();

            RAISE NOTICE
                'sp_rebuild_all: complete — items=%, sources=%, recipes=%',
                (SELECT count(*) FROM enrichment.items),
                (SELECT count(*) FROM enrichment.item_sources),
                (SELECT count(*) FROM enrichment.item_recipes);
        END;
        $$
    """)


def downgrade():
    # Restore 0109 sp_update_item_categories (with crafted step + full reset)
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
            UPDATE enrichment.items SET item_category = 'unclassified';

            -- 1. Crafted
            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_crafted = ROW_COUNT;

            -- 2. Tier: strict slot match + in season + NO direct raid/dungeon source.
            UPDATE enrichment.items ei
               SET item_category = 'tier'
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM guild_identity.tier_token_attrs tta
                      WHERE tta.target_slot = ei.slot_type
                        AND (ei.armor_type = tta.armor_type OR tta.armor_type = 'any')
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   )
               AND NOT EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type IN ('raid', 'dungeon')
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 3. Catalyst: quality_track='C' in catalyst slots, in season
            UPDATE enrichment.items ei
               SET item_category = 'catalyst'
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 4. Raid: non-junk raid source, in season
            UPDATE enrichment.items ei
               SET item_category = 'raid'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'raid'
                        AND NOT s.is_junk
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_raid = ROW_COUNT;

            -- 5. Dungeon: non-junk dungeon source, in season
            UPDATE enrichment.items ei
               SET item_category = 'dungeon'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'dungeon'
                        AND NOT s.is_junk
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;

            -- 6. World boss: non-junk world_boss source, in season
            UPDATE enrichment.items ei
               SET item_category = 'world_boss'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type = 'world_boss'
                        AND NOT s.is_junk
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
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

    # Restore 0115 sp_rebuild_all (wrong order — categories before seasons)
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_all()
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE NOTICE 'sp_rebuild_all: starting enrichment rebuild';

            -- 1. Items first (all other enrichment tables FK to this)
            CALL enrichment.sp_rebuild_items();

            -- 2. Sources + recipes (needed for category classification)
            CALL enrichment.sp_rebuild_item_sources();
            CALL enrichment.sp_rebuild_item_recipes();

            -- 3. Classify item categories (requires sources + recipes)
            CALL enrichment.sp_update_item_categories();

            -- 4. Item seasons (requires item_category)
            CALL enrichment.sp_rebuild_item_seasons();

            -- 5. Flag junk sources (requires item_category)
            CALL enrichment.sp_flag_junk_sources();

            RAISE NOTICE
                'sp_rebuild_all: complete — items=%, sources=%, recipes=%',
                (SELECT count(*) FROM enrichment.items),
                (SELECT count(*) FROM enrichment.item_sources),
                (SELECT count(*) FROM enrichment.item_recipes);
        END;
        $$
    """)
