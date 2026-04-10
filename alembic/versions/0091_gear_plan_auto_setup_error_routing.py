"""seed error_routing row for gear_plan_auto_setup_failed.

Routes gear plan auto-setup failures (from equipment sync) to the Discord
audit channel so they are visible without checking server logs.

Revision ID: 0091
Revises: 0090
"""

from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO common.error_routing
            (issue_type, min_severity, dest_audit_log, dest_discord, first_only, notes)
        VALUES
            ('gear_plan_auto_setup_failed', 'warning', TRUE, TRUE, FALSE,
             'Gear plan auto-setup failures during equipment sync')
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    op.execute("""
        DELETE FROM common.error_routing
         WHERE issue_type = 'gear_plan_auto_setup_failed'
    """)
