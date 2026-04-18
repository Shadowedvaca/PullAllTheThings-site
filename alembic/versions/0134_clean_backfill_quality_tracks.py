"""fix: clear incorrectly backfilled landing.blizzard_item_quality_tracks

Migration 0133 seeded landing.blizzard_item_quality_tracks from
guild_identity.wow_items.  Landing tables should be populated from the
Blizzard API (via Section A), not from guild_identity.  This migration
removes the incorrectly backfilled rows so the table starts clean and
is repopulated by the next Landing Catch Up / Flush & Fill run.

Python changes (applied alongside this migration):
  bis_routes.py — _run_landing_fill():
    - Adds appearance crawl as Step 4 (after item sets):
        derives tier suffixes from enrichment.items, fetches appearance
        set index, matches sets by suffix, fetches appearances, writes
        to landing.blizzard_appearances and
        landing.blizzard_item_quality_tracks, and adds all item IDs to
        the fetch queue.
    - Adds landing.blizzard_item_quality_tracks to the Flush & Fill
      TRUNCATE list.
    - total_steps bumped from 4 to 5.

Revision ID: 0134
Revises: 0133
"""

revision = "0134"
down_revision = "0133"

from alembic import op


def upgrade():
    op.execute("DELETE FROM landing.blizzard_item_quality_tracks")


def downgrade():
    pass
