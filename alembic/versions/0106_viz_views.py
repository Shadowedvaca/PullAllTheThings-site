"""Phase C — Build viz views on top of enrichment tables.

Creates 4 views in the viz schema (schema created in 0104, left empty).
Views are named for UI use cases and join enrichment.* tables.
guild_identity.* tables are still referenced for:
  - tier_token_attrs / wow_items (token bridge in viz.tier_piece_sources)
  - recipes / professions / character_recipes / wow_characters / player_characters /
    players (guild crafter resolution in viz.crafters_by_item)
  - bis_list_sources (source metadata in viz.bis_recommendations)

Phase D will switch Python to read from these views instead of the
guild_identity tables directly.

Views added:
  viz.slot_items            — items for a slot, with sources pre-joined
  viz.tier_piece_sources    — tier piece → token → boss chain
  viz.crafters_by_item      — item → guild crafters sorted by rank
  viz.bis_recommendations   — BIS recs with source metadata and quality tracks

Revision ID: 0106
Revises: 0105
"""

from alembic import op

revision = "0106"
down_revision = "0105"
branch_labels = None
depends_on = None


def upgrade():
    # -------------------------------------------------------------------------
    # viz.slot_items
    #
    # All items for a given slot with their source rows pre-joined.
    # Junk rows are filtered out so callers never see them.
    # Python queries with: WHERE slot_type = $1 AND (armor_type = $2 OR armor_type IS NULL)
    # and groups the flat result by item_category + instance_type.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE VIEW viz.slot_items AS
        SELECT
            i.blizzard_item_id,
            i.name,
            i.icon_url,
            i.slot_type,
            i.armor_type,
            i.primary_stat,
            i.item_category,
            i.tier_set_suffix,
            i.quality_track,
            s.id                    AS source_id,
            s.instance_type,
            s.encounter_name,
            s.instance_name,
            s.blizzard_instance_id,
            s.blizzard_encounter_id,
            s.quality_tracks,
            s.is_junk
        FROM enrichment.items i
        LEFT JOIN enrichment.item_sources s
               ON s.blizzard_item_id = i.blizzard_item_id
        WHERE NOT COALESCE(s.is_junk, FALSE)
    """)

    # -------------------------------------------------------------------------
    # viz.tier_piece_sources
    #
    # Ports guild_identity.v_tier_piece_sources to use enrichment tables.
    # Uses enrichment.items for tier pieces (item_category = 'tier') and
    # enrichment.item_sources for boss source data.
    # Still bridges through guild_identity.tier_token_attrs and
    # guild_identity.wow_items to resolve token items, since tier_token_attrs
    # carries FKs into guild_identity.wow_items.id.  This bridge will be
    # removed in Phase E when guild_identity legacy tables are retired.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE VIEW viz.tier_piece_sources AS
        SELECT
            ei.blizzard_item_id         AS tier_piece_blizzard_id,
            ei.name                     AS tier_piece_name,
            ei.slot_type,
            wi_tk.blizzard_item_id      AS token_blizzard_id,
            wi_tk.name                  AS token_name,
            es.instance_type,
            es.encounter_name           AS boss_name,
            es.instance_name,
            es.blizzard_encounter_id,
            es.blizzard_instance_id
        FROM enrichment.items ei
        JOIN guild_identity.tier_token_attrs tta
            ON (tta.target_slot = ei.slot_type OR tta.target_slot = 'any')
           AND (ei.armor_type   = tta.armor_type   OR tta.armor_type   = 'any')
        JOIN guild_identity.wow_items wi_tk
            ON wi_tk.id = tta.token_item_id
        JOIN enrichment.item_sources es
            ON es.blizzard_item_id = wi_tk.blizzard_item_id
           AND NOT es.is_junk
        WHERE ei.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
          AND ei.armor_type IS NOT NULL
          AND ei.item_category = 'tier'
    """)

    # -------------------------------------------------------------------------
    # viz.crafters_by_item
    #
    # For each craftable item, lists every guild member character that knows a
    # relevant recipe, sorted highest rank first then name ascending.
    # Python queries with: WHERE blizzard_item_id = $1
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE VIEW viz.crafters_by_item AS
        SELECT
            ir.blizzard_item_id,
            r.name          AS recipe_name,
            p.name          AS profession_name,
            wc.id           AS character_id,
            wc.character_name,
            gr.level        AS rank_level,
            gr.name         AS rank_name
        FROM enrichment.item_recipes ir
        JOIN guild_identity.recipes r
            ON r.id = ir.recipe_id
        JOIN guild_identity.professions p
            ON p.id = r.profession_id
        JOIN guild_identity.character_recipes cr
            ON cr.recipe_id = r.id
        JOIN guild_identity.wow_characters wc
            ON wc.id = cr.character_id
           AND wc.in_guild = TRUE
        JOIN guild_identity.player_characters pc
            ON pc.character_id = wc.id
        JOIN guild_identity.players pl
            ON pl.id = pc.player_id
        JOIN common.guild_ranks gr
            ON gr.id = pl.guild_rank_id
        ORDER BY ir.blizzard_item_id, gr.level DESC, wc.character_name ASC
    """)

    # -------------------------------------------------------------------------
    # viz.bis_recommendations
    #
    # BIS entries enriched with source metadata and aggregated quality tracks
    # from item_sources.  quality_tracks is the union of all non-junk tracks for
    # the item (ARRAY_AGG deduped) — callers use this to show upgrade labels.
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


def downgrade():
    op.execute("DROP VIEW IF EXISTS viz.bis_recommendations")
    op.execute("DROP VIEW IF EXISTS viz.crafters_by_item")
    op.execute("DROP VIEW IF EXISTS viz.tier_piece_sources")
    op.execute("DROP VIEW IF EXISTS viz.slot_items")
