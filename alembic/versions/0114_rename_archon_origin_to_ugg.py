"""Rename origin='archon' to origin='ugg' in bis_list_sources.

origin='archon' was a legacy code ID from when the site was originally
called archon.gg. We scrape u.gg (u.gg/wow/...) and 'archon' will be
reserved for the actual archon.gg site in a future integration.

Revision ID: 0114
Revises: 0113
"""

from alembic import op

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE guild_identity.bis_list_sources
        SET origin = 'ugg'
        WHERE origin = 'archon'
    """)


def downgrade():
    op.execute("""
        UPDATE guild_identity.bis_list_sources
        SET origin = 'archon'
        WHERE origin = 'ugg'
    """)
