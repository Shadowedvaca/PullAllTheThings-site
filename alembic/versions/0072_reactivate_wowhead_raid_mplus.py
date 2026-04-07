"""Re-activate Wowhead Raid and Wowhead M+ sources

Revision ID: 0072
Revises: 0071
Create Date: 2026-04-05

Migration 0071 incorrectly deactivated Wowhead Raid + Wowhead M+ based on an
observation that Wowhead currently shows one BIS page per spec.  Those sources
should stay active — Wowhead may add raid/M+ sections in the future and the
source definitions cost nothing to keep.
"""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET is_active = TRUE
         WHERE name IN ('Wowhead Raid', 'Wowhead M+')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE guild_identity.bis_list_sources
           SET is_active = FALSE
         WHERE name IN ('Wowhead Raid', 'Wowhead M+')
    """)
