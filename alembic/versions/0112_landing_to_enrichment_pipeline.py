"""Switch enrichment sprocs to read from landing tables (new pipeline).

Adds landing.blizzard_journal_instances to store per-instance metadata
(name, type, expansion) alongside encounter records — required so
sp_rebuild_item_sources can determine instance_type and instance_name
from the landing data alone.

Rewrites two core sprocs:
  sp_rebuild_items        — reads from landing.blizzard_items
                            (raw Blizzard item payloads)
  sp_rebuild_item_sources — reads from landing.blizzard_journal_encounters
                            joined to landing.blizzard_journal_instances

All other sprocs (bis_entries, trinket_ratings, item_recipes) continue to
read from guild_identity until BIS scraping and crafting are also moved to
the new path.

Revision ID: 0112
Revises: 0111
"""

from alembic import op

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade():
    # -------------------------------------------------------------------------
    # landing.blizzard_journal_instances
    # Stores per-instance metadata fetched at landing-fill time.
    # instance_type: 'raid' | 'dungeon' | 'world_boss'
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE landing.blizzard_journal_instances (
            id            SERIAL PRIMARY KEY,
            fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            instance_id   INTEGER NOT NULL,
            instance_name TEXT NOT NULL,
            instance_type VARCHAR(20) NOT NULL,
            expansion_id  INTEGER NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX ix_landing_bji_instance
            ON landing.blizzard_journal_instances (instance_id)
    """)

    # -------------------------------------------------------------------------
    # sp_rebuild_items — reads from landing.blizzard_items
    #
    # Replaces the Phase B version that read from guild_identity.wow_items.
    # icon_url is intentionally NULL here — filled by a Python enrichment step
    # (Blizzard media API) that runs after this sproc as part of Section C.
    # quality_track is NULL initially; set by sp_update_item_categories.
    # -------------------------------------------------------------------------
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
                NULL::text,
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
            ) bi;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $proc$
    """)

    # -------------------------------------------------------------------------
    # sp_rebuild_item_sources — reads from landing encounter + instance tables
    #
    # Extracts item→encounter→instance relationships purely from landing data.
    # Items in the landing.blizzard_journal_encounters payload that don't have
    # a matching row in enrichment.items (i.e. not yet in landing.blizzard_items)
    # are silently skipped via the WHERE filter.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_sources()
        LANGUAGE plpgsql AS $proc$
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
            SELECT DISTINCT
                (item_entry->'item'->>'id')::int            AS blizzard_item_id,
                i.instance_type,
                e.payload->>'name'                           AS encounter_name,
                i.instance_name,
                i.instance_id                                AS blizzard_instance_id,
                e.encounter_id                               AS blizzard_encounter_id,
                enrichment._quality_tracks(i.instance_type) AS quality_tracks,
                FALSE                                        AS is_junk
            FROM (
                SELECT DISTINCT ON (encounter_id)
                    encounter_id, instance_id, payload
                FROM landing.blizzard_journal_encounters
                ORDER BY encounter_id, fetched_at DESC
            ) e
            JOIN (
                SELECT DISTINCT ON (instance_id)
                    instance_id, instance_name, instance_type
                FROM landing.blizzard_journal_instances
                ORDER BY instance_id, fetched_at DESC
            ) i ON i.instance_id = e.instance_id
            CROSS JOIN LATERAL jsonb_array_elements(e.payload->'items') AS item_entry
            WHERE (item_entry->'item'->>'id')::int IN (
                SELECT blizzard_item_id FROM enrichment.items
            );

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_sources: % rows inserted', v_count;
        END;
        $proc$
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS landing.blizzard_journal_instances")

    # Restore sp_rebuild_items to read from guild_identity.wow_items
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
        $proc$
    """)

    # Restore sp_rebuild_item_sources to read from guild_identity.item_sources
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_item_sources()
        LANGUAGE plpgsql AS $proc$
        DECLARE v_count BIGINT;
        BEGIN
            TRUNCATE enrichment.item_sources;
            INSERT INTO enrichment.item_sources (
                blizzard_item_id, instance_type, encounter_name, instance_name,
                blizzard_instance_id, blizzard_encounter_id, quality_tracks, is_junk
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
            WHERE wi.blizzard_item_id IN (SELECT blizzard_item_id FROM enrichment.items);
            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_item_sources: % rows inserted', v_count;
        END;
        $proc$
    """)
