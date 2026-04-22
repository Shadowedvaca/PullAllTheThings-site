"""add landing.iv_page_sections — section metadata from Icy Veins BIS pages

Stores section-level metadata extracted from IV pages during scraping
(h3 ids, content type, outlier flags, row counts). Raw HTML stays in
landing.bis_scrape_raw; items are re-parsed from there during enrichment.

Revision ID: 0161
Revises: 0160
Create Date: 2026-04-20
"""
from alembic import op

revision = "0161"
down_revision = "0160"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE landing.iv_page_sections (
            id                 BIGSERIAL PRIMARY KEY,
            spec_id            INTEGER NOT NULL REFERENCES ref.specializations(id),
            source_id          INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
            page_url           TEXT NOT NULL,
            section_h3_id      TEXT NOT NULL,
            section_title      TEXT NOT NULL,
            content_type       VARCHAR(20),
            is_trinket_section BOOLEAN NOT NULL DEFAULT FALSE,
            row_count          INTEGER NOT NULL DEFAULT 0,
            is_outlier         BOOLEAN NOT NULL DEFAULT FALSE,
            outlier_reason     TEXT,
            scraped_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (spec_id, source_id, section_h3_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE landing.iv_page_sections")
