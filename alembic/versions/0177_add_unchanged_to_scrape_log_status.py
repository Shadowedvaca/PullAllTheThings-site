"""fix: add 'unchanged' to log.bis_scrape_log status check constraint

Phase 1.7-C introduced 'unchanged' as a valid sync_target() status but the
check constraint on log.bis_scrape_log was never updated to include it.

Revision ID: 0177
Revises: 0176
Create Date: 2026-04-23
"""
from alembic import op

revision = "0177"
down_revision = "0176"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE log.bis_scrape_log DROP CONSTRAINT bis_scrape_log_status_check")
    op.execute("""
        ALTER TABLE log.bis_scrape_log
            ADD CONSTRAINT bis_scrape_log_status_check
            CHECK (status IN ('success', 'partial', 'failed', 'unchanged'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE log.bis_scrape_log DROP CONSTRAINT bis_scrape_log_status_check")
    op.execute("""
        ALTER TABLE log.bis_scrape_log
            ADD CONSTRAINT bis_scrape_log_status_check
            CHECK (status IN ('success', 'partial', 'failed'))
    """)
