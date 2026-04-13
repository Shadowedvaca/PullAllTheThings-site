"""Revert quality_ilvl_map column — Phase 2C approach scrapped.

The ilvl map will be derived from the Blizzard API, not stored in site_config.

Revision ID: 0098
Revises: 0097
"""

from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE common.site_config
          DROP COLUMN IF EXISTS quality_ilvl_map
    """)


def downgrade():
    op.execute("""
        ALTER TABLE common.site_config
          ADD COLUMN IF NOT EXISTS quality_ilvl_map JSONB
    """)
