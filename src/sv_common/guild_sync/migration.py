"""
One-time migration from Google Sheets CSV exports to PostgreSQL.

Usage:
  python -m sv_common.guild_sync.migration \
    --characters characters.csv \
    --discord-ids discord_ids.csv

CSV formats:
  characters.csv: Discord,Character,Class,Spec,Role,MainAlt
  discord_ids.csv: Discord,DiscordID
"""

import argparse
import asyncio
import csv
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# Role category mapping from spec
ROLE_MAP = {
    # Tanks
    "Protection": "Tank",   # Warrior or Paladin
    "Guardian": "Tank",
    "Blood": "Tank",
    "Vengeance": "Tank",
    "Brewmaster": "Tank",
    # Healers
    "Restoration": "Healer",  # Druid or Shaman
    "Holy": "Healer",         # Priest or Paladin
    "Discipline": "Healer",
    "Mistweaver": "Healer",
    "Preservation": "Healer",
    # Melee DPS
    "Arms": "Melee", "Fury": "Melee",
    "Retribution": "Melee",
    "Enhancement": "Melee",
    "Feral": "Melee",
    "Windwalker": "Melee",
    "Havoc": "Melee",
    "Assassination": "Melee", "Outlaw": "Melee", "Subtlety": "Melee",
    "Unholy": "Melee", "Frost": "Melee",  # DK Frost — context-dependent
    "Survival": "Melee",
    # Ranged DPS
    "Balance": "Ranged",
    "Elemental": "Ranged",
    "Shadow": "Ranged",
    "Arcane": "Ranged", "Fire": "Ranged",  # Mage Frost handled below
    "Affliction": "Ranged", "Demonology": "Ranged", "Destruction": "Ranged",
    "Beast Mastery": "Ranged", "Marksmanship": "Ranged",
    "Devastation": "Ranged",
    "Augmentation": "Ranged",
}


def get_role_category(wow_class: str, spec: str, explicit_role: str = "") -> str:
    """Determine role category from class + spec, falling back to explicit role."""
    if explicit_role in ("Tank", "Healer", "Melee", "Ranged"):
        return explicit_role

    # Handle ambiguous specs
    spec_lower = spec.lower().strip()
    class_lower = wow_class.lower().strip()

    if spec_lower == "frost":
        return "Melee" if class_lower == "death knight" else "Ranged"  # Mage
    if spec_lower == "holy":
        return "Healer"  # Both Priest and Paladin Holy are healers
    if spec_lower == "protection":
        return "Tank"  # Both Warrior and Paladin Prot are tanks
    if spec_lower == "restoration":
        return "Healer"  # Both Druid and Shaman Resto are healers

    return ROLE_MAP.get(spec, "Ranged")  # Default to Ranged if unknown


async def migrate_from_csv(
    db_pool: asyncpg.Pool,
    characters_csv: str,
    discord_ids_csv: str,
) -> dict:
    """Import existing Google Sheet data into the identity system."""

    # Load Discord ID mappings
    discord_map = {}  # lowercase discord name → discord_id
    with open(discord_ids_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Discord", "").strip().lower()
            did = row.get("DiscordID", "").strip()
            if name and did:
                discord_map[name] = did

    # Group characters by discord name to create persons
    persons = {}  # lowercase discord name → list of character rows
    with open(characters_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            discord_name = row.get("Discord", "").strip()
            if not discord_name:
                continue
            key = discord_name.lower()
            if key not in persons:
                persons[key] = {"discord_name": discord_name, "characters": []}
            persons[key]["characters"].append(row)

    stats = {"persons": 0, "discord_links": 0, "characters": 0}

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for discord_key, data in persons.items():
                discord_name = data["discord_name"]
                chars = data["characters"]

                # Create person
                person_id = await conn.fetchval(
                    """INSERT INTO guild_identity.persons (display_name)
                       VALUES ($1) RETURNING id""",
                    discord_name,
                )
                stats["persons"] += 1

                # Create discord member if we have an ID
                discord_id = discord_map.get(discord_key)
                if discord_id:
                    dm_id = await conn.fetchval(
                        """INSERT INTO guild_identity.discord_members
                           (person_id, discord_id, username)
                           VALUES ($1, $2, $3) RETURNING id""",
                        person_id, discord_id, discord_name,
                    )
                    # Create identity link for discord
                    await conn.execute(
                        """INSERT INTO guild_identity.identity_links
                           (person_id, discord_member_id, link_source, confidence, is_confirmed)
                           VALUES ($1, $2, 'migrated_sheet', 'high', TRUE)""",
                        person_id, dm_id,
                    )
                    stats["discord_links"] += 1

                # Create characters
                for char_row in chars:
                    char_name = char_row.get("Character", "").strip()
                    if not char_name:
                        continue

                    wow_class = char_row.get("Class", "").strip()
                    spec = char_row.get("Spec", "").strip()
                    role = char_row.get("Role", "").strip()
                    main_alt = char_row.get("MainAlt", char_row.get("Main/Alt", "")).strip()

                    role_cat = get_role_category(wow_class, spec, role)
                    is_main = main_alt.lower() == "main"

                    wc_id = await conn.fetchval(
                        """INSERT INTO guild_identity.wow_characters
                           (person_id, character_name, realm_slug, character_class,
                            active_spec, role_category, is_main)
                           VALUES ($1, $2, 'unknown', $3, $4, $5, $6)
                           ON CONFLICT (character_name, realm_slug) DO UPDATE
                           SET person_id = $1, character_class = $3, active_spec = $4,
                               role_category = $5, is_main = $6
                           RETURNING id""",
                        person_id, char_name, wow_class, spec, role_cat, is_main,
                    )

                    # Create identity link for character
                    await conn.execute(
                        """INSERT INTO guild_identity.identity_links
                           (person_id, wow_character_id, link_source, confidence, is_confirmed)
                           VALUES ($1, $2, 'migrated_sheet', 'high', TRUE)
                           ON CONFLICT (wow_character_id) DO NOTHING""",
                        person_id, wc_id,
                    )
                    stats["characters"] += 1

    logger.info(
        "Migration complete: %d persons, %d discord links, %d characters",
        stats["persons"], stats["discord_links"], stats["characters"],
    )
    return stats


async def _main():
    parser = argparse.ArgumentParser(description="Migrate Google Sheets data to PostgreSQL")
    parser.add_argument("--characters", required=True, help="Path to characters.csv")
    parser.add_argument("--discord-ids", required=True, help="Path to discord_ids.csv")
    parser.add_argument("--database-url", required=True, help="asyncpg DSN (postgresql://...)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    pool = await asyncpg.create_pool(args.database_url)
    try:
        stats = await migrate_from_csv(pool, args.characters, args.discord_ids)
        print(f"Done: {stats}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
