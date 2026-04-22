"""extend bis_scrape_targets preferred_technique CHECK to include json_embed_archon

Revision ID: 0174
Revises: 0173
Create Date: 2026-04-22
"""
from alembic import op

revision = "0174"
down_revision = "0173"
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
                'html_parse_method', 'json_embed_archon', 'simc', 'manual'
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
                'json_embed', 'wh_gatherer', 'html_parse',
                'html_parse_method', 'simc', 'manual'
            ]))
    """)
