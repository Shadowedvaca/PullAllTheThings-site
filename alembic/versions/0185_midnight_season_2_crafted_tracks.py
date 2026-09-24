"""fix: remove unsupported Champion crafted track from Midnight Season 2

Revision ID: 0185
Revises: 0184
Create Date: 2026-09-07
"""

from alembic import op


revision = "0185"
down_revision = "0184"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE patt.raid_seasons
           SET crafted_ilvl_map = COALESCE(crafted_ilvl_map, '{}'::JSONB) - 'C'
         WHERE lower(expansion_name) = 'midnight'
           AND season_number = 2
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE patt.raid_seasons
           SET crafted_ilvl_map = COALESCE(crafted_ilvl_map, '{}'::JSONB)
               || '{"C":{"min":292,"max":305}}'::JSONB
         WHERE lower(expansion_name) = 'midnight'
           AND season_number = 2
        """
    )
