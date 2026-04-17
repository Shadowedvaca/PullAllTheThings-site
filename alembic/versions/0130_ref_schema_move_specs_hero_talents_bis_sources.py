"""feat: move specializations, hero_talents, bis_list_sources to ref schema

Phase F of gear plan schema overhaul. These three tables are pure reference /
game-config data and belong in the ref schema alongside ref.classes (Phase E).

Changes:
  - ALTER TABLE guild_identity.specializations    SET SCHEMA ref
  - ALTER TABLE guild_identity.hero_talents       SET SCHEMA ref
  - ALTER TABLE guild_identity.bis_list_sources   SET SCHEMA ref
  - Recreate viz.bis_recommendations to JOIN ref.bis_list_sources
    (views reference tables by name, not OID, so this is required)

FK constraints on config.bis_scrape_targets and guild_identity.* tables that
point to these three tables do NOT need updating — PostgreSQL FK constraints
reference the table OID, not the schema-qualified name, so they survive a
schema rename automatically.

Revision ID: 0130
Revises: 0129
Create Date: 2026-04-17
"""
from alembic import op

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None


def upgrade():
    # Move the three tables to the ref schema (ref schema exists since 0127)
    op.execute("ALTER TABLE guild_identity.specializations  SET SCHEMA ref")
    op.execute("ALTER TABLE guild_identity.hero_talents     SET SCHEMA ref")
    op.execute("ALTER TABLE guild_identity.bis_list_sources SET SCHEMA ref")

    # viz.bis_recommendations references guild_identity.bis_list_sources by name —
    # drop and recreate with the new ref.bis_list_sources reference.
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
            be.priority,
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


def downgrade():
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
            be.priority,
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
        JOIN guild_identity.bis_list_sources bls
            ON bls.id = be.source_id
    """)

    op.execute("ALTER TABLE ref.bis_list_sources SET SCHEMA guild_identity")
    op.execute("ALTER TABLE ref.hero_talents     SET SCHEMA guild_identity")
    op.execute("ALTER TABLE ref.specializations  SET SCHEMA guild_identity")
