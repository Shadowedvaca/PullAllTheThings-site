"""ref schema: move guild_identity.classes → ref.classes, add blizzard_class_id; viz.slot_items crafted epic filter

Revision ID: 0127
Revises: 0126
Create Date: 2026-04-17

Two changes:
1. Move guild_identity.classes to a new ref schema and add blizzard_class_id (Blizzard's
   own class numbering — different from the internal sequential IDs). This lets the enrichment
   layer compare playable_class_ids (Blizzard IDs from the API payload) against the character's
   class without depending on the internal ID ordering.

2. Add a crafted quality filter to viz.slot_items. Classification captures all crafted items
   regardless of quality; the view filters to EPIC-only for display. Other item categories
   are unaffected.
"""
from alembic import op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    op.execute("ALTER TABLE guild_identity.classes SET SCHEMA ref")

    op.execute("ALTER TABLE ref.classes ADD COLUMN blizzard_class_id INTEGER")
    op.execute("""
        UPDATE ref.classes SET blizzard_class_id = CASE name
            WHEN 'Death Knight'  THEN 6
            WHEN 'Demon Hunter'  THEN 12
            WHEN 'Druid'         THEN 11
            WHEN 'Evoker'        THEN 13
            WHEN 'Hunter'        THEN 3
            WHEN 'Mage'          THEN 8
            WHEN 'Monk'          THEN 10
            WHEN 'Paladin'       THEN 2
            WHEN 'Priest'        THEN 5
            WHEN 'Rogue'         THEN 4
            WHEN 'Shaman'        THEN 7
            WHEN 'Warlock'       THEN 9
            WHEN 'Warrior'       THEN 1
        END
    """)

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
    """)

    op.execute("ALTER TABLE ref.classes DROP COLUMN IF EXISTS blizzard_class_id")
    op.execute("ALTER TABLE ref.classes SET SCHEMA guild_identity")
    op.execute("DROP SCHEMA IF EXISTS ref")
