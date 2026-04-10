"""feat: add roster_needs admin nav entry (Officer+).

Revision ID: 0089
Revises: 0088
"""

from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO common.screen_permissions
            (screen_key, display_name, url_path, category, category_label,
             category_order, nav_order, min_rank_level)
        VALUES
            ('roster_needs', 'Roster Needs', '/admin/roster-needs',
             'guild_tools', 'Guild Tools', 2, 11, 4)
        ON CONFLICT (screen_key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute(
        "DELETE FROM common.screen_permissions WHERE screen_key = 'roster_needs'"
    )
