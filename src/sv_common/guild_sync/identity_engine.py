"""
Identity matching engine.

Links WoW characters and Discord accounts to unified "player" entities.

Strategy (revised):
1. Group unlinked characters by guild_note (first meaningful word).
   Characters with the same note belong to the same player.
   e.g. note="Sho" on Shodoom, Adrenalgland, Dontfoxmybox → one group.

2. For each group, try to find the matching Discord user using the note
   as the search key. Multiple strategies in priority order:
     a. Exact match on Discord username
     b. Exact match on Discord display_name
     c. Key exactly matches a word in display_name (split on / - space)
     d. Key is a substring of Discord username (min 3 chars)
     e. Key is a substring of Discord display_name (min 3 chars)

3. Create one Player per group:
   - With Discord link if a match was found
   - Without Discord link (stub) if no match — can be linked manually

4. Characters with no guild note fall back to character-name matching
   against Discord usernames/display_names.

Rules:
- A character can only belong to one player
- A Discord account can only belong to one player
- Multiple characters CAN belong to the same player (alts)
"""

import hashlib
import logging
import re
from collections import defaultdict
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize a name for comparison — lowercase, strip accents."""
    if not name:
        return ""
    normalized = name.lower().strip()
    accent_map = str.maketrans(
        "àáâãäåèéêëìíîïòóôõöùúûüñ",
        "aaaaaaeeeeiiiiooooouuuun",
    )
    normalized = normalized.translate(accent_map)
    return normalized


def _extract_note_key(char: dict) -> str:
    """
    Extract the primary grouping key from a character's guild_note.

    Takes the first word, strips possessives/punctuation/server-name suffixes.
    e.g. "Rocket's DH waifu" → "rocket"
         "Rocket-mental 702"  → "rocket"
         "shodooms shammy"    → "shodoom" (strip trailing s)
         "Sho"                → "sho"
         ""                   → ""
    """
    note = (char.get("guild_note") or "").strip()
    if not note:
        return ""

    # Take first word only
    first_word = note.split()[0]

    # Split on hyphen, take first part (e.g. "Rocket-mental" → "Rocket")
    first_word = first_word.split("-")[0]

    # Strip possessive 's and common punctuation
    first_word = re.sub(r"'s$", "", first_word, flags=re.IGNORECASE)
    first_word = re.sub(r"['\.,;:!?()]", "", first_word)

    key = normalize_name(first_word)

    # If key ends in 's' and is longer than 3 chars, try without it
    # to normalise "rockets" → "rocket", "shodooms" → "shodoom"
    if len(key) > 3 and key.endswith("s"):
        key = key[:-1]

    return key if len(key) >= 2 else ""


def _find_discord_for_key(key: str, all_discord: list) -> Optional[dict]:
    """
    Find the Discord user that best matches the given key string.

    Strategies (in priority order):
      1. Exact match on username
      2. Exact match on display_name
      3. Key exactly matches any word in display_name (split on / - space)
      4. Key is a substring of username          (key >= 3 chars)
      5. Key is a substring of display_name      (key >= 3 chars)
    """
    if not key or len(key) < 2:
        return None

    # Pass 1: exact username
    for du in all_discord:
        if normalize_name(du["username"]) == key:
            return du

    # Pass 2: exact display_name
    for du in all_discord:
        if du["display_name"] and normalize_name(du["display_name"]) == key:
            return du

    # Pass 3: key matches any word/part of display_name
    for du in all_discord:
        if du["display_name"]:
            parts = [
                normalize_name(p)
                for p in re.split(r"[/\-\s]+", du["display_name"])
                if p.strip()
            ]
            if key in parts:
                return du

    if len(key) < 3:
        return None  # Don't do substring matching for very short keys

    # Pass 4: key is substring of username
    for du in all_discord:
        if key in normalize_name(du["username"]):
            return du

    # Pass 5: key is substring of display_name
    for du in all_discord:
        if du["display_name"] and key in normalize_name(du["display_name"]):
            return du

    return None


async def run_matching(pool: asyncpg.Pool, min_rank_level: int | None = None) -> dict:
    """
    Run the note-group matching engine.

    Steps:
    1. Load unlinked characters (optionally rank-filtered).
    2. Group them by their guild_note key.
    3. For each group, find the best Discord user match.
    4. Create one Player per group (with or without Discord link).
    5. For characters with no note, fall back to character-name matching.

    Returns stats dict.
    """
    stats = {
        "players_created": 0,
        "chars_linked": 0,
        "discord_linked": 0,
        "no_discord_match": 0,
        "skipped": 0,
    }

    async with pool.acquire() as conn:

        # --- Load unlinked characters ---
        if min_rank_level is not None:
            unlinked_chars = await conn.fetch(
                """SELECT wc.id, wc.character_name, wc.guild_note, wc.officer_note,
                          wc.guild_rank_id
                   FROM guild_identity.wow_characters wc
                   JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
                   WHERE wc.removed_at IS NULL
                     AND gr.level >= $1
                     AND wc.id NOT IN (
                         SELECT character_id FROM guild_identity.player_characters
                     )""",
                min_rank_level,
            )
        else:
            unlinked_chars = await conn.fetch(
                """SELECT id, character_name, guild_note, officer_note, guild_rank_id
                   FROM guild_identity.wow_characters
                   WHERE removed_at IS NULL
                     AND id NOT IN (
                         SELECT character_id FROM guild_identity.player_characters
                     )"""
            )

        # --- Load all Discord users (guild members only) ---
        all_discord = await conn.fetch(
            """SELECT du.id, du.discord_id, du.username, du.display_name,
                      p.id AS player_id
               FROM guild_identity.discord_users du
               LEFT JOIN guild_identity.players p ON p.discord_user_id = du.id
               WHERE du.is_present = TRUE
                 AND du.highest_guild_role IS NOT NULL"""
        )

        # --- Group characters by guild note key ---
        note_groups: dict[str, list] = defaultdict(list)
        no_note_chars = []

        for char in unlinked_chars:
            key = _extract_note_key(char)
            if key:
                note_groups[key].append(char)
            else:
                no_note_chars.append(char)

        # discord_user_id → player_id: tracks assignments made THIS run
        # so we reuse the same player when multiple note groups match one Discord user
        discord_player_cache: dict[int, int] = {}
        for du in all_discord:
            if du["player_id"]:
                discord_player_cache[du["id"]] = du["player_id"]

        # --- Process each note group ---
        for note_key, chars in note_groups.items():
            discord_user = _find_discord_for_key(note_key, all_discord)
            await _create_player_group(
                conn, chars, discord_user, note_key, discord_player_cache, stats
            )

        # --- Fallback: chars with no note → try character-name matching ---
        for char in no_note_chars:
            char_norm = normalize_name(char["character_name"])
            discord_user = _find_discord_for_key(char_norm, all_discord)
            if discord_user:
                await _create_player_group(
                    conn, [char], discord_user, char_norm, discord_player_cache, stats
                )
            else:
                stats["skipped"] += 1

    logger.info(
        "Matching complete: %d players created, %d chars linked, "
        "%d with Discord, %d stubs (no Discord), %d skipped (no note/name match)",
        stats["players_created"],
        stats["chars_linked"],
        stats["discord_linked"],
        stats["no_discord_match"],
        stats["skipped"],
    )
    return stats


async def _create_player_group(
    conn: asyncpg.Connection,
    chars: list,
    discord_user: Optional[dict],
    display_hint: str,
    discord_player_cache: dict[int, int],
    stats: dict,
):
    """
    Create (or find) one Player for a group of characters and link them all.

    - If discord_user is provided and already has a player, reuse it.
    - If discord_user is provided but has no player, create one with Discord linked.
    - If discord_user is None, create a stub player using display_hint as the name.
    - All characters in the group are linked to the player via player_characters.
    """
    player_id = None

    # Check cache first (player created earlier this run for same Discord user)
    if discord_user:
        player_id = discord_player_cache.get(discord_user["id"])

    async with conn.transaction():
        if not player_id:
            if discord_user:
                # Re-check DB in case it was created outside this run
                player_id = await conn.fetchval(
                    "SELECT id FROM guild_identity.players WHERE discord_user_id = $1",
                    discord_user["id"],
                )

            if not player_id:
                # Create the player
                if discord_user:
                    display = discord_user.get("display_name") or discord_user["username"]
                    discord_uid = discord_user["id"]
                else:
                    display = display_hint.title()
                    discord_uid = None

                # Derive the best rank from the characters in this group
                char_rank_ids = [ch["guild_rank_id"] for ch in chars if ch.get("guild_rank_id")]
                best_rank_id = None
                if char_rank_ids:
                    best_rank_id = await conn.fetchval(
                        """SELECT id FROM common.guild_ranks
                           WHERE id = ANY($1::int[])
                           ORDER BY level DESC LIMIT 1""",
                        char_rank_ids,
                    )

                player_id = await conn.fetchval(
                    """INSERT INTO guild_identity.players
                           (display_name, discord_user_id, guild_rank_id, guild_rank_source)
                       VALUES ($1, $2, $3, $4) RETURNING id""",
                    display,
                    discord_uid,
                    best_rank_id,
                    "wow_character" if best_rank_id else None,
                )
                stats["players_created"] += 1
                if discord_user:
                    stats["discord_linked"] += 1
                    discord_player_cache[discord_user["id"]] = player_id
                    logger.info(
                        "Created player '%s' linked to Discord '%s' (note key: %s)",
                        display, discord_user["username"], display_hint,
                    )
                else:
                    stats["no_discord_match"] += 1
                    logger.info(
                        "Created stub player '%s' (no Discord match for note key: %s)",
                        display, display_hint,
                    )
            else:
                # Existing player found in DB
                discord_player_cache[discord_user["id"]] = player_id

        # Link all characters to this player
        for char in chars:
            existing_owner = await conn.fetchval(
                "SELECT player_id FROM guild_identity.player_characters WHERE character_id = $1",
                char["id"],
            )
            if existing_owner:
                if existing_owner != player_id:
                    logger.warning(
                        "Character '%s' already claimed by player %d — skipping for player %d",
                        char["character_name"], existing_owner, player_id,
                    )
                continue

            await conn.execute(
                """INSERT INTO guild_identity.player_characters (player_id, character_id)
                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                player_id,
                char["id"],
            )
            stats["chars_linked"] += 1
