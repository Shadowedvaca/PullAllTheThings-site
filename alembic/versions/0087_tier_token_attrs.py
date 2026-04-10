"""Add tier_token_attrs table and v_tier_piece_sources view.

Tier tokens (e.g. "Alnwoven Riftbloom") are exchanged for tier gear pieces.
This table records the parsed slot and armor type for each token, enabling
the v_tier_piece_sources view to resolve: tier piece → token → boss.

The view is used by gear_plan_service to show correct boss/instance data for
tier piece desired items instead of stale direct-drop rows.

Revision ID: 0087
Revises: 0086
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tier_token_attrs",
        sa.Column("token_item_id", sa.Integer(), nullable=False),
        sa.Column("target_slot", sa.String(20), nullable=False),
        sa.Column("armor_type", sa.String(20), nullable=False),
        sa.Column(
            "eligible_class_ids",
            ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "is_auto_detected",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "is_manual_override",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("override_notes", sa.Text(), nullable=True),
        sa.Column(
            "last_processed",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["token_item_id"],
            ["guild_identity.wow_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_item_id"),
        schema="guild_identity",
    )

    # View: tier piece → token → boss source
    # Joins tier piece items to tier_token_attrs (slot + armor type match),
    # then to the token's item_sources rows to get the boss that drops the token.
    op.execute(
        """
        CREATE OR REPLACE VIEW guild_identity.v_tier_piece_sources AS
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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS guild_identity.v_tier_piece_sources")
    op.drop_table("tier_token_attrs", schema="guild_identity")
