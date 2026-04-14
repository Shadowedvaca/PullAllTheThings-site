"""Phase B — Enrichment schema: tables and stored procedures.

Adds 5 tables to the enrichment schema (created in 0104) plus 2 helper
functions and 8 stored procedures that rebuild the enrichment layer from
guild_identity data.

Transitional note (Phase B): Sprocs read from guild_identity.* as the
primary data source.  Full landing-based reads follow in Phase D+ once
landing tables have complete coverage after a fresh expansion re-sync.

Procedures added:
  enrichment.sp_rebuild_items()         — populate enrichment.items
  enrichment.sp_rebuild_item_sources()  — populate enrichment.item_sources
  enrichment.sp_rebuild_item_recipes()  — populate enrichment.item_recipes
  enrichment.sp_rebuild_bis_entries()   — populate enrichment.bis_entries
  enrichment.sp_rebuild_trinket_ratings() — populate enrichment.trinket_ratings
  enrichment.sp_update_item_categories() — classify item_category in bulk
  enrichment.sp_flag_junk_sources()     — mark is_junk on known-spurious rows
  enrichment.sp_rebuild_all()           — call all of the above in order

Revision ID: 0105
Revises: 0104
"""

from alembic import op

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade():
    # ─────────────────────────────────────────────────────────────────────────
    # TABLES
    # ─────────────────────────────────────────────────────────────────────────

    # enrichment.items — structured, categorized item facts.
    # item_category is set to 'unknown' on insert by sp_rebuild_items();
    # sp_update_item_categories() fills it in after sources + recipes exist.
    op.execute("""
        CREATE TABLE enrichment.items (
            blizzard_item_id    INTEGER PRIMARY KEY,
            name                TEXT NOT NULL,
            icon_url            TEXT,
            slot_type           VARCHAR(30),
            armor_type          VARCHAR(20),
            primary_stat        VARCHAR(10),
            item_category       VARCHAR(20) NOT NULL DEFAULT 'unknown'
                                CHECK (item_category IN
                                       ('tier','catalyst','crafted','drop','unknown')),
            tier_set_suffix     TEXT,
            quality_track       VARCHAR(1),
            enriched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source_blizzard_at  TIMESTAMPTZ,
            source_wowhead_at   TIMESTAMPTZ
        )
    """)

    # enrichment.item_sources — structured source rows with quality tracks pre-computed.
    # quality_tracks is ARRAY['V','C','H','M'] etc., computed by _quality_tracks().
    # is_junk mirrors guild_identity.item_sources.is_suspected_junk and is
    # recalculated by sp_flag_junk_sources().
    op.execute("""
        CREATE TABLE enrichment.item_sources (
            id                    SERIAL PRIMARY KEY,
            blizzard_item_id      INTEGER NOT NULL
                                  REFERENCES enrichment.items (blizzard_item_id),
            instance_type         VARCHAR(20) NOT NULL
                                  CHECK (instance_type IN
                                         ('raid','dungeon','world_boss','catalyst')),
            encounter_name        TEXT,
            instance_name         TEXT,
            blizzard_instance_id  INTEGER,
            blizzard_encounter_id INTEGER,
            quality_tracks        TEXT[] NOT NULL,
            is_junk               BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE (blizzard_item_id, instance_type, encounter_name)
        )
    """)
    op.execute("""
        CREATE INDEX ix_enrichment_item_sources_item
            ON enrichment.item_sources (blizzard_item_id)
    """)

    # enrichment.item_recipes — item → craftable recipe relationships.
    op.execute("""
        CREATE TABLE enrichment.item_recipes (
            id               SERIAL PRIMARY KEY,
            blizzard_item_id INTEGER NOT NULL
                             REFERENCES enrichment.items (blizzard_item_id),
            recipe_id        INTEGER NOT NULL,
            match_type       VARCHAR(50),
            confidence       INTEGER CHECK (confidence BETWEEN 0 AND 100),
            UNIQUE (blizzard_item_id, recipe_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_enrichment_item_recipes_item
            ON enrichment.item_recipes (blizzard_item_id)
    """)

    # enrichment.bis_entries — BIS recommendations per spec/source.
    # source_id / spec_id / hero_talent_id reference guild_identity tables;
    # no FK enforced here since cross-schema FKs are not used in this project.
    op.execute("""
        CREATE TABLE enrichment.bis_entries (
            id               SERIAL PRIMARY KEY,
            source_id        INTEGER NOT NULL,
            spec_id          INTEGER NOT NULL,
            hero_talent_id   INTEGER,
            slot             VARCHAR(30) NOT NULL,
            blizzard_item_id INTEGER NOT NULL
                             REFERENCES enrichment.items (blizzard_item_id),
            priority         INTEGER NOT NULL DEFAULT 0,
            UNIQUE (source_id, spec_id, hero_talent_id, slot, blizzard_item_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_enrichment_bis_entries_spec
            ON enrichment.bis_entries (spec_id, source_id)
    """)

    # enrichment.trinket_ratings — S/A/B/C/D/F ratings per spec/source/item.
    op.execute("""
        CREATE TABLE enrichment.trinket_ratings (
            id               SERIAL PRIMARY KEY,
            source_id        INTEGER NOT NULL,
            spec_id          INTEGER NOT NULL,
            hero_talent_id   INTEGER,
            blizzard_item_id INTEGER NOT NULL
                             REFERENCES enrichment.items (blizzard_item_id),
            tier             VARCHAR(2) NOT NULL
                             CHECK (tier IN ('S','A','B','C','D','F')),
            sort_order       INTEGER NOT NULL DEFAULT 0,
            UNIQUE (source_id, spec_id, hero_talent_id, blizzard_item_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_enrichment_trinket_ratings_spec
            ON enrichment.trinket_ratings (spec_id, source_id)
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER FUNCTIONS
    # ─────────────────────────────────────────────────────────────────────────

    # _quality_tracks: convert instance_type to the pre-computed TEXT[] used in
    # enrichment.item_sources.  Matches source_config.TRACKS_BY_TYPE.
    op.execute("""
        CREATE OR REPLACE FUNCTION enrichment._quality_tracks(p_instance_type TEXT)
        RETURNS TEXT[]
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
            SELECT CASE p_instance_type
                WHEN 'raid'       THEN ARRAY['V','C','H','M']::TEXT[]
                WHEN 'world_boss' THEN ARRAY['C','H','M']::TEXT[]
                WHEN 'dungeon'    THEN ARRAY['C','H','M']::TEXT[]
                WHEN 'catalyst'   THEN ARRAY['C','H','M']::TEXT[]
                ELSE                   ARRAY['C','H','M']::TEXT[]
            END
        $$
    """)

    # _tooltip_slot: parse slot_type from Wowhead tooltip HTML.
    # Matches item_service._slot_from_tooltip() and TOOLTIP_SLOT_MAP.
    # The slot is embedded as plain text in a stats table that appears
    # after "Binds when" in the tooltip:
    #   <table width="100%"><tr><td>Hands</td><th>...Plate...</th></tr></table>
    op.execute(r"""
        CREATE OR REPLACE FUNCTION enrichment._tooltip_slot(p_tooltip_html TEXT)
        RETURNS VARCHAR(30)
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
        DECLARE
            v_search TEXT;
            v_slot   TEXT;
        BEGIN
            IF p_tooltip_html IS NULL THEN RETURN 'other'; END IF;
            v_search := CASE
                WHEN POSITION('Binds when' IN p_tooltip_html) > 0
                THEN substring(p_tooltip_html
                               FROM POSITION('Binds when' IN p_tooltip_html))
                ELSE p_tooltip_html
            END;
            v_slot := lower(trim(
                (regexp_match(
                    v_search,
                    '<table width="100%"><tr><td>([^<]+)</td>'
                ))[1]
            ));
            RETURN CASE v_slot
                WHEN 'head'             THEN 'head'
                WHEN 'neck'             THEN 'neck'
                WHEN 'shoulder'         THEN 'shoulder'
                WHEN 'shoulders'        THEN 'shoulder'
                WHEN 'back'             THEN 'back'
                WHEN 'chest'            THEN 'chest'
                WHEN 'waist'            THEN 'waist'
                WHEN 'legs'             THEN 'legs'
                WHEN 'feet'             THEN 'feet'
                WHEN 'wrist'            THEN 'wrist'
                WHEN 'wrists'           THEN 'wrist'
                WHEN 'hands'            THEN 'hands'
                WHEN 'finger'           THEN 'ring_1'
                WHEN 'trinket'          THEN 'trinket_1'
                WHEN 'main hand'        THEN 'main_hand'
                WHEN 'one-hand'         THEN 'main_hand'
                WHEN 'two-hand'         THEN 'main_hand'
                WHEN 'off hand'         THEN 'off_hand'
                WHEN 'held in off-hand' THEN 'off_hand'
                WHEN 'ranged'           THEN 'main_hand'
                ELSE 'other'
            END;
        END;
        $$
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # STORED PROCEDURES
    # ─────────────────────────────────────────────────────────────────────────

    # sp_rebuild_items: populate enrichment.items from guild_identity.wow_items.
    #
    # TRUNCATE ... CASCADE drops all dependent enrichment rows first.
    # item_category is set to 'unknown' for all rows; call
    # sp_update_item_categories() after item_sources and item_recipes are built.
    #
    # Phase B source: guild_identity.wow_items (full coverage, transitional).
    # Future (Phase D+): switch to landing.blizzard_items + landing.wowhead_tooltips
    # once those tables have complete coverage via re-sync.
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
                item_category,
                quality_track,
                enriched_at
            )
            SELECT
                wi.blizzard_item_id,
                COALESCE(NULLIF(trim(wi.name), ''), 'Unknown Item'),
                wi.icon_url,
                wi.slot_type,
                LOWER(wi.armor_type),
                'unknown',
                wi.quality_track,
                NOW()
            FROM guild_identity.wow_items wi
            WHERE wi.blizzard_item_id IS NOT NULL;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $$
    """)

    # sp_rebuild_item_sources: mirror item_sources from guild_identity.
    #
    # Pre-computes quality_tracks via _quality_tracks(instance_type).
    # Copies is_suspected_junk into is_junk for initial population; call
    # sp_flag_junk_sources() to recalculate from enrichment tables directly.
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_sources()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.item_sources;

            INSERT INTO enrichment.item_sources (
                blizzard_item_id,
                instance_type,
                encounter_name,
                instance_name,
                blizzard_instance_id,
                blizzard_encounter_id,
                quality_tracks,
                is_junk
            )
            SELECT
                wi.blizzard_item_id,
                s.instance_type,
                s.encounter_name,
                s.instance_name,
                s.blizzard_instance_id,
                s.blizzard_encounter_id,
                enrichment._quality_tracks(s.instance_type),
                s.is_suspected_junk
            FROM guild_identity.item_sources s
            JOIN guild_identity.wow_items wi ON wi.id = s.item_id
            WHERE wi.blizzard_item_id IN (
                SELECT blizzard_item_id FROM enrichment.items
            );

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_sources: % rows inserted', v_count;
        END;
        $$
    """)

    # sp_rebuild_item_recipes: mirror item_recipe_links from guild_identity.
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

    # sp_rebuild_bis_entries: mirror bis_list_entries from guild_identity.
    #
    # Note: the UNIQUE constraint on (source_id, spec_id, hero_talent_id,
    # slot, blizzard_item_id) uses standard PostgreSQL NULL semantics
    # (NULLs are distinct).  Since we TRUNCATE first, there are no prior
    # rows to conflict with — the constraint is enforced by the TRUNCATE.
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_bis_entries()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.bis_entries;

            INSERT INTO enrichment.bis_entries (
                source_id,
                spec_id,
                hero_talent_id,
                slot,
                blizzard_item_id,
                priority
            )
            SELECT
                ble.source_id,
                ble.spec_id,
                ble.hero_talent_id,
                ble.slot,
                wi.blizzard_item_id,
                ble.priority
            FROM guild_identity.bis_list_entries ble
            JOIN guild_identity.wow_items wi ON wi.id = ble.item_id
            WHERE wi.blizzard_item_id IN (
                SELECT blizzard_item_id FROM enrichment.items
            );

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_bis_entries: % rows inserted', v_count;
        END;
        $$
    """)

    # sp_rebuild_trinket_ratings: mirror trinket_tier_ratings from guild_identity.
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_trinket_ratings()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.trinket_ratings;

            INSERT INTO enrichment.trinket_ratings (
                source_id,
                spec_id,
                hero_talent_id,
                blizzard_item_id,
                tier,
                sort_order
            )
            SELECT
                ttr.source_id,
                ttr.spec_id,
                ttr.hero_talent_id,
                wi.blizzard_item_id,
                ttr.tier,
                ttr.sort_order
            FROM guild_identity.trinket_tier_ratings ttr
            JOIN guild_identity.wow_items wi ON wi.id = ttr.item_id
            WHERE wi.blizzard_item_id IN (
                SELECT blizzard_item_id FROM enrichment.items
            );

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_trinket_ratings: % rows inserted', v_count;
        END;
        $$
    """)

    # sp_update_item_categories: classify item_category for all enrichment.items.
    #
    # Must be called AFTER sp_rebuild_item_sources and sp_rebuild_item_recipes.
    # Rules applied in priority order (each UPDATE only touches rows not yet
    # assigned to a higher-priority category):
    #
    #   1. 'crafted'  — has at least one row in enrichment.item_recipes
    #   2. 'catalyst' — quality_track = 'C' and not crafted
    #   3. 'tier'     — /item-set= link in Wowhead tooltip, tier slot, not crafted/catalyst
    #   4. 'drop'     — has a non-junk raid/dungeon/world_boss source, not above
    #   5. 'unknown'  — no evidence for any of the above
    #
    # Note: tier classification reads wowhead_tooltip_html from guild_identity.wow_items
    # during Phase B (transitional).  Once landing.wowhead_tooltips has full
    # coverage, this will switch to reading from landing directly.
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
            -- Reset all to 'unknown' before applying rules
            UPDATE enrichment.items SET item_category = 'unknown';

            -- 1. Crafted: item has at least one recipe link
            UPDATE enrichment.items ei
               SET item_category = 'crafted'
             WHERE EXISTS (
                     SELECT 1 FROM enrichment.item_recipes ir
                      WHERE ir.blizzard_item_id = ei.blizzard_item_id
                   );
            GET DIAGNOSTICS v_crafted = ROW_COUNT;

            -- 2. Catalyst: quality_track = 'C', not crafted
            UPDATE enrichment.items ei
               SET item_category = 'catalyst'
             WHERE ei.quality_track = 'C'
               AND ei.item_category <> 'crafted';
            GET DIAGNOSTICS v_catalyst = ROW_COUNT;

            -- 3. Tier: Wowhead tooltip confirms set membership + tier slot
            --    Reads from guild_identity.wow_items (Phase B transitional).
            UPDATE enrichment.items ei
               SET item_category = 'tier'
              FROM guild_identity.wow_items wi
             WHERE wi.blizzard_item_id = ei.blizzard_item_id
               AND wi.wowhead_tooltip_html LIKE '%/item-set=%'
               AND ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
               AND ei.item_category NOT IN ('crafted', 'catalyst');
            GET DIAGNOSTICS v_tier = ROW_COUNT;

            -- 4. Drop: at least one non-junk raid/dungeon/world_boss source
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

    # sp_flag_junk_sources: mark is_junk = TRUE on known-spurious source rows.
    #
    # Safe to re-run — clears all flags first, then re-applies.
    #
    # Junk rules:
    #   1. World boss rows with no encounter/instance IDs (placeholder rows).
    #   2. Raid/world_boss sources for items with item_category = 'tier'.
    #      Tier pieces are accessed via the tier token chain, not as direct
    #      boss drops.  The viz.tier_piece_sources view shows the correct chain.
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_flag_junk_sources()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_wb BIGINT;
            v_tp BIGINT;
        BEGIN
            -- Clear all flags
            UPDATE enrichment.item_sources SET is_junk = FALSE;

            -- 1. World boss rows with no encounter/instance IDs
            UPDATE enrichment.item_sources
               SET is_junk = TRUE
             WHERE instance_type = 'world_boss'
               AND blizzard_encounter_id IS NULL
               AND blizzard_instance_id IS NULL;
            GET DIAGNOSTICS v_wb = ROW_COUNT;

            -- 2. Raid/world_boss sources for tier pieces
            UPDATE enrichment.item_sources s
               SET is_junk = TRUE
              FROM enrichment.items i
             WHERE i.blizzard_item_id = s.blizzard_item_id
               AND i.item_category = 'tier'
               AND s.instance_type IN ('raid', 'world_boss');
            GET DIAGNOSTICS v_tp = ROW_COUNT;

            RAISE NOTICE
                'sp_flag_junk_sources: % world_boss + % tier_piece = % total flagged',
                v_wb, v_tp, (v_wb + v_tp);
        END;
        $$
    """)

    # sp_rebuild_all: convenience wrapper — rebuild all enrichment tables in order.
    #
    # Equivalent to running all sp_rebuild_* procedures in the correct sequence.
    # Call this after running Sync Loot Tables, Enrich Items, and Sync BIS Lists
    # to populate landing data, then verify enrichment.items matches guild_identity.
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_all()
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE NOTICE 'sp_rebuild_all: starting full enrichment rebuild';

            -- 1. Items first (all other enrichment tables FK to this)
            CALL enrichment.sp_rebuild_items();

            -- 2. Sources + recipes (needed for category classification)
            CALL enrichment.sp_rebuild_item_sources();
            CALL enrichment.sp_rebuild_item_recipes();

            -- 3. BIS + trinket ratings (depend only on enrichment.items)
            CALL enrichment.sp_rebuild_bis_entries();
            CALL enrichment.sp_rebuild_trinket_ratings();

            -- 4. Classify item categories (requires sources + recipes)
            CALL enrichment.sp_update_item_categories();

            -- 5. Flag junk sources (requires item_category to be set)
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


def downgrade():
    # Drop procedures in reverse dependency order
    for proc in [
        "enrichment.sp_rebuild_all()",
        "enrichment.sp_flag_junk_sources()",
        "enrichment.sp_update_item_categories()",
        "enrichment.sp_rebuild_trinket_ratings()",
        "enrichment.sp_rebuild_bis_entries()",
        "enrichment.sp_rebuild_item_recipes()",
        "enrichment.sp_rebuild_item_sources()",
        "enrichment.sp_rebuild_items()",
    ]:
        op.execute(f"DROP PROCEDURE IF EXISTS {proc} CASCADE")

    # Drop helper functions
    op.execute("DROP FUNCTION IF EXISTS enrichment._tooltip_slot(TEXT) CASCADE")
    op.execute("DROP FUNCTION IF EXISTS enrichment._quality_tracks(TEXT) CASCADE")

    # Drop tables in reverse FK dependency order
    op.execute("DROP TABLE IF EXISTS enrichment.trinket_ratings")
    op.execute("DROP TABLE IF EXISTS enrichment.bis_entries")
    op.execute("DROP TABLE IF EXISTS enrichment.item_recipes")
    op.execute("DROP TABLE IF EXISTS enrichment.item_sources")
    op.execute("DROP TABLE IF EXISTS enrichment.items")
