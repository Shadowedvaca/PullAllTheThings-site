"""fix: Evoker (class ID 13) is mail, not leather

Revision ID: 0124
Revises: 0123
Create Date: 2026-04-17

Bug: sp_rebuild_tier_tokens listed Evoker (class ID 13) in the leather group
(4, 10, 11, 12, 13).  Evokers have always worn mail.  Result: the Alncast/
Voidcast tokens {3, 7, 13} (Hunter, Shaman, Evoker) fired the leather bool_or
check on ID 13 before reaching the mail check → labeled leather.  The genuine
leather tokens {4, 10, 11, 12} also matched → two leather rows per slot,
zero mail rows.

Fix: leather = (4, 10, 11, 12), mail = (3, 7, 13).
Expected result after re-running Process Tier Tokens: 20 slot-specific rows —
5 slots × 4 armor types (cloth/leather/mail/plate).
"""
from alembic import op

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE OR REPLACE PROCEDURE enrichment.sp_rebuild_tier_tokens()
        LANGUAGE plpgsql AS $$
        DECLARE
            v_slot_tokens BIGINT;
            v_any_tokens  BIGINT;
        BEGIN
            TRUNCATE enrichment.tier_tokens;

            -- Pass 1: Miscellaneous/Junk slot-specific tokens.
            -- Slot: parsed from the USE spell description in preview_item.
            -- armor_type: inferred from playable_classes in preview_item.requirements.
            --   Cloth  = 5 (Priest), 8 (Mage), 9 (Warlock)
            --   Leather= 4 (Rogue), 10 (Monk), 11 (Druid), 12 (Demon Hunter)
            --   Mail   = 3 (Hunter), 7 (Shaman), 13 (Evoker)
            --   Plate  = 1 (Warrior), 2 (Paladin), 6 (Death Knight)
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
                        SELECT bool_or((cls ->> 'id')::int IN (4, 10, 11, 12))
                          FROM jsonb_array_elements(
                               COALESCE(bi.payload -> 'preview_item'
                                        -> 'requirements' -> 'playable_classes' -> 'links',
                                        '[]'::jsonb)) AS cls
                    ) THEN 'leather'
                    WHEN (
                        SELECT bool_or((cls ->> 'id')::int IN (3, 7, 13))
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


def downgrade():
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
