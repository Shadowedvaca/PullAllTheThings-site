"""Refresh AH tracking for the Midnight expansion.

Revision ID: 0186
Revises: 0185
"""

from alembic import op
import sqlalchemy as sa


revision = "0186"
down_revision = "0185"
branch_labels = None
depends_on = None


# Highest-quality auctionable item ID for each selected Midnight recipe output.
# Cauldrons, recycling recipes, profession-tool enchants, and profession gems are
# intentionally excluded from the broad raid/M+ consumable list.
MIDNIGHT_ITEMS = [
    # Flasks and potions
    (271884, "Concentrated Silvermoon Health Potion", "consumable"),
    (241321, "Flask of Thalassian Resistance", "consumable"),
    (241325, "Flask of the Blood Knights", "consumable"),
    (241323, "Flask of the Magisters", "consumable"),
    (241327, "Flask of the Shattered Sun", "consumable"),
    (241301, "Lightfused Mana Potion", "consumable"),
    (241295, "Potion of Devoured Dreams", "consumable"),
    (241289, "Potion of Recklessness", "consumable"),
    (241297, "Potion of Zealotry", "consumable"),
    (241305, "Silvermoon Health Potion", "consumable"),
    (241334, "Vicious Thalassian Flask of Honor", "consumable"),
    # Combat enchants (profession-tool enchants excluded)
    (244009, "Enchant Boots - Farstrider's Hunt", "enchant"),
    (243953, "Enchant Boots - Lynx's Dexterity", "enchant"),
    (243947, "Enchant Chest - Mark of Nalorakk", "enchant"),
    (244003, "Enchant Chest - Mark of the Magister", "enchant"),
    (243975, "Enchant Chest - Mark of the Rootwarden", "enchant"),
    (243977, "Enchant Chest - Mark of the Worldsoul", "enchant"),
    (243979, "Enchant Helm - Blessing of Speed", "enchant"),
    (243981, "Enchant Helm - Empowered Blessing of Speed", "enchant"),
    (243951, "Enchant Helm - Empowered Hex of Leeching", "enchant"),
    (243949, "Enchant Helm - Hex of Leeching", "enchant"),
    (244005, "Enchant Helm - Rune of Avoidance", "enchant"),
    (243955, "Enchant Ring - Amani Mastery", "enchant"),
    (243957, "Enchant Ring - Eyes of the Eagle", "enchant"),
    (243985, "Enchant Ring - Nature's Wrath", "enchant"),
    (244015, "Enchant Ring - Silvermoon's Alacrity", "enchant"),
    (244017, "Enchant Ring - Silvermoon's Tenacity", "enchant"),
    (244011, "Enchant Ring - Thalassian Haste", "enchant"),
    (244013, "Enchant Ring - Thalassian Versatility", "enchant"),
    (243959, "Enchant Ring - Zul'jin's Mastery", "enchant"),
    (243963, "Enchant Shoulders - Akil'zon's Swiftness", "enchant"),
    (243961, "Enchant Shoulders - Flight of the Eagle", "enchant"),
    (243989, "Enchant Shoulders - Nature's Grace", "enchant"),
    (244021, "Enchant Shoulders - Silvermoon's Mending", "enchant"),
    (244019, "Enchant Shoulders - Thalassian Recovery", "enchant"),
    (244029, "Enchant Weapon - Acuity of the Ren'dorei", "enchant"),
    (244031, "Enchant Weapon - Arcane Mastery", "enchant"),
    (243973, "Enchant Weapon - Berserker's Rage", "enchant"),
    (244027, "Enchant Weapon - Flames of the Sin'dorei", "enchant"),
    (243971, "Enchant Weapon - Jan'alai's Precision", "enchant"),
    (273072, "Enchant Weapon - Rite of the Hash'ey", "enchant"),
    (243969, "Enchant Weapon - Strength of Halazzi", "enchant"),
    (243999, "Enchant Weapon - Worldsoul Aegis", "enchant"),
    (243997, "Enchant Weapon - Worldsoul Cradle", "enchant"),
    (244001, "Enchant Weapon - Worldsoul Tenacity", "enchant"),
    # Runes and contracts
    (259085, "Void-Touched Augment Rune", "consumable"),
    (245798, "Contract: The Amani Tribe", "consumable"),
    (245796, "Contract: The Hara'ti", "consumable"),
    (245800, "Contract: The Silvermoon Court", "consumable"),
    (245794, "Contract: The Singularity", "consumable"),
    (277969, "Contract: Zul'jarra's Forces", "consumable"),
    (245880, "Vantus Rune: Radiant", "consumable"),
    (272195, "Vantus Rune: Tides", "consumable"),
    # Highest-quality combat gems
    (240898, "Flawless Deadly Amethyst", "gem"),
    (240904, "Flawless Deadly Garnet", "gem"),
    (240914, "Flawless Deadly Lapis", "gem"),
    (240890, "Flawless Deadly Peridot", "gem"),
    (240896, "Flawless Masterful Amethyst", "gem"),
    (240908, "Flawless Masterful Garnet", "gem"),
    (240918, "Flawless Masterful Lapis", "gem"),
    (240892, "Flawless Masterful Peridot", "gem"),
    (240900, "Flawless Quick Amethyst", "gem"),
    (240906, "Flawless Quick Garnet", "gem"),
    (240916, "Flawless Quick Lapis", "gem"),
    (240888, "Flawless Quick Peridot", "gem"),
    (240902, "Flawless Versatile Amethyst", "gem"),
    (240910, "Flawless Versatile Garnet", "gem"),
    (240912, "Flawless Versatile Lapis", "gem"),
    (240894, "Flawless Versatile Peridot", "gem"),
    (240983, "Indecipherable Eversong Diamond", "gem"),
    (240967, "Powerful Eversong Diamond", "gem"),
    (240971, "Stoic Eversong Diamond", "gem"),
    (240969, "Telluric Eversong Diamond", "gem"),
]


def upgrade():
    conn = op.get_bind()
    item_ids = [item_id for item_id, _, _ in MIDNIGHT_ITEMS]

    # Keep historical snapshots intact while removing stale items from displays
    # and hourly fetches.
    conn.execute(sa.text("UPDATE guild_identity.tracked_items SET is_active = FALSE"))
    conn.execute(
        sa.text("DELETE FROM guild_identity.tracked_items WHERE item_id = 213746")
    )

    for display_order, (item_id, item_name, category) in enumerate(MIDNIGHT_ITEMS, 1):
        conn.execute(
            sa.text("""
                INSERT INTO guild_identity.tracked_items
                    (item_id, item_name, category, display_order, is_active)
                VALUES (:item_id, :item_name, :category, :display_order, TRUE)
                ON CONFLICT (item_id) DO UPDATE SET
                    item_name = EXCLUDED.item_name,
                    category = EXCLUDED.category,
                    display_order = EXCLUDED.display_order,
                    is_active = TRUE
            """),
            {
                "item_id": item_id,
                "item_name": item_name,
                "category": category,
                "display_order": display_order,
            },
        )


def downgrade():
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE guild_identity.tracked_items
               SET is_active = FALSE
             WHERE item_id = ANY(:item_ids)
        """).bindparams(sa.bindparam("item_ids", type_=sa.ARRAY(sa.Integer))),
        {"item_ids": [item_id for item_id, _, _ in MIDNIGHT_ITEMS]},
    )
    conn.execute(
        sa.text("""
            UPDATE guild_identity.tracked_items
               SET is_active = TRUE
             WHERE item_id IN (212241, 212248, 212246, 222732,
                               222509, 222510, 222524)
        """)
    )
