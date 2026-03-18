"""local feedback submissions table

Revision ID: 0050
Revises: 0049
Create Date: 2026-03-17
"""
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE common.feedback_submissions (
            id                    SERIAL PRIMARY KEY,
            program_name          VARCHAR(80)  NOT NULL,
            submitted_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

            is_authenticated_user BOOLEAN      NOT NULL DEFAULT FALSE,
            is_anonymous          BOOLEAN      NOT NULL DEFAULT FALSE,
            contact_info          VARCHAR(255),
            privacy_token         VARCHAR(64),

            score                 INTEGER      CHECK (score BETWEEN 1 AND 10),
            raw_feedback          TEXT         NOT NULL,

            hub_feedback_id       INTEGER,
            hub_synced_at         TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX idx_fs_program
            ON common.feedback_submissions (program_name)
    """)
    op.execute("""
        CREATE INDEX idx_fs_submitted
            ON common.feedback_submissions (submitted_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS common.feedback_submissions")
