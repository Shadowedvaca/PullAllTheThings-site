"""Add simc_imported_at and equipped_source to guild_identity.gear_plans for Phase 1E.6.

Lets users switch the paperdoll equipped-gear display between Blizzard API data
(always fresh from the background sync) and a previously imported SimC profile
(instant snapshot from the in-game addon).

Revision ID: 0094
Revises: 0093
"""

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE guild_identity.gear_plans
        ADD COLUMN IF NOT EXISTS simc_imported_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS equipped_source VARCHAR(10)
            NOT NULL DEFAULT 'blizzard'
            CHECK (equipped_source IN ('blizzard', 'simc'))
    """)


def downgrade():
    op.execute("""
        ALTER TABLE guild_identity.gear_plans
        DROP COLUMN IF EXISTS simc_imported_at,
        DROP COLUMN IF EXISTS equipped_source
    """)
