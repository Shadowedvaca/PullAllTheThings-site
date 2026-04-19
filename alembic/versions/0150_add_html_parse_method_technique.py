"""extend bis_scrape_targets preferred_technique CHECK to include html_parse_method

Revision ID: 0150
Revises: 0149
Create Date: 2026-04-19
"""
from alembic import op

revision = "0150"
down_revision = "0149"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE config.bis_scrape_targets
            DROP CONSTRAINT bis_scrape_targets_preferred_technique_check
    """)
    op.execute("""
        ALTER TABLE config.bis_scrape_targets
            ADD CONSTRAINT bis_scrape_targets_preferred_technique_check
            CHECK (preferred_technique = ANY (ARRAY[
                'json_embed', 'wh_gatherer', 'html_parse',
                'html_parse_method', 'simc', 'manual'
            ]))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE config.bis_scrape_targets
            DROP CONSTRAINT bis_scrape_targets_preferred_technique_check
    """)
    op.execute("""
        ALTER TABLE config.bis_scrape_targets
            ADD CONSTRAINT bis_scrape_targets_preferred_technique_check
            CHECK (preferred_technique = ANY (ARRAY[
                'json_embed', 'wh_gatherer', 'html_parse', 'simc', 'manual'
            ]))
    """)
