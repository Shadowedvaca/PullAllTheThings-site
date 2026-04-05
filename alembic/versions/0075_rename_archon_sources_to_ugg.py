"""Rename bis_list_sources display names from 'Archon' to 'u.gg'

Revision ID: 0075
Revises: 0074
Create Date: 2026-04-05

The BIS sources labelled 'Archon' scrape u.gg (u.gg/wow/...), not archon.gg.
These are two different sites — u.gg is popularity/usage-based while
archon.gg is sim-based.  Update display names and labels to reflect the
actual data source.  The origin column stays 'archon' as a code identifier
for the extraction technique (json_embed / u.gg SSR parsing).

Note: Priest hero talents named 'Archon' in guild_identity.hero_talents
are real WoW hero talent names and are NOT changed by this migration.
"""

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET name = 'u.gg Raid',    short_label = 'u.gg R'
         WHERE name = 'Archon Raid'
    """)
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET name = 'u.gg M+',      short_label = 'u.gg M+'
         WHERE name = 'Archon M+'
    """)
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET name = 'u.gg Overall', short_label = 'u.gg'
         WHERE name = 'Archon Overall'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET name = 'Archon Raid',    short_label = 'Archon R'
         WHERE name = 'u.gg Raid'
    """)
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET name = 'Archon M+',      short_label = 'Archon M+'
         WHERE name = 'u.gg M+'
    """)
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET name = 'Archon Overall', short_label = 'Archon'
         WHERE name = 'u.gg Overall'
    """)
