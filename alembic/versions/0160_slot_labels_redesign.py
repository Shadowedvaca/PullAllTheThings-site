"""redesign config.slot_labels — universal text labels + separate wowhead_invtypes

Migration 0159 stored slot labels keyed by (origin, page_label), but text
labels like "back", "cloak", "helm" are universal across sources. Wowhead
is the only source that uses numeric inventory_type codes, so those belong
in a dedicated table. Drop the origin-keyed table; replace with:

  config.slot_labels(page_label PK, slot_key)  — deduped universal text labels
  config.wowhead_invtypes(invtype_id PK, slot_key)  — Blizzard invtype → slot

Revision ID: 0160
Revises: 0159
Create Date: 2026-04-20
"""
from alembic import op

revision = "0160"
down_revision = "0159"
branch_labels = None
depends_on = None

# Deduplicated universal text labels (no conflicts across sources)
_LABELS = [
    ("back",              "back"),
    ("belt",              "waist"),
    ("boots",             "feet"),
    ("cape",              "back"),
    ("chest",             "chest"),
    ("cloak",             "back"),
    ("feet",              "feet"),
    ("gloves",            "hands"),
    ("hands",             "hands"),
    ("head",              "head"),
    ("helm",              "head"),
    ("legs",              "legs"),
    ("main hand",         "main_hand"),
    ("main hand (2h)",    "main_hand"),
    ("main hand (dw)",    "main_hand"),
    ("main-hand",         "main_hand"),
    ("main_hand",         "main_hand"),
    ("neck",              "neck"),
    ("off hand",          "off_hand"),
    ("off-hand",          "off_hand"),
    ("off_hand",          "off_hand"),
    ("one hand",          "main_hand"),
    ("one-hand",          "main_hand"),
    ("ring",              None),        # ambiguous — callers resolve by occurrence order
    ("ring 1",            "ring_1"),
    ("ring 2",            "ring_2"),
    ("ring1",             "ring_1"),
    ("ring2",             "ring_2"),
    ("shoulder",          "shoulder"),
    ("shoulders",         "shoulder"),
    ("trinket",           None),        # ambiguous — callers resolve by occurrence order
    ("trinket 1",         "trinket_1"),
    ("trinket 2",         "trinket_2"),
    ("trinket1",          "trinket_1"),
    ("trinket2",          "trinket_2"),
    ("two hand weapon",   "main_hand"),
    ("two-hand weapon",   "main_hand"),
    ("waist",             "waist"),
    ("weapon",            "main_hand"),
    ("weapon1",           "main_hand"),
    ("weapon2",           "off_hand"),
    ("wrist",             "wrist"),
    ("wrists",            "wrist"),
]

# Blizzard inventory_type codes used by Wowhead
_INVTYPES = [
    (1,  "head"),
    (2,  "neck"),
    (3,  "shoulder"),
    (5,  "chest"),       # INVTYPE_CHEST
    (6,  "waist"),
    (7,  "legs"),
    (8,  "feet"),
    (9,  "wrist"),
    (10, "hands"),
    (11, "ring"),        # both ring slots — callers resolve ring_1/ring_2 by order
    (12, "trinket"),     # both trinket slots — callers resolve trinket_1/trinket_2 by order
    (13, "main_hand"),   # INVTYPE_WEAPON (1H)
    (14, "off_hand"),    # INVTYPE_SHIELD
    (15, "main_hand"),   # INVTYPE_RANGED (bow/gun/crossbow)
    (16, "back"),        # INVTYPE_CLOAK
    (17, "main_hand"),   # INVTYPE_2HWEAPON
    (20, "chest"),       # INVTYPE_ROBE
    (21, "main_hand"),   # INVTYPE_MAINHAND
    (22, "off_hand"),    # INVTYPE_OFFHAND
    (23, "off_hand"),    # INVTYPE_HOLDABLE
]


def upgrade() -> None:
    # Drop the origin-keyed table from migration 0159
    op.execute("DROP TABLE config.slot_labels")

    # Universal text label table — no origin, deduped
    op.execute("""
        CREATE TABLE config.slot_labels (
            page_label  VARCHAR(40) PRIMARY KEY,
            slot_key    VARCHAR(20)
        )
    """)
    rows = ", ".join(
        f"('{lbl}', {'NULL' if key is None else repr(key)})"
        for lbl, key in _LABELS
    )
    op.execute(f"INSERT INTO config.slot_labels (page_label, slot_key) VALUES {rows}")

    # Wowhead-specific numeric invtype table
    op.execute("""
        CREATE TABLE config.wowhead_invtypes (
            invtype_id  INTEGER PRIMARY KEY,
            slot_key    VARCHAR(20) NOT NULL
        )
    """)
    invtype_rows = ", ".join(f"({iid}, '{key}')" for iid, key in _INVTYPES)
    op.execute(
        f"INSERT INTO config.wowhead_invtypes (invtype_id, slot_key) VALUES {invtype_rows}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE config.wowhead_invtypes")
    op.execute("DROP TABLE config.slot_labels")
    # Restore origin-keyed table from 0159
    op.execute("""
        CREATE TABLE config.slot_labels (
            origin      VARCHAR(20) NOT NULL,
            page_label  VARCHAR(40) NOT NULL,
            slot_key    VARCHAR(20),
            PRIMARY KEY (origin, page_label)
        )
    """)
