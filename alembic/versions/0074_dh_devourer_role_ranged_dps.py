"""Fix Devourer spec role: Melee DPS → Ranged DPS

Revision ID: 0074
Revises: 0073
Create Date: 2026-04-05

Migration 0073 assumed Melee DPS — Devourer is actually Ranged DPS.
"""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE guild_identity.specializations sp
           SET default_role_id = (
               SELECT id FROM guild_identity.roles WHERE name = 'Ranged DPS'
           )
          FROM guild_identity.classes c
         WHERE sp.class_id = c.id
           AND sp.name = 'Devourer'
           AND c.name = 'Demon Hunter'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE guild_identity.specializations sp
           SET default_role_id = (
               SELECT id FROM guild_identity.roles WHERE name = 'Melee DPS'
           )
          FROM guild_identity.classes c
         WHERE sp.class_id = c.id
           AND sp.name = 'Devourer'
           AND c.name = 'Demon Hunter'
    """)
