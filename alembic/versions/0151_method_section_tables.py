"""create landing.method_page_sections and config.method_section_overrides

Revision ID: 0151
Revises: 0150
Create Date: 2026-04-19
"""
from alembic import op

revision = "0151"
down_revision = "0150"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS config.method_section_overrides")
    op.execute("DROP TABLE IF EXISTS landing.method_page_sections")
