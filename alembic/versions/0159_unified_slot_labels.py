"""consolidate slot label maps into config.slot_labels

Replaces config.method_slot_labels with config.slot_labels, which covers
all BIS source origins (method, ugg, wowhead, icy_veins) in one table.

Revision ID: 0159
Revises: 0158
Create Date: 2026-04-20
"""
from alembic import op

revision = "0159"
down_revision = "0158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE config.slot_labels (
            origin      VARCHAR(20) NOT NULL,
            page_label  VARCHAR(40) NOT NULL,
            slot_key    VARCHAR(20),
            PRIMARY KEY (origin, page_label)
        )
    """)

    # Migrate existing method rows (accumulated across migrations 0153–0158)
    op.execute("""
        INSERT INTO config.slot_labels (origin, page_label, slot_key)
        SELECT 'method', page_label, slot_key
          FROM config.method_slot_labels
    """)

    # u.gg rows — from _UGG_SLOT_MAP in bis_sync.py
    op.execute("""
        INSERT INTO config.slot_labels (origin, page_label, slot_key) VALUES
            ('ugg', 'head',      'head'),
            ('ugg', 'neck',      'neck'),
            ('ugg', 'shoulder',  'shoulder'),
            ('ugg', 'back',      'back'),
            ('ugg', 'cape',      'back'),
            ('ugg', 'chest',     'chest'),
            ('ugg', 'wrist',     'wrist'),
            ('ugg', 'gloves',    'hands'),
            ('ugg', 'hands',     'hands'),
            ('ugg', 'belt',      'waist'),
            ('ugg', 'waist',     'waist'),
            ('ugg', 'legs',      'legs'),
            ('ugg', 'feet',      'feet'),
            ('ugg', 'ring1',     'ring_1'),
            ('ugg', 'ring2',     'ring_2'),
            ('ugg', 'trinket1',  'trinket_1'),
            ('ugg', 'trinket2',  'trinket_2'),
            ('ugg', 'weapon1',   'main_hand'),
            ('ugg', 'weapon2',   'off_hand'),
            ('ugg', 'main_hand', 'main_hand'),
            ('ugg', 'off_hand',  'off_hand')
    """)

    # Wowhead rows — integer inventory_type codes stored as text strings
    # from _WOWHEAD_SLOT_MAP in bis_sync.py
    op.execute("""
        INSERT INTO config.slot_labels (origin, page_label, slot_key) VALUES
            ('wowhead', '1',  'head'),
            ('wowhead', '2',  'neck'),
            ('wowhead', '3',  'shoulder'),
            ('wowhead', '5',  'chest'),
            ('wowhead', '6',  'waist'),
            ('wowhead', '7',  'legs'),
            ('wowhead', '8',  'feet'),
            ('wowhead', '9',  'wrist'),
            ('wowhead', '10', 'hands'),
            ('wowhead', '11', 'ring'),
            ('wowhead', '12', 'trinket'),
            ('wowhead', '13', 'main_hand'),
            ('wowhead', '14', 'off_hand'),
            ('wowhead', '15', 'main_hand'),
            ('wowhead', '16', 'back'),
            ('wowhead', '17', 'main_hand'),
            ('wowhead', '20', 'chest'),
            ('wowhead', '21', 'main_hand'),
            ('wowhead', '22', 'off_hand'),
            ('wowhead', '23', 'off_hand')
    """)

    # Icy Veins rows — title-cased labels stored lowercase
    op.execute("""
        INSERT INTO config.slot_labels (origin, page_label, slot_key) VALUES
            ('icy_veins', 'helm',      'head'),
            ('icy_veins', 'head',      'head'),
            ('icy_veins', 'neck',      'neck'),
            ('icy_veins', 'shoulders', 'shoulder'),
            ('icy_veins', 'shoulder',  'shoulder'),
            ('icy_veins', 'back',      'back'),
            ('icy_veins', 'cloak',     'back'),
            ('icy_veins', 'chest',     'chest'),
            ('icy_veins', 'wrists',    'wrist'),
            ('icy_veins', 'wrist',     'wrist'),
            ('icy_veins', 'hands',     'hands'),
            ('icy_veins', 'gloves',    'hands'),
            ('icy_veins', 'waist',     'waist'),
            ('icy_veins', 'belt',      'waist'),
            ('icy_veins', 'legs',      'legs'),
            ('icy_veins', 'feet',      'feet'),
            ('icy_veins', 'boots',     'feet'),
            ('icy_veins', 'ring 1',    'ring_1'),
            ('icy_veins', 'ring 2',    'ring_2'),
            ('icy_veins', 'ring',      NULL),
            ('icy_veins', 'trinket 1', 'trinket_1'),
            ('icy_veins', 'trinket 2', 'trinket_2'),
            ('icy_veins', 'trinket',   NULL),
            ('icy_veins', 'main hand', 'main_hand'),
            ('icy_veins', 'off hand',  'off_hand'),
            ('icy_veins', 'weapon',    'main_hand')
    """)

    op.execute("DROP TABLE config.method_slot_labels")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE config.method_slot_labels (
            page_label  VARCHAR(40) PRIMARY KEY,
            slot_key    VARCHAR(20)
        )
    """)
    op.execute("""
        INSERT INTO config.method_slot_labels (page_label, slot_key)
        SELECT page_label, slot_key
          FROM config.slot_labels
         WHERE origin = 'method'
    """)
    op.execute("DROP TABLE config.slot_labels")
