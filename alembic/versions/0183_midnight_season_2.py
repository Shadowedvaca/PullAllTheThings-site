"""feat: activate Midnight Season 2 configuration

Revision ID: 0183
Revises: 0182
Create Date: 2026-09-03
"""

from alembic import op


def _execute(sql: str) -> None:
    op.get_bind().exec_driver_sql(sql)


revision = "0183"
down_revision = "0182"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _execute("UPDATE patt.raid_seasons SET is_active = FALSE WHERE is_active")
    _execute(
        """
        INSERT INTO patt.raid_seasons (
            expansion_name, season_number, start_date, is_active,
            is_new_expansion, blizzard_mplus_season_id, current_raid_ids,
            current_instance_ids, tier_set_ids, quality_ilvl_map, crafted_ilvl_map
        )
        SELECT
            'Midnight', 2, DATE '2026-08-18', TRUE, FALSE, 18,
            ARRAY[1317,1320]::INTEGER[],
            ARRAY[1322,1304,1311,1309,1313,1041,1030,1202]::INTEGER[],
            ARRAY[2055,2056,2057,2058,2059,2060,2061,2062,2063,2064,2065,2066,2067]::INTEGER[],
            '{"A":{"min":266,"max":282},"V":{"min":279,"max":295},"C":{"min":292,"max":308},"H":{"min":305,"max":321},"M":{"min":318,"max":334}}'::JSONB,
            '{"A":{"min":266,"max":279},"V":{"min":279,"max":292},"C":{"min":292,"max":305},"H":{"min":305,"max":318},"M":{"min":318,"max":331}}'::JSONB
        WHERE NOT EXISTS (
            SELECT 1 FROM patt.raid_seasons
             WHERE lower(expansion_name) = 'midnight' AND season_number = 2
        )
        """
    )
    _execute(
        """
        UPDATE patt.raid_seasons
           SET start_date = DATE '2026-08-18', is_active = TRUE,
               is_new_expansion = FALSE, blizzard_mplus_season_id = 18,
               current_raid_ids = ARRAY[1317,1320]::INTEGER[],
               current_instance_ids = ARRAY[1322,1304,1311,1309,1313,1041,1030,1202]::INTEGER[],
               tier_set_ids = ARRAY[2055,2056,2057,2058,2059,2060,2061,2062,2063,2064,2065,2066,2067]::INTEGER[],
               quality_ilvl_map = '{"A":{"min":266,"max":282},"V":{"min":279,"max":295},"C":{"min":292,"max":308},"H":{"min":305,"max":321},"M":{"min":318,"max":334}}'::JSONB,
               crafted_ilvl_map = '{"A":{"min":266,"max":279},"V":{"min":279,"max":292},"C":{"min":292,"max":305},"H":{"min":305,"max":318},"M":{"min":318,"max":331}}'::JSONB
         WHERE lower(expansion_name) = 'midnight' AND season_number = 2
        """
    )
    _execute(
        """CREATE UNIQUE INDEX uq_raid_seasons_one_active
               ON patt.raid_seasons (is_active) WHERE is_active"""
    )


def downgrade() -> None:
    _execute("DROP INDEX IF EXISTS patt.uq_raid_seasons_one_active")
    _execute(
        "UPDATE patt.raid_seasons SET is_active = FALSE "
        "WHERE lower(expansion_name) = 'midnight' AND season_number = 2"
    )
    _execute(
        """
        UPDATE patt.raid_seasons SET is_active = TRUE
         WHERE id = (
            SELECT id FROM patt.raid_seasons
             WHERE NOT (lower(expansion_name) = 'midnight' AND season_number = 2)
             ORDER BY start_date DESC, id DESC LIMIT 1
         )
        """
    )
