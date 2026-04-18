"""feat: Phase H — add blizzard_item_id to item_recipe_links; retire wow_items from enrichment

Phase H of the gear plan schema overhaul.

Track 1 — Fix the recipe bridge:
  - Add blizzard_item_id INTEGER to guild_identity.item_recipe_links
  - Backfill from guild_identity.wow_items via the existing item_id FK
  - Rewrite sp_rebuild_item_recipes to use irl.blizzard_item_id directly,
    eliminating the JOIN guild_identity.wow_items that was the only reason
    wow_items was needed by the enrichment layer.

The item_recipe_links table is kept as a bridge; wow_items is no longer
needed by any stored procedure in the enrichment schema.

Revision ID: 0132
Revises: 0131
Create Date: 2026-04-17
"""
from alembic import op

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None


def upgrade():
    # ── Track 1: Add blizzard_item_id to item_recipe_links ────────────────────
    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
        ADD COLUMN blizzard_item_id INTEGER;
    """)

    # Backfill from wow_items (all existing rows have a valid item_id FK)
    op.execute("""
        UPDATE guild_identity.item_recipe_links irl
           SET blizzard_item_id = wi.blizzard_item_id
          FROM guild_identity.wow_items wi
         WHERE wi.id = irl.item_id;
    """)

    # ── Update sp_rebuild_item_recipes to eliminate the wow_items JOIN ─────────
    #
    # Previously: JOIN guild_identity.wow_items wi ON wi.id = irl.item_id
    #             to resolve irl.item_id → wi.blizzard_item_id
    # Now:        irl.blizzard_item_id is stored directly on item_recipe_links
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_recipes()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_recipes  BIGINT;
            v_promoted BIGINT;
        BEGIN
            TRUNCATE enrichment.item_recipes;

            INSERT INTO enrichment.item_recipes (
                blizzard_item_id,
                recipe_id,
                match_type,
                confidence
            )
            SELECT
                irl.blizzard_item_id,
                irl.recipe_id,
                irl.match_type,
                irl.confidence
            FROM guild_identity.item_recipe_links irl
            WHERE irl.blizzard_item_id IS NOT NULL
              AND irl.blizzard_item_id IN (
                  SELECT blizzard_item_id FROM enrichment.items
              );
            GET DIAGNOSTICS v_recipes = ROW_COUNT;

            -- Promote 'unclassified' items that now have a recipe to 'crafted'
            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_promoted = ROW_COUNT;

            -- Link newly-crafted items to the active season (idempotent)
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ir.blizzard_item_id, rs.id
              FROM enrichment.item_recipes ir
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
            ON CONFLICT DO NOTHING;

            RAISE NOTICE
                'sp_rebuild_item_recipes: % recipe rows inserted, % unclassified→crafted promoted',
                v_recipes, v_promoted;
        END;
        $$
    """)


def downgrade():
    # Restore old sp_rebuild_item_recipes with wow_items JOIN
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_recipes()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_recipes  BIGINT;
            v_promoted BIGINT;
        BEGIN
            TRUNCATE enrichment.item_recipes;

            INSERT INTO enrichment.item_recipes (
                blizzard_item_id,
                recipe_id,
                match_type,
                confidence
            )
            SELECT
                wi.blizzard_item_id,
                irl.recipe_id,
                irl.match_type,
                irl.confidence
            FROM guild_identity.item_recipe_links irl
            JOIN guild_identity.wow_items wi ON wi.id = irl.item_id
            WHERE wi.blizzard_item_id IN (
                SELECT blizzard_item_id FROM enrichment.items
            );
            GET DIAGNOSTICS v_recipes = ROW_COUNT;

            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_promoted = ROW_COUNT;

            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ir.blizzard_item_id, rs.id
              FROM enrichment.item_recipes ir
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
            ON CONFLICT DO NOTHING;

            RAISE NOTICE
                'sp_rebuild_item_recipes: % recipe rows inserted, % unclassified→crafted promoted',
                v_recipes, v_promoted;
        END;
        $$
    """)

    op.execute("""
        ALTER TABLE guild_identity.item_recipe_links
        DROP COLUMN blizzard_item_id;
    """)
