"""Persist Catalyst goal routes and keep tier sources season-aligned.

Revision ID: 0188
Revises: 0187
Create Date: 2026-09-08
"""

from alembic import op


revision = "0188"
down_revision = "0187"
branch_labels = None
depends_on = None


def _create_tier_piece_sources_view(*, season_aligned: bool) -> None:
    season_joins = (
        """
        JOIN enrichment.item_seasons tier_season
            ON tier_season.blizzard_item_id = ei.blizzard_item_id
        JOIN patt.raid_seasons tier_rs
            ON tier_rs.id = tier_season.season_id
           AND tier_rs.is_active = TRUE
    """
        if season_aligned
        else ""
    )
    season_source_filter = (
        """JOIN enrichment.item_seasons token_season
            ON token_season.blizzard_item_id = tt.blizzard_item_id
           AND token_season.season_id = tier_season.season_id
        WHERE es.blizzard_instance_id = ANY(tier_rs.current_raid_ids)"""
        if season_aligned
        else ""
    )
    op.execute(f"""
        CREATE VIEW viz.tier_piece_sources AS
        SELECT
            ei.blizzard_item_id         AS tier_piece_blizzard_id,
            ei.name                     AS tier_piece_name,
            ei.slot_type,
            ei.armor_type,
            tt.blizzard_item_id         AS token_blizzard_id,
            ek.name                     AS token_name,
            es.instance_type,
            es.encounter_name           AS boss_name,
            es.instance_name,
            es.blizzard_encounter_id,
            es.blizzard_instance_id
        FROM enrichment.items ei
        {season_joins}
        JOIN enrichment.tier_tokens tt
            ON (tt.target_slot = ei.slot_type OR tt.target_slot = 'any')
           AND (ei.armor_type  = tt.armor_type  OR tt.armor_type  = 'any')
        JOIN enrichment.items ek
            ON ek.blizzard_item_id = tt.blizzard_item_id
        JOIN enrichment.item_sources es
            ON es.blizzard_item_id = tt.blizzard_item_id
           AND NOT es.is_junk
        {season_source_filter}
        {"AND" if season_aligned else "WHERE"} ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
          AND ei.armor_type IS NOT NULL
          AND ei.item_category = 'tier'
    """)


def _create_bis_recommendations_view() -> None:
    op.execute("""
        CREATE VIEW viz.bis_recommendations AS
        SELECT
            be.source_id,
            bls.name AS source_name,
            bls.short_label AS source_short_label,
            bls.origin AS source_origin,
            bls.content_type,
            be.spec_id,
            be.hero_talent_id,
            be.slot,
            be.guide_order,
            be.bis_note,
            be.recommendation_type,
            be.catalyst_tier_item_id,
            catalyst_item.name AS catalyst_tier_item_name,
            catalyst_item.icon_url AS catalyst_tier_icon_url,
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
            ) AS quality_tracks
        FROM enrichment.bis_entries be
        JOIN enrichment.items i
            ON i.blizzard_item_id = be.blizzard_item_id
        LEFT JOIN enrichment.items catalyst_item
            ON catalyst_item.blizzard_item_id = be.catalyst_tier_item_id
        JOIN ref.bis_list_sources bls
            ON bls.id = be.source_id
    """)


def upgrade() -> None:
    op.execute("""
        ALTER TABLE guild_identity.gear_plan_slots
            ADD COLUMN recommendation_type VARCHAR(10) NOT NULL DEFAULT 'direct',
            ADD COLUMN catalyst_base_item_id INTEGER,
            ADD COLUMN catalyst_base_item_name VARCHAR(200),
            ADD CONSTRAINT ck_gear_plan_slots_recommendation_type
                CHECK (recommendation_type IN ('direct', 'catalyst')),
            ADD CONSTRAINT ck_gear_plan_slots_catalyst_route
                CHECK (
                    (recommendation_type = 'direct'
                     AND catalyst_base_item_id IS NULL
                     AND catalyst_base_item_name IS NULL)
                    OR
                    (recommendation_type = 'catalyst'
                     AND catalyst_base_item_id IS NOT NULL
                     AND catalyst_base_item_id <> blizzard_item_id)
                )
    """)

    op.execute("DROP VIEW IF EXISTS viz.bis_recommendations")
    op.execute("DROP VIEW IF EXISTS viz.tier_piece_sources")
    _create_tier_piece_sources_view(season_aligned=True)

    _create_bis_recommendations_view()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS viz.bis_recommendations")
    op.execute("DROP VIEW IF EXISTS viz.tier_piece_sources")
    _create_tier_piece_sources_view(season_aligned=False)

    _create_bis_recommendations_view()
    op.execute("""
        ALTER TABLE guild_identity.gear_plan_slots
            DROP CONSTRAINT IF EXISTS ck_gear_plan_slots_catalyst_route,
            DROP CONSTRAINT IF EXISTS ck_gear_plan_slots_recommendation_type,
            DROP COLUMN IF EXISTS catalyst_base_item_name,
            DROP COLUMN IF EXISTS catalyst_base_item_id,
            DROP COLUMN IF EXISTS recommendation_type
    """)
