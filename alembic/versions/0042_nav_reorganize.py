"""Reorganize admin nav: Attendance → Event Management, AH Pricing → Crafting.

Revision ID: 0042
Revises: 0041
Create Date: 2026-03-16
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    # Move Attendance from spurious 'raid_tools' category into 'event_management'
    op.execute("""
        UPDATE common.screen_permissions
        SET category = 'event_management',
            category_label = 'Event Management',
            category_order = 1,
            nav_order = 2
        WHERE screen_key = 'attendance_report'
    """)

    # Move AH Pricing from 'guild_tools' into 'crafting'
    op.execute("""
        UPDATE common.screen_permissions
        SET category = 'crafting',
            category_label = 'Crafting',
            category_order = 3,
            nav_order = 1
        WHERE screen_key = 'ah_pricing'
    """)


def downgrade():
    op.execute("""
        UPDATE common.screen_permissions
        SET category = 'raid_tools',
            category_label = 'Raid Tools',
            category_order = 3,
            nav_order = 5
        WHERE screen_key = 'attendance_report'
    """)

    op.execute("""
        UPDATE common.screen_permissions
        SET category = 'guild_tools',
            category_label = 'Guild Tools',
            category_order = 2,
            nav_order = 1
        WHERE screen_key = 'ah_pricing'
    """)
