"""Add quality_ilvl_map and crafted_ilvl_map to raid_seasons — Phase 2C.

These JSONB columns store the season-specific item level ranges for each
quality track, used by the gear plan to compute target tooltip ilvls.

Seeded immediately for Midnight Season 1:
  quality_ilvl_map — raid/dungeon/tier drop ilvl bands (V/C/H/M)
  crafted_ilvl_map — crafted item ilvl bands; no Champion tier in Midnight S1

Update these columns at the start of each new season or when Blizzard adds
upgrade ranks mid-patch — no code deploy required.

Revision ID: 0099
Revises: 0096
"""

from alembic import op

revision = "0099"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE patt.raid_seasons
          ADD COLUMN IF NOT EXISTS quality_ilvl_map JSONB,
          ADD COLUMN IF NOT EXISTS crafted_ilvl_map JSONB
    """)

    # Seed Midnight Season 1 (id = 1) — ilvl ranges confirmed from Wowhead April 2026.
    # A (Adventurer) and V (Veteran) included for completeness; filtered from display
    # by existing quality filters (crafted filter: class="q4" = epic only).
    # No Champion (C) crafted tier in Midnight S1 — intentionally absent.
    op.execute("""
        UPDATE patt.raid_seasons SET
          quality_ilvl_map = '{
            "A": {"min": 220, "max": 237},
            "V": {"min": 233, "max": 250},
            "C": {"min": 246, "max": 263},
            "H": {"min": 259, "max": 276},
            "M": {"min": 272, "max": 289}
          }'::jsonb,
          crafted_ilvl_map = '{
            "A": {"min": 220, "max": 233},
            "V": {"min": 233, "max": 246},
            "H": {"min": 259, "max": 272},
            "M": {"min": 272, "max": 285}
          }'::jsonb
        WHERE id = 1
    """)


def downgrade():
    op.execute("""
        ALTER TABLE patt.raid_seasons
          DROP COLUMN IF EXISTS quality_ilvl_map,
          DROP COLUMN IF EXISTS crafted_ilvl_map
    """)
