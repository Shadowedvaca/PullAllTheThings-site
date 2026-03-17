"""Phase 6.4 — seed per-type routing rules for background job error types.

Adds explicit error_routing rows for the five background job failure types
so officers can configure their Discord/audit routing independently.
The existing wildcard catch-all rules already cover them, but explicit rows
give per-type control without modifying the wildcards.

Revision ID: 0049
Revises: 0048
Create Date: 2026-03-16
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO common.error_routing
            (issue_type, min_severity, dest_audit_log, dest_discord, first_only, notes)
        VALUES
            ('blizzard_sync_failed',   'warning', TRUE, TRUE, TRUE, 'Blizzard API sync failure'),
            ('crafting_sync_failed',   'warning', TRUE, TRUE, TRUE, 'Crafting recipe sync failure'),
            ('wcl_sync_failed',        'warning', TRUE, TRUE, TRUE, 'Warcraft Logs sync failure'),
            ('attendance_sync_failed', 'warning', TRUE, TRUE, TRUE, 'Attendance processing failure'),
            ('ah_sync_failed',         'warning', TRUE, TRUE, TRUE, 'AH pricing sync failure')
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    op.execute("""
        DELETE FROM common.error_routing
         WHERE issue_type IN (
             'blizzard_sync_failed',
             'crafting_sync_failed',
             'wcl_sync_failed',
             'attendance_sync_failed',
             'ah_sync_failed'
         )
    """)
