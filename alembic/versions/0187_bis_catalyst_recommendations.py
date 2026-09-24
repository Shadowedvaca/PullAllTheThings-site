"""Preserve explicit Catalyst base and result items in BIS recommendations.

Revision ID: 0187
Revises: 0186
Create Date: 2026-09-07
"""

from alembic import op


revision = "0187"
down_revision = "0186"
branch_labels = None
depends_on = None


def _create_bis_view() -> None:
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
            be.recommendation_type,
            be.catalyst_tier_item_id,
            catalyst_item.name           AS catalyst_tier_item_name,
            catalyst_item.icon_url       AS catalyst_tier_icon_url,
            catalyst_item.tier_set_suffix AS catalyst_tier_set_suffix,
            EXISTS (
                SELECT 1
                  FROM viz.tier_piece_sources catalyst_source
                 WHERE catalyst_source.tier_piece_blizzard_id = be.catalyst_tier_item_id
            ) AS catalyst_tier_direct_available,
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.item_category,
            i.tier_set_suffix,
            i.armor_type,
            i.quality_track,
            i.primary_stats,
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
        LEFT JOIN enrichment.items catalyst_item
            ON catalyst_item.blizzard_item_id = be.catalyst_tier_item_id
        JOIN ref.bis_list_sources bls
            ON bls.id = be.source_id
    """)


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS viz.bis_recommendations")
    op.execute("""
        ALTER TABLE enrichment.bis_entries
            ADD COLUMN recommendation_type VARCHAR(10) NOT NULL DEFAULT 'direct',
            ADD COLUMN catalyst_tier_item_id INTEGER
                REFERENCES enrichment.items (blizzard_item_id),
            ADD CONSTRAINT ck_bis_entries_recommendation_type
                CHECK (recommendation_type IN ('direct', 'catalyst')),
            ADD CONSTRAINT ck_bis_entries_catalyst_relationship
                CHECK (
                    (recommendation_type = 'direct' AND catalyst_tier_item_id IS NULL)
                    OR
                    (recommendation_type = 'catalyst'
                     AND catalyst_tier_item_id IS NOT NULL
                     AND catalyst_tier_item_id <> blizzard_item_id)
                )
    """)
    _create_bis_view()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS viz.bis_recommendations")
    op.execute("""
        ALTER TABLE enrichment.bis_entries
            DROP CONSTRAINT IF EXISTS ck_bis_entries_catalyst_relationship,
            DROP CONSTRAINT IF EXISTS ck_bis_entries_recommendation_type,
            DROP COLUMN IF EXISTS catalyst_tier_item_id,
            DROP COLUMN IF EXISTS recommendation_type
    """)
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
