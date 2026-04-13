"""Add quality_track to wow_items — Phase 2B Full Variant Mapping.

Tracks which quality tier (V/C/H/M) a specific item ID represents.
Catalyst-slot tier pieces have distinct item IDs per quality tier;
this column lets us link "the Hero variant of this tier piece" at query time.
Journal encounter items drop at multiple qualities, so they stay NULL.

Revision ID: 0096
Revises: 0095
"""

from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE guild_identity.wow_items
          ADD COLUMN IF NOT EXISTS quality_track VARCHAR(1)
          CHECK (quality_track IN ('V','C','H','M'))
    """)


def downgrade():
    op.execute("""
        ALTER TABLE guild_identity.wow_items
          DROP COLUMN IF EXISTS quality_track
    """)
