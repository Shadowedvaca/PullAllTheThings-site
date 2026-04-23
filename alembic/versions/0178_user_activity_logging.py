"""feat: user activity logging — add tracking columns + user_activity table

Phase 1.8-A: adds last_active_at, last_login_at, login_count to common.users
and creates common.user_activity for daily page-view rollups.

Revision ID: 0178
Revises: 0177
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0178"
down_revision = "0177"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE common.users
            ADD COLUMN last_active_at  TIMESTAMPTZ,
            ADD COLUMN last_login_at   TIMESTAMPTZ,
            ADD COLUMN login_count     INTEGER NOT NULL DEFAULT 0
    """)

    op.execute("""
        CREATE TABLE common.user_activity (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL REFERENCES common.users(id) ON DELETE CASCADE,
            activity_date   DATE NOT NULL DEFAULT CURRENT_DATE,
            page_views      INTEGER NOT NULL DEFAULT 0,
            pages_visited   TEXT[] NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, activity_date)
        )
    """)

    op.execute("CREATE INDEX ix_user_activity_user_id ON common.user_activity (user_id)")
    op.execute("CREATE INDEX ix_user_activity_date    ON common.user_activity (activity_date DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS common.user_activity")
    op.execute("""
        ALTER TABLE common.users
            DROP COLUMN IF EXISTS last_active_at,
            DROP COLUMN IF EXISTS last_login_at,
            DROP COLUMN IF EXISTS login_count
    """)
