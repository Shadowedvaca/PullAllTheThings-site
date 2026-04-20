"""add weapon variant labels to config.method_slot_labels

Main Hand (2h) and Main Hand (dw) both map to main_hand.
The first match wins; the duplicate is silently skipped during rebuild.
A proper weapon-build variant feature is planned for a future phase.

Revision ID: 0154
Revises: 0153
Create Date: 2026-04-20
"""
from alembic import op

revision = "0154"
down_revision = "0153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO config.method_slot_labels (page_label, slot_key) VALUES
            ('main hand (2h)', 'main_hand'),
            ('main hand (dw)', 'main_hand')
        ON CONFLICT (page_label) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM config.method_slot_labels
         WHERE page_label IN ('main hand (2h)', 'main hand (dw)')
    """)
