"""create common.guide_sites table with seed data

Revision ID: 0053
Revises: 0052
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guide_sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("badge_label", sa.String(50), nullable=False),
        sa.Column("url_template", sa.Text(), nullable=False),
        sa.Column("role_dps_slug", sa.String(20), nullable=False, server_default="dps"),
        sa.Column("role_tank_slug", sa.String(20), nullable=False, server_default="tank"),
        sa.Column("role_healer_slug", sa.String(20), nullable=False, server_default="healer"),
        sa.Column("badge_bg_color", sa.String(7), nullable=False, server_default="#333333"),
        sa.Column("badge_text_color", sa.String(7), nullable=False, server_default="#ffffff"),
        sa.Column("badge_border_color", sa.String(7)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        schema="common",
    )

    op.execute("""
        INSERT INTO common.guide_sites
            (id, name, badge_label, url_template,
             role_dps_slug, role_tank_slug, role_healer_slug,
             badge_bg_color, badge_text_color, badge_border_color,
             enabled, sort_order)
        VALUES
            (1, 'Wowhead', 'Wowhead',
             'https://www.wowhead.com/guide/classes/{class}/{spec}/overview-pve-{role}',
             'dps', 'tank', 'healer',
             '#8b1a1a', '#ffd280', '#cc4444', TRUE, 1),
            (2, 'Icy Veins', 'Icy Veins',
             'https://www.icy-veins.com/wow/{spec}-{class}-pve-{role}-guide',
             'dps', 'tank', 'healing',
             '#0d3a5c', '#7ed4f7', '#2a7aaa', TRUE, 2),
            (3, 'u.gg', 'u.gg',
             'https://u.gg/wow/{spec}/{class}/talents',
             'dps', 'tank', 'healer',
             '#3d2000', '#f59c3c', '#a05a00', TRUE, 3)
    """)

    # Reset sequence so future inserts don't collide
    op.execute("SELECT setval('common.guide_sites_id_seq', 3)")


def downgrade() -> None:
    op.drop_table("guide_sites", schema="common")
