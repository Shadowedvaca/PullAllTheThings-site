"""Add quality_ilvl_map to site_config — Phase 2C quality-aware display.

Stores per-quality-track ilvl ranges for the current season.
Display logic reads this at query time to compute the target ilvl
for Wowhead tooltip links (?ilvl=N) in the gear plan slot drawer.

Seeded with Midnight Season 1 values derived from live character_equipment data:
  H: 269–276 (8 ranks — 8 consecutive ilvls observed)
  M: 282–285 (4 ranks estimated; 285 observed as max)
  C: 263–268 (6 ranks estimated; 263 observed as min)
  V: 250–262 (8 ranks estimated; no live data — update via Admin → Site Config)

Revision ID: 0097
Revises: 0096
"""

from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

_MIDNIGHT_S1_MAP = """{
  "V": {"min": 250, "max": 262, "ranks": 8},
  "C": {"min": 263, "max": 268, "ranks": 6},
  "H": {"min": 269, "max": 276, "ranks": 8},
  "M": {"min": 282, "max": 285, "ranks": 4}
}"""


def upgrade():
    op.execute("""
        ALTER TABLE common.site_config
          ADD COLUMN IF NOT EXISTS quality_ilvl_map JSONB
    """)
    op.execute(f"""
        UPDATE common.site_config
           SET quality_ilvl_map = '{_MIDNIGHT_S1_MAP}'::jsonb
         WHERE quality_ilvl_map IS NULL
    """)


def downgrade():
    op.execute("""
        ALTER TABLE common.site_config
          DROP COLUMN IF EXISTS quality_ilvl_map
    """)
