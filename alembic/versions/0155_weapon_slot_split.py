"""feat: weapon slot split — main_hand → main_hand_1h / main_hand_2h; priority → guide_order

Migration 0155 — Phase 1 of the weapon build variant feature:

1. Rename enrichment.bis_entries.priority → guide_order (SMALLINT, default 1).
2. Re-classify enrichment.bis_entries rows where slot = 'main_hand' using
   enrichment.items.slot_type:
     two_hand / ranged → main_hand_2h
     one_hand          → main_hand_1h
     other / unknown   → main_hand_2h (safe fallback)
3. Re-classify guild_identity.gear_plan_slots rows where slot = 'main_hand'
   using the same JOIN.
4. Update ref.gear_plan_slots:
   - Delete the 'main_hand' row.
   - Insert 'main_hand_2h' (slot_order=15) and 'main_hand_1h' (slot_order=16).
   - Bump off_hand from slot_order=16 to slot_order=17.
5. Recreate viz.bis_recommendations to expose guide_order instead of priority.

Revision ID: 0155
Revises: 0154
Create Date: 2026-04-20
"""
from alembic import op

revision = "0155"
down_revision = "0154"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Drop viz.bis_recommendations first — it references priority by name, and
    # PostgreSQL won't allow renaming/retyping a column used by an active view.
    # The view is recreated at the end of this migration.
    # -------------------------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS viz.bis_recommendations")

    # -------------------------------------------------------------------------
    # 1. Rename enrichment.bis_entries.priority → guide_order
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE enrichment.bis_entries
            RENAME COLUMN priority TO guide_order
    """)
    # Change type to SMALLINT and set default to 1 (was INTEGER DEFAULT 0)
    op.execute("""
        ALTER TABLE enrichment.bis_entries
            ALTER COLUMN guide_order SET DEFAULT 1,
            ALTER COLUMN guide_order TYPE SMALLINT USING guide_order::SMALLINT
    """)
    # Existing rows were inserted with priority=1; keep them at guide_order=1
    # (some legacy rows from sp_rebuild_bis_entries may have priority=0 — bump to 1)
    op.execute("""
        UPDATE enrichment.bis_entries SET guide_order = 1 WHERE guide_order < 1
    """)

    # -------------------------------------------------------------------------
    # 2. Re-classify enrichment.bis_entries main_hand → main_hand_1h/main_hand_2h
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE enrichment.bis_entries be
           SET slot = CASE
               WHEN ei.slot_type IN ('two_hand', 'ranged') THEN 'main_hand_2h'
               WHEN ei.slot_type = 'one_hand'              THEN 'main_hand_1h'
               ELSE                                              'main_hand_2h'
           END
          FROM enrichment.items ei
         WHERE be.slot = 'main_hand'
           AND ei.blizzard_item_id = be.blizzard_item_id
    """)
    # Any remaining main_hand rows whose item is missing from enrichment.items → 2h fallback
    op.execute("""
        UPDATE enrichment.bis_entries
           SET slot = 'main_hand_2h'
         WHERE slot = 'main_hand'
    """)

    # -------------------------------------------------------------------------
    # 3. Re-classify guild_identity.gear_plan_slots main_hand rows
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE guild_identity.gear_plan_slots gps
           SET slot = CASE
               WHEN ei.slot_type IN ('two_hand', 'ranged') THEN 'main_hand_2h'
               WHEN ei.slot_type = 'one_hand'              THEN 'main_hand_1h'
               ELSE                                              'main_hand_2h'
           END
          FROM enrichment.items ei
         WHERE gps.slot = 'main_hand'
           AND ei.blizzard_item_id = gps.blizzard_item_id
    """)
    op.execute("""
        UPDATE guild_identity.gear_plan_slots
           SET slot = 'main_hand_2h'
         WHERE slot = 'main_hand'
    """)

    # -------------------------------------------------------------------------
    # 4. Update ref.gear_plan_slots
    # -------------------------------------------------------------------------
    # Bump off_hand slot_order from 16 to 17 to make room for two weapon rows
    op.execute("""
        UPDATE ref.gear_plan_slots SET slot_order = 17 WHERE plan_slot = 'off_hand'
    """)
    # Remove old main_hand row
    op.execute("""
        DELETE FROM ref.gear_plan_slots WHERE plan_slot = 'main_hand'
    """)
    # Insert the two new weapon rows
    op.execute("""
        INSERT INTO ref.gear_plan_slots
            (plan_slot, display_name, slot_order, enrichment_slot_type,
             paired_slot, is_armor_slot, is_weapon_slot,
             is_tier_catalyst_slot, is_main_tier_slot)
        VALUES
            ('main_hand_2h', 'Main Hand', 15, 'two_hand',  NULL, FALSE, TRUE, FALSE, FALSE),
            ('main_hand_1h', 'Main Hand', 16, 'one_hand',  NULL, FALSE, TRUE, FALSE, FALSE)
        ON CONFLICT (plan_slot) DO NOTHING
    """)

    # -------------------------------------------------------------------------
    # 5. Recreate viz.bis_recommendations with guide_order
    # (view was already dropped at the top of this migration)
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE VIEW viz.bis_recommendations AS
        SELECT
            be.source_id,
            bls.name            AS source_name,
            bls.short_label     AS source_short_label,
            bls.origin          AS source_origin,
            bls.content_type,
            be.spec_id,
            be.hero_talent_id,
            be.slot,
            be.guide_order,
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.item_category,
            i.tier_set_suffix,
            i.armor_type,
            i.quality_track,
            (
                SELECT ARRAY(
                    SELECT DISTINCT UNNEST(s.quality_tracks)
                      FROM enrichment.item_sources s
                     WHERE s.blizzard_item_id = i.blizzard_item_id
                       AND NOT s.is_junk
                )
            )                   AS quality_tracks
        FROM enrichment.bis_entries be
        JOIN enrichment.items i
            ON i.blizzard_item_id = be.blizzard_item_id
        JOIN ref.bis_list_sources bls
            ON bls.id = be.source_id
    """)


def downgrade() -> None:
    # Recreate viz.bis_recommendations with priority
    op.execute("DROP VIEW IF EXISTS viz.bis_recommendations")
    op.execute("""
        CREATE VIEW viz.bis_recommendations AS
        SELECT
            be.source_id,
            bls.name            AS source_name,
            bls.short_label     AS source_short_label,
            bls.origin          AS source_origin,
            bls.content_type,
            be.spec_id,
            be.hero_talent_id,
            be.slot,
            be.guide_order      AS priority,
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.item_category,
            i.tier_set_suffix,
            i.armor_type,
            i.quality_track,
            (
                SELECT ARRAY(
                    SELECT DISTINCT UNNEST(s.quality_tracks)
                      FROM enrichment.item_sources s
                     WHERE s.blizzard_item_id = i.blizzard_item_id
                       AND NOT s.is_junk
                )
            )                   AS quality_tracks
        FROM enrichment.bis_entries be
        JOIN enrichment.items i
            ON i.blizzard_item_id = be.blizzard_item_id
        JOIN ref.bis_list_sources bls
            ON bls.id = be.source_id
    """)

    # Restore ref.gear_plan_slots
    op.execute("DELETE FROM ref.gear_plan_slots WHERE plan_slot IN ('main_hand_2h', 'main_hand_1h')")
    op.execute("UPDATE ref.gear_plan_slots SET slot_order = 16 WHERE plan_slot = 'off_hand'")
    op.execute("""
        INSERT INTO ref.gear_plan_slots
            (plan_slot, display_name, slot_order, enrichment_slot_type,
             paired_slot, is_armor_slot, is_weapon_slot,
             is_tier_catalyst_slot, is_main_tier_slot)
        VALUES
            ('main_hand', 'Main Hand', 15, 'one_hand', NULL, FALSE, TRUE, FALSE, FALSE)
        ON CONFLICT (plan_slot) DO NOTHING
    """)

    # Revert gear_plan_slots (best-effort: reclassify back to main_hand)
    op.execute("""
        UPDATE guild_identity.gear_plan_slots
           SET slot = 'main_hand'
         WHERE slot IN ('main_hand_2h', 'main_hand_1h')
    """)

    # Revert bis_entries (best-effort: reclassify back to main_hand)
    op.execute("""
        UPDATE enrichment.bis_entries
           SET slot = 'main_hand'
         WHERE slot IN ('main_hand_2h', 'main_hand_1h')
    """)

    # Rename guide_order back to priority
    op.execute("""
        ALTER TABLE enrichment.bis_entries
            ALTER COLUMN guide_order TYPE INTEGER USING guide_order::INTEGER,
            ALTER COLUMN guide_order SET DEFAULT 0
    """)
    op.execute("""
        ALTER TABLE enrichment.bis_entries
            RENAME COLUMN guide_order TO priority
    """)
