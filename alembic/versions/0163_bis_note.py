"""enrichment.bis_entries: add bis_note; update viz.bis_recommendations

Revision ID: 0163
Revises: 0162
Create Date: 2026-04-21
"""
from alembic import op

revision = "0163"
down_revision = "0162"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE enrichment.bis_entries
            ADD COLUMN bis_note VARCHAR(100)
    """)

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
            be.guide_order,
            be.bis_note,
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

    op.execute("""
        ALTER TABLE enrichment.bis_entries
            DROP COLUMN IF EXISTS bis_note
    """)
