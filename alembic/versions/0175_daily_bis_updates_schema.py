"""feat: Phase 1.7-A — daily BIS update schema foundations

Adds:
- config.bis_scrape_targets: is_active, check_interval_days, next_check_at
- landing.bis_scrape_raw: content_hash (SHA-256 of page content)
- landing.bis_daily_runs: new table for daily job run records
- common.site_config: 7 SMTP/email columns for daily BIS email reports
- Backfills next_check_at on all existing targets
- Backfills content_hash on all existing bis_scrape_raw rows
- Silences known-dead targets (status='failed', items_found=0, fetched)

Revision ID: 0175
Revises: 0174
Create Date: 2026-04-22
"""

import hashlib
from alembic import op
from sqlalchemy import text

revision = "0175"
down_revision = "0174"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. config.bis_scrape_targets additions
    # -----------------------------------------------------------------------
    op.execute("""
        ALTER TABLE config.bis_scrape_targets
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS check_interval_days SMALLINT NOT NULL DEFAULT 3,
            ADD COLUMN IF NOT EXISTS next_check_at TIMESTAMPTZ
    """)

    # Backfill next_check_at: COALESCE(last_fetched, NOW()) + interval
    op.execute("""
        UPDATE config.bis_scrape_targets
        SET next_check_at = COALESCE(last_fetched, NOW()) + (check_interval_days || ' days')::INTERVAL
        WHERE next_check_at IS NULL
    """)

    # Silence known-dead targets
    op.execute("""
        UPDATE config.bis_scrape_targets
        SET is_active = FALSE
        WHERE status = 'failed'
          AND items_found = 0
          AND last_fetched IS NOT NULL
    """)

    # -----------------------------------------------------------------------
    # 2. landing.bis_scrape_raw: content_hash column
    # -----------------------------------------------------------------------
    op.execute("""
        ALTER TABLE landing.bis_scrape_raw
            ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)
    """)

    # Backfill content_hash for existing rows (Python loop — no pgcrypto needed)
    bind = op.get_bind()
    rows = bind.execute(
        text("SELECT id, content FROM landing.bis_scrape_raw WHERE content IS NOT NULL AND content_hash IS NULL")
    ).fetchall()
    for row in rows:
        try:
            h = hashlib.sha256(row.content.encode("utf-8", errors="replace")).hexdigest()
            bind.execute(
                text("UPDATE landing.bis_scrape_raw SET content_hash = :h WHERE id = :id"),
                {"h": h, "id": row.id},
            )
        except Exception:
            pass  # leave NULL rather than crashing the migration

    # -----------------------------------------------------------------------
    # 3. landing.bis_daily_runs: new table
    # -----------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS landing.bis_daily_runs (
            id                    SERIAL PRIMARY KEY,
            run_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            triggered_by          VARCHAR(20) NOT NULL DEFAULT 'scheduled',
            patch_signal          BOOLEAN NOT NULL DEFAULT FALSE,
            targets_checked       INTEGER NOT NULL DEFAULT 0,
            targets_changed       INTEGER NOT NULL DEFAULT 0,
            targets_unchanged     INTEGER NOT NULL DEFAULT 0,
            targets_failed        INTEGER NOT NULL DEFAULT 0,
            targets_skipped       INTEGER NOT NULL DEFAULT 0,
            bis_entries_before    INTEGER NOT NULL DEFAULT 0,
            bis_entries_after     INTEGER NOT NULL DEFAULT 0,
            trinket_ratings_before INTEGER NOT NULL DEFAULT 0,
            trinket_ratings_after  INTEGER NOT NULL DEFAULT 0,
            delta_added           JSONB,
            delta_removed         JSONB,
            duration_seconds      NUMERIC(8,2),
            email_sent_at         TIMESTAMPTZ,
            notes                 TEXT
        )
    """)

    # -----------------------------------------------------------------------
    # 4. common.site_config: SMTP / email columns
    # -----------------------------------------------------------------------
    op.execute("""
        ALTER TABLE common.site_config
            ADD COLUMN IF NOT EXISTS bis_encounter_count    INTEGER,
            ADD COLUMN IF NOT EXISTS bis_report_email       VARCHAR(255),
            ADD COLUMN IF NOT EXISTS smtp_host              VARCHAR(255),
            ADD COLUMN IF NOT EXISTS smtp_port              SMALLINT DEFAULT 587,
            ADD COLUMN IF NOT EXISTS smtp_user              VARCHAR(255),
            ADD COLUMN IF NOT EXISTS smtp_password_encrypted TEXT,
            ADD COLUMN IF NOT EXISTS smtp_from_address      VARCHAR(255)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE common.site_config
            DROP COLUMN IF EXISTS smtp_from_address,
            DROP COLUMN IF EXISTS smtp_password_encrypted,
            DROP COLUMN IF EXISTS smtp_user,
            DROP COLUMN IF EXISTS smtp_port,
            DROP COLUMN IF EXISTS smtp_host,
            DROP COLUMN IF EXISTS bis_report_email,
            DROP COLUMN IF EXISTS bis_encounter_count
    """)

    op.execute("DROP TABLE IF EXISTS landing.bis_daily_runs")

    op.execute("""
        ALTER TABLE landing.bis_scrape_raw
            DROP COLUMN IF EXISTS content_hash
    """)

    op.execute("""
        ALTER TABLE config.bis_scrape_targets
            DROP COLUMN IF EXISTS next_check_at,
            DROP COLUMN IF EXISTS check_interval_days,
            DROP COLUMN IF EXISTS is_active
    """)
