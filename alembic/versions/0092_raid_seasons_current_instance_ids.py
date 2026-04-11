"""Add current_instance_ids to patt.raid_seasons for Available from Content filtering.

current_instance_ids stores the M+ dungeon pool for the season.
Raids are tracked separately in current_raid_ids.
get_available_items() combines both when filtering item sources.

Revision ID: 0092
Revises: 0091
"""

from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None

# Midnight Season 1 M+ dungeon pool:
#   New Midnight dungeons: Windrunner Spire (1299), Magisters' Terrace new (1300),
#                          Maisara Caverns (1315), Nexus-Point Xenas (1316)
#   Legacy dungeons in rotation: Algeth'ar Academy (1201), Seat of the Triumvirate (945),
#                                Skyreach (476), Pit of Saron (278)
#   Note: 1300 is the new Midnight Magisters' Terrace; 249 is the old BC dungeon.
#   Raids (1307 Voidspire, 1308 March on Quel'Danas, 1314 Dreamrift) are in current_raid_ids.
_MIDNIGHT_S1_DUNGEON_IDS = (
    278,   # Pit of Saron (Wrath legacy)
    476,   # Skyreach (WoD legacy)
    945,   # Seat of the Triumvirate (Legion legacy)
    1201,  # Algeth'ar Academy (DF legacy)
    1299,  # Windrunner Spire (Midnight new)
    1300,  # Magisters' Terrace (Midnight new — not ID 249 which is the BC original)
    1315,  # Maisara Caverns (Midnight new)
    1316,  # Nexus-Point Xenas (Midnight new)
)


def upgrade():
    op.execute("""
        ALTER TABLE patt.raid_seasons
        ADD COLUMN IF NOT EXISTS current_instance_ids INTEGER[] NOT NULL DEFAULT '{}'
    """)
    ids_literal = "{" + ",".join(str(i) for i in _MIDNIGHT_S1_DUNGEON_IDS) + "}"
    op.execute(f"""
        UPDATE patt.raid_seasons
           SET current_instance_ids = '{ids_literal}'
         WHERE is_active = TRUE
    """)


def downgrade():
    op.execute("""
        ALTER TABLE patt.raid_seasons
        DROP COLUMN IF EXISTS current_instance_ids
    """)
