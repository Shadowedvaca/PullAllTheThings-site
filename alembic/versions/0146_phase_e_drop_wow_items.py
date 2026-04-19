"""feat: Phase E — drop guild_identity.wow_items

All inbound FK references were removed in migration 0145. This migration drops
the table itself, completing the wow_items retirement plan.

After this migration:
  - guild_identity.wow_items no longer exists
  - enrichment.items is the sole canonical item store
  - landing.wowhead_tooltips is the sole canonical tooltip store
  - landing.blizzard_items is the sole canonical Blizzard API payload store

Revision ID: 0146
Revises: 0145
"""

revision = "0146"
down_revision = "0145"

from alembic import op


def upgrade():
    # Verify no FK constraints remain before dropping (informational guard).
    # If any FK still points at wow_items, DROP TABLE will fail here with a
    # clear error rather than silently corrupting data.
    op.execute("""
        DROP TABLE guild_identity.wow_items
    """)


def downgrade():
    # Recreate the bare table structure; data cannot be restored from this migration.
    # Full rollback requires a DB snapshot restore.
    op.execute("""
        CREATE TABLE IF NOT EXISTS guild_identity.wow_items (
            id                   SERIAL PRIMARY KEY,
            blizzard_item_id     INTEGER NOT NULL UNIQUE,
            name                 VARCHAR(200) NOT NULL,
            icon_url             VARCHAR(500),
            slot_type            VARCHAR(20) NOT NULL,
            armor_type           VARCHAR(20),
            weapon_type          VARCHAR(30),
            wowhead_tooltip_html TEXT,
            quality_track        VARCHAR(1),
            fetched_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
