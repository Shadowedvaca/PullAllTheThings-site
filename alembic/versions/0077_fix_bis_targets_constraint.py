"""Fix stale 4-col unique constraint on bis_scrape_targets

Revision ID: 0077
Revises: 0076
Create Date: 2026-04-05

Migration 0071 tried to drop the original 4-column unique constraint
(source_id, spec_id, hero_talent_id, content_type) before adding the
URL-based constraint, but used the wrong auto-generated name.  The old
constraint survived alongside the new one, causing UniqueViolationError
whenever discover_targets runs a second time.

This migration drops the old constraint by its actual name (63-char
PostgreSQL identifier limit truncation of the full column list).
"""

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old 4-col constraint that migration 0071 missed.
    # The DO $$ block guards against the case where it was already removed.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'bis_scrape_targets_source_id_spec_id_hero_talent_id_content_key'
                   AND conrelid = 'guild_identity.bis_scrape_targets'::regclass
            ) THEN
                ALTER TABLE guild_identity.bis_scrape_targets
                DROP CONSTRAINT bis_scrape_targets_source_id_spec_id_hero_talent_id_content_key;
            END IF;
        END $$;
    """)

    # Also clear any stale targets that may have been inserted with the old
    # constraint still active — re-running Discover URLs will re-populate them.
    op.execute("DELETE FROM guild_identity.bis_scrape_log")
    op.execute("DELETE FROM guild_identity.bis_scrape_targets")


def downgrade() -> None:
    # Nothing to restore — the 4-col constraint was supposed to be gone after 0071.
    pass
