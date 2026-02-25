"""fix: remove San'layn from specializations — it is a Hero Talent, not a spec

Revision ID: 0024
Revises: 0023
Create Date: 2026-02-25
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DELETE FROM guild_identity.specializations
        WHERE name = 'San''layn'
        AND class_id = (SELECT id FROM guild_identity.classes WHERE name = 'Death Knight')
    """)


def downgrade():
    pass
