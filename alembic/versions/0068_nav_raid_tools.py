"""Nav: move Progression + Warcraft Logs into Raid/M+ Tools category

Revision ID: 0068
Revises: 0067
Create Date: 2026-04-04

Moves guild_tools category label to "Raid / M+ Tools" and pulls
Progression + Warcraft Logs out of Player Management into that group.
"""

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Move Progression into the guild_tools / Raid / M+ Tools category
    op.execute("""
        UPDATE common.screen_permissions
           SET category       = 'guild_tools',
               category_label = 'Raid / M+ Tools',
               category_order = 2,
               nav_order      = 0
         WHERE screen_key = 'progression'
    """)

    # Move Warcraft Logs into the same category
    op.execute("""
        UPDATE common.screen_permissions
           SET category       = 'guild_tools',
               category_label = 'Raid / M+ Tools',
               category_order = 2,
               nav_order      = 1
         WHERE screen_key = 'warcraft_logs'
    """)

    # Rename the existing Gear Plan / BIS entry label + reorder after the two above
    op.execute("""
        UPDATE common.screen_permissions
           SET category_label = 'Raid / M+ Tools',
               nav_order      = 2
         WHERE screen_key = 'gear_plan'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE common.screen_permissions
           SET category       = 'player_management',
               category_label = 'Player Management',
               category_order = 0,
               nav_order      = 5
         WHERE screen_key = 'progression'
    """)
    op.execute("""
        UPDATE common.screen_permissions
           SET category       = 'player_management',
               category_label = 'Player Management',
               category_order = 0,
               nav_order      = 6
         WHERE screen_key = 'warcraft_logs'
    """)
    op.execute("""
        UPDATE common.screen_permissions
           SET category_label = 'Guild Tools',
               nav_order      = 10
         WHERE screen_key = 'gear_plan'
    """)
