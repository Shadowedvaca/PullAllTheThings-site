"""Add quality_ilvl_map to site_config — Phase 2C quality-aware display.

Stores per-quality-track ilvl ranges for the current season.
Display logic reads this at query time to compute the target ilvl
for Wowhead tooltip links (?ilvl=N) in the gear plan slot drawer.

Left NULL on migration — configure via Admin → Site Config after confirming
the actual Midnight Season ilvl ceilings from in-game data.

Revision ID: 0097
Revises: 0096
"""

from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE common.site_config
          ADD COLUMN IF NOT EXISTS quality_ilvl_map JSONB
    """)


def downgrade():
    op.execute("""
        ALTER TABLE common.site_config
          DROP COLUMN IF EXISTS quality_ilvl_map
    """)
