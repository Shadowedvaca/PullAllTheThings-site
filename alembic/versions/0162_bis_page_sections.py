"""unified landing.bis_page_sections + config.bis_section_overrides

Replaces landing.method_page_sections, landing.iv_page_sections, and
config.method_section_overrides with source-aware unified tables that work
for all BIS sources (Method, Icy Veins, any future source).

Revision ID: 0162
Revises: 0161
Create Date: 2026-04-20
"""
from alembic import op

revision = "0162"
down_revision = "0161"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE landing.bis_page_sections (
            id                 BIGSERIAL PRIMARY KEY,
            spec_id            INTEGER NOT NULL REFERENCES ref.specializations(id),
            source_id          INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
            page_url           TEXT NOT NULL,
            section_key        TEXT NOT NULL,
            section_title      TEXT NOT NULL,
            sort_order         INTEGER,
            content_type       VARCHAR(20),
            is_trinket_section BOOLEAN NOT NULL DEFAULT FALSE,
            row_count          INTEGER NOT NULL DEFAULT 0,
            is_outlier         BOOLEAN NOT NULL DEFAULT FALSE,
            outlier_reason     TEXT,
            scraped_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (spec_id, source_id, section_key)
        )
    """)

    op.execute("""
        CREATE TABLE config.bis_section_overrides (
            spec_id      INTEGER NOT NULL REFERENCES ref.specializations(id),
            source_id    INTEGER NOT NULL REFERENCES ref.bis_list_sources(id),
            content_type VARCHAR(20) NOT NULL
                             CHECK (content_type IN ('overall', 'raid', 'mythic_plus')),
            section_key  TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (spec_id, source_id, content_type)
        )
    """)

    # Migrate Method sections — one row per (spec, source, section_heading)
    op.execute("""
        INSERT INTO landing.bis_page_sections
            (spec_id, source_id, page_url, section_key, section_title,
             sort_order, content_type, is_trinket_section, row_count,
             is_outlier, outlier_reason, scraped_at)
        SELECT DISTINCT ON (mps.spec_id, bt.source_id, mps.section_heading)
            mps.spec_id,
            bt.source_id,
            bt.url,
            mps.section_heading,
            mps.section_heading,
            mps.table_index,
            mps.inferred_content_type,
            FALSE,
            mps.row_count,
            mps.is_outlier,
            mps.outlier_reason,
            mps.fetched_at
        FROM landing.method_page_sections mps
        JOIN config.bis_scrape_targets bt
            ON bt.spec_id = mps.spec_id
            AND bt.source_id IN (
                SELECT id FROM ref.bis_list_sources WHERE name ILIKE '%method%'
            )
        ORDER BY mps.spec_id, bt.source_id, mps.section_heading
        ON CONFLICT (spec_id, source_id, section_key) DO NOTHING
    """)

    # Migrate IV sections
    op.execute("""
        INSERT INTO landing.bis_page_sections
            (spec_id, source_id, page_url, section_key, section_title,
             sort_order, content_type, is_trinket_section, row_count,
             is_outlier, outlier_reason, scraped_at)
        SELECT
            spec_id, source_id, page_url,
            section_h3_id,
            section_title,
            NULL,
            content_type,
            is_trinket_section,
            row_count, is_outlier, outlier_reason, scraped_at
        FROM landing.iv_page_sections
        ON CONFLICT (spec_id, source_id, section_key) DO NOTHING
    """)

    # Migrate Method overrides (one per source — broadcast to all Method sources for that spec)
    op.execute("""
        INSERT INTO config.bis_section_overrides
            (spec_id, source_id, content_type, section_key, created_at)
        SELECT DISTINCT ON (mso.spec_id, bt.source_id, mso.content_type)
            mso.spec_id,
            bt.source_id,
            mso.content_type,
            mso.section_heading,
            mso.created_at
        FROM config.method_section_overrides mso
        JOIN config.bis_scrape_targets bt
            ON bt.spec_id = mso.spec_id
            AND bt.source_id IN (
                SELECT id FROM ref.bis_list_sources WHERE name ILIKE '%method%'
            )
        ORDER BY mso.spec_id, bt.source_id, mso.content_type
        ON CONFLICT DO NOTHING
    """)

    # Drop retired tables
    op.execute("DROP TABLE config.method_section_overrides")
    op.execute("DROP TABLE landing.iv_page_sections")
    op.execute("DROP TABLE landing.method_page_sections")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE landing.method_page_sections (
            id              SERIAL PRIMARY KEY,
            spec_id         INTEGER NOT NULL
                                REFERENCES ref.specializations(id) ON DELETE CASCADE,
            section_heading TEXT NOT NULL,
            table_index     INTEGER NOT NULL,
            row_count       INTEGER NOT NULL DEFAULT 0,
            slots           JSONB NOT NULL DEFAULT '[]',
            inferred_content_type VARCHAR(20),
            is_outlier      BOOLEAN NOT NULL DEFAULT FALSE,
            outlier_reason  TEXT,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (spec_id, section_heading)
        )
    """)
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
    op.execute("""
        CREATE TABLE config.method_section_overrides (
            spec_id      INTEGER NOT NULL
                             REFERENCES ref.specializations(id) ON DELETE CASCADE,
            content_type VARCHAR(20) NOT NULL
                             CHECK (content_type IN ('overall', 'raid', 'mythic_plus')),
            section_heading TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (spec_id, content_type)
        )
    """)
    op.execute("DROP TABLE IF EXISTS config.bis_section_overrides")
    op.execute("DROP TABLE IF EXISTS landing.bis_page_sections")
