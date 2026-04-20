"""add one-hand label to config.method_slot_labels

Preservation Evoker and other 1H-only specs render "One-Hand" as the slot
label on Method.gg gearing pages.  Map it to main_hand so _resolve_weapon_slot
can classify it as main_hand_1h via enrichment.items.slot_type.

Revision ID: 0157
Revises: 0156
Create Date: 2026-04-20
"""
from alembic import op

revision = "0157"
down_revision = "0156"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO config.method_slot_labels (page_label, slot_key) VALUES
            ('one-hand', 'main_hand'),
            ('one hand', 'main_hand')
        ON CONFLICT (page_label) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM config.method_slot_labels
         WHERE page_label IN ('one-hand', 'one hand')
    """)
