"""
Identity matching engine.

Links WoW characters and Discord accounts to unified "player" entities.
Uses multiple signals to establish links with varying confidence levels.

Matching strategies (in priority order):
1. Existing confirmed links (from migration or manual confirmation)
2. Exact name match: Discord username/nickname matches character name
3. Guild note parsing: note contains Discord username patterns
4. Officer note parsing: similar to guild note
5. Fuzzy match: character name is very similar to Discord name

Rules:
- A character can only belong to one player
- A Discord account can only belong to one player
- Multiple characters CAN belong to the same player (alts)
- High-confidence matches auto-link; medium/low flag for review
"""

import hashlib
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize a name for comparison — lowercase, strip special chars."""
    if not name:
        return ""
    normalized = name.lower().strip()
    # Remove common accent characters for fuzzy matching
    accent_map = str.maketrans(
        "àáâãäåèéêëìíîïòóôõöùúûüñ",
        "aaaaaa" + "eeee" + "iiii" + "ooooo" + "uuuu" + "n",
    )
    normalized = normalized.translate(accent_map)
    return normalized


def extract_discord_hints_from_note(note: str) -> list[str]:
    """
    Parse a guild note or officer note for Discord username hints.

    Common patterns in guild notes:
    - "Discord: username"
    - "DC: username"
    - "disc: username"
    - "@username"
    - "alt of CharacterName"
    - "Main: CharacterName"
    - Just a Discord username by itself
    """
    if not note or not note.strip():
        return []

    hints = []
    note_clean = note.strip()

    # Pattern: "Discord: username" or "DC: username" or "Disc: username"
    dc_patterns = [
        r"(?:discord|disc|dc)\s*[:=]\s*(\S+)",
        r"@(\S+)",
    ]

    for pattern in dc_patterns:
        matches = re.findall(pattern, note_clean, re.IGNORECASE)
        hints.extend(matches)

    # Pattern: "alt of X" or "X's alt" — hints at character grouping
    alt_patterns = [
        r"alt\s+(?:of|for)\s+(\S+)",
        r"(\S+)'s?\s+alt",
        r"main\s*[:=]\s*(\S+)",
    ]

    for pattern in alt_patterns:
        matches = re.findall(pattern, note_clean, re.IGNORECASE)
        hints.extend(matches)

    # Clean up hints
    cleaned = []
    for h in hints:
        h = h.strip().rstrip(".,;:!)")
        if len(h) >= 2:  # Ignore single-char hints
            cleaned.append(h)

    return cleaned


def fuzzy_match_score(name1: str, name2: str) -> float:
    """
    Calculate similarity between two names.
    Returns a score from 0.0 to 1.0.
    """
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)

    if not n1 or not n2:
        return 0.0

    # Exact match after normalization
    if n1 == n2:
        return 1.0

    # One contains the other (e.g., "trog" and "trogmoon")
    if n1 in n2 or n2 in n1:
        shorter = min(len(n1), len(n2))
        longer = max(len(n1), len(n2))
        return shorter / longer  # Score based on how much overlap

    # SequenceMatcher for general similarity
    return SequenceMatcher(None, n1, n2).ratio()


async def run_matching(pool: asyncpg.Pool, min_rank_level: int | None = None) -> dict:
    """
    Run the matching engine across all unlinked characters and Discord users.

    min_rank_level: if set, only processes characters whose guild_rank_id corresponds
                   to a rank with level >= min_rank_level (e.g. 4 = Officers+).

    Returns stats about matches found.
    """
    stats = {"exact": 0, "guild_note": 0, "officer_note": 0, "fuzzy": 0, "skipped": 0}

    async with pool.acquire() as conn:

        # Get all unlinked WoW characters (not yet in player_characters)
        if min_rank_level is not None:
            unlinked_chars = await conn.fetch(
                """SELECT wc.id, wc.character_name, wc.guild_note, wc.officer_note
                   FROM guild_identity.wow_characters wc
                   JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
                   WHERE wc.removed_at IS NULL
                     AND gr.level >= $1
                     AND wc.id NOT IN (SELECT character_id FROM guild_identity.player_characters)""",
                min_rank_level,
            )
        else:
            unlinked_chars = await conn.fetch(
                """SELECT id, character_name, guild_note, officer_note
                   FROM guild_identity.wow_characters
                   WHERE removed_at IS NULL
                     AND id NOT IN (SELECT character_id FROM guild_identity.player_characters)"""
            )

        # Get all unlinked Discord users (present in server with guild role, no player link)
        unlinked_discord = await conn.fetch(
            """SELECT id, discord_id, username, display_name
               FROM guild_identity.discord_users
               WHERE is_present = TRUE
                 AND highest_guild_role IS NOT NULL
                 AND id NOT IN (
                     SELECT discord_user_id FROM guild_identity.players
                     WHERE discord_user_id IS NOT NULL
                 )"""
        )

        # All Discord users for note-based matching (even already-linked ones)
        all_discord = await conn.fetch(
            """SELECT du.id, du.discord_id, du.username, du.display_name,
                      p.id AS player_id
               FROM guild_identity.discord_users du
               LEFT JOIN guild_identity.players p ON p.discord_user_id = du.id
               WHERE du.is_present = TRUE"""
        )

        # Build lookup maps
        discord_by_name = {}
        for du in all_discord:
            discord_by_name[normalize_name(du["username"])] = du
            if du["display_name"]:
                discord_by_name[normalize_name(du["display_name"])] = du

        unlinked_discord_by_name = {}
        for du in unlinked_discord:
            unlinked_discord_by_name[normalize_name(du["username"])] = du
            if du["display_name"]:
                unlinked_discord_by_name[normalize_name(du["display_name"])] = du

        for char in unlinked_chars:
            char_name_norm = normalize_name(char["character_name"])

            # --- Strategy 1: Exact name match ---
            matched_discord = None
            link_source = None

            if char_name_norm in unlinked_discord_by_name:
                matched_discord = unlinked_discord_by_name[char_name_norm]
                link_source = "exact_name_match"
                stats["exact"] += 1

            # --- Strategy 2: Guild note parsing ---
            if not matched_discord and char["guild_note"]:
                hints = extract_discord_hints_from_note(char["guild_note"])
                for hint in hints:
                    hint_norm = normalize_name(hint)
                    if hint_norm in discord_by_name:
                        matched_discord = discord_by_name[hint_norm]
                        link_source = "guild_note"
                        stats["guild_note"] += 1
                        break

            # --- Strategy 3: Officer note parsing ---
            if not matched_discord and char["officer_note"]:
                hints = extract_discord_hints_from_note(char["officer_note"])
                for hint in hints:
                    hint_norm = normalize_name(hint)
                    if hint_norm in discord_by_name:
                        matched_discord = discord_by_name[hint_norm]
                        link_source = "officer_note"
                        stats["officer_note"] += 1
                        break

            # --- Strategy 4: Fuzzy match (only for unlinked Discord users) ---
            if not matched_discord:
                best_score = 0.0
                best_match = None
                for du in unlinked_discord:
                    for name_field in [du["username"], du["display_name"]]:
                        if not name_field:
                            continue
                        score = fuzzy_match_score(char["character_name"], name_field)
                        if score > best_score:
                            best_score = score
                            best_match = du

                if best_score >= 0.85:
                    matched_discord = best_match
                    link_source = "fuzzy_match"
                    stats["fuzzy"] += 1
                elif best_score >= 0.7:
                    # Low confidence — create an audit issue suggestion instead
                    await _create_link_suggestion(conn, char, best_match, best_score)
                    stats["skipped"] += 1
                    continue

            if not matched_discord:
                stats["skipped"] += 1
                continue

            # --- Create the link ---
            await _create_player_and_link(conn, char, matched_discord, link_source)

    logger.info(
        "Matching complete: %d exact, %d guild_note, %d officer_note, %d fuzzy, %d skipped",
        stats["exact"], stats["guild_note"], stats["officer_note"],
        stats["fuzzy"], stats["skipped"],
    )
    return stats


async def _create_player_and_link(
    conn: asyncpg.Connection,
    char: dict,
    discord_user: dict,
    link_source: str,
):
    """Create or find a player and link the WoW character to it via player_characters.

    Player creation and character linking are atomic — if the character link fails
    (already claimed), the player row is not created either.
    """

    # If the Discord user already has a player, use it
    player_id = discord_user.get("player_id")

    async with conn.transaction():
        if not player_id:
            # Check if a player already exists for this Discord user (race guard)
            existing_player_id = await conn.fetchval(
                "SELECT id FROM guild_identity.players WHERE discord_user_id = $1",
                discord_user["id"],
            )
            if existing_player_id:
                player_id = existing_player_id
                logger.debug(
                    "Reusing existing player %d for discord: %s",
                    player_id, discord_user["username"],
                )
            else:
                # Create a new player with this Discord user linked
                display = discord_user.get("display_name") or discord_user["username"]
                player_id = await conn.fetchval(
                    """INSERT INTO guild_identity.players (display_name, discord_user_id)
                       VALUES ($1, $2) RETURNING id""",
                    display, discord_user["id"],
                )
                logger.info(
                    "Created new player '%s' (discord: %s, source: %s)",
                    display, discord_user["username"], link_source,
                )

        # Check if character is already claimed (can happen if same char appears
        # twice due to stale snapshot or concurrent run)
        already_linked = await conn.fetchval(
            "SELECT player_id FROM guild_identity.player_characters WHERE character_id = $1",
            char["id"],
        )
        if already_linked and already_linked != player_id:
            logger.warning(
                "Character '%s' already claimed by player %d — skipping link to player %d",
                char["character_name"], already_linked, player_id,
            )
            return

        # Link character to player (ON CONFLICT DO NOTHING — idempotent)
        await conn.execute(
            """INSERT INTO guild_identity.player_characters (player_id, character_id)
               VALUES ($1, $2)
               ON CONFLICT (character_id) DO NOTHING""",
            player_id, char["id"],
        )

    logger.info(
        "Linked character '%s' to player %d (source: %s)",
        char["character_name"], player_id, link_source,
    )


async def _create_link_suggestion(
    conn: asyncpg.Connection,
    char: dict,
    discord_user: Optional[dict],
    score: float,
):
    """Create an audit issue suggesting a possible link for human review."""
    if not discord_user:
        return

    issue_hash = hashlib.sha256(
        f"auto_link_suggestion:{char['id']}:{discord_user['id']}".encode()
    ).hexdigest()

    await conn.execute(
        """INSERT INTO guild_identity.audit_issues
           (issue_type, severity, wow_character_id, discord_member_id,
            summary, details, issue_hash)
           VALUES ('auto_link_suggestion', 'info', $1, $2, $3, $4, $5)
           ON CONFLICT (issue_hash, resolved_at) DO NOTHING""",
        char["id"], discord_user["id"],
        f"Possible match: {char['character_name']} ↔ {discord_user['username']} (score: {score:.0%})",
        {"score": score, "char_name": char["character_name"], "discord_name": discord_user["username"]},
        issue_hash,
    )
