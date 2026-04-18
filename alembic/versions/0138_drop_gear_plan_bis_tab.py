"""chore: remove gear_plan screen_permission (Gear Plan / BIS tab retired)

The /admin/gear-plan BIS Sync dashboard is retired in favour of
/admin/gear-plan-admin.  Drop the screen_permission row so the nav tab
no longer appears for any user.

Revision ID: 0138
Revises: 0137
"""

revision = "0138"
down_revision = "0137"

from alembic import op


def upgrade():
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'gear_plan'")


def downgrade():
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_order, nav_order, min_rank_level)
        VALUES
            ('gear_plan', 'Gear Plan / BIS', '/admin/gear-plan',
             'Admin', 3, 90, 5)
        ON CONFLICT (screen_key) DO NOTHING
    """)
