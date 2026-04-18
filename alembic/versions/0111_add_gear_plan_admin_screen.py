"""Add gear_plan_admin screen permission (GL only).

Revision ID: 0111
Revises: 0110
"""

from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('gear_plan_admin', 'Gear Plan Admin', '/admin/gear-plan-admin',
             'guild_tools', 'Guild Tools', 2, 11, 5)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade():
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'gear_plan_admin'"
    )
