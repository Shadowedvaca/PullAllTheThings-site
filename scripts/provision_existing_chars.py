#!/usr/bin/env python3
"""
Link guild_identity characters to existing common.guild_members.

For every player who already exists in common.guild_members and has characters
grouped under their name in guild notes, this script ensures all those
guild_identity chars are linked in common.characters.

Also links Hit's alts that have descriptive notes (hit dh, hit mito, etc.).
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

# Existing players: (display_name, note_key)
# note_key is the normalized guild note prefix that identifies their chars.
EXISTING_PLAYERS = [
    ("Trog",        "trog"),
    ("Kronas",      "kronas"),
    ("Elrek",       "elrek"),
    ("Rocket",      "rocket"),
    ("Porax",       "porax"),
    ("Hit",         "hit"),
    ("Mito",        "mito"),
    ("Skate",       "skate"),
    ("Wyland",      "wyland"),
    ("Anob",        "anob"),
    ("Drizi",       "drizi"),
    ("Tazz",        "tazz"),
    ("Dragrik",     "dragrik"),
    ("Bearwithme",  "bearwithme"),
    ("Celena",      "celena"),
    ("Meg",         "meg"),
    ("Meowstorm",   "meowstorm"),
]

# Hit's alts have descriptive notes like "hit dh", "hit mito", etc.
# These should all be linked to the Hit player.
HIT_EXTRA_NOTE_PREFIXES = ["hit dh", "hit mito", "hit monk", "hit rogue"]


def normalize_note(note: str) -> str:
    """Strip alt/alts suffix and normalize to lowercase."""
    if not note:
        return ""
    cleaned = note.strip().lower()
    cleaned = re.sub(r"\s+(alt|alts)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r",.*$", "", cleaned)          # strip ", anything" suffix
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


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
            return existing["id"], False, "linked"
        elif existing["member_id"] == member_id:
            if is_main:
                await conn.execute(
                    "UPDATE common.characters SET main_alt = $1 WHERE id = $2",
                    main_alt, existing["id"],
                )
            return existing["id"], False, "already_ours"
        else:
            return existing["id"], False, "owned_by_other"
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
        return char_id, True, "created"


async def link_chars_for_player(conn, display_name: str, note_key: str, all_chars_by_key: dict):
    """Find an existing guild_member and link their guild_identity chars."""
    member_id = await conn.fetchval(
        "SELECT id FROM common.guild_members WHERE LOWER(display_name) = LOWER($1)",
        display_name,
    )
    if not member_id:
        # Try by note_key as discord_username (fallback)
        member_id = await conn.fetchval(
            "SELECT id FROM common.guild_members WHERE LOWER(discord_username) = LOWER($1)",
            note_key,
        )
    if not member_id:
        print(f"  [{display_name}] NOT FOUND in common.guild_members — skipping")
        return

    chars = all_chars_by_key.get(note_key, [])
    if not chars:
        print(f"  [{display_name}] no guild_identity chars for note '{note_key}'")
        return

    # Determine main: name match first, then is_main=True, then best rank
    name_match = next((c for c in chars if c["character_name"].lower() == note_key), None)
    if not name_match:
        name_match = next((c for c in chars if c["is_main"]), None)
    if not name_match:
        name_match = min(chars, key=lambda c: (c["guild_rank"] if c["guild_rank"] is not None else 99))

    counts = {"created": 0, "linked": 0, "already_ours": 0, "owned_by_other": 0}
    for wc in chars:
        is_main = (wc["character_name"] == name_match["character_name"]
                   and wc["realm_name"] == name_match["realm_name"])
        _, was_created, status = await link_or_create_char(conn, member_id, wc, is_main)
        counts[status] += 1

    print(f"  [{display_name}] id={member_id}: "
          f"{counts['created']} created, {counts['linked']} linked, "
          f"{counts['already_ours']} already ours, {counts['owned_by_other']} owned by other")


async def link_hit_extras(conn, all_chars_by_key: dict):
    """Link Hit's descriptive-note alts (hit dh, hit mito, etc.) to Hit."""
    hit_id = await conn.fetchval(
        "SELECT id FROM common.guild_members WHERE LOWER(display_name) = 'hit'"
    )
    if not hit_id:
        print("  [Hit] NOT FOUND in common.guild_members")
        return

    extra_chars = []
    for key, chars in all_chars_by_key.items():
        for prefix in HIT_EXTRA_NOTE_PREFIXES:
            if key.startswith(prefix):
                extra_chars.extend(chars)
                print(f"  [Hit extras] note='{key}' → {[c['character_name'] for c in chars]}")
                break

    if not extra_chars:
        print("  [Hit extras] no descriptive-note chars found")
        return

    counts = {"created": 0, "linked": 0, "already_ours": 0, "owned_by_other": 0}
    for wc in extra_chars:
        _, was_created, status = await link_or_create_char(conn, hit_id, wc, False)  # all alts
        counts[status] += 1

    print(f"  [Hit extras] id={hit_id}: "
          f"{counts['created']} created, {counts['linked']} linked, "
          f"{counts['already_ours']} already ours, {counts['owned_by_other']} owned by other")


async def main():
    db_url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        print("DATABASE_URL not set"); sys.exit(1)

    pool = await asyncpg.create_pool(db_url)
    async with pool.acquire() as conn:

        # Load all guild_identity chars with notes
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

        # ── Link chars for existing players ──────────────────────────────
        print("=" * 60)
        print("Linking chars for existing players")
        print("=" * 60)
        for display_name, note_key in EXISTING_PLAYERS:
            await link_chars_for_player(conn, display_name, note_key, by_key)

        # ── Link Hit's descriptive-note alts ─────────────────────────────
        print("\n" + "=" * 60)
        print("Hit's descriptive-note alts")
        print("=" * 60)
        await link_hit_extras(conn, by_key)

    await pool.close()
    print("\n✓ All done.")


if __name__ == "__main__":
    asyncio.run(main())
