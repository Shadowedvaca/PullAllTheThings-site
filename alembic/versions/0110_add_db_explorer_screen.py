"""Add db_explorer screen permission (GL only).

Revision ID: 0110
Revises: 0109
"""

from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('db_explorer', 'DB Explorer', '/admin/db-explorer',
             'admin', 'Admin', 10, 97, 5)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade():
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'db_explorer'"
    )
