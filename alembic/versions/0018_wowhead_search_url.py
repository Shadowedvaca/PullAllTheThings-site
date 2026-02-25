"""fix: change wowhead_url to search URL until we resolve Blizzard spell ID mapping

The blizzard_recipe_id from the Character Professions API is a SkillLineAbility
catalog ID, NOT a WoW spell ID. Wowhead indexes recipes by spell ID.
Until we can resolve the mapping, use a Wowhead search URL which reliably
surfaces the correct recipe as the top result.

Revision ID: 0018
Revises: 0017
Create Date: 2026-02-25
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the /recipe= generated column (broken — catalog IDs ≠ Wowhead IDs)
    op.execute("ALTER TABLE guild_identity.recipes DROP COLUMN wowhead_url")

    # Replace with a search URL using the recipe name.
    # replace handles spaces; %27 handles apostrophes (common in WoW names).
    op.execute(
        """
        ALTER TABLE guild_identity.recipes
        ADD COLUMN wowhead_url VARCHAR(400) GENERATED ALWAYS AS (
            'https://www.wowhead.com/search?q='
            || replace(replace(name, ' ', '+'), '''', '%27')
        ) STORED
        """
    )


def downgrade():
    op.execute("ALTER TABLE guild_identity.recipes DROP COLUMN wowhead_url")
    op.execute(
        """
        ALTER TABLE guild_identity.recipes
        ADD COLUMN wowhead_url VARCHAR(300) GENERATED ALWAYS AS (
            'https://www.wowhead.com/recipe=' || blizzard_recipe_id
        ) STORED
        """
    )
