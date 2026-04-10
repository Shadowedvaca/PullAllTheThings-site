"""Add is_suspected_junk flag to item_sources.

Marks rows that should be excluded from gear plan display:
  - Null-ID world boss rows (alpha/beta artifacts with no valid encounter reference)
  - Tier piece direct-source rows (tier pieces come via tokens, not direct drops)

Flagging is performed by flag_junk_sources() in item_source_sync.py,
called manually from the BIS admin page or as part of process_tier_tokens()
in Phase 1D.5.

Revision ID: 0086
Revises: 0085
"""

from alembic import op
import sqlalchemy as sa

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item_sources",
        sa.Column(
            "is_suspected_junk",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="guild_identity",
    )


def downgrade() -> None:
    op.drop_column("item_sources", "is_suspected_junk", schema="guild_identity")
