"""Phase 6.1: Error Catalogue — common.error_log table + indexes.

Revision ID: 0046
Revises: 0045
Create Date: 2026-03-16
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE common.error_log (
            id                SERIAL PRIMARY KEY,
            issue_type        VARCHAR(80)  NOT NULL,
            severity          VARCHAR(10)  NOT NULL DEFAULT 'warning',
            source_module     VARCHAR(80),
            identifier        VARCHAR(255),
            summary           TEXT         NOT NULL,
            details           JSONB,
            issue_hash        VARCHAR(64)  NOT NULL,
            occurrence_count  INTEGER      NOT NULL DEFAULT 1,
            first_occurred_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            resolved_at       TIMESTAMPTZ,
            resolved_by       VARCHAR(80)
        )
    """)

    # One open (unresolved) record per hash at a time.
    # When resolved_at IS NOT NULL the record is "closed" and a new INSERT can create a
    # fresh first_occurred_at record if the error recurs.
    op.execute("""
        CREATE UNIQUE INDEX uq_error_log_hash_active
            ON common.error_log (issue_hash)
            WHERE resolved_at IS NULL
    """)

    op.execute("""
        CREATE INDEX idx_error_log_type
            ON common.error_log (issue_type)
    """)

    op.execute("""
        CREATE INDEX idx_error_log_severity
            ON common.error_log (severity)
    """)

    op.execute("""
        CREATE INDEX idx_error_log_active
            ON common.error_log (resolved_at)
            WHERE resolved_at IS NULL
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS common.error_log CASCADE")
