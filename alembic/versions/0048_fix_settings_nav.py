"""Fix Settings nav: move error_routing into Settings, resolve category_order collision.

error_routing was placed in player_management; move to settings_admin.
guide (Help) had category_order=5 colliding with settings_admin (Settings=5),
causing two separate sections to appear adjacent. Bump guide to order=6.

Revision ID: 0048
Revises: 0047
Create Date: 2026-03-17
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    # Move error_routing from player_management into the Settings section
    op.execute("""
        UPDATE common.screen_permissions
           SET category        = 'settings_admin',
               category_label  = 'Settings',
               category_order  = 5,
               nav_order       = 2
         WHERE screen_key = 'error_routing'
    """)

    # guide had category_order=5 (same as settings_admin), creating two sections.
    # Bump it to 6 so Help renders after Settings, cleanly separated.
    op.execute("""
        UPDATE common.screen_permissions
           SET category_order = 6
         WHERE screen_key = 'guide'
    """)


def downgrade():
    op.execute("""
        UPDATE common.screen_permissions
           SET category        = 'player_management',
               category_label  = 'Player Management',
               category_order  = 0,
               nav_order       = 5
         WHERE screen_key = 'error_routing'
    """)

    op.execute("""
        UPDATE common.screen_permissions
           SET category_order = 5
         WHERE screen_key = 'guide'
    """)
