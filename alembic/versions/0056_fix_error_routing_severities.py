"""fix error routing severities: bnet_token_expired→info, sync failures→critical

Revision ID: 0056
Revises: 0055
Create Date: 2026-03-22
"""
from alembic import op


def upgrade():
    # bnet_token_expired is informational (player action needed, not a system failure)
    op.execute("""
        UPDATE common.error_routing
        SET min_severity = 'info'
        WHERE issue_type = 'bnet_token_expired'
    """)

    # Sync pipeline failures are critical — insert specific routing rows for each type
    op.execute("""
        INSERT INTO common.error_routing
            (issue_type, min_severity, dest_audit_log, dest_discord, first_only, notes)
        VALUES
            ('blizzard_sync_failed',  'critical', TRUE, TRUE, FALSE, 'Blizzard API sync pipeline failure'),
            ('discord_sync_failed',   'critical', TRUE, TRUE, FALSE, 'Discord member sync pipeline failure'),
            ('crafting_sync_failed',  'critical', TRUE, TRUE, FALSE, 'Crafting professions sync failure'),
            ('wcl_sync_failed',       'critical', TRUE, TRUE, FALSE, 'Warcraft Logs sync failure'),
            ('ah_sync_failed',        'critical', TRUE, TRUE, FALSE, 'Auction House pricing sync failure'),
            ('attendance_sync_failed','critical', TRUE, TRUE, FALSE, 'Attendance processing failure')
    """)


def downgrade():
    op.execute("""
        UPDATE common.error_routing
        SET min_severity = 'warning'
        WHERE issue_type = 'bnet_token_expired'
    """)

    op.execute("""
        DELETE FROM common.error_routing
        WHERE issue_type IN (
            'blizzard_sync_failed', 'discord_sync_failed', 'crafting_sync_failed',
            'wcl_sync_failed', 'ah_sync_failed', 'attendance_sync_failed'
        )
    """)
