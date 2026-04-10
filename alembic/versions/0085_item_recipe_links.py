"""Add item_recipe_links table for connecting wow_items to crafting recipes.

Links are built by name-matching during the post-processing step after
Sync Loot Tables.  confidence indicates match quality:
  100 = exact name match
   90 = name match after stripping recipe prefix (e.g. "Recipe: ", "Pattern: ")

Revision ID: 0085
Revises: 0084
"""

from alembic import op
import sqlalchemy as sa

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_recipe_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("match_type", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_item_recipe_confidence"),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["guild_identity.wow_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["guild_identity.recipes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "recipe_id", name="uq_item_recipe"),
        schema="guild_identity",
    )
    op.create_index(
        "idx_item_recipe_links_item",
        "item_recipe_links",
        ["item_id"],
        schema="guild_identity",
    )
    op.create_index(
        "idx_item_recipe_links_recipe",
        "item_recipe_links",
        ["recipe_id"],
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_index("idx_item_recipe_links_recipe", table_name="item_recipe_links", schema="guild_identity")
    op.drop_index("idx_item_recipe_links_item", table_name="item_recipe_links", schema="guild_identity")
    op.drop_table("item_recipe_links", schema="guild_identity")
