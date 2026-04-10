"""Fix v_tier_piece_sources to include Midnight expansion tier pieces.

The original view filtered on wowhead_tooltip_html LIKE '%/item-set=%'.
Midnight expansion items have no Wowhead tooltip data, so they were always
excluded even when armor_type and slot_type were correctly populated by the
Blizzard enrichment pipeline.

New filter: a tier piece is any tier-slot item that:
  1. Has armor_type NOT NULL (populated by Blizzard API enrichment or
     process_tier_tokens), AND
  2. Either has the classic Wowhead item-set marker in its tooltip,
     OR has no direct non-junk item_sources rows (meaning all its sources
     were flagged as junk by process_tier_tokens — the same heuristic used
     in gear_plan_service.py for tier piece detection).

Non-tier helms/shoulders/etc. have real drop sources with is_suspected_junk=FALSE,
so they are correctly excluded.  True tier pieces only have junk-flagged rows
(added by enrich_catalyst_tier_items and flagged by flag_junk_sources).

Revision ID: 0088
Revises: 0087
"""

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS guild_identity.v_tier_piece_sources")
    op.execute(
        """
        CREATE VIEW guild_identity.v_tier_piece_sources AS
        SELECT
            tp.id               AS tier_piece_id,
            tp.blizzard_item_id AS tier_piece_blizzard_id,
            tp.name             AS tier_piece_name,
            tp.slot_type,
            tk.id               AS token_item_id,
            tk.name             AS token_name,
            tk.blizzard_item_id AS token_blizzard_id,
            is2.instance_type,
            is2.encounter_name  AS boss_name,
            is2.instance_name,
            is2.blizzard_encounter_id,
            is2.blizzard_instance_id
        FROM guild_identity.wow_items tp
        JOIN guild_identity.tier_token_attrs tta
            ON (tta.target_slot = tp.slot_type OR tta.target_slot = 'any')
           AND (tp.armor_type = tta.armor_type   OR tta.armor_type = 'any')
        JOIN guild_identity.wow_items tk
            ON tk.id = tta.token_item_id
        JOIN guild_identity.item_sources is2
            ON is2.item_id = tk.id
           AND NOT is2.is_suspected_junk
        WHERE tp.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
          AND tp.armor_type IS NOT NULL
          AND (
            tp.wowhead_tooltip_html LIKE '%/item-set=%'
            OR NOT EXISTS (
                SELECT 1
                  FROM guild_identity.item_sources s
                 WHERE s.item_id = tp.id
                   AND NOT s.is_suspected_junk
            )
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS guild_identity.v_tier_piece_sources")
    op.execute(
        """
        CREATE VIEW guild_identity.v_tier_piece_sources AS
        SELECT
            tp.id               AS tier_piece_id,
            tp.blizzard_item_id AS tier_piece_blizzard_id,
            tp.name             AS tier_piece_name,
            tp.slot_type,
            tk.id               AS token_item_id,
            tk.name             AS token_name,
            tk.blizzard_item_id AS token_blizzard_id,
            is2.instance_type,
            is2.encounter_name  AS boss_name,
            is2.instance_name,
            is2.blizzard_encounter_id,
            is2.blizzard_instance_id
        FROM guild_identity.wow_items tp
        JOIN guild_identity.tier_token_attrs tta
            ON (tta.target_slot = tp.slot_type OR tta.target_slot = 'any')
           AND (tp.armor_type = tta.armor_type   OR tta.armor_type = 'any')
        JOIN guild_identity.wow_items tk
            ON tk.id = tta.token_item_id
        JOIN guild_identity.item_sources is2
            ON is2.item_id = tk.id
           AND NOT is2.is_suspected_junk
        WHERE tp.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
          AND tp.wowhead_tooltip_html LIKE '%/item-set=%'
        """
    )
