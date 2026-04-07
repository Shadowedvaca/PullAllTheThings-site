"""feat: add my_gear_plan screen_permission for member nav

Revision ID: 0079
Revises: 0078
Create Date: 2026-04-07

Adds the /gear-plan member page entry to screen_permissions so it
appears in the Settings sidebar for logged-in members (level 1+).
"""

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('my_gear_plan', 'Gear Plan', '/gear-plan',
             'player_management', 'Player Management', 0, 5, 1)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'my_gear_plan'")
