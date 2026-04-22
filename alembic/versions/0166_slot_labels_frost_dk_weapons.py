"""slot_labels: add Frost DK IV weapon slot names

Revision ID: 0166
Revises: 0165
Create Date: 2026-04-22
"""
from alembic import op

revision = "0166"
down_revision = "0165"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO config.slot_labels (page_label, slot_key)
        VALUES
            ('2h weapon',             'main_hand'),
            ('mainhand 1h weapon',    'main_hand'),
            ('offhand 1h weapon',     'off_hand')
        ON CONFLICT (page_label) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM config.slot_labels
        WHERE page_label IN ('2h weapon', 'mainhand 1h weapon', 'offhand 1h weapon')
    """)
