"""fix: add Sporefall to Midnight Season 1 raids

Revision ID: 0184
Revises: 0183
Create Date: 2026-09-07
"""

from alembic import op


revision = "0184"
down_revision = "0183"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE patt.raid_seasons
           SET current_raid_ids = array_append(
                   COALESCE(current_raid_ids, '{}'::INTEGER[]), 1305
               )
         WHERE lower(expansion_name) = 'midnight'
           AND season_number = 1
           AND NOT (1305 = ANY(COALESCE(current_raid_ids, '{}'::INTEGER[])))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE patt.raid_seasons
           SET current_raid_ids = array_remove(current_raid_ids, 1305)
         WHERE lower(expansion_name) = 'midnight'
           AND season_number = 1
        """
    )
