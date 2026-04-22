"""config.slot_labels: add Bracers/bracers → wrist mapping.

Icy Veins uses "Bracers" (title-case) as the slot label for the wrist slot.
The resolver is case-sensitive, so this was silently dropping wrist BIS
entries for any spec that uses the Bracers label on IV pages.

Revision ID: 0165
Revises: 0164
Create Date: 2026-04-21
"""

from alembic import op

revision = "0165"
down_revision = "0164"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO config.slot_labels (page_label, slot_key)
        VALUES ('Bracers', 'wrist'), ('bracers', 'wrist')
        ON CONFLICT (page_label) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM config.slot_labels WHERE page_label IN ('Bracers', 'bracers')
    """)
