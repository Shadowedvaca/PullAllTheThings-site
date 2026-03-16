"""Rename Campaigns nav section to Social Tools and reorder below Crafting.

Revision ID: 0043
Revises: 0042
Create Date: 2026-03-16
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    # Campaigns → Social Tools, move below Crafting (order 4)
    op.execute("""
        UPDATE common.screen_permissions
        SET category = 'social_tools',
            category_label = 'Social Tools',
            category_order = 4
        WHERE category = 'campaigns'
    """)

    # Settings bumps from 4 → 5 to make room
    op.execute("""
        UPDATE common.screen_permissions
        SET category_order = 5
        WHERE category = 'settings_admin'
    """)


def downgrade():
    op.execute("""
        UPDATE common.screen_permissions
        SET category_order = 4
        WHERE category = 'settings_admin'
    """)

    op.execute("""
        UPDATE common.screen_permissions
        SET category = 'campaigns',
            category_label = 'Campaigns',
            category_order = 2
        WHERE category = 'social_tools'
    """)
