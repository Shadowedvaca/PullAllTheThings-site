"""
Identity matching engine.

Links WoW characters and Discord accounts to unified "person" entities.
Uses multiple signals to establish links with varying confidence levels.

Matching strategies (in priority order):
1. Existing confirmed links (from migration or manual confirmation)
2. Exact name match: Discord username/nickname matches character name
3. Guild note parsing: note contains Discord username patterns
4. Officer note parsing: similar to guild note
5. Fuzzy match: character name is very similar to Discord name

Rules:
- A character can only belong to one person
- A Discord account can only belong to one person
- Multiple characters CAN belong to the same person (alts)
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


async def run_matching(pool: asyncpg.Pool) -> dict:
    """
    Run the full matching engine across all unlinked characters and Discord members.

    Returns stats about matches found.
    """
    stats = {"exact": 0, "guild_note": 0, "officer_note": 0, "fuzzy": 0, "skipped": 0}

    async with pool.acquire() as conn:

        # Get all unlinked WoW characters (no person_id set)
        unlinked_chars = await conn.fetch(
            """SELECT id, character_name, guild_note, officer_note
               FROM guild_identity.wow_characters
               WHERE person_id IS NULL AND removed_at IS NULL"""
        )

        # Get all unlinked Discord members (no person_id set)
        unlinked_discord = await conn.fetch(
            """SELECT id, discord_id, username, display_name
               FROM guild_identity.discord_members
               WHERE person_id IS NULL AND is_present = TRUE"""
        )

        # Also get all Discord members for matching (even linked ones, for note parsing)
        all_discord = await conn.fetch(
            """SELECT id, discord_id, username, display_name, person_id
               FROM guild_identity.discord_members
               WHERE is_present = TRUE"""
        )

        # Build lookup maps
        discord_by_name = {}
        for dm in all_discord:
            discord_by_name[normalize_name(dm["username"])] = dm
            if dm["display_name"]:
                discord_by_name[normalize_name(dm["display_name"])] = dm

        unlinked_discord_by_name = {}
        for dm in unlinked_discord:
            unlinked_discord_by_name[normalize_name(dm["username"])] = dm
            if dm["display_name"]:
                unlinked_discord_by_name[normalize_name(dm["display_name"])] = dm

        for char in unlinked_chars:
            char_name_norm = normalize_name(char["character_name"])

            # --- Strategy 1: Exact name match ---
            matched_discord = None
            link_source = None
            confidence = None

            if char_name_norm in unlinked_discord_by_name:
                matched_discord = unlinked_discord_by_name[char_name_norm]
                link_source = "exact_name_match"
                confidence = "high"
                stats["exact"] += 1

            # --- Strategy 2: Guild note parsing ---
            if not matched_discord and char["guild_note"]:
                hints = extract_discord_hints_from_note(char["guild_note"])
                for hint in hints:
                    hint_norm = normalize_name(hint)
                    if hint_norm in discord_by_name:
                        matched_discord = discord_by_name[hint_norm]
                        link_source = "guild_note"
                        confidence = "high"
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
                        confidence = "high"
                        stats["officer_note"] += 1
                        break

            # --- Strategy 4: Fuzzy match (only for unlinked Discord members) ---
            if not matched_discord:
                best_score = 0.0
                best_match = None
                for dm in unlinked_discord:
                    for name_field in [dm["username"], dm["display_name"]]:
                        if not name_field:
                            continue
                        score = fuzzy_match_score(char["character_name"], name_field)
                        if score > best_score:
                            best_score = score
                            best_match = dm

                if best_score >= 0.85:
                    matched_discord = best_match
                    link_source = "fuzzy_match"
                    confidence = "medium"  # Fuzzy matches need confirmation
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
            await _create_person_and_link(
                conn, char, matched_discord, link_source, confidence
            )

    logger.info(
        "Matching complete: %d exact, %d guild_note, %d officer_note, %d fuzzy, %d skipped",
        stats["exact"], stats["guild_note"], stats["officer_note"],
        stats["fuzzy"], stats["skipped"],
    )
    return stats


async def _create_person_and_link(
    conn: asyncpg.Connection,
    char: dict,
    discord_member: dict,
    link_source: str,
    confidence: str,
):
    """Create a person entity and link both the character and discord member to it."""

    # If the Discord member already has a person, use that person
    if discord_member.get("person_id"):
        person_id = discord_member["person_id"]
    else:
        # Create new person using the Discord username as display name
        display = discord_member.get("display_name") or discord_member["username"]
        person_id = await conn.fetchval(
            """INSERT INTO guild_identity.persons (display_name)
               VALUES ($1) RETURNING id""",
            display,
        )

        # Link Discord member to person
        await conn.execute(
            """UPDATE guild_identity.discord_members SET person_id = $1 WHERE id = $2""",
            person_id, discord_member["id"],
        )
        await conn.execute(
            """INSERT INTO guild_identity.identity_links
               (person_id, discord_member_id, link_source, confidence, is_confirmed)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (discord_member_id) DO NOTHING""",
            person_id, discord_member["id"], link_source,
            confidence, confidence == "high",
        )

    # Link character to person
    await conn.execute(
        """UPDATE guild_identity.wow_characters SET person_id = $1 WHERE id = $2""",
        person_id, char["id"],
    )
    await conn.execute(
        """INSERT INTO guild_identity.identity_links
           (person_id, wow_character_id, link_source, confidence, is_confirmed)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (wow_character_id) DO NOTHING""",
        person_id, char["id"], link_source,
        confidence, confidence == "high",
    )

    logger.info(
        "Linked character '%s' to Discord '%s' (source: %s, confidence: %s)",
        char["character_name"], discord_member["username"], link_source, confidence,
    )


async def _create_link_suggestion(
    conn: asyncpg.Connection,
    char: dict,
    discord_member: Optional[dict],
    score: float,
):
    """Create an audit issue suggesting a possible link for human review."""
    if not discord_member:
        return

    issue_hash = hashlib.sha256(
        f"auto_link_suggestion:{char['id']}:{discord_member['id']}".encode()
    ).hexdigest()

    await conn.execute(
        """INSERT INTO guild_identity.audit_issues
           (issue_type, severity, wow_character_id, discord_member_id,
            summary, details, issue_hash)
           VALUES ('auto_link_suggestion', 'info', $1, $2, $3, $4, $5)
           ON CONFLICT (issue_hash, resolved_at) DO NOTHING""",
        char["id"], discord_member["id"],
        f"Possible match: {char['character_name']} ↔ {discord_member['username']} (score: {score:.0%})",
        {"score": score, "char_name": char["character_name"], "discord_name": discord_member["username"]},
        issue_hash,
    )
