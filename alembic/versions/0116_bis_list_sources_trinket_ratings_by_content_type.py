"""Add trinket_ratings_by_content_type to guild_identity.bis_list_sources.

Controls how rebuild_trinket_ratings_from_landing() deduplicates raw HTML rows
when rebuilding enrichment.trinket_ratings:

  FALSE (default) — ratings are identical across all content types for this
                    source, so collapse Overall/Raid/M+ to the single most-
                    recently-fetched page per spec.  Use for Wowhead.

  TRUE            — ratings differ by content type (raid vs M+ vs overall),
                    so keep one row per (spec, source_id).  Use if/when u.gg
                    or another source publishes distinct trinket tier lists per
                    content type.

All existing rows default to FALSE (current behaviour for Wowhead sources).

Revision ID: 0116
Revises: 0115
Create Date: 2026-04-15
"""

from alembic import op

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE guild_identity.bis_list_sources
        ADD COLUMN trinket_ratings_by_content_type
            BOOLEAN NOT NULL DEFAULT FALSE
    """)

    # Rename legacy 'archon' source label in landing.bis_scrape_raw to 'ugg'.
    # The origin was renamed in migration 0114; existing raw rows retained the
    # old label because landing is append-only.  This corrects the historical rows
    # so rebuild_bis_from_landing() can find them with the current source check.
    op.execute("""
        UPDATE landing.bis_scrape_raw
           SET source = 'ugg'
         WHERE source = 'archon'
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE guild_identity.bis_list_sources
        DROP COLUMN trinket_ratings_by_content_type
    """)

    op.execute("""
        UPDATE landing.bis_scrape_raw
           SET source = 'archon'
         WHERE source = 'ugg'
    """)
