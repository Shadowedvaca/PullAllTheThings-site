"""create config.method_slot_labels and seed from hardcoded map

Revision ID: 0153
Revises: 0152
Create Date: 2026-04-20
"""
from alembic import op

revision = "0153"
down_revision = "0152"
branch_labels = None
depends_on = None

_LABELS = [
    ("head",      "head"),
    ("neck",      "neck"),
    ("shoulders", "shoulder"),
    ("shoulder",  "shoulder"),
    ("back",      "back"),
    ("cloak",     "back"),
    ("chest",     "chest"),
    ("wrists",    "wrist"),
    ("wrist",     "wrist"),
    ("hands",     "hands"),
    ("gloves",    "hands"),
    ("waist",     "waist"),
    ("belt",      "waist"),
    ("legs",      "legs"),
    ("feet",      "feet"),
    ("boots",     "feet"),
    ("ring 1",    "ring_1"),
    ("ring 2",    "ring_2"),
    ("ring",      None),
    ("trinket 1", "trinket_1"),
    ("trinket 2", "trinket_2"),
    ("trinket",   None),
    ("main hand", "main_hand"),
    ("main-hand", "main_hand"),
    ("weapon",    "main_hand"),
    ("off hand",  "off_hand"),
    ("off-hand",  "off_hand"),
]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE config.method_slot_labels (
            page_label  VARCHAR(40) PRIMARY KEY,
            slot_key    VARCHAR(20)
        )
    """)
    rows = ", ".join(
        f"('{lbl}', {'NULL' if key is None else repr(key)})"
        for lbl, key in _LABELS
    )
    op.execute(f"INSERT INTO config.method_slot_labels (page_label, slot_key) VALUES {rows}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS config.method_slot_labels")
