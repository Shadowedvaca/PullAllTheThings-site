"""feat: landing.blizzard_item_quality_tracks — retire wow_items from catalyst path

Creates landing.blizzard_item_quality_tracks to store the V/C/H/M quality track
mapping that the appearance crawl derives from appearance set names.  This is a
*derived* value (not in the Blizzard item API payload) so it gets its own landing
table rather than being embedded in landing.blizzard_items.

Changes:
  landing.blizzard_item_quality_tracks — new table:
    blizzard_item_id INTEGER PRIMARY KEY
    quality_track    CHAR(1) NOT NULL  (V/C/H/M)

  Backfill: copies existing quality_track rows from guild_identity.wow_items
    where quality_track IS NOT NULL.

  sp_rebuild_items() updated to LEFT JOIN landing.blizzard_item_quality_tracks
    for quality_track instead of the hardcoded NULL it had before.

Python changes (applied alongside this migration):
  item_source_sync.py — sync_catalyst_items_via_appearance():
    - tier_rows query: guild_identity.wow_items + guild_identity.item_sources
      → enrichment.items + enrichment.item_sources
    - stub INSERT: guild_identity.wow_items removed;
      writes to landing.blizzard_item_quality_tracks (quality_track) AND
      landing.blizzard_items (full item payload via client.get_item())
  item_source_sync.py — enrich_catalyst_tier_items():
    - quality_track='C' DELETE now uses landing.blizzard_item_quality_tracks
  bis_routes.py — _run_landing_fill():
    - Adds item IDs from landing.blizzard_item_quality_tracks to the fetch queue
      so Catch Up automatically fetches item records for appearance-crawled items.

Revision ID: 0133
Revises: 0132
"""

revision = "0133"
down_revision = "0132"

from alembic import op


def upgrade():
    # ── 1. Create landing.blizzard_item_quality_tracks ────────────────────────
    op.execute("""
        CREATE TABLE landing.blizzard_item_quality_tracks (
            blizzard_item_id  INTEGER NOT NULL PRIMARY KEY,
            quality_track     CHAR(1) NOT NULL,
            fetched_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── 2. Backfill from guild_identity.wow_items ─────────────────────────────
    op.execute("""
        INSERT INTO landing.blizzard_item_quality_tracks
               (blizzard_item_id, quality_track)
        SELECT blizzard_item_id, quality_track
          FROM guild_identity.wow_items
         WHERE quality_track IS NOT NULL
           AND blizzard_item_id IS NOT NULL
        ON CONFLICT (blizzard_item_id) DO NOTHING
    """)

    # ── 3. Update sp_rebuild_items() to read quality_track from landing ───────
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
                quality,
                playable_class_ids,
                enriched_at
            )
            SELECT
                bi.blizzard_item_id,
                COALESCE(NULLIF(trim(bi.payload->>'name'), ''), 'Unknown Item'),
                lii.icon_url,
                CASE bi.payload->'inventory_type'->>'type'
                    WHEN 'HEAD'       THEN 'head'
                    WHEN 'NECK'       THEN 'neck'
                    WHEN 'SHOULDER'   THEN 'shoulder'
                    WHEN 'BACK'       THEN 'back'
                    WHEN 'CLOAK'      THEN 'back'
                    WHEN 'CHEST'      THEN 'chest'
                    WHEN 'ROBE'       THEN 'chest'
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
                qt.quality_track,
                bi.payload -> 'quality' ->> 'type',
                CASE
                    WHEN jsonb_array_length(
                             COALESCE(
                                 bi.payload -> 'preview_item' -> 'requirements'
                                           -> 'playable_classes' -> 'links',
                                 '[]'::jsonb
                             )
                         ) = 0
                    THEN NULL
                    ELSE ARRAY(
                        SELECT (cls ->> 'id')::int
                          FROM jsonb_array_elements(
                               bi.payload -> 'preview_item' -> 'requirements'
                                          -> 'playable_classes' -> 'links'
                          ) AS cls
                    )
                END,
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id)
                    blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            LEFT JOIN landing.blizzard_item_icons lii
                ON lii.blizzard_item_id = bi.blizzard_item_id
            LEFT JOIN landing.blizzard_item_quality_tracks qt
                ON qt.blizzard_item_id = bi.blizzard_item_id;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $$
    """)


def downgrade():
    # Restore sp_rebuild_items to hardcoded NULL quality_track
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
                quality,
                playable_class_ids,
                enriched_at
            )
            SELECT
                bi.blizzard_item_id,
                COALESCE(NULLIF(trim(bi.payload->>'name'), ''), 'Unknown Item'),
                lii.icon_url,
                CASE bi.payload->'inventory_type'->>'type'
                    WHEN 'HEAD'       THEN 'head'
                    WHEN 'NECK'       THEN 'neck'
                    WHEN 'SHOULDER'   THEN 'shoulder'
                    WHEN 'BACK'       THEN 'back'
                    WHEN 'CLOAK'      THEN 'back'
                    WHEN 'CHEST'      THEN 'chest'
                    WHEN 'ROBE'       THEN 'chest'
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
                bi.payload -> 'quality' ->> 'type',
                CASE
                    WHEN jsonb_array_length(
                             COALESCE(
                                 bi.payload -> 'preview_item' -> 'requirements'
                                           -> 'playable_classes' -> 'links',
                                 '[]'::jsonb
                             )
                         ) = 0
                    THEN NULL
                    ELSE ARRAY(
                        SELECT (cls ->> 'id')::int
                          FROM jsonb_array_elements(
                               bi.payload -> 'preview_item' -> 'requirements'
                                          -> 'playable_classes' -> 'links'
                          ) AS cls
                    )
                END,
                NOW()
            FROM (
                SELECT DISTINCT ON (blizzard_item_id)
                    blizzard_item_id, payload
                FROM landing.blizzard_items
                ORDER BY blizzard_item_id, fetched_at DESC
            ) bi
            LEFT JOIN landing.blizzard_item_icons lii
                ON lii.blizzard_item_id = bi.blizzard_item_id;

            GET DIAGNOSTICS v_count = ROW_COUNT;
            RAISE NOTICE 'sp_rebuild_items: % rows inserted', v_count;
        END;
        $$
    """)

    op.execute("DROP TABLE IF EXISTS landing.blizzard_item_quality_tracks")
