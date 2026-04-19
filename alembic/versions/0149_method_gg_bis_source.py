"""add Method.gg as a BIS source (guide_sites + bis_list_sources)

Revision ID: 0149
Revises: 0148
Create Date: 2026-04-19
"""
from alembic import op

revision = "0149"
down_revision = "0148"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO common.guide_sites
            (id, name, badge_label, url_template,
             role_dps_slug, role_tank_slug, role_healer_slug,
             badge_bg_color, badge_text_color, badge_border_color,
             slug_separator, enabled, sort_order)
        VALUES
            (4, 'Method', 'Method',
             'https://www.method.gg/guides/{spec}-{class}',
             'dps', 'tank', 'healer',
             '#0d2b1f', '#4ade80', '#16a34a',
             '-', TRUE, 4)
        ON CONFLICT DO NOTHING
    """)
    op.execute("SELECT setval('common.guide_sites_id_seq', 4)")

    op.execute("""
        INSERT INTO ref.bis_list_sources
            (name, short_label, origin, content_type, is_default, is_active,
             sort_order, guide_site_id, trinket_ratings_by_content_type)
        VALUES
            ('Method Overall', 'Method', 'method', 'overall',     FALSE, TRUE, 30,
             (SELECT id FROM common.guide_sites WHERE name = 'Method'), FALSE),
            ('Method Raid',    'Method', 'method', 'raid',        FALSE, TRUE, 31,
             (SELECT id FROM common.guide_sites WHERE name = 'Method'), FALSE),
            ('Method M+',      'Method', 'method', 'mythic_plus', FALSE, TRUE, 32,
             (SELECT id FROM common.guide_sites WHERE name = 'Method'), FALSE)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM ref.bis_list_sources WHERE origin = 'method'")
    op.execute("DELETE FROM common.guide_sites WHERE name = 'Method'")
