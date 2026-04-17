"""fix: tier token JSON path + tighten tier classification

Revision ID: 0121
Revises: 0120
Create Date: 2026-04-17

Two bugs found after first live run:

Bug 1 — Wrong JSON paths in sp_rebuild_tier_tokens:
  The Blizzard item API returns tier token data inside the 'preview_item'
  sub-object, not at the top level.
    - Slot:         payload->'preview_item'->'spells'->0->>'description'
                    e.g. "Use: Synthesize a soulbound set hand item..."
    - armor_type:   payload->'preview_item'->'requirements'->'playable_classes'->'links'
  The top-level 'description' and 'requirements' fields are empty/absent
  for Miscellaneous/Junk tokens.  Result: all slot-specific tokens had
  target_slot=NULL and armor_type='any'.

Bug 2 — Tier classification too broad (3981 items):
  The 0120 tier check used item_set_members alone: any item in ANY of the
  942 Blizzard item sets (cosmetic, PvP, crafted, dungeon, etc.) in a tier
  slot with no direct raid/dungeon source was classified as 'tier'.
  Fix: require BOTH item_set_members (confirms this exact item ID is in a
  known set) AND tier_tokens match (confirms this expansion actually has
  tokens for this slot+armor_type — naturally limits to current season
  since only current raid tokens are in landing).
"""
from alembic import op

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


def upgrade():
    # ── Fix sp_rebuild_tier_tokens — correct preview_item JSON paths ──────────
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_tier_tokens()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_slot_tokens BIGINT;
            v_any_tokens  BIGINT;
        BEGIN
            TRUNCATE enrichment.tier_tokens;

            -- Pass 1: Miscellaneous/Junk slot-specific tokens.
            -- Slot name is in the USE spell description inside preview_item.
            -- armor_type is inferred from playable_classes inside preview_item.requirements.
            INSERT INTO enrichment.tier_tokens
                   (blizzard_item_id, token_name, target_slot, armor_type, detected_at)
            SELECT
                bi.blizzard_item_id,
                bi.payload ->> 'name',
                CASE
                    WHEN lower(bi.payload -> 'preview_item' -> 'spells' -> 0 ->> 'description')
                         LIKE '%head%'     THEN 'head'
                    WHEN lower(bi.payload -> 'preview_item' -> 'spells' -> 0 ->> 'description')
                         LIKE '%shoulder%' THEN 'shoulder'
                    WHEN lower(bi.payload -> 'preview_item' -> 'spells' -> 0 ->> 'description')
                         LIKE '%chest%'    THEN 'chest'
                    WHEN lower(bi.payload -> 'preview_item' -> 'spells' -> 0 ->> 'description')
                         LIKE '%hand%'     THEN 'hands'
                    WHEN lower(bi.payload -> 'preview_item' -> 'spells' -> 0 ->> 'description')
                         LIKE '%leg%'      THEN 'legs'
                    ELSE NULL
                END,
                CASE
                    WHEN (
                        SELECT bool_or((cls ->> 'id')::int IN (5, 8, 9))
                          FROM jsonb_array_elements(
                               COALESCE(bi.payload -> 'preview_item'
                                        -> 'requirements' -> 'playable_classes' -> 'links',
                                        '[]'::jsonb)) AS cls
                    ) THEN 'cloth'
                    WHEN (
                        SELECT bool_or((cls ->> 'id')::int IN (4, 10, 11, 12, 13))
                          FROM jsonb_array_elements(
                               COALESCE(bi.payload -> 'preview_item'
                                        -> 'requirements' -> 'playable_classes' -> 'links',
                                        '[]'::jsonb)) AS cls
                    ) THEN 'leather'
                    WHEN (
                        SELECT bool_or((cls ->> 'id')::int IN (3, 7))
                          FROM jsonb_array_elements(
                               COALESCE(bi.payload -> 'preview_item'
                                        -> 'requirements' -> 'playable_classes' -> 'links',
                                        '[]'::jsonb)) AS cls
                    ) THEN 'mail'
                    WHEN (
                        SELECT bool_or((cls ->> 'id')::int IN (1, 2, 6))
                          FROM jsonb_array_elements(
                               COALESCE(bi.payload -> 'preview_item'
                                        -> 'requirements' -> 'playable_classes' -> 'links',
                                        '[]'::jsonb)) AS cls
                    ) THEN 'plate'
                    ELSE 'any'
                END,
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id) blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            WHERE (bi.payload -> 'item_class'   ->> 'name') = 'Miscellaneous'
              AND (bi.payload -> 'item_subclass' ->> 'name') = 'Junk'
              AND EXISTS (
                    SELECT 1
                    FROM landing.blizzard_journal_encounters lje
                    JOIN landing.blizzard_journal_instances  lji
                      ON lji.instance_id = lje.instance_id
                    WHERE lji.instance_type = 'raid'
                      AND EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(lje.payload -> 'items') AS item_entry
                            WHERE (item_entry -> 'item' ->> 'id') IS NOT NULL
                              AND (item_entry -> 'item' ->> 'id')::int = bi.blizzard_item_id
                          )
                  );
            GET DIAGNOSTICS v_slot_tokens = ROW_COUNT;

            -- Pass 2: Reagent/Context Token — any-slot, any-class tokens.
            INSERT INTO enrichment.tier_tokens
                   (blizzard_item_id, token_name, target_slot, armor_type, detected_at)
            SELECT
                bi.blizzard_item_id,
                bi.payload ->> 'name',
                'any',
                'any',
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id) blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            WHERE (bi.payload -> 'item_class'   ->> 'name') = 'Reagent'
              AND (bi.payload -> 'item_subclass' ->> 'name') = 'Context Token'
              AND EXISTS (
                    SELECT 1
                    FROM landing.blizzard_journal_encounters lje
                    JOIN landing.blizzard_journal_instances  lji
                      ON lji.instance_id = lje.instance_id
                    WHERE lji.instance_type = 'raid'
                      AND EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(lje.payload -> 'items') AS item_entry
                            WHERE (item_entry -> 'item' ->> 'id') IS NOT NULL
                              AND (item_entry -> 'item' ->> 'id')::int = bi.blizzard_item_id
                          )
                  );
            GET DIAGNOSTICS v_any_tokens = ROW_COUNT;

            RAISE NOTICE 'sp_rebuild_tier_tokens: % slot-specific, % any-slot tokens',
                v_slot_tokens, v_any_tokens;
        END;
        $$
    """)

    # ── Fix sp_update_item_categories — require BOTH item_set_members + tier_tokens
    # item_set_members: confirms this exact item ID is in a Blizzard item set.
    # tier_tokens:      confirms this expansion has tokens for this slot+armor_type,
    #                   which naturally limits tier classification to current season
    #                   (only current raid tokens are in landing.blizzard_items).
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

            -- 1. Tier: item is in a known item set (item_set_members confirms the
            --    item ID) AND a tier token exists for this slot+armor_type combination
            --    (tier_tokens confirms this is an active raid tier set).
            --    No direct raid/dungeon source (tokens deliver them, not boss loot tables).
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
                      WHERE (tt.target_slot = ei.slot_type OR tt.target_slot = 'any')
                        AND (ei.armor_type  = tt.armor_type  OR tt.armor_type  = 'any')
                   )
               AND NOT EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type IN ('raid', 'dungeon')
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 2. Catalyst: quality_track='C' in catalyst slots
            UPDATE enrichment.items ei
               SET item_category = 'catalyst'
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
               AND ei.item_category = 'unclassified';
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 3. Raid: non-junk raid source
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

            -- 4. Dungeon: non-junk dungeon source
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

            -- 5. World boss: non-junk world_boss source
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
    # Restore 0120 versions (wrong JSON paths, over-broad tier check)
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_tier_tokens()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_slot_tokens BIGINT;
            v_any_tokens  BIGINT;
        BEGIN
            TRUNCATE enrichment.tier_tokens;
            INSERT INTO enrichment.tier_tokens
                   (blizzard_item_id, token_name, target_slot, armor_type, detected_at)
            SELECT
                bi.blizzard_item_id,
                bi.payload ->> 'name',
                CASE
                    WHEN lower(bi.payload ->> 'description') LIKE '%head%'     THEN 'head'
                    WHEN lower(bi.payload ->> 'description') LIKE '%shoulder%' THEN 'shoulder'
                    WHEN lower(bi.payload ->> 'description') LIKE '%chest%'    THEN 'chest'
                    WHEN lower(bi.payload ->> 'description') LIKE '%hand%'     THEN 'hands'
                    WHEN lower(bi.payload ->> 'description') LIKE '%leg%'      THEN 'legs'
                    ELSE NULL
                END,
                CASE
                    WHEN (SELECT bool_or((cls ->> 'id')::int IN (5, 8, 9))
                            FROM jsonb_array_elements(
                                COALESCE(bi.payload->'requirements'->'playable_classes'->'links',
                                         '[]'::jsonb)) AS cls)
                    THEN 'cloth'
                    WHEN (SELECT bool_or((cls ->> 'id')::int IN (4, 10, 11, 12, 13))
                            FROM jsonb_array_elements(
                                COALESCE(bi.payload->'requirements'->'playable_classes'->'links',
                                         '[]'::jsonb)) AS cls)
                    THEN 'leather'
                    WHEN (SELECT bool_or((cls ->> 'id')::int IN (3, 7))
                            FROM jsonb_array_elements(
                                COALESCE(bi.payload->'requirements'->'playable_classes'->'links',
                                         '[]'::jsonb)) AS cls)
                    THEN 'mail'
                    WHEN (SELECT bool_or((cls ->> 'id')::int IN (1, 2, 6))
                            FROM jsonb_array_elements(
                                COALESCE(bi.payload->'requirements'->'playable_classes'->'links',
                                         '[]'::jsonb)) AS cls)
                    THEN 'plate'
                    ELSE 'any'
                END,
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id) blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            WHERE (bi.payload -> 'item_class'   ->> 'name') = 'Miscellaneous'
              AND (bi.payload -> 'item_subclass' ->> 'name') = 'Junk'
              AND EXISTS (
                    SELECT 1 FROM landing.blizzard_journal_encounters lje
                    JOIN landing.blizzard_journal_instances lji ON lji.instance_id = lje.instance_id
                    WHERE lji.instance_type = 'raid'
                      AND EXISTS (
                            SELECT 1 FROM jsonb_array_elements(lje.payload -> 'items') AS item_entry
                            WHERE (item_entry -> 'item' ->> 'id') IS NOT NULL
                              AND (item_entry -> 'item' ->> 'id')::int = bi.blizzard_item_id
                          )
                  );
            GET DIAGNOSTICS v_slot_tokens = ROW_COUNT;
            INSERT INTO enrichment.tier_tokens
                   (blizzard_item_id, token_name, target_slot, armor_type, detected_at)
            SELECT bi.blizzard_item_id, bi.payload ->> 'name', 'any', 'any', NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id) blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            WHERE (bi.payload -> 'item_class'   ->> 'name') = 'Reagent'
              AND (bi.payload -> 'item_subclass' ->> 'name') = 'Context Token'
              AND EXISTS (
                    SELECT 1 FROM landing.blizzard_journal_encounters lje
                    JOIN landing.blizzard_journal_instances lji ON lji.instance_id = lje.instance_id
                    WHERE lji.instance_type = 'raid'
                      AND EXISTS (
                            SELECT 1 FROM jsonb_array_elements(lje.payload -> 'items') AS item_entry
                            WHERE (item_entry -> 'item' ->> 'id') IS NOT NULL
                              AND (item_entry -> 'item' ->> 'id')::int = bi.blizzard_item_id
                          )
                  );
            GET DIAGNOSTICS v_any_tokens = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_tier_tokens: % slot-specific, % any-slot tokens',
                v_slot_tokens, v_any_tokens;
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
            UPDATE enrichment.items SET item_category = 'unclassified'
             WHERE item_category != 'crafted';
            SELECT count(*) INTO v_crafted FROM enrichment.items WHERE item_category = 'crafted';
            UPDATE enrichment.items ei SET item_category = 'tier'
             WHERE ei.slot_type IN ('head','shoulder','chest','hands','legs')
               AND ei.armor_type IS NOT NULL AND ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_set_members ism
                            WHERE ism.blizzard_item_id = ei.blizzard_item_id)
               AND NOT EXISTS (SELECT 1 FROM enrichment.item_sources s
                                WHERE s.blizzard_item_id = ei.blizzard_item_id
                                  AND s.instance_type IN ('raid','dungeon') AND NOT s.is_junk);
            GET DIAGNOSTICS v_tier = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'catalyst'
             WHERE ei.slot_type IN ('back','wrist','waist','feet')
               AND ei.quality_track = 'C' AND ei.item_category = 'unclassified';
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'raid'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_sources s
                            WHERE s.blizzard_item_id = ei.blizzard_item_id
                              AND s.instance_type = 'raid' AND NOT s.is_junk);
            GET DIAGNOSTICS v_raid = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'dungeon'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_sources s
                            WHERE s.blizzard_item_id = ei.blizzard_item_id
                              AND s.instance_type = 'dungeon' AND NOT s.is_junk);
            GET DIAGNOSTICS v_dungeon = ROW_COUNT;
            UPDATE enrichment.items ei SET item_category = 'world_boss'
             WHERE ei.item_category = 'unclassified'
               AND EXISTS (SELECT 1 FROM enrichment.item_sources s
                            WHERE s.blizzard_item_id = ei.blizzard_item_id
                              AND s.instance_type = 'world_boss' AND NOT s.is_junk);
            GET DIAGNOSTICS v_world_boss = ROW_COUNT;
            SELECT count(*) INTO v_unclassed FROM enrichment.items WHERE item_category = 'unclassified';
            RAISE NOTICE 'sp_update_item_categories: crafted=%, tier=%, catalyst=%, raid=%, dungeon=%, world_boss=%, unclassified=%',
                v_crafted, v_tier, v_catalyst, v_raid, v_dungeon, v_world_boss, v_unclassed;
        END;
        $$
    """)
