"""
Integrity checker — detects mismatches, orphans, and data quality issues.

Run after each sync operation to detect NEW issues.
Only creates audit_issues for problems not already tracked.

Issue Types:
- note_mismatch: Guild note changed and no longer matches linked player (logged by db_sync)
- orphan_wow: Character in guild but no player link
- orphan_discord: Discord member with guild role but no player link
- role_mismatch: In-game rank doesn't match Discord role
- stale_character: Character hasn't logged in for >30 days
- no_guild_role: Discord member linked to a player but has no guild Discord role
"""

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg

from .discord_sync import DISCORD_TO_INGAME_RANK

logger = logging.getLogger(__name__)

# Reverse mapping: in-game rank name → expected Discord role
INGAME_TO_DISCORD_ROLE = {v: k for k, v in DISCORD_TO_INGAME_RANK.items()}

# How long before a character is considered stale (in days)
STALE_THRESHOLD_DAYS = 30


def make_issue_hash(issue_type: str, *identifiers) -> str:
    """Create a deterministic hash for deduplication."""
    raw = f"{issue_type}:" + ":".join(str(i) for i in identifiers)
    return hashlib.sha256(raw.encode()).hexdigest()


async def _upsert_issue(
    conn: asyncpg.Connection,
    issue_type: str,
    severity: str,
    summary: str,
    details: dict,
    issue_hash: str,
    wow_character_id: Optional[int] = None,
    discord_member_id: Optional[int] = None,
) -> bool:
    """
    Create an audit issue if it doesn't already exist (unresolved).
    Returns True if a NEW issue was created.
    """
    existing = await conn.fetchval(
        """SELECT id FROM guild_identity.audit_issues
           WHERE issue_hash = $1 AND resolved_at IS NULL""",
        issue_hash,
    )

    if existing:
        # Update summary/details in case they've changed
        await conn.execute(
            """UPDATE guild_identity.audit_issues SET summary = $2, details = $3
               WHERE id = $1""",
            existing, summary, details,
        )
        return False

    await conn.execute(
        """INSERT INTO guild_identity.audit_issues
           (issue_type, severity, wow_character_id, discord_member_id,
            summary, details, issue_hash)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        issue_type, severity, wow_character_id, discord_member_id,
        summary, details, issue_hash,
    )
    return True


# ---------------------------------------------------------------------------
# Named detection functions — one per rule
# ---------------------------------------------------------------------------


async def detect_note_mismatch(conn: asyncpg.Connection) -> int:
    """
    Detect linked characters whose guild note key doesn't match the player's Discord identity.

    We compare the note key against Discord username and display_name only — NOT against
    player.display_name, because display_name is often derived from the note key itself
    (when run_matching creates a stub player from an unmatched note group). Using
    display_name as a signal would cause false negatives for exactly those cases.

    Characters whose player has no Discord user linked are skipped — we cannot
    reliably detect a mismatch without a Discord identity to compare against.

    Returns count of new issues created.
    """
    import re
    from .identity_engine import _extract_note_key, normalize_name

    # Load all linked characters that have a discord user on their player
    rows = await conn.fetch(
        """SELECT
               wc.id          AS char_id,
               wc.character_name,
               wc.guild_note,
               pc.player_id,
               p.display_name          AS player_display_name,
               du.username             AS discord_username,
               du.display_name         AS discord_display_name
           FROM guild_identity.player_characters pc
           JOIN guild_identity.wow_characters wc  ON wc.id = pc.character_id
           JOIN guild_identity.players p           ON p.id  = pc.player_id
           JOIN guild_identity.discord_users du    ON du.id = p.discord_user_id
           WHERE wc.removed_at IS NULL
             AND wc.guild_note IS NOT NULL
             AND wc.guild_note != ''
             AND du.is_present = TRUE"""
    )

    new_count = 0
    for row in rows:
        note_key = _extract_note_key({"guild_note": row["guild_note"]})
        if not note_key:
            continue

        # Check note key against Discord identities only (not player.display_name,
        # which is often the note key itself for stub players).
        discord_candidates = [
            normalize_name(row["discord_username"] or ""),
            normalize_name(row["discord_display_name"] or ""),
        ]

        still_matches = False
        for name in discord_candidates:
            if not name:
                continue
            if name == note_key:
                still_matches = True
                break
            words = re.split(r"[/\-\s]+", name)
            if note_key in words:
                still_matches = True
                break
            if len(note_key) >= 3 and note_key in name:
                still_matches = True
                break

        if still_matches:
            continue

        h = make_issue_hash("note_mismatch", row["char_id"])
        discord_identity = row["discord_display_name"] or row["discord_username"] or "?"
        created = await _upsert_issue(
            conn,
            issue_type="note_mismatch",
            severity="warning",
            wow_character_id=row["char_id"],
            summary=(
                f"'{row['character_name']}' note says '{note_key}' "
                f"but is linked to Discord user '{discord_identity}'"
            ),
            details={
                "character_name": row["character_name"],
                "note_key": note_key,
                "guild_note": row["guild_note"],
                "player_id": row["player_id"],
                "player_display_name": row["player_display_name"],
                "discord_username": row["discord_username"],
                "discord_display_name": row["discord_display_name"],
            },
            issue_hash=h,
        )
        if created:
            new_count += 1
            logger.info(
                "note_mismatch detected: '%s' note key '%s' doesn't match Discord '%s'",
                row["character_name"], note_key, discord_identity,
            )

    return new_count


async def detect_orphan_wow(conn: asyncpg.Connection) -> int:
    """Detect WoW characters in the guild with no player link. Returns count of new issues."""
    orphan_chars = await conn.fetch(
        """SELECT wc.id, wc.character_name, wc.realm_slug
           FROM guild_identity.wow_characters wc
           WHERE wc.removed_at IS NULL
             AND wc.id NOT IN (
                 SELECT character_id FROM guild_identity.player_characters
             )"""
    )

    new_count = 0
    for char in orphan_chars:
        h = make_issue_hash("orphan_wow", char["id"])
        created = await _upsert_issue(
            conn,
            issue_type="orphan_wow",
            severity="warning",
            wow_character_id=char["id"],
            summary=(
                f"WoW character '{char['character_name']}' "
                f"({char['realm_slug']}) has no player link"
            ),
            details={
                "character_name": char["character_name"],
                "realm": char["realm_slug"],
            },
            issue_hash=h,
        )
        if created:
            new_count += 1
    return new_count


async def detect_orphan_discord(conn: asyncpg.Connection) -> int:
    """Detect Discord members with guild roles but no player link. Returns count of new issues."""
    orphan_discord = await conn.fetch(
        """SELECT du.id, du.discord_id, du.username, du.display_name,
                  du.highest_guild_role
           FROM guild_identity.discord_users du
           WHERE du.is_present = TRUE
             AND du.highest_guild_role IS NOT NULL
             AND du.id NOT IN (
                 SELECT discord_user_id FROM guild_identity.players
                 WHERE discord_user_id IS NOT NULL
             )"""
    )

    new_count = 0
    for du in orphan_discord:
        h = make_issue_hash("orphan_discord", du["id"])
        display = du["display_name"] or du["username"]
        created = await _upsert_issue(
            conn,
            issue_type="orphan_discord",
            severity="warning",
            discord_member_id=du["id"],
            summary=(
                f"Discord member '{display}' (role: {du['highest_guild_role']}) "
                f"has no player link"
            ),
            details={
                "username": du["username"],
                "display_name": du["display_name"],
                "role": du["highest_guild_role"],
                "discord_id": du["discord_id"],
            },
            issue_hash=h,
        )
        if created:
            new_count += 1
    return new_count


async def detect_role_mismatch(conn: asyncpg.Connection) -> tuple[int, int]:
    """
    Detect players where in-game rank doesn't match Discord role.
    Returns (role_mismatch_new, no_guild_role_new).
    """
    linked_players = await conn.fetch(
        """SELECT p.id AS player_id, p.display_name,
                  wc.character_name,
                  gr.name AS guild_rank_name, gr.level AS guild_rank_level,
                  du.username, du.display_name AS discord_display,
                  du.highest_guild_role, du.discord_id, du.id AS discord_user_id
           FROM guild_identity.players p
           JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
           JOIN guild_identity.player_characters pc ON pc.player_id = p.id
           JOIN guild_identity.wow_characters wc ON wc.id = pc.character_id
           LEFT JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
           WHERE wc.removed_at IS NULL AND du.is_present = TRUE"""
    )

    # Group by player, tracking highest in-game rank level
    player_data = {}
    for row in linked_players:
        pid = row["player_id"]
        if pid not in player_data:
            player_data[pid] = {
                "display_name": row["display_name"],
                "discord_username": row["username"],
                "discord_display": row["discord_display"],
                "discord_role": row["highest_guild_role"],
                "discord_id": row["discord_id"],
                "discord_user_id": row["discord_user_id"],
                "highest_rank_level": 0,
                "highest_rank_name": None,
                "characters": [],
            }
        player_data[pid]["characters"].append(row["character_name"])
        rank_level = row["guild_rank_level"] or 0
        if rank_level > player_data[pid]["highest_rank_level"]:
            player_data[pid]["highest_rank_level"] = rank_level
            player_data[pid]["highest_rank_name"] = row["guild_rank_name"]

    role_mismatch_new = 0
    no_guild_role_new = 0

    for pid, data in player_data.items():
        highest_rank = data["highest_rank_name"]
        if not highest_rank:
            continue

        expected_discord_role = INGAME_TO_DISCORD_ROLE.get(highest_rank)
        actual_discord_role = data["discord_role"]

        if expected_discord_role and actual_discord_role:
            if expected_discord_role.lower() != actual_discord_role.lower():
                h = make_issue_hash("role_mismatch", pid)
                display = data["discord_display"] or data["discord_username"]
                created = await _upsert_issue(
                    conn,
                    issue_type="role_mismatch",
                    severity="warning",
                    discord_member_id=data["discord_user_id"],
                    summary=(
                        f"'{display}' is {highest_rank} in-game "
                        f"but {actual_discord_role} on Discord "
                        f"(expected: {expected_discord_role})"
                    ),
                    details={
                        "player_display": data["display_name"],
                        "ingame_rank": highest_rank,
                        "discord_role": actual_discord_role,
                        "expected_discord_role": expected_discord_role,
                        "characters": data["characters"],
                    },
                    issue_hash=h,
                )
                if created:
                    role_mismatch_new += 1

        elif expected_discord_role and not actual_discord_role:
            h = make_issue_hash("no_guild_role", pid)
            display = data["discord_display"] or data["discord_username"]
            created = await _upsert_issue(
                conn,
                issue_type="no_guild_role",
                severity="warning",
                discord_member_id=data["discord_user_id"],
                summary=(
                    f"'{display}' is {highest_rank} in-game "
                    f"but has NO guild role on Discord"
                ),
                details={
                    "player_display": data["display_name"],
                    "ingame_rank": highest_rank,
                    "expected_discord_role": expected_discord_role,
                    "characters": data["characters"],
                },
                issue_hash=h,
            )
            if created:
                no_guild_role_new += 1

    return role_mismatch_new, no_guild_role_new


async def detect_stale_character(conn: asyncpg.Connection) -> int:
    """Detect characters that haven't logged in for >30 days. Returns count of new issues."""
    stale_threshold = datetime.now(timezone.utc) - timedelta(days=STALE_THRESHOLD_DAYS)
    stale_ts = int(stale_threshold.timestamp() * 1000)  # Blizzard uses milliseconds

    stale_chars = await conn.fetch(
        """SELECT id, character_name, last_login_timestamp
           FROM guild_identity.wow_characters
           WHERE removed_at IS NULL
             AND last_login_timestamp IS NOT NULL
             AND last_login_timestamp < $1""",
        stale_ts,
    )

    new_count = 0
    for char in stale_chars:
        h = make_issue_hash("stale_character", char["id"])
        last_login = datetime.fromtimestamp(
            char["last_login_timestamp"] / 1000, tz=timezone.utc
        )
        days_ago = (datetime.now(timezone.utc) - last_login).days

        created = await _upsert_issue(
            conn,
            issue_type="stale_character",
            severity="info",
            wow_character_id=char["id"],
            summary=(
                f"'{char['character_name']}' "
                f"hasn't logged in for {days_ago} days"
            ),
            details={
                "character_name": char["character_name"],
                "last_login": last_login.isoformat(),
                "days_inactive": days_ago,
            },
            issue_hash=h,
        )
        if created:
            new_count += 1
    return new_count


# Mapping for admin scan-by-type endpoint
DETECT_FUNCTIONS = {
    "note_mismatch": detect_note_mismatch,
    "orphan_wow": detect_orphan_wow,
    "orphan_discord": detect_orphan_discord,
    "stale_character": detect_stale_character,
    # role_mismatch handled specially (returns tuple)
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_integrity_check(pool: asyncpg.Pool) -> dict:
    """
    Run all integrity checks and create audit issues for new problems.

    Returns stats: {orphan_wow, orphan_discord, role_mismatch, stale, no_guild_role, total_new}
    """
    stats = {
        "note_mismatch": 0,
        "orphan_wow": 0,
        "orphan_discord": 0,
        "role_mismatch": 0,
        "stale": 0,
        "no_guild_role": 0,
        "total_new": 0,
    }

    async with pool.acquire() as conn:
        stats["note_mismatch"] = await detect_note_mismatch(conn)
        stats["orphan_wow"] = await detect_orphan_wow(conn)
        stats["orphan_discord"] = await detect_orphan_discord(conn)

        role_mismatch_new, no_role_new = await detect_role_mismatch(conn)
        stats["role_mismatch"] = role_mismatch_new
        stats["no_guild_role"] = no_role_new

        stats["stale"] = await detect_stale_character(conn)

        stats["total_new"] = (
            stats["note_mismatch"]
            + stats["orphan_wow"]
            + stats["orphan_discord"]
            + stats["role_mismatch"]
            + stats["no_guild_role"]
            + stats["stale"]
        )

        # Auto-resolve issues where the underlying problem no longer exists
        await _auto_resolve_fixed_issues(conn)

    logger.info(
        "Integrity check: %d note_mismatch, %d orphan_wow, %d orphan_discord, "
        "%d role_mismatch, %d stale, %d no_role — %d total new issues",
        stats["note_mismatch"], stats["orphan_wow"], stats["orphan_discord"],
        stats["role_mismatch"], stats["stale"], stats["no_guild_role"], stats["total_new"],
    )
    return stats


async def _auto_resolve_fixed_issues(conn: asyncpg.Connection):
    """
    Auto-resolve issues where the underlying problem no longer exists.
    """
    now = datetime.now(timezone.utc)

    # Resolve orphan_wow issues where the character now has a player_characters entry
    await conn.execute(
        """UPDATE guild_identity.audit_issues SET
            resolved_at = $1, resolved_by = 'auto'
           WHERE issue_type = 'orphan_wow'
             AND resolved_at IS NULL
             AND wow_character_id IN (
                 SELECT character_id FROM guild_identity.player_characters
             )""",
        now,
    )

    # Resolve orphan_discord issues where the discord user now has a player link
    await conn.execute(
        """UPDATE guild_identity.audit_issues SET
            resolved_at = $1, resolved_by = 'auto'
           WHERE issue_type = 'orphan_discord'
             AND resolved_at IS NULL
             AND discord_member_id IN (
                 SELECT discord_user_id FROM guild_identity.players
                 WHERE discord_user_id IS NOT NULL
             )""",
        now,
    )

    # Resolve stale_character issues where the character has logged in recently
    stale_threshold = datetime.now(timezone.utc) - timedelta(days=STALE_THRESHOLD_DAYS)
    stale_ts = int(stale_threshold.timestamp() * 1000)

    await conn.execute(
        """UPDATE guild_identity.audit_issues SET
            resolved_at = $1, resolved_by = 'auto'
           WHERE issue_type = 'stale_character'
             AND resolved_at IS NULL
             AND wow_character_id IN (
                 SELECT id FROM guild_identity.wow_characters
                 WHERE last_login_timestamp >= $2
             )""",
        now, stale_ts,
    )
