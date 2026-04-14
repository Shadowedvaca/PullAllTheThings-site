"""Fix tier classification — exclude items with direct raid/dungeon sources.

Bug: sp_update_item_categories() and sp_rebuild_item_seasons() classified 2394
items as 'tier' instead of the expected ~20 real tier pieces.  Root causes:

  1. guild_identity.tier_token_attrs has one wildcard row (target_slot='any',
     armor_type='any') which matches EVERY item in tier slots with any armor_type.

  2. No discriminator separated real tier pieces from regular boss drops in the
     same slots — regular drops (e.g. Mask of Darkest Intent) also match the
     slot+armor_type condition.

Fix: real tier pieces have NO direct raid/dungeon boss-drop sources in
item_sources — they come via the tier token exchange, not as direct drops.
Adding NOT EXISTS (direct non-junk raid/dungeon source) to both procedures
correctly isolates real tier pieces from regular drops.

Also adds strict slot matching: requires tta.target_slot = ei.slot_type
(not 'any') to exclude the wildcard row from classification logic.
The wildcard row is still useful for the viz.tier_piece_sources view
(which uses it for token-chain lookups on already-classified items) but
should not drive classification.

Revision ID: 0109
Revises: 0108
"""

from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None


def upgrade():
    # ── Fix sp_rebuild_item_seasons() — tier step ─────────────────────────────
    # Added: strict slot match (tta.target_slot = ei.slot_type, not 'any')
    # Added: NOT EXISTS direct non-junk raid/dungeon source
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

            -- 3. Tier pieces: token chain + NO direct raid/dungeon source.
            --    Uses STRICT slot matching (tta.target_slot = ei.slot_type) to exclude
            --    the wildcard (any, any) row in tier_token_attrs.
            --    Real tier pieces have no direct boss-drop source rows — they arrive
            --    via the tier token exchange, so the NOT EXISTS guard is correct.
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN guild_identity.tier_token_attrs tta
                ON tta.target_slot = ei.slot_type
               AND (ei.armor_type = tta.armor_type OR tta.armor_type = 'any')
              JOIN guild_identity.wow_items wi_tk
                ON wi_tk.id = tta.token_item_id
              JOIN guild_identity.item_sources s
                ON s.item_id = wi_tk.id
               AND NOT s.is_suspected_junk
              JOIN patt.raid_seasons rs
                ON s.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               -- Real tier pieces have no direct raid/dungeon drop source
               AND NOT EXISTS (
                     SELECT 1 FROM enrichment.item_sources es
                      WHERE es.blizzard_item_id = ei.blizzard_item_id
                        AND es.instance_type IN ('raid', 'dungeon')
                        AND NOT es.is_junk
                   )
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 4. Catalyst pieces: quality_track='C' in catalyst slots, active season
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 5. Crafted items: link to active season
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

    # ── Fix sp_update_item_categories() — tier step ───────────────────────────
    # Added: strict slot match (tta.target_slot = ei.slot_type)
    # Added: NOT EXISTS direct non-junk raid/dungeon source
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
            --    Strict match (tta.target_slot = ei.slot_type) excludes the wildcard
            --    (any, any) row in tier_token_attrs.
            --    NOT EXISTS guard ensures regular drops in tier slots are not swept up.
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


def downgrade():
    # Restore 0107 versions (without strict slot match / no-source guard)
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

            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs
                ON eis.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE eis.instance_type = 'raid'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_raid = ROW_COUNT;

            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs
                ON eis.blizzard_instance_id = ANY(rs.current_instance_ids)
             WHERE eis.instance_type = 'dungeon'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;

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

            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT ei.blizzard_item_id, rs.id
              FROM enrichment.items ei
              JOIN patt.raid_seasons rs ON rs.is_active = TRUE
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

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

            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_crafted = ROW_COUNT;

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
