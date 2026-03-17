"""Phase 6.2: Error Routing config table — common.error_routing + seed rules.

Revision ID: 0047
Revises: 0046
Create Date: 2026-03-16
"""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE common.error_routing (
            id           SERIAL PRIMARY KEY,
            issue_type   VARCHAR(80),
            min_severity VARCHAR(10)  NOT NULL DEFAULT 'warning',
            dest_audit_log  BOOLEAN NOT NULL DEFAULT TRUE,
            dest_discord    BOOLEAN NOT NULL DEFAULT TRUE,
            first_only      BOOLEAN NOT NULL DEFAULT TRUE,
            enabled      BOOLEAN NOT NULL DEFAULT TRUE,
            notes        TEXT,
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        INSERT INTO common.error_routing
            (issue_type, min_severity, dest_audit_log, dest_discord, first_only, notes)
        VALUES
            (NULL, 'critical', TRUE, TRUE,  FALSE, 'Critical: always everywhere, every time'),
            (NULL, 'warning',  TRUE, TRUE,  TRUE,  'Warning: audit log + Discord, first occurrence only'),
            (NULL, 'info',     TRUE, FALSE, TRUE,  'Info: audit log only, no Discord'),
            ('bnet_token_expired', 'warning', TRUE, TRUE, TRUE,
                'BNet token expired — player must re-link Battle.net'),
            ('bnet_sync_error', 'warning', TRUE, TRUE, TRUE,
                'BNet character sync error')
    """)

    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('error_routing', 'Error Routing', '/admin/error-routing',
             'player_management', 'Player Management', 0, 5, 4)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade():
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'error_routing'")
    op.execute("DROP TABLE IF EXISTS common.error_routing CASCADE")
