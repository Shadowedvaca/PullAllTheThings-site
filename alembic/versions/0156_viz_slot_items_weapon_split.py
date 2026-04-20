"""feat: viz.slot_items weapon_plan_slot + character_equipment main_hand reclassification

Migration 0156 — Phase 2 of the weapon build variant feature:

1. Update guild_identity.character_equipment.slot for existing 'main_hand' rows:
   - Reclassify using enrichment.items.slot_type JOIN (two_hand/ranged → main_hand_2h,
     one_hand → main_hand_1h).
   - Any items not in enrichment.items default to main_hand_2h.
2. Rebuild viz.slot_items to add weapon_plan_slot computed column:
   - Maps enrichment.items.slot_type → plan_slot key for weapon items.

Revision ID: 0156
Revises: 0155
Create Date: 2026-04-20
"""
from alembic import op

revision = "0156"
down_revision = "0155"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Reclassify character_equipment.slot for main_hand rows
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE guild_identity.character_equipment ce
           SET slot = CASE
               WHEN ei.slot_type IN ('two_hand', 'ranged') THEN 'main_hand_2h'
               WHEN ei.slot_type = 'one_hand'              THEN 'main_hand_1h'
               ELSE                                              'main_hand_2h'
           END
          FROM enrichment.items ei
         WHERE ce.slot = 'main_hand'
           AND ei.blizzard_item_id = ce.blizzard_item_id
    """)
    # Remaining 'main_hand' rows (item not in enrichment.items) → 2h default
    op.execute("""
        UPDATE guild_identity.character_equipment
           SET slot = 'main_hand_2h'
         WHERE slot = 'main_hand'
    """)

    # -------------------------------------------------------------------------
    # 2. Rebuild viz.slot_items — add weapon_plan_slot computed column
    # -------------------------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS viz.slot_items")
    op.execute("""
        CREATE VIEW viz.slot_items AS
        SELECT
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.slot_type,
            i.armor_type,
            i.weapon_subtype,
            i.primary_stat,
            i.item_category,
            i.tier_set_suffix,
            i.quality_track,
            s.id               AS source_id,
            s.instance_type,
            s.encounter_name,
            s.instance_name,
            s.blizzard_instance_id,
            s.blizzard_encounter_id,
            s.quality_tracks,
            s.is_junk,
            i.playable_class_ids,
            CASE
                WHEN i.slot_type IN ('two_hand', 'ranged') THEN 'main_hand_2h'
                WHEN i.slot_type = 'one_hand'              THEN 'main_hand_1h'
                WHEN i.slot_type = 'off_hand'              THEN 'off_hand'
                ELSE NULL
            END AS weapon_plan_slot
        FROM enrichment.items i
        JOIN enrichment.item_seasons ise ON ise.blizzard_item_id = i.blizzard_item_id
        JOIN patt.raid_seasons rs        ON rs.id = ise.season_id AND rs.is_active = TRUE
        LEFT JOIN enrichment.item_sources s
               ON s.blizzard_item_id = i.blizzard_item_id
              AND (
                      s.instance_type = 'world_boss'
                  OR (s.instance_type = 'dungeon' AND s.blizzard_instance_id = ANY(rs.current_instance_ids))
                  OR (s.instance_type = 'raid'    AND s.blizzard_instance_id = ANY(rs.current_raid_ids))
              )
        WHERE NOT COALESCE(s.is_junk, FALSE)
          AND (i.item_category != 'crafted' OR i.quality = 'EPIC')
    """)


def downgrade() -> None:
    # Revert character_equipment weapon slot reclassification (best-effort)
    op.execute("""
        UPDATE guild_identity.character_equipment
           SET slot = 'main_hand'
         WHERE slot IN ('main_hand_2h', 'main_hand_1h')
    """)

    # Rebuild viz.slot_items without weapon_plan_slot
    op.execute("DROP VIEW IF EXISTS viz.slot_items")
    op.execute("""
        CREATE VIEW viz.slot_items AS
        SELECT
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.slot_type,
            i.armor_type,
            i.weapon_subtype,
            i.primary_stat,
            i.item_category,
            i.tier_set_suffix,
            i.quality_track,
            s.id               AS source_id,
            s.instance_type,
            s.encounter_name,
            s.instance_name,
            s.blizzard_instance_id,
            s.blizzard_encounter_id,
            s.quality_tracks,
            s.is_junk,
            i.playable_class_ids
        FROM enrichment.items i
        JOIN enrichment.item_seasons ise ON ise.blizzard_item_id = i.blizzard_item_id
        JOIN patt.raid_seasons rs        ON rs.id = ise.season_id AND rs.is_active = TRUE
        LEFT JOIN enrichment.item_sources s
               ON s.blizzard_item_id = i.blizzard_item_id
              AND (
                      s.instance_type = 'world_boss'
                  OR (s.instance_type = 'dungeon' AND s.blizzard_instance_id = ANY(rs.current_instance_ids))
                  OR (s.instance_type = 'raid'    AND s.blizzard_instance_id = ANY(rs.current_raid_ids))
              )
        WHERE NOT COALESCE(s.is_junk, FALSE)
          AND (i.item_category != 'crafted' OR i.quality = 'EPIC')
    """)
