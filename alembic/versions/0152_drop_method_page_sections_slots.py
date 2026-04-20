"""drop slots column from landing.method_page_sections

Slots are parsed item data and belong in the enrichment layer, not landing.
rebuild_bis_from_landing now re-parses raw HTML from landing.bis_scrape_raw.

Revision ID: 0152
Revises: 0151
Create Date: 2026-04-20
"""
from alembic import op

revision = "0152"
down_revision = "0151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE landing.method_page_sections DROP COLUMN IF EXISTS slots")


def downgrade() -> None:
    op.execute("""
        ALTER TABLE landing.method_page_sections
            ADD COLUMN slots JSONB NOT NULL DEFAULT '[]'
    """)
