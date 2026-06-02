"""feat: recruiting contest — patt.recruiting_contests + patt.recruiting_submissions + screen_permission

Revision ID: 0179
Revises: 0178
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0179"
down_revision = "0178"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE patt.recruiting_contests (
            id                 SERIAL PRIMARY KEY,
            title              VARCHAR(200) NOT NULL,
            description        TEXT,
            deadline           TIMESTAMPTZ,
            bounty_per_recruit INTEGER NOT NULL DEFAULT 10000,
            promotion_bounty   INTEGER NOT NULL DEFAULT 10000,
            leader_bonus       INTEGER NOT NULL DEFAULT 100000,
            status             VARCHAR(20) NOT NULL DEFAULT 'open'
                               CHECK (status IN ('open', 'closed')),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE patt.recruiting_submissions (
            id                      SERIAL PRIMARY KEY,
            contest_id              INTEGER NOT NULL
                                    REFERENCES patt.recruiting_contests(id) ON DELETE CASCADE,
            recruiter_player_id     INTEGER NOT NULL
                                    REFERENCES guild_identity.players(id),
            recruit_display_name    VARCHAR(100) NOT NULL,
            screenshot_url          TEXT,
            payout_type             VARCHAR(20) NOT NULL
                                    CHECK (payout_type IN ('recruit_raid', 'promotion')),
            gold_amount             INTEGER NOT NULL DEFAULT 0,
            approved                BOOLEAN NOT NULL DEFAULT FALSE,
            approved_at             TIMESTAMPTZ,
            approved_by_player_id   INTEGER REFERENCES guild_identity.players(id),
            paid                    BOOLEAN NOT NULL DEFAULT FALSE,
            paid_at                 TIMESTAMPTZ,
            notes                   TEXT,
            submitted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX ix_rec_submissions_contest   ON patt.recruiting_submissions (contest_id)")
    op.execute("CREATE INDEX ix_rec_submissions_recruiter ON patt.recruiting_submissions (recruiter_player_id)")

    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('recruiting_contest', 'Recruiting Contest', '/admin/recruiting-contest',
             'social_tools', 'Social Tools', 4, 3, 5)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'recruiting_contest'")
    op.execute("DROP TABLE IF EXISTS patt.recruiting_submissions")
    op.execute("DROP TABLE IF EXISTS patt.recruiting_contests")
