"""Create config and log schemas; move bis_scrape_* tables out of guild_identity.

Architectural change: pipeline-config and operational-log tables belong in
dedicated schemas, not mixed with identity data.

Changes:
  - CREATE SCHEMA config; CREATE SCHEMA log
  - CREATE TABLE config.bis_scrape_targets  (mirror + copy from guild_identity)
  - CREATE TABLE log.bis_scrape_log          (FK → config.bis_scrape_targets)
  - Copy data from guild_identity.bis_scrape_targets → config.bis_scrape_targets
  - Copy data from guild_identity.bis_scrape_log   → log.bis_scrape_log
  - ALTER landing.bis_scrape_raw ADD COLUMN target_id → config.bis_scrape_targets
  - DROP guild_identity.bis_scrape_log
  - DROP guild_identity.bis_scrape_targets
  - Add UNIQUE(instance_id)  on landing.blizzard_journal_instances (dedup first)
  - Add UNIQUE(encounter_id) on landing.blizzard_journal_encounters (dedup first)
  - Remove sp_rebuild_bis_entries + sp_rebuild_trinket_ratings from sp_rebuild_all
    (Python enrichment layer handles BIS + trinket rebuild from landing.bis_scrape_raw)
  - Drop sp_rebuild_bis_entries and sp_rebuild_trinket_ratings procedures

Revision ID: 0115
Revises: 0114
"""

from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


def upgrade():
    # ─────────────────────────────────────────────────────────────────────────
    # New schemas
    # ─────────────────────────────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS config")
    op.execute("CREATE SCHEMA IF NOT EXISTS log")

    # ─────────────────────────────────────────────────────────────────────────
    # config.bis_scrape_targets — pipeline config (was guild_identity)
    # FKs to guild_identity tables remain (they are reference data).
    # ─────────────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE config.bis_scrape_targets (
            id                  SERIAL PRIMARY KEY,
            source_id           INTEGER NOT NULL
                                    REFERENCES guild_identity.bis_list_sources(id) ON DELETE CASCADE,
            spec_id             INTEGER NOT NULL
                                    REFERENCES guild_identity.specializations(id) ON DELETE CASCADE,
            hero_talent_id      INTEGER
                                    REFERENCES guild_identity.hero_talents(id) ON DELETE SET NULL,
            content_type        VARCHAR(20),
            url                 TEXT,
            area_label          TEXT,
            preferred_technique VARCHAR(20)
                                    CHECK (preferred_technique IN (
                                        'json_embed','wh_gatherer','html_parse','simc','manual')),
            status              VARCHAR(20) NOT NULL DEFAULT 'pending',
            items_found         INTEGER NOT NULL DEFAULT 0,
            last_fetched        TIMESTAMP WITH TIME ZONE,
            UNIQUE (source_id, spec_id, url)
        )
    """)

    # Copy data from guild_identity — preserve IDs so FK from bis_scrape_log works
    op.execute("""
        INSERT INTO config.bis_scrape_targets
            (id, source_id, spec_id, hero_talent_id, content_type, url,
             area_label, preferred_technique, status, items_found, last_fetched)
        SELECT
            id, source_id, spec_id, hero_talent_id, content_type, url,
            area_label, preferred_technique, status, items_found, last_fetched
        FROM guild_identity.bis_scrape_targets
    """)

    # Reset sequence past the copied data
    op.execute("""
        SELECT setval(
            pg_get_serial_sequence('config.bis_scrape_targets', 'id'),
            COALESCE((SELECT MAX(id) FROM config.bis_scrape_targets), 0) + 1
        )
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # log.bis_scrape_log — operational log (was guild_identity)
    # FK to config.bis_scrape_targets now.
    # ─────────────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE log.bis_scrape_log (
            id            SERIAL PRIMARY KEY,
            target_id     INTEGER NOT NULL
                              REFERENCES config.bis_scrape_targets(id) ON DELETE CASCADE,
            technique     VARCHAR(20) NOT NULL,
            status        VARCHAR(20) NOT NULL
                              CHECK (status IN ('success','partial','failed')),
            items_found   INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    op.execute("""
        INSERT INTO log.bis_scrape_log
            (id, target_id, technique, status, items_found, error_message, created_at)
        SELECT
            id, target_id, technique, status, items_found, error_message, created_at
        FROM guild_identity.bis_scrape_log
    """)

    op.execute("""
        SELECT setval(
            pg_get_serial_sequence('log.bis_scrape_log', 'id'),
            COALESCE((SELECT MAX(id) FROM log.bis_scrape_log), 0) + 1
        )
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # Add target_id FK to landing.bis_scrape_raw
    # Allows rebuild_bis_from_landing / rebuild_trinket_ratings_from_landing
    # to know which spec×source×hero_talent each raw record belongs to.
    # ─────────────────────────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE landing.bis_scrape_raw
        ADD COLUMN target_id INTEGER REFERENCES config.bis_scrape_targets(id) ON DELETE SET NULL
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # Drop the old guild_identity tables (log first — it FKs to targets)
    # ─────────────────────────────────────────────────────────────────────────
    op.execute("DROP TABLE guild_identity.bis_scrape_log")
    op.execute("DROP TABLE guild_identity.bis_scrape_targets")

    # ─────────────────────────────────────────────────────────────────────────
    # Add UNIQUE constraints to landing journal tables for gap-filling catch-up.
    # Deduplicate first (keep most-recently-fetched row per instance/encounter).
    # ─────────────────────────────────────────────────────────────────────────
    op.execute("""
        DELETE FROM landing.blizzard_journal_instances a
        USING landing.blizzard_journal_instances b
        WHERE a.instance_id = b.instance_id
          AND a.id < b.id
    """)
    op.execute("""
        ALTER TABLE landing.blizzard_journal_instances
        ADD CONSTRAINT uq_landing_bji_instance_id UNIQUE (instance_id)
    """)

    op.execute("""
        DELETE FROM landing.blizzard_journal_encounters a
        USING landing.blizzard_journal_encounters b
        WHERE a.encounter_id = b.encounter_id
          AND a.id < b.id
    """)
    op.execute("""
        ALTER TABLE landing.blizzard_journal_encounters
        ADD CONSTRAINT uq_landing_bje_encounter_id UNIQUE (encounter_id)
    """)

    # ─────────────────────────────────────────────────────────────────────────
    # Update sp_rebuild_all: remove bis_entries + trinket_ratings calls.
    # Python enrichment functions rebuild_bis_from_landing() and
    # rebuild_trinket_ratings_from_landing() in bis_sync.py handle those now,
    # driven by enrich-and-classify in bis_routes.py after sp_rebuild_all().
    # ─────────────────────────────────────────────────────────────────────────
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

    # Drop the old SQL-layer BIS/trinket sprocs (Python layer handles them now)
    op.execute("DROP PROCEDURE IF EXISTS enrichment.sp_rebuild_bis_entries()")
    op.execute("DROP PROCEDURE IF EXISTS enrichment.sp_rebuild_trinket_ratings()")


def downgrade():
    # Restore sp_rebuild_all with BIS + trinket sprocs
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
            CALL enrichment.sp_rebuild_item_seasons();
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

    # Restore sp_rebuild_bis_entries
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_bis_entries()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.bis_entries;
            INSERT INTO enrichment.bis_entries (
                source_id, spec_id, hero_talent_id, slot, blizzard_item_id, priority
            )
            SELECT ble.source_id, ble.spec_id, ble.hero_talent_id, ble.slot,
                   wi.blizzard_item_id, ble.priority
            FROM guild_identity.bis_list_entries ble
            JOIN guild_identity.wow_items wi ON wi.id = ble.item_id
            WHERE wi.blizzard_item_id IN (SELECT blizzard_item_id FROM enrichment.items);
            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_bis_entries: % rows inserted', v_count;
        END;
        $$
    """)

    # Restore sp_rebuild_trinket_ratings
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_trinket_ratings()
        LANGUAGE plpgsql AS $$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.trinket_ratings;
            INSERT INTO enrichment.trinket_ratings (
                source_id, spec_id, hero_talent_id, blizzard_item_id, tier, sort_order
            )
            SELECT ttr.source_id, ttr.spec_id, ttr.hero_talent_id,
                   wi.blizzard_item_id, ttr.tier, ttr.sort_order
            FROM guild_identity.trinket_tier_ratings ttr
            JOIN guild_identity.wow_items wi ON wi.id = ttr.item_id
            WHERE wi.blizzard_item_id IN (SELECT blizzard_item_id FROM enrichment.items);
            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_trinket_ratings: % rows inserted', v_count;
        END;
        $$
    """)

    # Drop unique constraints from landing tables
    op.execute("""
        ALTER TABLE landing.blizzard_journal_instances
        DROP CONSTRAINT IF EXISTS uq_landing_bji_instance_id
    """)
    op.execute("""
        ALTER TABLE landing.blizzard_journal_encounters
        DROP CONSTRAINT IF EXISTS uq_landing_bje_encounter_id
    """)

    # Restore guild_identity.bis_scrape_targets
    op.execute("""
        CREATE TABLE guild_identity.bis_scrape_targets (
            id                  SERIAL PRIMARY KEY,
            source_id           INTEGER NOT NULL
                                    REFERENCES guild_identity.bis_list_sources(id) ON DELETE CASCADE,
            spec_id             INTEGER NOT NULL
                                    REFERENCES guild_identity.specializations(id) ON DELETE CASCADE,
            hero_talent_id      INTEGER
                                    REFERENCES guild_identity.hero_talents(id) ON DELETE SET NULL,
            content_type        VARCHAR(20),
            url                 TEXT,
            area_label          TEXT,
            preferred_technique VARCHAR(20)
                                    CHECK (preferred_technique IN (
                                        'json_embed','wh_gatherer','html_parse','simc','manual')),
            status              VARCHAR(20) NOT NULL DEFAULT 'pending',
            items_found         INTEGER NOT NULL DEFAULT 0,
            last_fetched        TIMESTAMP WITH TIME ZONE,
            UNIQUE (source_id, spec_id, url)
        )
    """)
    op.execute("""
        INSERT INTO guild_identity.bis_scrape_targets
            (id, source_id, spec_id, hero_talent_id, content_type, url,
             area_label, preferred_technique, status, items_found, last_fetched)
        SELECT
            id, source_id, spec_id, hero_talent_id, content_type, url,
            area_label, preferred_technique, status, items_found, last_fetched
        FROM config.bis_scrape_targets
    """)
    op.execute("""
        SELECT setval(
            pg_get_serial_sequence('guild_identity.bis_scrape_targets', 'id'),
            COALESCE((SELECT MAX(id) FROM guild_identity.bis_scrape_targets), 0) + 1
        )
    """)

    # Restore guild_identity.bis_scrape_log
    op.execute("""
        CREATE TABLE guild_identity.bis_scrape_log (
            id           SERIAL PRIMARY KEY,
            target_id    INTEGER NOT NULL
                             REFERENCES guild_identity.bis_scrape_targets(id) ON DELETE CASCADE,
            technique    VARCHAR(20) NOT NULL,
            status       VARCHAR(20) NOT NULL
                             CHECK (status IN ('success','partial','failed')),
            items_found  INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    op.execute("""
        INSERT INTO guild_identity.bis_scrape_log
            (id, target_id, technique, status, items_found, error_message, created_at)
        SELECT id, target_id, technique, status, items_found, error_message, created_at
        FROM log.bis_scrape_log
    """)
    op.execute("""
        SELECT setval(
            pg_get_serial_sequence('guild_identity.bis_scrape_log', 'id'),
            COALESCE((SELECT MAX(id) FROM guild_identity.bis_scrape_log), 0) + 1
        )
    """)

    # Remove target_id from landing.bis_scrape_raw
    op.execute("ALTER TABLE landing.bis_scrape_raw DROP COLUMN IF EXISTS target_id")

    # Drop new schemas
    op.execute("DROP TABLE IF EXISTS log.bis_scrape_log")
    op.execute("DROP SCHEMA IF EXISTS log")
    op.execute("DROP TABLE IF EXISTS config.bis_scrape_targets")
    op.execute("DROP SCHEMA IF EXISTS config")
