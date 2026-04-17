"""feat: item set pipeline — tier pieces via Blizzard Item Set API

Revision ID: 0120
Revises: 0119
Create Date: 2026-04-16

Adds a landing-native path for tier piece item IDs and a self-contained
tier token lookup table so the enrichment layer no longer depends on
guild_identity.tier_token_attrs or guild_identity.wow_items for tier
classification or sourcing.

New tables:
  landing.blizzard_item_sets     — raw Blizzard /data/wow/item-set/{id} payloads
  enrichment.item_set_members    — set_id → blizzard_item_id membership
  enrichment.tier_tokens         — isolated lookup: token → (target_slot, armor_type)
                                   populated by parsing landing item payloads;
                                   update this table when expansion token structure
                                   changes without touching other enrichment logic.

New sprocs:
  sp_rebuild_item_set_members()  — reads landing.blizzard_item_sets → item_set_members
  sp_rebuild_tier_tokens()       — identifies Miscellaneous/Junk + Reagent/Context Token
                                   items that drop from raids; parses description for
                                   target_slot; infers armor_type from playable_classes.

Updated:
  sp_update_item_categories()    — tier check now uses enrichment.item_set_members
                                   (item IS in a known set) instead of guild_identity
                                   .tier_token_attrs (any token matches slot+armor_type).
  sp_rebuild_all()               — includes new sprocs before classification.
  viz.tier_piece_sources         — rebuilt using enrichment.tier_tokens +
                                   enrichment.item_set_members; no longer references
                                   guild_identity.tier_token_attrs or
                                   guild_identity.wow_items.
"""
from alembic import op

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None


def upgrade():
    # ── landing.blizzard_item_sets ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE landing.blizzard_item_sets (
            set_id      INTEGER PRIMARY KEY,
            set_name    TEXT NOT NULL,
            item_ids    INTEGER[] NOT NULL DEFAULT '{}',
            payload     JSONB NOT NULL,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── enrichment.item_set_members ───────────────────────────────────────────
    op.execute("""
        CREATE TABLE enrichment.item_set_members (
            set_id              INTEGER NOT NULL,
            set_name            TEXT NOT NULL,
            blizzard_item_id    INTEGER NOT NULL,
            PRIMARY KEY (set_id, blizzard_item_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_enrichment_ism_item_id
            ON enrichment.item_set_members (blizzard_item_id)
    """)

    # ── enrichment.tier_tokens ────────────────────────────────────────────────
    # Isolated lookup: one row per tier token item.
    # target_slot: 'head'/'shoulder'/'chest'/'hands'/'legs'/'any'  (NULL = parse failed)
    # armor_type:  'cloth'/'leather'/'mail'/'plate'/'any'
    # Update this table (or re-run sp_rebuild_tier_tokens) when a new season's
    # tokens use a different description format.
    op.execute("""
        CREATE TABLE enrichment.tier_tokens (
            blizzard_item_id    INTEGER PRIMARY KEY,
            token_name          TEXT,
            target_slot         VARCHAR(20),
            armor_type          VARCHAR(20),
            detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── sp_rebuild_item_set_members ───────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_set_members()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.item_set_members;
            INSERT INTO enrichment.item_set_members (set_id, set_name, blizzard_item_id)
            SELECT s.set_id, s.set_name, item_id
            FROM landing.blizzard_item_sets s,
                 unnest(s.item_ids) AS item_id;
            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_set_members: % rows inserted', v_count;
        END;
        $$
    """)

    # ── sp_rebuild_tier_tokens ────────────────────────────────────────────────
    # Two passes:
    #   1. Miscellaneous/Junk items from raid encounters — slot-specific tokens.
    #      target_slot parsed from item description text.
    #      armor_type inferred from playable_classes class IDs.
    #   2. Reagent/Context Token items from raid encounters — any-slot tokens.
    #
    # Class ID → armor type mapping (stable across expansions):
    #   Cloth:   Priest(5), Mage(8), Warlock(9)
    #   Leather: Rogue(4), Monk(10), Druid(11), Demon Hunter(12), Evoker(13)
    #   Mail:    Hunter(3), Shaman(7)
    #   Plate:   Warrior(1), Paladin(2), Death Knight(6)
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_tier_tokens()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_slot_tokens BIGINT;
            v_any_tokens  BIGINT;
        BEGIN
            TRUNCATE enrichment.tier_tokens;

            -- Pass 1: Miscellaneous/Junk slot-specific tokens
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
                                COALESCE(bi.payload -> 'requirements' -> 'playable_classes' -> 'links',
                                         '[]'::jsonb)) AS cls)
                    THEN 'cloth'
                    WHEN (SELECT bool_or((cls ->> 'id')::int IN (4, 10, 11, 12, 13))
                            FROM jsonb_array_elements(
                                COALESCE(bi.payload -> 'requirements' -> 'playable_classes' -> 'links',
                                         '[]'::jsonb)) AS cls)
                    THEN 'leather'
                    WHEN (SELECT bool_or((cls ->> 'id')::int IN (3, 7))
                            FROM jsonb_array_elements(
                                COALESCE(bi.payload -> 'requirements' -> 'playable_classes' -> 'links',
                                         '[]'::jsonb)) AS cls)
                    THEN 'mail'
                    WHEN (SELECT bool_or((cls ->> 'id')::int IN (1, 2, 6))
                            FROM jsonb_array_elements(
                                COALESCE(bi.payload -> 'requirements' -> 'playable_classes' -> 'links',
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
            WHERE (bi.payload -> 'item_class'    ->> 'name') = 'Miscellaneous'
              AND (bi.payload -> 'item_subclass'  ->> 'name') = 'Junk'
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

            -- Pass 2: Reagent/Context Token any-slot tokens
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
            WHERE (bi.payload -> 'item_class'    ->> 'name') = 'Reagent'
              AND (bi.payload -> 'item_subclass'  ->> 'name') = 'Context Token'
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

    # ── sp_update_item_categories (tier check → item_set_members) ────────────
    # Tier check now uses enrichment.item_set_members: if this exact item is in
    # a known Blizzard item set, it's a tier piece.  Replaces the old
    # guild_identity.tier_token_attrs slot+armor_type heuristic.
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

            -- 1. Tier: in a Blizzard item set + tier slot + armor_type known
            --    + NO direct raid/dungeon source (regular boss drops in those
            --    slots must not be misclassified).
            UPDATE enrichment.items ei
               SET item_category = 'tier'
             WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.armor_type IS NOT NULL
               AND ei.item_category = 'unclassified'
               AND EXISTS (
                     SELECT 1 FROM enrichment.item_set_members ism
                      WHERE ism.blizzard_item_id = ei.blizzard_item_id
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

    # ── sp_rebuild_all (add item_set_members + tier_tokens before classification)
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_all()
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE NOTICE 'sp_rebuild_all: starting enrichment rebuild';

            -- 1. Items (all other enrichment tables depend on this)
            CALL enrichment.sp_rebuild_items();

            -- 2. Item set membership (landing.blizzard_item_sets → enrichment)
            CALL enrichment.sp_rebuild_item_set_members();

            -- 3. Tier tokens (landing item payloads → isolated lookup table)
            CALL enrichment.sp_rebuild_tier_tokens();

            -- 4. Sources (needed for category classification + junk flagging)
            CALL enrichment.sp_rebuild_item_sources();

            -- 5. Recipes (promotes unclassified→crafted)
            CALL enrichment.sp_rebuild_item_recipes();

            -- 6. Item seasons (requires item_recipes for crafted step)
            CALL enrichment.sp_rebuild_item_seasons();

            -- 7. Classify item categories (uses item_set_members + item_sources)
            CALL enrichment.sp_update_item_categories();

            -- 8. Flag junk sources (requires item_category)
            CALL enrichment.sp_flag_junk_sources();

            RAISE NOTICE
                'sp_rebuild_all: complete — items=%, sources=%, recipes=%',
                (SELECT count(*) FROM enrichment.items),
                (SELECT count(*) FROM enrichment.item_sources),
                (SELECT count(*) FROM enrichment.item_recipes);
        END;
        $$
    """)

    # ── viz.tier_piece_sources (rewritten — no guild_identity deps) ───────────
    op.execute("DROP VIEW IF EXISTS viz.tier_piece_sources")
    op.execute("""
        CREATE VIEW viz.tier_piece_sources AS
        SELECT
            ei.blizzard_item_id         AS tier_piece_blizzard_id,
            ei.name                     AS tier_piece_name,
            ei.slot_type,
            ei.armor_type,
            tt.blizzard_item_id         AS token_blizzard_id,
            ek.name                     AS token_name,
            es.instance_type,
            es.encounter_name           AS boss_name,
            es.instance_name,
            es.blizzard_encounter_id,
            es.blizzard_instance_id
        FROM enrichment.items ei
        JOIN enrichment.tier_tokens tt
            ON (tt.target_slot = ei.slot_type OR tt.target_slot = 'any')
           AND (ei.armor_type  = tt.armor_type  OR tt.armor_type  = 'any')
        JOIN enrichment.items ek
            ON ek.blizzard_item_id = tt.blizzard_item_id
        JOIN enrichment.item_sources es
            ON es.blizzard_item_id = tt.blizzard_item_id
           AND NOT es.is_junk
        WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
          AND ei.armor_type IS NOT NULL
          AND ei.item_category = 'tier'
    """)


def downgrade():
    # Restore viz.tier_piece_sources to 0106 version (guild_identity deps)
    op.execute("DROP VIEW IF EXISTS viz.tier_piece_sources")
    op.execute("""
        CREATE VIEW viz.tier_piece_sources AS
        SELECT
            ei.blizzard_item_id         AS tier_piece_blizzard_id,
            ei.name                     AS tier_piece_name,
            ei.slot_type,
            wi_tk.blizzard_item_id      AS token_blizzard_id,
            wi_tk.name                  AS token_name,
            es.instance_type,
            es.encounter_name           AS boss_name,
            es.instance_name,
            es.blizzard_encounter_id,
            es.blizzard_instance_id
        FROM enrichment.items ei
        JOIN guild_identity.tier_token_attrs tta
            ON (tta.target_slot = ei.slot_type OR tta.target_slot = 'any')
           AND (ei.armor_type   = tta.armor_type   OR tta.armor_type   = 'any')
        JOIN guild_identity.wow_items wi_tk
            ON wi_tk.id = tta.token_item_id
        JOIN enrichment.item_sources es
            ON es.blizzard_item_id = wi_tk.blizzard_item_id
           AND NOT es.is_junk
        WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
          AND ei.armor_type IS NOT NULL
          AND ei.item_category = 'tier'
    """)

    # Restore sp_rebuild_all to 0119 version
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_all()
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE NOTICE 'sp_rebuild_all: starting enrichment rebuild';
            CALL enrichment.sp_rebuild_items();
            CALL enrichment.sp_rebuild_item_sources();
            CALL enrichment.sp_rebuild_item_recipes();
            CALL enrichment.sp_rebuild_item_seasons();
            CALL enrichment.sp_update_item_categories();
            CALL enrichment.sp_flag_junk_sources();
            RAISE NOTICE
                'sp_rebuild_all: complete — items=%, sources=%, recipes=%',
                (SELECT count(*) FROM enrichment.items),
                (SELECT count(*) FROM enrichment.item_sources),
                (SELECT count(*) FROM enrichment.item_recipes);
        END;
        $$
    """)

    # Restore sp_update_item_categories to 0119 version (guild_identity.tier_token_attrs)
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
               AND NOT EXISTS (
                     SELECT 1 FROM enrichment.item_sources s
                      WHERE s.blizzard_item_id = ei.blizzard_item_id
                        AND s.instance_type IN ('raid', 'dungeon')
                        AND NOT s.is_junk
                   );
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            UPDATE enrichment.items ei
               SET item_category = 'catalyst'
             WHERE ei.slot_type IN ('back', 'wrist', 'waist', 'feet')
               AND ei.quality_track = 'C'
               AND ei.item_category = 'unclassified';
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

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

    op.execute("DROP PROCEDURE IF EXISTS enrichment.sp_rebuild_tier_tokens()")
    op.execute("DROP PROCEDURE IF EXISTS enrichment.sp_rebuild_item_set_members()")
    op.execute("DROP TABLE IF EXISTS enrichment.tier_tokens")
    op.execute("DROP TABLE IF EXISTS enrichment.item_set_members")
    op.execute("DROP TABLE IF EXISTS landing.blizzard_item_sets")
