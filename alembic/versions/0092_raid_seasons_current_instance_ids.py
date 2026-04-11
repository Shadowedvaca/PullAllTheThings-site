"""Add current_instance_ids to patt.raid_seasons for Available from Content filtering.

Revision ID: 0092
Revises: 0091
"""

from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None

# Midnight Season 1:
#   Legacy M+ in rotation: Dire Maul Warpwood (1276), Dire Maul Gordok (1277),
#                          Stratholme Service Entrance (1292), Operation Floodgate (1298)
#   New Midnight content:  Windrunner Spire (1299), Magisters' Terrace (1300),
#                          Eco-Dome Al'dani (1303), Murder Row (1304),
#                          The Voidspire (1307), March on Quel'Danas (1308),
#                          The Blinding Vale (1309), Den of Nalorakk (1311),
#                          Midnight world boss (1312), Voidscar Arena (1313),
#                          The Dreamrift (1314), Maisara Caverns (1315),
#                          Nexus-Point Xenas (1316)
_MIDNIGHT_S1_INSTANCE_IDS = (
    1276, 1277, 1292, 1298,
    1299, 1300, 1303, 1304,
    1307, 1308, 1309, 1311,
    1312, 1313, 1314, 1315, 1316,
)


def upgrade():
    op.execute("""
        ALTER TABLE patt.raid_seasons
        ADD COLUMN IF NOT EXISTS current_instance_ids INTEGER[] NOT NULL DEFAULT '{}'
    """)
    ids_literal = "{" + ",".join(str(i) for i in _MIDNIGHT_S1_INSTANCE_IDS) + "}"
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
