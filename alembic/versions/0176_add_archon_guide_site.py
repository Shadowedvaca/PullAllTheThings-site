"""add Archon.gg to common.guide_sites

Revision ID: 0176
Revises: 0175
Create Date: 2026-04-22
"""
from alembic import op

revision = "0176"
down_revision = "0175"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO common.guide_sites
            (name, badge_label, url_template,
             role_dps_slug, role_tank_slug, role_healer_slug,
             badge_bg_color, badge_text_color, badge_border_color,
             slug_separator, enabled, sort_order)
        VALUES
            ('Archon', 'Archon',
             'https://www.archon.gg/wow/builds/{spec}/{class}/raid',
             'dps', 'tank', 'healer',
             '#1a0f00', '#f97316', '#f97316',
             '-', TRUE, 4)
    """)


def downgrade() -> None:
    op.execute("DELETE FROM common.guide_sites WHERE name = 'Archon'")
