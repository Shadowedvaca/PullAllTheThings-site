"""refactor: replace raid_seasons.name with expansion_name + season_number

Season names like "Midnight Season 1" are now stored as structured fields
(expansion_name="Midnight", season_number=1). The display name is computed
in code as "{expansion_name} Season {season_number}".

Revision ID: 0019
Revises: 0018
Create Date: 2026-02-25
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    # Add new structured columns (nullable for data migration)
    op.execute("ALTER TABLE patt.raid_seasons ADD COLUMN expansion_name VARCHAR(50)")
    op.execute("ALTER TABLE patt.raid_seasons ADD COLUMN season_number INTEGER")

    # Parse existing names matching "{expansion} Season {n}" pattern
    op.execute(
        """
        UPDATE patt.raid_seasons
        SET
            expansion_name = TRIM(SPLIT_PART(name, ' Season ', 1)),
            season_number  = CAST(TRIM(SPLIT_PART(name, ' Season ', 2)) AS INTEGER)
        WHERE name LIKE '% Season %'
          AND TRIM(SPLIT_PART(name, ' Season ', 2)) ~ '^[0-9]+$'
        """
    )
    # Fallback: names that don't match the pattern get the whole name as expansion
    op.execute(
        """
        UPDATE patt.raid_seasons
        SET expansion_name = name
        WHERE expansion_name IS NULL
        """
    )

    # Drop the freeform name column
    op.execute("ALTER TABLE patt.raid_seasons DROP COLUMN name")


def downgrade():
    op.execute("ALTER TABLE patt.raid_seasons ADD COLUMN name VARCHAR(100)")
    op.execute(
        """
        UPDATE patt.raid_seasons
        SET name = CASE
            WHEN season_number IS NOT NULL
            THEN expansion_name || ' Season ' || season_number::text
            ELSE COALESCE(expansion_name, 'Unknown')
        END
        """
    )
    op.execute("ALTER TABLE patt.raid_seasons ALTER COLUMN name SET NOT NULL")
    op.execute("ALTER TABLE patt.raid_seasons DROP COLUMN expansion_name")
    op.execute("ALTER TABLE patt.raid_seasons DROP COLUMN season_number")
