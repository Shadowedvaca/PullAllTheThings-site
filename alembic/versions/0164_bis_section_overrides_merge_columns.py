"""config.bis_section_overrides: add merge columns for guide folding.

Adds four nullable columns to support merging two guide sections into one
content_type target (e.g. Blood DK hero-talent-split Overall):

  secondary_section_key — section key to fold into the primary; triggers merge pass
  primary_note          — bis_note stamped on items that appear only in primary
  match_note            — bis_note stamped on items that appear in both sections
  secondary_note        — bis_note stamped on items that appear only in secondary

A row with secondary_section_key IS NULL behaves exactly as before (simple redirect).

Revision ID: 0164
Revises: 0163
Create Date: 2026-04-21
"""
from alembic import op

revision = "0164"
down_revision = "0163"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE config.bis_section_overrides
            ADD COLUMN secondary_section_key VARCHAR(100),
            ADD COLUMN primary_note          VARCHAR(100),
            ADD COLUMN match_note            VARCHAR(100),
            ADD COLUMN secondary_note        VARCHAR(100)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE config.bis_section_overrides
            DROP COLUMN IF EXISTS secondary_section_key,
            DROP COLUMN IF EXISTS primary_note,
            DROP COLUMN IF EXISTS match_note,
            DROP COLUMN IF EXISTS secondary_note
    """)
