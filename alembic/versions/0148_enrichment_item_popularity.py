"""feat: enrichment.item_popularity table + viz.item_popularity view

Table grain: (source_id, spec_id, slot, blizzard_item_id)
- source_id encodes both the site (u.gg, Archon, etc.) and guide type (raid, M+)
- slot is the normalized name from the source — 'ring' and 'trinket' are already
  unified in u.gg's items_table, so no paired-slot duplication exists
- count: players using this item in this slot from this source
- total: total sample size for this source × spec × slot (needed to correctly
  weight cross-source percentage when Archon and other sources are added)

View: viz.item_popularity
- Aggregates count and total across all sources that share the same content_type
- Outputs popularity_pct = SUM(count) / SUM(total) * 100
- Join key for service layer: (content_type, spec_id, slot, blizzard_item_id)
"""

revision = "0148"
down_revision = "0147"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE TABLE enrichment.item_popularity (
            id               SERIAL PRIMARY KEY,
            source_id        INTEGER NOT NULL
                             REFERENCES ref.bis_list_sources(id) ON DELETE RESTRICT,
            spec_id          INTEGER NOT NULL
                             REFERENCES ref.specializations(id) ON DELETE RESTRICT,
            slot             VARCHAR(20) NOT NULL,
            blizzard_item_id INTEGER NOT NULL,
            count            INTEGER NOT NULL,
            total            INTEGER NOT NULL,
            scraped_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            UNIQUE (source_id, spec_id, slot, blizzard_item_id)
        )
    """)

    op.execute("""
        CREATE INDEX idx_item_popularity_spec_slot
            ON enrichment.item_popularity (spec_id, slot)
    """)

    op.execute("""
        CREATE OR REPLACE VIEW viz.item_popularity AS
        SELECT
            src.content_type,
            ip.spec_id,
            ip.slot,
            ip.blizzard_item_id,
            SUM(ip.count)::INTEGER                                          AS total_count,
            SUM(ip.total)::INTEGER                                          AS total_slot_fills,
            ROUND(SUM(ip.count)::NUMERIC / NULLIF(SUM(ip.total), 0) * 100, 2) AS popularity_pct
        FROM enrichment.item_popularity ip
        JOIN ref.bis_list_sources src ON src.id = ip.source_id
        GROUP BY src.content_type, ip.spec_id, ip.slot, ip.blizzard_item_id
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS viz.item_popularity")
    op.execute("DROP TABLE IF EXISTS enrichment.item_popularity")
