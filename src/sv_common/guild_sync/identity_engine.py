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

import difflib
import logging
import re
from typing import Optional

import asyncpg
from .integrity_checker import upsert_note_alias

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


def extract_discord_hints_from_note(note: str | None) -> list[str]:
    """
    Extract Discord username hints from a guild note.

    Recognises patterns officers use to identify members:
      "Discord: Trog", "DC: Trog", "Disc: Trog"
      "@Trog"
      "alt of Trogmoon"
      "Main: Trogmoon"

    Returns a list of candidate hint strings (original case, trailing
    punctuation stripped).  Returns [] if note is empty or has no matches.
    """
    if not note or not note.strip():
        return []

    patterns = [
        r'(?:discord|disc|dc)\s*:\s*(\S+)',
        r'@(\S+)',
        r'alt\s+of\s+(\S+)',
        r'main\s*:\s*(\S+)',
    ]
    hints = []
    for pattern in patterns:
        for match in re.finditer(pattern, note, re.IGNORECASE):
            hint = match.group(1).rstrip('.,;:!?').strip()
            if hint:
                hints.append(hint)
    return hints


def fuzzy_match_score(a: str, b: str) -> float:
    """
    Score the similarity between two names, returning a value in [0.0, 1.0].

    Rules (in priority order):
      - Either empty → 0.0
      - Equal (case-insensitive, accent-stripped) → 1.0
      - One is a substring of the other → len(shorter) / len(longer)
      - Otherwise → difflib.SequenceMatcher ratio
    """
    a_norm = normalize_name(a)
    b_norm = normalize_name(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    shorter, longer = (a_norm, b_norm) if len(a_norm) <= len(b_norm) else (b_norm, a_norm)
    if shorter in longer:
        return len(shorter) / len(longer)
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


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


def _find_discord_for_key(key: str, all_discord: list) -> tuple[Optional[dict], str]:
    """
    Find the Discord user that best matches the given key string.

    Returns (discord_user_or_None, match_type).
    match_type is one of: "exact_username", "exact_display", "word_in_display",
                          "substring_username", "substring_display", "none"

    Strategies (in priority order):
      1. Exact match on username
      2. Exact match on display_name
      3. Key exactly matches any word in display_name (split on / - space)
      4. Key is a substring of username          (key >= 3 chars)
      5. Key is a substring of display_name      (key >= 3 chars)
    """
    if not key or len(key) < 2:
        return None, "none"

    # Pass 1: exact username
    for du in all_discord:
        if normalize_name(du["username"]) == key:
            return du, "exact_username"

    # Pass 2: exact display_name
    for du in all_discord:
        if du["display_name"] and normalize_name(du["display_name"]) == key:
            return du, "exact_display"

    # Pass 3: key matches any word/part of display_name
    for du in all_discord:
        if du["display_name"]:
            parts = [
                normalize_name(p)
                for p in re.split(r"[/\-\s]+", du["display_name"])
                if p.strip()
            ]
            if key in parts:
                return du, "word_in_display"

    if len(key) < 3:
        return None, "none"  # Don't do substring matching for very short keys

    # Pass 4: key is substring of username
    for du in all_discord:
        if key in normalize_name(du["username"]):
            return du, "substring_username"

    # Pass 5: key is substring of display_name
    for du in all_discord:
        if du["display_name"] and key in normalize_name(du["display_name"]):
            return du, "substring_display"

    return None, "none"


def _attribution_for_match(
    match_type: str,
    discord_user: Optional[dict],
    from_note: bool,
) -> tuple[str, str]:
    """
    Derive (link_source, confidence) from how the match was made.

    from_note=True  → character was grouped by guild note key
    from_note=False → character had no note; matched by character name
    """
    if discord_user is None:
        return "note_key_stub", "low"

    if from_note:
        if match_type in ("exact_username", "exact_display"):
            return "note_key", "high"
        # word_in_display, substring_username, substring_display
        return "note_key", "medium"
    else:
        # No-note path: character name was the key
        if match_type in ("exact_username", "exact_display"):
            return "exact_name", "high"
        return "fuzzy_name", "medium"


def _note_still_matches_player(note_key: str, player_display: str, discord_username: str, discord_display: str) -> bool:
    """Return True if the note key still plausibly belongs to this player.

    Mirrors the matching strategy in _find_discord_for_key (passes 1-3 + substring).
    """
    candidates = [
        normalize_name(player_display or ""),
        normalize_name(discord_username or ""),
        normalize_name(discord_display or ""),
    ]
    for name in candidates:
        if not name:
            continue
        if name == note_key:
            return True
        # Key matches a word within the name (e.g. "trog" in "Trog/Moon")
        words = re.split(r"[/\-\s]+", name)
        if note_key in words:
            return True
        # Substring match for short aliases (e.g. "trog" in "trogmoon")
        if len(note_key) >= 3 and note_key in name:
            return True
    return False


async def relink_note_changed_characters(pool: asyncpg.Pool, char_ids: list[int]) -> dict:
    """Re-evaluate player assignments for characters whose guild note changed.

    For each character:
    - If the new note key still matches the current player → leave it alone.
    - If the new note key no longer matches → unlink the character (and clear
      main_character_id / offspec_character_id if needed) so run_matching()
      can reassign it to the correct player.

    Does NOT create new links itself — that is left to run_matching().
    """
    if not char_ids:
        return {"unlinked": 0, "skipped": 0}

    stats = {"unlinked": 0, "skipped": 0}

    async with pool.acquire() as conn:
        for char_id in char_ids:
            row = await conn.fetchrow(
                """SELECT
                       wc.id,
                       wc.character_name,
                       wc.guild_note,
                       pc.player_id,
                       p.display_name          AS player_display_name,
                       du.username             AS discord_username,
                       du.display_name         AS discord_display_name
                   FROM guild_identity.wow_characters wc
                   LEFT JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
                   LEFT JOIN guild_identity.players p            ON p.id = pc.player_id
                   LEFT JOIN guild_identity.discord_users du     ON du.id = p.discord_user_id
                   WHERE wc.id = $1""",
                char_id,
            )

            if not row or not row["player_id"]:
                # Already unlinked — run_matching will handle it
                stats["skipped"] += 1
                continue

            note_key = _extract_note_key(dict(row))
            if not note_key:
                # Empty note — can't determine intent, leave it alone
                stats["skipped"] += 1
                continue

            if _note_still_matches_player(
                note_key,
                row["player_display_name"],
                row["discord_username"],
                row["discord_display_name"],
            ):
                stats["skipped"] += 1
                continue

            # Note no longer matches current player — unlink so run_matching reassigns
            async with conn.transaction():
                # Clear main/offspec pointers on the old player if they referenced this char
                await conn.execute(
                    """UPDATE guild_identity.players
                       SET main_character_id = NULL
                       WHERE id = $1 AND main_character_id = $2""",
                    row["player_id"], char_id,
                )
                await conn.execute(
                    """UPDATE guild_identity.players
                       SET offspec_character_id = NULL
                       WHERE id = $1 AND offspec_character_id = $2""",
                    row["player_id"], char_id,
                )
                await conn.execute(
                    "DELETE FROM guild_identity.player_characters WHERE character_id = $1",
                    char_id,
                )

            logger.info(
                "Note change: unlinked '%s' from player '%s' (new note key: '%s'). "
                "run_matching() will reassign.",
                row["character_name"],
                row["player_display_name"],
                note_key,
            )
            stats["unlinked"] += 1

    logger.info(
        "Note-change relink: %d unlinked for reassignment, %d unchanged",
        stats["unlinked"], stats["skipped"],
    )
    return stats


async def run_matching(pool: asyncpg.Pool, min_rank_level: int | None = None) -> dict:
    """
    Run the iterative matching engine.  Delegates to the rule runner.

    Returns a stats dict that includes both the new structured format
    (passes, converged, results, totals) and the old flat keys
    (players_created, chars_linked, discord_linked, no_discord_match, skipped)
    for backward compatibility.
    """
    from .matching_rules.runner import run_matching_rules

    return await run_matching_rules(pool, min_rank_level=min_rank_level)


async def _create_player_group(
    conn: asyncpg.Connection,
    chars: list,
    discord_user: Optional[dict],
    display_hint: str,
    discord_player_cache: dict[int, int],
    stats: dict,
    match_type: str = "none",
    from_note: bool = True,
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

        # Determine attribution for all characters in this group
        link_source, confidence = _attribution_for_match(match_type, discord_user, from_note)

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
                """INSERT INTO guild_identity.player_characters
                       (player_id, character_id, link_source, confidence)
                   VALUES ($1, $2, $3, $4) ON CONFLICT (character_id) DO NOTHING""",
                player_id,
                char["id"],
                link_source,
                confidence,
            )
            stats["chars_linked"] += 1

            # Record this note key as a confirmed alias for this player
            note_key = _extract_note_key(char)
            if note_key:
                await upsert_note_alias(conn, player_id, note_key, source="note_match")
