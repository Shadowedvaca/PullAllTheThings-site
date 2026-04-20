"""add two-hand weapon label to config.method_slot_labels

Method.gg renders "Two-Hand Weapon" as the slot label for some specs
(e.g. Preservation Evoker alternate weapon suggestions).  Map it to
main_hand so _resolve_weapon_slot classifies it as main_hand_2h via
enrichment.items.slot_type.

Revision ID: 0158
Revises: 0157
Create Date: 2026-04-20
"""
from alembic import op

revision = "0158"
down_revision = "0157"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO config.method_slot_labels (page_label, slot_key) VALUES
            ('two-hand weapon', 'main_hand'),
            ('two hand weapon', 'main_hand')
        ON CONFLICT (page_label) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM config.method_slot_labels
         WHERE page_label IN ('two-hand weapon', 'two hand weapon')
    """)
