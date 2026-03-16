"""Phase 4.6: Auction House Pricing

Revision ID: 0040
Revises: 0039
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    # ── guild_identity.tracked_items ──────────────────────────────────────
    op.create_table(
        "tracked_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("item_id", sa.Integer, nullable=False, unique=True),
        sa.Column("item_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(50), server_default="consumable"),
        sa.Column("display_order", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "added_by_player_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.players.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        schema="guild_identity",
    )

    # ── guild_identity.item_price_history ─────────────────────────────────
    op.create_table(
        "item_price_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "tracked_item_id",
            sa.Integer,
            sa.ForeignKey("guild_identity.tracked_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("min_buyout", sa.BigInteger, nullable=False),
        sa.Column("median_price", sa.BigInteger),
        sa.Column("mean_price", sa.BigInteger),
        sa.Column("quantity_available", sa.Integer, nullable=False, server_default="0"),
        sa.Column("num_auctions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("connected_realm_id", sa.Integer, nullable=False),
        sa.UniqueConstraint(
            "tracked_item_id",
            "snapshot_at",
            name="uq_item_price_snapshot",
        ),
        schema="guild_identity",
    )
    op.execute(
        "CREATE INDEX idx_price_history_item ON guild_identity.item_price_history(tracked_item_id)"
    )
    op.execute(
        "CREATE INDEX idx_price_history_time ON guild_identity.item_price_history(snapshot_at DESC)"
    )

    # ── common.site_config: add connected_realm_id ────────────────────────
    op.add_column(
        "site_config",
        sa.Column("connected_realm_id", sa.Integer, nullable=True),
        schema="common",
    )

    # ── screen_permission for ah_pricing ──────────────────────────────────
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            INSERT INTO common.screen_permissions
                (screen_key, display_name, url_path, category, category_label,
                 category_order, nav_order, min_rank_level)
            VALUES
                ('ah_pricing', 'AH Pricing', '/admin/ah-pricing',
                 'guild_tools', 'Guild Tools', 2, 1, 4)
            ON CONFLICT (screen_key) DO NOTHING
        """)
    )

    # ── Seed common consumables / enchants / gems ─────────────────────────
    # Item IDs verified for The War Within Season 2.
    conn.execute(
        sa.text("""
            INSERT INTO guild_identity.tracked_items
                (item_id, item_name, category, display_order)
            VALUES
                (212241, 'Flask of Alchemical Chaos', 'consumable', 1),
                (212248, 'Flask of Tempered Versatility', 'consumable', 2),
                (212246, 'Flask of Tempered Mastery', 'consumable', 3),
                (222732, 'Heartseeking Health Injector', 'consumable', 4),
                (222509, 'Enchant Weapon - Authority of Radiant Power', 'enchant', 10),
                (222510, 'Enchant Weapon - Authority of Storms', 'enchant', 11),
                (222524, 'Enchant Cloak - Chant of Leeching Fangs', 'enchant', 12),
                (213746, 'Magnificent Jeweler''s Setting', 'gem', 20)
            ON CONFLICT (item_id) DO NOTHING
        """)
    )


def downgrade():
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM common.screen_permissions WHERE screen_key = 'ah_pricing'"
        )
    )
    op.drop_column("site_config", "connected_realm_id", schema="common")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_price_history_time")
    op.execute("DROP INDEX IF EXISTS guild_identity.idx_price_history_item")
    op.drop_table("item_price_history", schema="guild_identity")
    op.drop_table("tracked_items", schema="guild_identity")
