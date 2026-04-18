"""Phase E — Enrichment classification overhaul + item_seasons bridge.

Bugs fixed:
  Bug 2 (raid drops misclassified as tier): sp_update_item_categories() used
    Wowhead /item-set= tooltip links for tier detection — matches any item in a
    named set, not just real tier pieces.  Replaced with guild_identity.tier_token_attrs
    lookup: an item is tier only if it matches a real tier token chain.
  Bug 3 (legacy tier pieces): old-expansion items had no season link, so they
    passed the old tier heuristic.  The new item_seasons bridge filters all
    categories to the active season — legacy items become 'unclassified' and
    are invisible to the gear plan.

Changes:
  enrichment.items.item_category:
    CHECK changed from ('tier','catalyst','crafted','drop','unknown')
                    to ('raid','dungeon','world_boss','crafted','tier','catalyst','unclassified')
    DEFAULT changed from 'unknown' to 'unclassified'

  New table:
    enrichment.item_seasons — many-to-many (blizzard_item_id × season_id)

  Stored procedures updated:
    sp_update_item_categories() — rewritten: tier via tier_token_attrs + item_seasons,
                                  not Wowhead tooltip HTML; new category values
    sp_rebuild_item_recipes()   — adds unclassified→crafted reclassification + season link
                                  (makes it safe to call from crafting sync)
    sp_rebuild_all()            — adds sp_rebuild_item_seasons() call

  New stored procedure:
    enrichment.sp_rebuild_item_seasons() — populates item_seasons bridge:
      raid     via source blizzard_instance_id in patt.raid_seasons.current_raid_ids
      dungeon  via source blizzard_instance_id in patt.raid_seasons.current_instance_ids
      tier     via token chain (token sources in raid → tier slot+armor_type match)
      catalyst via quality_track='C' in catalyst slots (back/wrist/waist/feet), active season
      crafted  via active season (all recipes are inherently current content)

  View updated:
    viz.slot_items — adds JOIN to item_seasons filtered to active season.
      Replaces the Python-side blizzard_instance_id filter in get_available_items().

Python changes (applied alongside this migration):
  gear_plan_service.get_available_items():
    - WHERE clause: 'drop' → IN ('raid','dungeon'); season instance filter removed (now in view)
    - Grouping code: cat=='drop' → cat in ('raid','dungeon'); group by cat not itype
  crafting_sync.run_crafting_sync():
    - Calls CALL enrichment.sp_rebuild_item_recipes() after sync to promote
      newly-craftable unclassified items to 'crafted' automatically.

Revision ID: 0107
Revises: 0106
"""

from alembic import op

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade():
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: Drop old CHECK constraint first, then migrate data, then add new
    # constraint.  Order matters: the data migration sets 'unclassified' which
    # is not valid under the old constraint, so the old constraint must go first.
    # ─────────────────────────────────────────────────────────────────────────

    op.execute("""
        ALTER TABLE enrichment.items
            DROP CONSTRAINT items_item_category_check
    """)

    # Convert old category values to new equivalents (no constraint in effect).
    # 'unknown' → 'unclassified' (name change only)
    # 'drop'    → 'unclassified' (will be reclassified as 'raid'/'dungeon' by sproc)
    op.execute("""
        UPDATE enrichment.items
           SET item_category = 'unclassified'
         WHERE item_category IN ('unknown', 'drop')
    """)

    # Add new constraint and update default
    op.execute("""
        ALTER TABLE enrichment.items
            ADD CONSTRAINT items_item_category_check
                CHECK (item_category IN
                       ('raid','dungeon','world_boss','crafted','tier','catalyst','unclassified')),
            ALTER COLUMN item_category SET DEFAULT 'unclassified'
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: Create enrichment.item_seasons bridge table
    #
    # Many-to-many: one item can belong to multiple seasons (e.g. a M+ dungeon
    # item reused across expansions), and one season has many items.
    # FK to enrichment.items ON DELETE CASCADE keeps this table clean when items
    # are rebuilt via sp_rebuild_items() → TRUNCATE enrichment.items CASCADE.
    # ─────────────────────────────────────────────────────────────────────────

    op.execute("""
        CREATE TABLE enrichment.item_seasons (
            blizzard_item_id  INTEGER NOT NULL
                              REFERENCES enrichment.items (blizzard_item_id)
                              ON DELETE CASCADE,
            season_id         INTEGER NOT NULL
                              REFERENCES patt.raid_seasons (id)
                              ON DELETE CASCADE,
            PRIMARY KEY (blizzard_item_id, season_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_enrichment_item_seasons_season
            ON enrichment.item_seasons (season_id)
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: New and updated stored procedures
    # ─────────────────────────────────────────────────────────────────────────

    # sp_rebuild_item_seasons: populate item_seasons from source instance IDs and
    # the tier token chain.
    #
    # Population order matters — step 4 (catalyst) depends on step 3 (tier)
    # having been inserted first so the season anchor exists.
    #
    # Step 1 — raid items: source blizzard_instance_id in current_raid_ids
    # Step 2 — dungeon items: source blizzard_instance_id in current_instance_ids
    # Step 3 — tier pieces: a token for this slot+armor_type has a source in the
    #           season's raid instances (token chain: token → tier piece)
    # Step 4 — catalyst pieces: quality_track='C' in catalyst slots; season-linked
    #           via active season (catalyst items have no direct instance source)
    # Step 5 — crafted items: linked to the active season (recipes are always
    #           current content by definition of the sync cadence)
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

            -- 3. Tier pieces: resolved via tier token chain.
            --    token_item_id (from tier_token_attrs) → token has a source in the
            --    season's raid → tier piece matches that token's target_slot+armor_type.
            --    This is the definitive test: no Wowhead tooltip HTML involved.
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN guild_identity.tier_token_attrs tta
                ON (tta.target_slot = ei.slot_type OR tta.target_slot = 'any')
               AND (ei.armor_type   = tta.armor_type   OR tta.armor_type   = 'any')
              JOIN guild_identity.wow_items wi_tk
                ON wi_tk.id = tta.token_item_id
              JOIN guild_identity.item_sources s
                ON s.item_id = wi_tk.id
               AND NOT s.is_suspected_junk
              JOIN patt.raid_seasons rs
                ON s.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 4. Catalyst pieces: quality_track='C' in catalyst slots.
            --    Catalyst back/wrist/waist/feet pieces have no direct instance source;
            --    link them to the active season directly.
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 5. Crafted items: link to active season (recipes are current content)
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

    # sp_rebuild_item_recipes: mirror item_recipe_links + reclassify unclassified items.
    #
    # The reclassification tail (after TRUNCATE+INSERT) makes this procedure safe
    # to call from the crafting sync without a full sp_rebuild_all().  When the
    # crafting sync discovers new recipes, calling sp_rebuild_item_recipes() will:
    #   a) rebuild the recipe list from guild_identity
    #   b) promote any 'unclassified' items that now have recipes to 'crafted'
    #   c) link those newly-crafted items to the active season in item_seasons
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

    # sp_update_item_categories: classify item_category for all enrichment.items.
    #
    # Rewritten to use definitive data relationships instead of Wowhead tooltip HTML.
    # Must be called AFTER sp_rebuild_item_sources, sp_rebuild_item_recipes,
    # and sp_rebuild_item_seasons.
    #
    # Rules applied in priority order (each step only touches 'unclassified' rows
    # except crafted which resets and re-applies first):
    #
    #   1. 'crafted'    — has a row in enrichment.item_recipes
    #   2. 'tier'       — tier slot + armor_type matches guild_identity.tier_token_attrs
    #                     + item is in item_seasons (current season, via token chain)
    #   3. 'catalyst'   — in catalyst slot (back/wrist/waist/feet), quality_track='C',
    #                     in item_seasons (active season link)
    #   4. 'raid'       — has non-junk raid source, in item_seasons
    #   5. 'dungeon'    — has non-junk dungeon source, in item_seasons
    #   6. 'world_boss' — has non-junk world_boss source, in item_seasons
    #   7. 'unclassified' — no evidence for any of the above; legacy items and
    #                        items with no current-season link stay here
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
            -- Reset all to 'unclassified' before applying rules
            UPDATE enrichment.items SET item_category = 'unclassified';

            -- 1. Crafted: item has at least one recipe link
            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_crafted = ROW_COUNT;

            -- 2. Tier: in tier slot, armor_type matches a token in tier_token_attrs,
            --    item is linked to a season (proven current via token chain in item_seasons).
            --    Uses guild_identity.tier_token_attrs — no Wowhead HTML heuristics.
            UPDATE enrichment.items ei
               SET item_category = 'tier'
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM guild_identity.tier_token_attrs tta
                      WHERE (tta.target_slot = ei.slot_type OR tta.target_slot = 'any')
                        AND (ei.armor_type = tta.armor_type OR tta.armor_type = 'any')
                   )
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_seasons eis
                      WHERE eis.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 3. Catalyst: quality_track='C' in catalyst slots, in current season.
            --    These are back/wrist/waist/feet set pieces obtainable only via the
            --    Revival Catalyst — they have no direct instance drop source.
            --    Future: add tier_set_suffix matching once that column is populated.
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

            -- 4. Raid: non-junk raid source, in current season
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

            -- 5. Dungeon: non-junk dungeon source, in current season
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

            -- 6. World boss: non-junk world_boss source, in current season
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

    # sp_rebuild_all: add sp_rebuild_item_seasons() between recipes and category update.
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_all()
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE NOTICE 'sp_rebuild_all: starting full enrichment rebuild';

            -- 1. Items first (all other enrichment tables FK to this)
            CALL enrichment.sp_rebuild_items();

            -- 2. Sources + recipes (needed for season and category classification)
            CALL enrichment.sp_rebuild_item_sources();
            CALL enrichment.sp_rebuild_item_recipes();

            -- 3. BIS + trinket ratings (depend only on enrichment.items)
            CALL enrichment.sp_rebuild_bis_entries();
            CALL enrichment.sp_rebuild_trinket_ratings();

            -- 4. Season membership bridge (requires sources + recipes)
            CALL enrichment.sp_rebuild_item_seasons();

            -- 5. Classify item categories (requires seasons + sources + recipes)
            CALL enrichment.sp_update_item_categories();

            -- 6. Flag junk sources (requires item_category to be set)
            CALL enrichment.sp_flag_junk_sources();

            RAISE NOTICE
                'sp_rebuild_all: complete — items=%, sources=%, recipes=%, '
                'seasons=%, bis=%, ratings=%',
                (SELECT count(*) FROM enrichment.items),
                (SELECT count(*) FROM enrichment.item_sources),
                (SELECT count(*) FROM enrichment.item_recipes),
                (SELECT count(*) FROM enrichment.item_seasons),
                (SELECT count(*) FROM enrichment.bis_entries),
                (SELECT count(*) FROM enrichment.trinket_ratings);
        END;
        $$
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Recreate viz.slot_items with item_seasons season filter
    #
    # Drops the view created in migration 0106 and recreates it with an INNER
    # JOIN to item_seasons, ensuring only active-season items are visible.
    # This replaces the Python-side blizzard_instance_id season filter in
    # get_available_items() — the view itself handles season scoping.
    # ─────────────────────────────────────────────────────────────────────────

    op.execute("DROP VIEW IF EXISTS viz.slot_items")
    op.execute("""
        CREATE VIEW viz.slot_items AS
        SELECT
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.slot_type,
            i.armor_type,
            i.primary_stat,
            i.item_category,
            i.tier_set_suffix,
            i.quality_track,
            s.id                    AS source_id,
            s.instance_type,
            s.encounter_name,
            s.instance_name,
            s.blizzard_instance_id,
            s.blizzard_encounter_id,
            s.quality_tracks,
            s.is_junk
        FROM enrichment.items i
        JOIN enrichment.item_seasons ise
            ON ise.blizzard_item_id = i.blizzard_item_id
        JOIN patt.raid_seasons rs
            ON rs.id = ise.season_id
           AND rs.is_active = TRUE
        LEFT JOIN enrichment.item_sources s
               ON s.blizzard_item_id = i.blizzard_item_id
        WHERE NOT COALESCE(s.is_junk, FALSE)
    """)


def downgrade():
    # ── Restore viz.slot_items to migration 0106 (no item_seasons join) ──────
    op.execute("DROP VIEW IF EXISTS viz.slot_items")
    op.execute("""
        CREATE VIEW viz.slot_items AS
        SELECT
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.slot_type,
            i.armor_type,
            i.primary_stat,
            i.item_category,
            i.tier_set_suffix,
            i.quality_track,
            s.id                    AS source_id,
            s.instance_type,
            s.encounter_name,
            s.instance_name,
            s.blizzard_instance_id,
            s.blizzard_encounter_id,
            s.quality_tracks,
            s.is_junk
        FROM enrichment.items i
        LEFT JOIN enrichment.item_sources s
               ON s.blizzard_item_id = i.blizzard_item_id
        WHERE NOT COALESCE(s.is_junk, FALSE)
    """)

    # ── Restore stored procedures to 0105 versions ───────────────────────────
    op.execute("DROP PROCEDURE IF EXISTS enrichment.sp_rebuild_item_seasons() CASCADE")

    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_all()
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE NOTICE 'sp_rebuild_all: starting full enrichment rebuild';
            CALL enrichment.sp_rebuild_items();
            CALL enrichment.sp_rebuild_item_sources();
            CALL enrichment.sp_rebuild_item_recipes();
            CALL enrichment.sp_rebuild_bis_entries();
            CALL enrichment.sp_rebuild_trinket_ratings();
            CALL enrichment.sp_update_item_categories();
            CALL enrichment.sp_flag_junk_sources();
            RAISE NOTICE
                'sp_rebuild_all: complete — items=%, sources=%, recipes=%, bis=%, ratings=%',
                (SELECT count(*) FROM enrichment.items),
                (SELECT count(*) FROM enrichment.item_sources),
                (SELECT count(*) FROM enrichment.item_recipes),
                (SELECT count(*) FROM enrichment.bis_entries),
                (SELECT count(*) FROM enrichment.trinket_ratings);
        END;
        $$
    """)

    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_update_item_categories()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_crafted  BIGINT;
            v_catalyst BIGINT;
            v_tier     BIGINT;
            v_drop     BIGINT;
            v_unknown  BIGINT;
        BEGIN
            UPDATE enrichment.items SET item_category = 'unknown';
            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_crafted = ROW_COUNT;
            UPDATE enrichment.items ei
               SET item_category = 'catalyst'
             WHERE ei.quality_track = 'C'
               AND ei.item_category <> 'crafted';
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;
            UPDATE enrichment.items ei
               SET item_category = 'tier'
              FROM guild_identity.wow_items wi
             WHERE wi.blizzard_item_id = ei.blizzard_item_id
               AND wi.wowhead_tooltip_html LIKE '%/item-set=%'
               AND ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.item_category NOT IN ('crafted', 'catalyst');
            GET DIAGNOSTICS v_tier = ROW_COUNT;
            UPDATE enrichment.items ei
               SET item_category = 'drop'
             WHERE ei.item_category = 'unknown'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type IN ('raid', 'dungeon', 'world_boss')
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_drop = ROW_COUNT;
            SELECT count(*) INTO v_unknown
              FROM enrichment.items WHERE item_category = 'unknown';
            RAISE NOTICE
                'sp_update_item_categories: crafted=%, catalyst=%, tier=%, drop=%, unknown=%',
                v_crafted, v_catalyst, v_tier, v_drop, v_unknown;
        END;
        $$
    """)

    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_recipes()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
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
            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_recipes: % rows inserted', v_count;
        END;
        $$
    """)

    # ── Drop item_seasons table (CASCADE handles FK deps) ─────────────────────
    op.execute("DROP TABLE IF EXISTS enrichment.item_seasons")

    # ── Restore old CHECK constraint and default ───────────────────────────────
    # First convert data: new values → old equivalents (done while new constraint still active)
    op.execute("""
        UPDATE enrichment.items
           SET item_category = 'drop'
         WHERE item_category IN ('raid', 'dungeon', 'world_boss')
    """)
    op.execute("""
        UPDATE enrichment.items
           SET item_category = 'unknown'
         WHERE item_category = 'unclassified'
    """)
    op.execute("""
        ALTER TABLE enrichment.items
            DROP CONSTRAINT items_item_category_check,
            ADD CONSTRAINT items_item_category_check
                CHECK (item_category IN
                       ('tier','catalyst','crafted','drop','unknown')),
            ALTER COLUMN item_category SET DEFAULT 'unknown'
    """)
