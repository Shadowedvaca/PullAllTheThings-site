"""Add Blizzard API Explorer to admin nav (GL-only debug tool).

Revision ID: 0095
Revises: 0094
"""

from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO common.screen_permissions
               (screen_key, display_name, url_path, category_order, nav_order, min_rank_level)
        VALUES ('blizzard_api', 'Blizzard API Explorer', '/admin/blizzard-api', 10, 96, 5)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade():
    op.execute("DELETE FROM common.screen_permissions WHERE screen_key = 'blizzard_api'")
