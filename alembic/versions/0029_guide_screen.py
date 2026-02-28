"""feat: guide screen — add Help/Guide nav item for all members

Revision ID: 0029
Revises: 0028
Create Date: 2026-02-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            INSERT INTO common.screen_permissions
                (screen_key, display_name, url_path, category, category_label,
                 category_order, nav_order, min_rank_level)
            VALUES
                ('guide', 'Guide', '/guide', 'help', 'Help', 5, 0, 1)
        """)
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM common.screen_permissions WHERE screen_key = 'guide'")
    )
