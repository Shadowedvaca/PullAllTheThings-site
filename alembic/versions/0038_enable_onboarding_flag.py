"""Phase 4.4.3: Add enable_onboarding flag to site_config

Revision ID: 0038
Revises: 0037
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "site_config",
        sa.Column("enable_onboarding", sa.Boolean(), nullable=False, server_default="true"),
        schema="common",
    )


def downgrade():
    op.drop_column("site_config", "enable_onboarding", schema="common")
