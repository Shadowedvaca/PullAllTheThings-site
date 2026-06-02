"""feat: recruiting contest additions — first_recruit_bonus, payout_type rename, multi-recruiter

Revision ID: 0180
Revises: 0179
Create Date: 2026-06-01
"""
from alembic import op

revision = "0180"
down_revision = "0179"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add first_recruit_bonus column to contests
    op.execute("""
        ALTER TABLE patt.recruiting_contests
            ADD COLUMN first_recruit_bonus INTEGER NOT NULL DEFAULT 5000
    """)

    # Rename recruit_raid → recruit and add first_recruit_bonus payout type
    op.execute("""
        UPDATE patt.recruiting_submissions
           SET payout_type = 'recruit'
         WHERE payout_type = 'recruit_raid'
    """)
    op.execute("""
        ALTER TABLE patt.recruiting_submissions
            DROP CONSTRAINT IF EXISTS recruiting_submissions_payout_type_check
    """)
    op.execute("""
        ALTER TABLE patt.recruiting_submissions
            ADD CONSTRAINT recruiting_submissions_payout_type_check
            CHECK (payout_type IN ('recruit', 'promotion', 'first_recruit_bonus'))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE patt.recruiting_submissions
            DROP CONSTRAINT IF EXISTS recruiting_submissions_payout_type_check
    """)
    op.execute("""
        DELETE FROM patt.recruiting_submissions WHERE payout_type = 'first_recruit_bonus'
    """)
    op.execute("""
        UPDATE patt.recruiting_submissions
           SET payout_type = 'recruit_raid'
         WHERE payout_type = 'recruit'
    """)
    op.execute("""
        ALTER TABLE patt.recruiting_submissions
            ADD CONSTRAINT recruiting_submissions_payout_type_check
            CHECK (payout_type IN ('recruit_raid', 'promotion'))
    """)
    op.execute("""
        ALTER TABLE patt.recruiting_contests
            DROP COLUMN IF EXISTS first_recruit_bonus
    """)
