"""feat: Phase A — Archon BIS sources, slot_labels rings, source_updated_at on bis_scrape_raw

Three changes for the Archon.gg BIS extraction feature:
1. Add source_updated_at TIMESTAMPTZ to landing.bis_scrape_raw — stores the source's own
   lastUpdated timestamp for change detection (avoids re-scraping unchanged pages).
2. Seed config.slot_labels with 'rings'/'Rings' → NULL (expand to ring_1 + ring_2).
3. Seed ref.bis_list_sources with two Archon rows (Raid + M+).

Revision ID: 0173
Revises: 0172
Create Date: 2026-04-22
"""

from alembic import op

revision = "0173"
down_revision = "0172"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add source_updated_at to landing.bis_scrape_raw
    op.execute("""
        ALTER TABLE landing.bis_scrape_raw
            ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ
    """)

    # 2. Seed slot_labels for archon paired-ring slot (NULL = expand to ring_1 + ring_2)
    op.execute("""
        INSERT INTO config.slot_labels (page_label, slot_key)
        VALUES ('rings', NULL), ('Rings', NULL)
        ON CONFLICT (page_label) DO NOTHING
    """)

    # 3. Seed ref.bis_list_sources with Archon Raid + Archon M+
    op.execute("""
        INSERT INTO ref.bis_list_sources
            (name, short_label, origin, content_type, is_default, is_active,
             sort_order, guide_site_id, trinket_ratings_by_content_type)
        VALUES
            ('Archon M+',   'Archon M+',   'archon', 'dungeon', FALSE, TRUE, 40, NULL, FALSE),
            ('Archon Raid', 'Archon Raid', 'archon', 'raid',    FALSE, TRUE, 41, NULL, FALSE)
        ON CONFLICT (name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM ref.bis_list_sources WHERE origin = 'archon'")
    op.execute("""
        DELETE FROM config.slot_labels WHERE page_label IN ('rings', 'Rings')
    """)
    op.execute("""
        ALTER TABLE landing.bis_scrape_raw
            DROP COLUMN IF EXISTS source_updated_at
    """)
