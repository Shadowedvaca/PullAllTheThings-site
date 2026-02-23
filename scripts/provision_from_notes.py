#!/usr/bin/env python3
"""
One-time script: provision guild members from guild note groupings.

- Renames Striate → Basix (the .striate Discord user IS Basix)
- Links Shodoom's alts (sho group)
- Creates new common.guild_members for players with 2+ chars sharing a note name
- Links all their common.characters entries
"""
import asyncio
import asyncpg
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ROLE_MAP = {
    "Tank":   "tank",
    "Healer": "healer",
    "Melee":  "melee_dps",
    "Ranged": "ranged_dps",
}

# New players to create from note groups (display_name, note_key)
NEW_PLAYERS = [
    ("Revenge",     "revenge"),
    ("Samah",       "samah"),
    ("Dart",        "dart"),
    ("Delta",       "delta"),
    ("Peon",        "peon"),
    ("Sirlos",      "sirlos"),
    ("Bam",         "bam"),
    ("Shamlee",     "shamlee"),
    ("Fort",        "fort"),
    ("Azz",         "azz"),
    ("Flintstoned", "flintstoned"),
    ("Helios",      "helios"),
    ("Widow",       "widow"),
]


def normalize_note(note: str) -> str:
    """Strip alt/alts suffix and normalize to lowercase."""
    if not note:
        return ""
    cleaned = note.strip().lower()
    cleaned = re.sub(r"\s+(alt|alts)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r",.*$", "", cleaned)          # strip ", anything" suffix
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


async def get_rank_id(conn, rank_name: str) -> int:
    rank_id = await conn.fetchval(
        "SELECT id FROM common.guild_ranks WHERE LOWER(name) = LOWER($1)", rank_name
    )
    if not rank_id:
        rank_id = await conn.fetchval(
            "SELECT id FROM common.guild_ranks ORDER BY level LIMIT 1"
        )
    return rank_id


async def link_or_create_char(conn, member_id: int, wc: dict, is_main: bool):
    """Link an existing common.characters row, or create it if missing."""
    existing = await conn.fetchrow(
        """SELECT id, member_id FROM common.characters
           WHERE LOWER(name) = LOWER($1) AND LOWER(realm) = LOWER($2)""",
        wc["character_name"], wc["realm_name"],
    )
    main_alt = "main" if is_main else "alt"

    if existing:
        if existing["member_id"] is None:
            await conn.execute(
                "UPDATE common.characters SET member_id = $1, main_alt = $2 WHERE id = $3",
                member_id, main_alt, existing["id"],
            )
        elif existing["member_id"] == member_id and is_main:
            # Already ours — just ensure main_alt is correct
            await conn.execute(
                "UPDATE common.characters SET main_alt = $1 WHERE id = $2",
                main_alt, existing["id"],
            )
        # else: owned by a different member — leave it alone
        return existing["id"], False   # (id, created)
    else:
        role = ROLE_MAP.get(wc["role_category"] or "", "melee_dps")
        char_id = await conn.fetchval(
            """INSERT INTO common.characters
               (member_id, name, realm, class, spec, role, main_alt)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (name, realm) DO UPDATE
                 SET member_id = COALESCE(common.characters.member_id, EXCLUDED.member_id)
               RETURNING id""",
            member_id,
            wc["character_name"], wc["realm_name"],
            wc["character_class"] or "", wc["active_spec"] or "",
            role, main_alt,
        )
        return char_id, True   # (id, created)


async def process_group(conn, display_name: str, note_key: str, all_chars_by_key: dict):
    chars = all_chars_by_key.get(note_key, [])
    if not chars:
        print(f"  [{display_name}] no characters found for note '{note_key}' — skipping")
        return

    # Check if player already exists by display_name or character name match
    member_id = await conn.fetchval(
        "SELECT id FROM common.guild_members WHERE LOWER(display_name) = LOWER($1)",
        display_name,
    )
    if not member_id:
        member_id = await conn.fetchval(
            """SELECT gm.id FROM common.guild_members gm
               JOIN common.characters c ON c.member_id = gm.id
               WHERE LOWER(c.name) = LOWER($1)""",
            note_key,
        )

    if member_id:
        print(f"  [{display_name}] already exists as member_id={member_id}, linking chars")
    else:
        # Use highest-ranked character's rank (lowest guild_rank number = highest rank)
        best = min(chars, key=lambda c: (c["guild_rank"] if c["guild_rank"] is not None else 99))
        rank_id = await get_rank_id(conn, best["guild_rank_name"])
        member_id = await conn.fetchval(
            """INSERT INTO common.guild_members
               (discord_username, display_name, rank_id, rank_source)
               VALUES ($1, $2, $3, 'note_sync')
               RETURNING id""",
            note_key, display_name, rank_id,
        )
        print(f"  [{display_name}] created member_id={member_id} "
              f"(rank={best['guild_rank_name']}, {len(chars)} chars)")

    # Determine which character is the "main"
    name_match = next(
        (c for c in chars if c["character_name"].lower() == note_key), None
    )
    # Fallback: first is_main=True, then highest-ranked
    if not name_match:
        name_match = next((c for c in chars if c["is_main"]), None)
    if not name_match:
        name_match = min(chars, key=lambda c: (c["guild_rank"] if c["guild_rank"] is not None else 99))

    created = linked = skipped = 0
    for wc in chars:
        is_main = (wc["character_name"] == name_match["character_name"]
                   and wc["realm_name"] == name_match["realm_name"])
        _, was_created = await link_or_create_char(conn, member_id, wc, is_main)
        if was_created:
            created += 1
        else:
            linked += 1

    print(f"    → {created} chars created, {linked} chars linked/updated")


async def main():
    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        print("DATABASE_URL not set"); sys.exit(1)

    pool = await asyncpg.create_pool(db_url)
    async with pool.acquire() as conn:

        # ── Load all guild_identity chars with notes ─────────────────────
        rows = await conn.fetch(
            """SELECT character_name, realm_name, character_class, active_spec,
                      role_category, guild_rank_name, guild_rank, is_main,
                      guild_note, officer_note
               FROM guild_identity.wow_characters
               WHERE removed_at IS NULL
               ORDER BY guild_rank ASC NULLS LAST, is_main DESC NULLS LAST"""
        )

        # Group by normalized note
        by_key: dict[str, list] = {}
        for r in rows:
            key = normalize_note(r["guild_note"] or "")
            if key and key not in ("unknown main", "unknown main.", ""):
                by_key.setdefault(key, []).append(dict(r))

        print(f"Loaded {len(rows)} guild_identity chars → {len(by_key)} note groups\n")

        # ── STEP 1: Rename Striate → Basix ───────────────────────────────
        print("=" * 60)
        print("STEP 1: Rename Striate → Basix")
        print("=" * 60)
        await conn.execute(
            "UPDATE common.guild_members SET display_name = 'Basix' WHERE id = 44"
        )
        # Striate character becomes an alt
        await conn.execute(
            "UPDATE common.characters SET main_alt = 'alt' WHERE member_id = 44 AND LOWER(name) = 'striate'"
        )
        # Process full Basix group
        await process_group(conn, "Basix", "basix", by_key)

        # ── STEP 2: Link Shodoom's alts ───────────────────────────────────
        print("\n" + "=" * 60)
        print("STEP 2: Shodoom's alts")
        print("=" * 60)
        shodoom_id = await conn.fetchval(
            "SELECT id FROM common.guild_members WHERE LOWER(display_name) = 'shodoom'"
        )
        print(f"  Shodoom member_id = {shodoom_id}")

        sho_chars = by_key.get("sho", [])
        # Also grab "sho again" and "shodooms shammy" directly
        extra = await conn.fetch(
            """SELECT character_name, realm_name, character_class, active_spec,
                      role_category, guild_rank_name, guild_rank, is_main, guild_note
               FROM guild_identity.wow_characters
               WHERE removed_at IS NULL
                 AND LOWER(TRIM(guild_note)) IN ('sho again', 'shodooms shammy')"""
        )
        all_sho = sho_chars + [dict(r) for r in extra]
        linked = created = 0
        for wc in all_sho:
            is_main = wc["character_name"].lower() == "shodoom"
            _, was_created = await link_or_create_char(conn, shodoom_id, wc, is_main)
            if was_created:
                created += 1
            else:
                linked += 1
        print(f"  → {created} created, {linked} linked for Shodoom ({len(all_sho)} total chars)")

        # ── STEP 3: Create new players ────────────────────────────────────
        print("\n" + "=" * 60)
        print("STEP 3: New players from note groups")
        print("=" * 60)
        for display_name, note_key in NEW_PLAYERS:
            await process_group(conn, display_name, note_key, by_key)

    await pool.close()
    print("\n✓ All done.")


if __name__ == "__main__":
    asyncio.run(main())
