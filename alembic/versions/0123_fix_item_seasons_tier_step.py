"""fix: item_seasons tier step + tighten 'any' token handling

Revision ID: 0123
Revises: 0122
Create Date: 2026-04-17

Two related bugs producing 3688 tier items in item_seasons (expected ~20):

Bug 1 — sp_rebuild_item_seasons step 3 still uses guild_identity tables:
  The tier piece → season linkage still joined through
  guild_identity.tier_token_attrs, guild_identity.wow_items, and
  guild_identity.item_sources.  tier_token_attrs has 21 rows from the old
  pipeline including the 'any'/'any' Chiming Void Curio, which matches every
  item in every tier slot → all 3688 item_set_members items in tier slots
  were added to item_seasons.
  Fix: rewrite step 3 to use enrichment.item_set_members, enrichment.tier_tokens,
  and enrichment.item_sources.

Bug 2 — 'any' target_slot wildcard in tier classification and item_seasons:
  The Chiming Void Curio (target_slot='any', armor_type='any') matches every
  slot+armor_type combination.  When used in EXISTS checks it makes every item
  in an item set qualify as 'tier' and for item_seasons membership.
  Fix: use strict target_slot = ei.slot_type matching (no OR 'any' branch) in
  both sp_update_item_categories and sp_rebuild_item_seasons step 3.
  The Curio still exists in enrichment.tier_tokens for viz.tier_piece_sources
  (where the broader match is intentional — it shows which bosses offer the
  universal token for any slot).
"""
from alembic import op

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None


def upgrade():
    # ── Fix sp_rebuild_item_seasons step 3 ───────────────────────────────────
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

            -- 1. Raid items: source instance is in current_raid_ids
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs
                ON eis.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE eis.instance_type = 'raid'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_raid = ROW_COUNT;

            -- 2. Dungeon items: source instance is in current_instance_ids
            INSERT INTO enrichment.item_seasons (blizzard_item_id, season_id)
            SELECT DISTINCT eis.blizzard_item_id, rs.id
              FROM enrichment.item_sources eis
              JOIN patt.raid_seasons rs
                ON eis.blizzard_instance_id = ANY(rs.current_instance_ids)
             WHERE eis.instance_type = 'dungeon'
            ON CONFLICT DO NOTHING;
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;

            -- 3. Tier pieces: item is in a known item set + a slot-specific tier
            --    token exists for this slot+armor_type + that token drops from a
            --    current-season raid boss.
            --    Strict target_slot = ei.slot_type (no 'any' wildcard) so the
            --    Chiming Void Curio's catch-all row does not flood item_seasons
            --    with old-expansion set items.
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

    # ── Fix sp_update_item_categories — strict slot match for tier ────────────
    # Same 'any' wildcard problem: replace OR tt.target_slot='any' with strict
    # equality so the Chiming Void Curio doesn't classify every set item as tier.
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

            -- 1. Tier: in a known item set + slot-specific token exists for this
            --    slot+armor_type + no direct raid/dungeon source.
            --    Strict target_slot match — excludes the 'any'/'any' Chiming Void
            --    Curio so only items with a genuine slot-matched token qualify.
            UPDATE enrichment.items ei
               SET item_category = 'tier'
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_set_members ism
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


def downgrade():
    # Restore 0121 versions (wrong guild_identity refs in item_seasons, 'any' wildcard)
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
              JOIN guild_identity.tier_token_attrs tta
                ON tta.target_slot = ei.slot_type
               AND (ei.armor_type = tta.armor_type OR tta.armor_type = 'any')
              JOIN guild_identity.wow_items wi_tk ON wi_tk.id = tta.token_item_id
              JOIN guild_identity.item_sources s ON s.item_id = wi_tk.id AND NOT s.is_suspected_junk
              JOIN patt.raid_seasons rs ON s.blizzard_instance_id = ANY(rs.current_raid_ids)
             WHERE ei.slot_type IN ('head','shoulder','chest','hands','legs')
               AND ei.armor_type IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM enrichment.item_sources es
                                WHERE es.blizzard_item_id = ei.blizzard_item_id
                                  AND es.instance_type IN ('raid','dungeon') AND NOT es.is_junk)
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
        CREATE OR REPLACE PROCEDURE enrichment.sp_update_item_categories()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_crafted BIGINT; v_tier BIGINT; v_catalyst BIGINT;
            v_raid BIGINT; v_dungeon BIGINT; v_world_boss BIGINT; v_unclassed BIGINT;
        BEGIN
            UPDATE enrichment.items SET item_category = 'unclassified' WHERE item_category != 'crafted';
            SELECT count(*) INTO v_crafted FROM enrichment.items WHERE item_category = 'crafted';
            UPDATE enrichment.items ei SET item_category = 'tier'
             WHERE ei.slot_type IN ('head','shoulder','chest','hands','legs')
               AND ei.armor_type IS NOT NULL AND ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_set_members ism WHERE ism.blizzard_item_id = ei.blizzard_item_id)
               AND EXISTS (SELECT 1 FROM enrichment.tier_tokens tt
                            WHERE (tt.target_slot = ei.slot_type OR tt.target_slot = 'any')
                              AND (ei.armor_type = tt.armor_type OR tt.armor_type = 'any'))
               AND NOT EXISTS (SELECT 1 FROM enrichment.item_sources s
                                WHERE s.blizzard_item_id = ei.blizzard_item_id
                                  AND s.instance_type IN ('raid','dungeon') AND NOT s.is_junk);
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
