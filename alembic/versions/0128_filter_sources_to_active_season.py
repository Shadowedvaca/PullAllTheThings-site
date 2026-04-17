"""viz.slot_items: filter item_sources join to active season instances only

Revision ID: 0128
Revises: 0127
Create Date: 2026-04-17

Items that drop from multiple instances (e.g. WoD items in 6 dungeons) were
showing all source rows once the item passed the item_seasons gate.  Move the
season instance ID filter into the JOIN so only sources from current-season
instances are displayed.  Classification and item_seasons are unchanged.
"""
from alembic import op

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE OR REPLACE VIEW viz.slot_items AS
        SELECT i.blizzard_item_id,
               i.name,
               i.icon_url,
               i.slot_type,
               i.armor_type,
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


def downgrade():
    op.execute("""
        CREATE OR REPLACE VIEW viz.slot_items AS
        SELECT i.blizzard_item_id,
               i.name,
               i.icon_url,
               i.slot_type,
               i.armor_type,
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
          LEFT JOIN enrichment.item_sources s ON s.blizzard_item_id = i.blizzard_item_id
         WHERE NOT COALESCE(s.is_junk, FALSE)
           AND (i.item_category != 'crafted' OR i.quality = 'EPIC')
    """)
