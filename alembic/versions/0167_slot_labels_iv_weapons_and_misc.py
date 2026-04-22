"""slot_labels: add missing IV slot names for weapons, shield, helmet, and aliases

Revision ID: 0167
Revises: 0166
Create Date: 2026-04-22
"""
from alembic import op

revision = "0167"
down_revision = "0166"
branch_labels = None
depends_on = None

_NEW_LABELS = [
    # Weapon slot variants used by various specs on IV
    ("1h weapon",                        "main_hand"),
    ("mainhand",                         "main_hand"),
    ("mainhand weapon",                  "main_hand"),
    ("offhand",                          "off_hand"),
    ("offhand weapon",                   "off_hand"),
    ("one-handed weapon",                "main_hand"),
    ("shield",                           "off_hand"),
    ("weapon (2h)",                      "main_hand"),
    ("weapon (staff)",                   "main_hand"),
    ("weapon (two-hand)",                "main_hand"),
    ("weapon (main-hand/off-hand)",      "main_hand"),
    ("weapon main-hand",                 "main_hand"),
    ("weapon off-hand",                  "off_hand"),
    ("two-handed weapon (alternative)",  "main_hand"),
    # Head slot alias
    ("helmet",                           "head"),
    # Hunter/Survival parsing artifacts — tier-name prefix gets merged with
    # slot name during HTML text extraction (e.g. "Pack Leader" + "Main Hand")
    ("pack leadermain hand",             "main_hand"),
    ("pack leaderoff-hand",              "off_hand"),
    ("sentinelweapon",                   "main_hand"),
]


def upgrade() -> None:
    values = ", ".join(f"('{label}', '{slot}')" for label, slot in _NEW_LABELS)
    op.execute(f"""
        INSERT INTO config.slot_labels (page_label, slot_key)
        VALUES {values}
        ON CONFLICT (page_label) DO NOTHING
    """)


def downgrade() -> None:
    labels = ", ".join(f"'{label}'" for label, _ in _NEW_LABELS)
    op.execute(f"DELETE FROM config.slot_labels WHERE page_label IN ({labels})")
