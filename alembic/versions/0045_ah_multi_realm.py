"""Phase 5.3: AH Multi-Realm — active_connected_realm_ids + updated unique constraint.

Revision ID: 0045
Revises: 0044
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    # Drop old unique constraint (only tracks item+time, not realm)
    op.drop_constraint("uq_item_price_snapshot", "item_price_history", schema="guild_identity")

    # New unique constraint includes realm so multiple realms can store same item at same time
    op.create_unique_constraint(
        "uq_item_price_snapshot_realm",
        "item_price_history",
        ["tracked_item_id", "snapshot_at", "connected_realm_id"],
        schema="guild_identity",
    )

    # Cache list of active connected realm IDs in site_config
    op.add_column(
        "site_config",
        sa.Column(
            "active_connected_realm_ids",
            sa.ARRAY(sa.Integer),
            nullable=False,
            server_default="{}",
        ),
        schema="common",
    )


def downgrade():
    op.drop_column("site_config", "active_connected_realm_ids", schema="common")
    op.drop_constraint("uq_item_price_snapshot_realm", "item_price_history", schema="guild_identity")
    op.create_unique_constraint(
        "uq_item_price_snapshot",
        "item_price_history",
        ["tracked_item_id", "snapshot_at"],
        schema="guild_identity",
    )
