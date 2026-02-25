"""feat: add San'layn DK and Devourer DH specializations

San'layn was missing from the 0007 seed (was in SPEC_CODES but not DB).
Devourer is a new Demon Hunter ranged DPS spec added in The War Within.

Revision ID: 0023
Revises: 0022
Create Date: 2026-02-25
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO guild_identity.specializations (class_id, name, default_role_id, wowhead_slug)
        SELECT c.id, s.name, r.id, s.slug FROM (VALUES
            ('Death Knight', 'San''layn', 'Melee DPS', 'san-layn-death-knight'),
            ('Demon Hunter', 'Devourer', 'Ranged DPS', 'devourer-demon-hunter')
        ) AS s(class_name, name, role_name, slug)
        JOIN guild_identity.classes c ON c.name = s.class_name
        JOIN guild_identity.roles r ON r.name = s.role_name
        ON CONFLICT (class_id, name) DO NOTHING
    """)


def downgrade():
    op.execute("""
        DELETE FROM guild_identity.specializations
        WHERE name IN ('San''layn', 'Devourer')
        AND class_id IN (
            SELECT id FROM guild_identity.classes WHERE name IN ('Death Knight', 'Demon Hunter')
        )
    """)
