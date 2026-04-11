"""Add excluded_item_ids to guild_identity.gear_plan_slots for Phase 1E.5.

Lets players permanently exclude specific items per slot so Fill BIS skips them,
BIS recommendations omit them, and the Available from Content list hides them.

Revision ID: 0093
Revises: 0092
"""

from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE guild_identity.gear_plan_slots
        ADD COLUMN IF NOT EXISTS excluded_item_ids INTEGER[] NOT NULL DEFAULT '{}'
    """)


def downgrade():
    op.execute("""
        ALTER TABLE guild_identity.gear_plan_slots
        DROP COLUMN IF EXISTS excluded_item_ids
    """)
