"""feat: ref.gear_plan_slots — slot metadata table replaces hardcoded Python constants

Creates ref.gear_plan_slots to store the 16 canonical WoW gear plan slots with
their display names, enrichment slot_type mappings, pairing relationships, and
boolean flags for slot behaviour (armor filter, weapon filter, tier/catalyst).

This replaces the following hardcoded constants in gear_plan_service.py:
  WOW_SLOTS, SLOT_DISPLAY, _SLOT_TYPE_QUERY_MAP, _PAIRED_SLOT_MAP,
  _ARMOR_FILTER_SLOTS, _WEAPON_SLOTS, _TIER_CATALYST_SLOTS, _MAIN_TIER_SLOTS

Revision ID: 0136
Revises: 0135
"""

revision = "0136"
down_revision = "0135"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE ref.gear_plan_slots (
            plan_slot              VARCHAR(20) NOT NULL PRIMARY KEY,
            display_name           VARCHAR(30) NOT NULL,
            slot_order             SMALLINT    NOT NULL,
            enrichment_slot_type   VARCHAR(20) NOT NULL,
            paired_slot            VARCHAR(20),
            is_armor_slot          BOOLEAN     NOT NULL DEFAULT FALSE,
            is_weapon_slot         BOOLEAN     NOT NULL DEFAULT FALSE,
            is_tier_catalyst_slot  BOOLEAN     NOT NULL DEFAULT FALSE,
            is_main_tier_slot      BOOLEAN     NOT NULL DEFAULT FALSE
        )
    """)

    op.execute("""
        INSERT INTO ref.gear_plan_slots
            (plan_slot, display_name, slot_order, enrichment_slot_type,
             paired_slot, is_armor_slot, is_weapon_slot,
             is_tier_catalyst_slot, is_main_tier_slot)
        VALUES
            ('head',      'Head',      1,  'head',     NULL,        TRUE,  FALSE, TRUE,  TRUE),
            ('neck',      'Neck',      2,  'neck',     NULL,        FALSE, FALSE, FALSE, FALSE),
            ('shoulder',  'Shoulder',  3,  'shoulder', NULL,        TRUE,  FALSE, TRUE,  TRUE),
            ('back',      'Back',      4,  'back',     NULL,        FALSE, FALSE, TRUE,  FALSE),
            ('chest',     'Chest',     5,  'chest',    NULL,        TRUE,  FALSE, TRUE,  TRUE),
            ('wrist',     'Wrist',     6,  'wrist',    NULL,        TRUE,  FALSE, TRUE,  FALSE),
            ('hands',     'Hands',     7,  'hands',    NULL,        TRUE,  FALSE, TRUE,  TRUE),
            ('waist',     'Waist',     8,  'waist',    NULL,        TRUE,  FALSE, TRUE,  FALSE),
            ('legs',      'Legs',      9,  'legs',     NULL,        TRUE,  FALSE, TRUE,  TRUE),
            ('feet',      'Feet',      10, 'feet',     NULL,        TRUE,  FALSE, TRUE,  FALSE),
            ('ring_1',    'Ring 1',    11, 'finger',   'ring_2',    FALSE, FALSE, FALSE, FALSE),
            ('ring_2',    'Ring 2',    12, 'finger',   'ring_1',    FALSE, FALSE, FALSE, FALSE),
            ('trinket_1', 'Trinket 1', 13, 'trinket',  'trinket_2', FALSE, FALSE, FALSE, FALSE),
            ('trinket_2', 'Trinket 2', 14, 'trinket',  'trinket_1', FALSE, FALSE, FALSE, FALSE),
            ('main_hand', 'Main Hand', 15, 'one_hand', NULL,        FALSE, TRUE,  FALSE, FALSE),
            ('off_hand',  'Off Hand',  16, 'off_hand', NULL,        FALSE, TRUE,  FALSE, FALSE)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS ref.gear_plan_slots")
