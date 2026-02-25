"""fix: rename blizzard_spell_id to blizzard_recipe_id and use /recipe= Wowhead URL

Revision ID: 0017
Revises: 0016
Create Date: 2026-02-25
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the generated column first (can't alter expression of a generated column)
    op.execute("ALTER TABLE guild_identity.recipes DROP COLUMN wowhead_url")

    # Rename blizzard_spell_id -> blizzard_recipe_id
    op.execute(
        "ALTER TABLE guild_identity.recipes RENAME COLUMN blizzard_spell_id TO blizzard_recipe_id"
    )

    # Re-add generated column with the correct /recipe= URL
    op.execute(
        """
        ALTER TABLE guild_identity.recipes
        ADD COLUMN wowhead_url VARCHAR(300) GENERATED ALWAYS AS (
            'https://www.wowhead.com/recipe=' || blizzard_recipe_id
        ) STORED
        """
    )


def downgrade():
    op.execute("ALTER TABLE guild_identity.recipes DROP COLUMN wowhead_url")
    op.execute(
        "ALTER TABLE guild_identity.recipes RENAME COLUMN blizzard_recipe_id TO blizzard_spell_id"
    )
    op.execute(
        """
        ALTER TABLE guild_identity.recipes
        ADD COLUMN wowhead_url VARCHAR(300) GENERATED ALWAYS AS (
            'https://www.wowhead.com/spell=' || blizzard_spell_id
        ) STORED
        """
    )
