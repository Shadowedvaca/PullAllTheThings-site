# Phase 2.5B: Identity Engine, Discord Sync, Integrity Checker & Reporting

## Prerequisites
- Phase 2.5A complete (schema created, Blizzard client working)
- Phase 2 Discord bot running and connected to the PATT server

## Task 1: Discord Member Sync

Extends the existing Phase 2 Discord bot to sync guild member data.

```python
# sv_common/guild_sync/discord_sync.py

"""
Discord server member and role synchronization.

Integrates with the existing Phase 2 Discord bot to:
- Pull the full member list with roles
- Determine each member's highest guild-relevant role
- Listen for real-time join/leave/role-change events
- Write data to guild_identity.discord_members

Guild-relevant Discord roles (in priority order):
  GM > Officer > Veteran > Member > Initiate

Members without ANY of these roles are tracked but flagged differently — 
they might be guests, bots, or people who haven't been assigned a role yet.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import discord

logger = logging.getLogger(__name__)

# Discord role names → normalized names, in priority order (highest first)
# The role name in Discord should match these exactly (case-insensitive matching applied)
GUILD_ROLE_PRIORITY = ["GM", "Officer", "Veteran", "Member", "Initiate"]

# Map Discord role to equivalent in-game rank name for comparison
DISCORD_TO_INGAME_RANK = {
    "GM": "Guild Leader",
    "Officer": "Officer",
    "Veteran": "Veteran",
    "Member": "Member",
    "Initiate": "Initiate",
}


def get_highest_guild_role(member: discord.Member) -> Optional[str]:
    """
    Determine a Discord member's highest guild-relevant role.
    
    Returns the role name (e.g., "Officer") or None if they have no guild roles.
    """
    member_role_names = [r.name for r in member.roles]
    
    for role_name in GUILD_ROLE_PRIORITY:
        for mr in member_role_names:
            if mr.lower() == role_name.lower():
                return role_name
    
    return None


def get_all_guild_roles(member: discord.Member) -> list[str]:
    """Get all guild-relevant roles a member has."""
    result = []
    member_role_names = [r.name.lower() for r in member.roles]
    
    for role_name in GUILD_ROLE_PRIORITY:
        if role_name.lower() in member_role_names:
            result.append(role_name)
    
    return result


async def sync_discord_members(
    pool: asyncpg.Pool,
    guild: discord.Guild,
) -> dict:
    """
    Full sync of all Discord server members into the database.
    
    Called periodically (every 15-30 min) or on demand.
    """
    now = datetime.now(timezone.utc)
    stats = {"found": 0, "updated": 0, "new": 0, "departed": 0}
    
    current_ids = set()
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            
            # Iterate all members (requires GUILD_MEMBERS intent)
            async for member in guild.fetch_members(limit=None):
                if member.bot:
                    continue
                
                stats["found"] += 1
                discord_id = str(member.id)
                current_ids.add(discord_id)
                
                highest_role = get_highest_guild_role(member)
                all_roles = get_all_guild_roles(member)
                display = member.nick or member.display_name
                
                existing = await conn.fetchrow(
                    """SELECT id, highest_guild_role, is_present
                       FROM guild_identity.discord_members
                       WHERE discord_id = $1""",
                    discord_id
                )
                
                if existing:
                    await conn.execute(
                        """UPDATE guild_identity.discord_members SET
                            username = $2,
                            display_name = $3,
                            highest_guild_role = $4,
                            all_guild_roles = $5,
                            last_sync = $6,
                            is_present = TRUE,
                            removed_at = NULL
                           WHERE discord_id = $1""",
                        discord_id,
                        member.name,  # New Discord username (no discriminator)
                        display,
                        highest_role,
                        all_roles,
                        now,
                    )
                    stats["updated"] += 1
                else:
                    await conn.execute(
                        """INSERT INTO guild_identity.discord_members
                           (discord_id, username, display_name, highest_guild_role,
                            all_guild_roles, joined_server_at, last_sync, is_present)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)""",
                        discord_id,
                        member.name,
                        display,
                        highest_role,
                        all_roles,
                        member.joined_at,
                        now,
                    )
                    stats["new"] += 1
            
            # Mark members who left
            all_present = await conn.fetch(
                """SELECT id, discord_id FROM guild_identity.discord_members
                   WHERE is_present = TRUE"""
            )
            
            for row in all_present:
                if row['discord_id'] not in current_ids:
                    await conn.execute(
                        """UPDATE guild_identity.discord_members SET
                            is_present = FALSE, removed_at = $2
                           WHERE id = $1""",
                        row['id'], now
                    )
                    stats["departed"] += 1
    
    logger.info(
        "Discord sync: %d found, %d updated, %d new, %d departed",
        stats["found"], stats["updated"], stats["new"], stats["departed"]
    )
    return stats


# --- Real-time event handlers (register with existing bot) ---

async def on_member_join(pool: asyncpg.Pool, member: discord.Member):
    """Handle a new member joining the Discord server."""
    if member.bot:
        return
    
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO guild_identity.discord_members
               (discord_id, username, display_name, joined_server_at, last_sync, is_present)
               VALUES ($1, $2, $3, $4, NOW(), TRUE)
               ON CONFLICT (discord_id) DO UPDATE SET
                 is_present = TRUE, removed_at = NULL, last_sync = NOW()""",
            str(member.id), member.name, member.nick or member.display_name,
            member.joined_at,
        )
    logger.info("Discord member joined: %s (%s)", member.name, member.id)


async def on_member_remove(pool: asyncpg.Pool, member: discord.Member):
    """Handle a member leaving the Discord server."""
    if member.bot:
        return
    
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE guild_identity.discord_members SET
                is_present = FALSE, removed_at = NOW()
               WHERE discord_id = $1""",
            str(member.id),
        )
    logger.info("Discord member left: %s (%s)", member.name, member.id)


async def on_member_update(pool: asyncpg.Pool, before: discord.Member, after: discord.Member):
    """Handle role changes or nickname changes."""
    if after.bot:
        return
    
    # Check if guild-relevant roles changed
    old_roles = get_all_guild_roles(before)
    new_roles = get_all_guild_roles(after)
    
    if old_roles != new_roles or before.nick != after.nick:
        highest = get_highest_guild_role(after)
        display = after.nick or after.display_name
        
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.discord_members SET
                    username = $2, display_name = $3,
                    highest_guild_role = $4, all_guild_roles = $5,
                    last_sync = NOW()
                   WHERE discord_id = $1""",
                str(after.id), after.name, display, highest, new_roles,
            )
        
        if old_roles != new_roles:
            logger.info(
                "Discord role change for %s: %s → %s",
                after.name, old_roles, new_roles
            )
```

## Task 2: Identity Matching Engine

The brain of the system — links WoW characters to Discord accounts via "person" entities.

```python
# sv_common/guild_sync/identity_engine.py

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
    # Remove common WoW special chars and accents for comparison
    # Keep the original for display, but compare normalized
    normalized = name.lower().strip()
    # Remove common accent characters for fuzzy matching
    accent_map = str.maketrans('àáâãäåèéêëìíîïòóôõöùúûüñ', 'aaaaaaeeeeiiiioooooeuuuun')
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
        r'(?:discord|disc|dc)\s*[:=]\s*(\S+)',
        r'@(\S+)',
    ]
    
    for pattern in dc_patterns:
        matches = re.findall(pattern, note_clean, re.IGNORECASE)
        hints.extend(matches)
    
    # Pattern: "alt of X" or "X's alt" — hints at character grouping
    alt_patterns = [
        r'alt\s+(?:of|for)\s+(\S+)',
        r"(\S+)'s?\s+alt",
        r'main\s*[:=]\s*(\S+)',
    ]
    
    for pattern in alt_patterns:
        matches = re.findall(pattern, note_clean, re.IGNORECASE)
        hints.extend(matches)
    
    # Clean up hints
    cleaned = []
    for h in hints:
        h = h.strip().rstrip('.,;:!)')
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
            discord_by_name[normalize_name(dm['username'])] = dm
            if dm['display_name']:
                discord_by_name[normalize_name(dm['display_name'])] = dm
        
        unlinked_discord_by_name = {}
        for dm in unlinked_discord:
            unlinked_discord_by_name[normalize_name(dm['username'])] = dm
            if dm['display_name']:
                unlinked_discord_by_name[normalize_name(dm['display_name'])] = dm
        
        for char in unlinked_chars:
            char_name_norm = normalize_name(char['character_name'])
            
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
            if not matched_discord and char['guild_note']:
                hints = extract_discord_hints_from_note(char['guild_note'])
                for hint in hints:
                    hint_norm = normalize_name(hint)
                    if hint_norm in discord_by_name:
                        target_dm = discord_by_name[hint_norm]
                        matched_discord = target_dm
                        link_source = "guild_note"
                        confidence = "high"
                        stats["guild_note"] += 1
                        break
            
            # --- Strategy 3: Officer note parsing ---
            if not matched_discord and char['officer_note']:
                hints = extract_discord_hints_from_note(char['officer_note'])
                for hint in hints:
                    hint_norm = normalize_name(hint)
                    if hint_norm in discord_by_name:
                        target_dm = discord_by_name[hint_norm]
                        matched_discord = target_dm
                        link_source = "officer_note"
                        confidence = "high"
                        stats["officer_note"] += 1
                        break
            
            # --- Strategy 4: Fuzzy match (only for unlinked Discord members) ---
            if not matched_discord:
                best_score = 0
                best_match = None
                for dm in unlinked_discord:
                    for name_field in [dm['username'], dm['display_name']]:
                        if not name_field:
                            continue
                        score = fuzzy_match_score(char['character_name'], name_field)
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
                    await _create_link_suggestion(
                        conn, char, best_match, best_score
                    )
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
        stats["fuzzy"], stats["skipped"]
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
    if discord_member.get('person_id'):
        person_id = discord_member['person_id']
    else:
        # Create new person using the Discord username as display name
        display = discord_member.get('display_name') or discord_member['username']
        person_id = await conn.fetchval(
            """INSERT INTO guild_identity.persons (display_name)
               VALUES ($1) RETURNING id""",
            display
        )
        
        # Link Discord member to person
        await conn.execute(
            """UPDATE guild_identity.discord_members SET person_id = $1 WHERE id = $2""",
            person_id, discord_member['id']
        )
        await conn.execute(
            """INSERT INTO guild_identity.identity_links
               (person_id, discord_member_id, link_source, confidence, is_confirmed)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (discord_member_id) DO NOTHING""",
            person_id, discord_member['id'], link_source,
            confidence, confidence == "high"
        )
    
    # Link character to person
    await conn.execute(
        """UPDATE guild_identity.wow_characters SET person_id = $1 WHERE id = $2""",
        person_id, char['id']
    )
    await conn.execute(
        """INSERT INTO guild_identity.identity_links
           (person_id, wow_character_id, link_source, confidence, is_confirmed)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (wow_character_id) DO NOTHING""",
        person_id, char['id'], link_source,
        confidence, confidence == "high"
    )
    
    logger.info(
        "Linked character '%s' to Discord '%s' (source: %s, confidence: %s)",
        char['character_name'], discord_member['username'], link_source, confidence
    )


async def _create_link_suggestion(
    conn: asyncpg.Connection,
    char: dict,
    discord_member: Optional[dict],
    score: float,
):
    """Create an audit issue suggesting a possible link for human review."""
    import hashlib
    
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
        char['id'], discord_member['id'],
        f"Possible match: {char['character_name']} ↔ {discord_member['username']} (score: {score:.0%})",
        {"score": score, "char_name": char['character_name'], "discord_name": discord_member['username']},
        issue_hash,
    )
```

## Task 3: Integrity Checker

Compares all data sources and creates audit issues for mismatches.

```python
# sv_common/guild_sync/integrity_checker.py

"""
Integrity checker — detects mismatches, orphans, and data quality issues.

Run after each sync operation to detect NEW issues.
Only creates audit_issues for problems not already tracked.

Issue Types:
- orphan_wow: Character in guild but no Discord link
- orphan_discord: Discord member with guild role but no WoW character link
- role_mismatch: In-game rank doesn't match Discord role
- stale_character: Character hasn't logged in for >30 days
- no_guild_role: Discord member linked to a character but has no guild Discord role
- rank_change: Character's rank changed since last check
"""

import hashlib
import logging
from datetime import datetime, timezone, timedelta

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


async def run_integrity_check(pool: asyncpg.Pool) -> dict:
    """
    Run all integrity checks and create audit issues for new problems.
    
    Returns stats: {orphan_wow, orphan_discord, role_mismatch, stale, no_role, total_new}
    """
    stats = {
        "orphan_wow": 0,
        "orphan_discord": 0,
        "role_mismatch": 0,
        "stale": 0,
        "no_guild_role": 0,
        "total_new": 0,
    }
    
    async with pool.acquire() as conn:
        
        # --- Check 1: Orphaned WoW Characters ---
        # Characters in the guild with no person_id (and thus no Discord link)
        orphan_chars = await conn.fetch(
            """SELECT id, character_name, realm_slug, guild_rank_name, character_class
               FROM guild_identity.wow_characters
               WHERE person_id IS NULL AND removed_at IS NULL"""
        )
        
        for char in orphan_chars:
            h = make_issue_hash("orphan_wow", char['id'])
            created = await _upsert_issue(
                conn,
                issue_type="orphan_wow",
                severity="warning",
                wow_character_id=char['id'],
                summary=f"WoW character '{char['character_name']}' ({char['character_class']}, {char['guild_rank_name']}) has no Discord link",
                details={
                    "character_name": char['character_name'],
                    "realm": char['realm_slug'],
                    "rank": char['guild_rank_name'],
                    "class": char['character_class'],
                },
                issue_hash=h,
            )
            if created:
                stats["orphan_wow"] += 1
                stats["total_new"] += 1
        
        # --- Check 2: Orphaned Discord Members ---
        # Discord members with a guild role but no person_id (no WoW link)
        orphan_discord = await conn.fetch(
            """SELECT id, discord_id, username, display_name, highest_guild_role
               FROM guild_identity.discord_members
               WHERE person_id IS NULL
                 AND is_present = TRUE
                 AND highest_guild_role IS NOT NULL"""
        )
        
        for dm in orphan_discord:
            h = make_issue_hash("orphan_discord", dm['id'])
            display = dm['display_name'] or dm['username']
            created = await _upsert_issue(
                conn,
                issue_type="orphan_discord",
                severity="warning",
                discord_member_id=dm['id'],
                summary=f"Discord member '{display}' (role: {dm['highest_guild_role']}) has no WoW character linked",
                details={
                    "username": dm['username'],
                    "display_name": dm['display_name'],
                    "role": dm['highest_guild_role'],
                    "discord_id": dm['discord_id'],
                },
                issue_hash=h,
            )
            if created:
                stats["orphan_discord"] += 1
                stats["total_new"] += 1
        
        # --- Check 3: Role Mismatches ---
        # People where in-game rank doesn't match Discord role
        linked_persons = await conn.fetch(
            """SELECT DISTINCT p.id AS person_id, p.display_name,
                      wc.character_name, wc.guild_rank_name, wc.is_main,
                      dm.username, dm.display_name AS discord_display,
                      dm.highest_guild_role, dm.discord_id
               FROM guild_identity.persons p
               JOIN guild_identity.wow_characters wc ON wc.person_id = p.id
               JOIN guild_identity.discord_members dm ON dm.person_id = p.id
               WHERE wc.removed_at IS NULL AND dm.is_present = TRUE"""
        )
        
        # Group by person to find highest in-game rank
        person_data = {}
        for row in linked_persons:
            pid = row['person_id']
            if pid not in person_data:
                person_data[pid] = {
                    "display_name": row['display_name'],
                    "discord_username": row['username'],
                    "discord_display": row['discord_display'],
                    "discord_role": row['highest_guild_role'],
                    "discord_id": row['discord_id'],
                    "ranks": [],
                    "characters": [],
                }
            person_data[pid]["ranks"].append(row['guild_rank_name'])
            person_data[pid]["characters"].append(row['character_name'])
        
        for pid, data in person_data.items():
            # Find the highest in-game rank for this person
            rank_priority = ["Guild Leader", "Officer", "Veteran", "Member", "Initiate"]
            highest_rank = None
            for rp in rank_priority:
                if rp in data["ranks"]:
                    highest_rank = rp
                    break
            
            if not highest_rank:
                continue
            
            expected_discord_role = INGAME_TO_DISCORD_ROLE.get(highest_rank)
            actual_discord_role = data["discord_role"]
            
            if expected_discord_role and actual_discord_role:
                if expected_discord_role.lower() != actual_discord_role.lower():
                    h = make_issue_hash("role_mismatch", pid)
                    display = data['discord_display'] or data['discord_username']
                    created = await _upsert_issue(
                        conn,
                        issue_type="role_mismatch",
                        severity="warning",
                        person_id=pid,
                        summary=(
                            f"'{display}' is {highest_rank} in-game "
                            f"but {actual_discord_role} on Discord "
                            f"(expected: {expected_discord_role})"
                        ),
                        details={
                            "person_display": data['display_name'],
                            "ingame_rank": highest_rank,
                            "discord_role": actual_discord_role,
                            "expected_discord_role": expected_discord_role,
                            "characters": data['characters'],
                        },
                        issue_hash=h,
                    )
                    if created:
                        stats["role_mismatch"] += 1
                        stats["total_new"] += 1
            
            elif expected_discord_role and not actual_discord_role:
                # Has in-game rank but NO guild Discord role at all
                h = make_issue_hash("no_guild_role", pid)
                display = data['discord_display'] or data['discord_username']
                created = await _upsert_issue(
                    conn,
                    issue_type="no_guild_role",
                    severity="warning",
                    person_id=pid,
                    summary=(
                        f"'{display}' is {highest_rank} in-game "
                        f"but has NO guild role on Discord"
                    ),
                    details={
                        "person_display": data['display_name'],
                        "ingame_rank": highest_rank,
                        "expected_discord_role": expected_discord_role,
                        "characters": data['characters'],
                    },
                    issue_hash=h,
                )
                if created:
                    stats["no_guild_role"] += 1
                    stats["total_new"] += 1
        
        # --- Check 4: Stale Characters ---
        stale_threshold = datetime.now(timezone.utc) - timedelta(days=STALE_THRESHOLD_DAYS)
        stale_ts = int(stale_threshold.timestamp() * 1000)  # Blizzard uses milliseconds
        
        stale_chars = await conn.fetch(
            """SELECT id, character_name, guild_rank_name, last_login_timestamp
               FROM guild_identity.wow_characters
               WHERE removed_at IS NULL
                 AND last_login_timestamp IS NOT NULL
                 AND last_login_timestamp < $1""",
            stale_ts,
        )
        
        for char in stale_chars:
            h = make_issue_hash("stale_character", char['id'])
            last_login = datetime.fromtimestamp(
                char['last_login_timestamp'] / 1000, tz=timezone.utc
            )
            days_ago = (datetime.now(timezone.utc) - last_login).days
            
            created = await _upsert_issue(
                conn,
                issue_type="stale_character",
                severity="info",
                wow_character_id=char['id'],
                summary=f"'{char['character_name']}' ({char['guild_rank_name']}) hasn't logged in for {days_ago} days",
                details={
                    "character_name": char['character_name'],
                    "rank": char['guild_rank_name'],
                    "last_login": last_login.isoformat(),
                    "days_inactive": days_ago,
                },
                issue_hash=h,
            )
            if created:
                stats["stale"] += 1
                stats["total_new"] += 1
        
        # --- Auto-resolve issues that are no longer problems ---
        await _auto_resolve_fixed_issues(conn)
    
    logger.info(
        "Integrity check: %d orphan_wow, %d orphan_discord, %d role_mismatch, "
        "%d stale, %d no_role — %d total new issues",
        stats["orphan_wow"], stats["orphan_discord"], stats["role_mismatch"],
        stats["stale"], stats["no_guild_role"], stats["total_new"]
    )
    return stats


async def _upsert_issue(
    conn: asyncpg.Connection,
    issue_type: str,
    severity: str,
    summary: str,
    details: dict,
    issue_hash: str,
    wow_character_id: int = None,
    discord_member_id: int = None,
    person_id: int = None,
) -> bool:
    """
    Create an audit issue if it doesn't already exist (unresolved).
    Returns True if a NEW issue was created.
    """
    # Check if this exact issue already exists and is unresolved
    existing = await conn.fetchval(
        """SELECT id FROM guild_identity.audit_issues
           WHERE issue_hash = $1 AND resolved_at IS NULL""",
        issue_hash,
    )
    
    if existing:
        # Update last_detected timestamp
        await conn.execute(
            """UPDATE guild_identity.audit_issues SET
                last_detected = NOW(), summary = $2, details = $3
               WHERE id = $1""",
            existing, summary, details,
        )
        return False
    
    # Create new issue
    await conn.execute(
        """INSERT INTO guild_identity.audit_issues
           (issue_type, severity, wow_character_id, discord_member_id, person_id,
            summary, details, issue_hash)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        issue_type, severity, wow_character_id, discord_member_id, person_id,
        summary, details, issue_hash,
    )
    return True


async def _auto_resolve_fixed_issues(conn: asyncpg.Connection):
    """
    Auto-resolve issues where the underlying problem no longer exists.
    
    For example, if a character was an orphan but now has a person_id,
    resolve the orphan_wow issue.
    """
    now = datetime.now(timezone.utc)
    
    # Resolve orphan_wow issues where the character now has a person_id
    resolved = await conn.execute(
        """UPDATE guild_identity.audit_issues ai SET
            resolved_at = $1, resolved_by = 'auto', resolution_note = 'Character now linked'
           WHERE ai.issue_type = 'orphan_wow'
             AND ai.resolved_at IS NULL
             AND ai.wow_character_id IN (
                SELECT id FROM guild_identity.wow_characters WHERE person_id IS NOT NULL
             )""",
        now,
    )
    
    # Resolve orphan_discord issues where the member now has a person_id
    await conn.execute(
        """UPDATE guild_identity.audit_issues ai SET
            resolved_at = $1, resolved_by = 'auto', resolution_note = 'Discord member now linked'
           WHERE ai.issue_type = 'orphan_discord'
             AND ai.resolved_at IS NULL
             AND ai.discord_member_id IN (
                SELECT id FROM guild_identity.discord_members WHERE person_id IS NOT NULL
             )""",
        now,
    )
    
    # Resolve stale_character issues where the character has logged in recently
    stale_threshold = datetime.now(timezone.utc) - timedelta(days=STALE_THRESHOLD_DAYS)
    stale_ts = int(stale_threshold.timestamp() * 1000)
    
    await conn.execute(
        """UPDATE guild_identity.audit_issues ai SET
            resolved_at = $1, resolved_by = 'auto', resolution_note = 'Character now active'
           WHERE ai.issue_type = 'stale_character'
             AND ai.resolved_at IS NULL
             AND ai.wow_character_id IN (
                SELECT id FROM guild_identity.wow_characters
                WHERE last_login_timestamp >= $2
             )""",
        now, stale_ts,
    )
```

## Task 4: Discord Reporter

Posts formatted embed messages to #audit-channel when new issues are found.

```python
# sv_common/guild_sync/reporter.py

"""
Discord reporter — sends formatted integrity reports to #audit-channel.

Only reports NEW issues (not previously notified).
Groups issues by type for readability.
Uses Discord embeds with color-coding by severity.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import discord

logger = logging.getLogger(__name__)

# Colors for embed severity
SEVERITY_COLORS = {
    "critical": 0xFF0000,   # Red
    "warning": 0xFFA500,    # Orange
    "info": 0x3498DB,       # Blue
}

# Emoji per issue type
ISSUE_EMOJI = {
    "orphan_wow": "🎮",
    "orphan_discord": "💬",
    "role_mismatch": "⚠️",
    "no_guild_role": "🏷️",
    "stale_character": "💤",
    "auto_link_suggestion": "🔗",
    "rank_change": "📊",
}

# Human-friendly type names
ISSUE_TYPE_NAMES = {
    "orphan_wow": "WoW Characters Without Discord Link",
    "orphan_discord": "Discord Members Without WoW Link",
    "role_mismatch": "Role Mismatches (In-Game vs Discord)",
    "no_guild_role": "Missing Discord Guild Role",
    "stale_character": "Inactive Characters (30+ days)",
    "auto_link_suggestion": "Suggested Auto-Links (Needs Review)",
}


async def send_new_issues_report(
    pool: asyncpg.Pool,
    channel: discord.TextChannel,
    force_full: bool = False,
) -> int:
    """
    Send a report of all un-notified audit issues to the audit channel.
    
    Args:
        pool: Database connection pool
        channel: Discord channel to post to (#audit-channel)
        force_full: If True, report ALL unresolved issues (for initial audit)
    
    Returns: Number of issues reported
    """
    async with pool.acquire() as conn:
        
        if force_full:
            # Report all unresolved issues
            issues = await conn.fetch(
                """SELECT * FROM guild_identity.audit_issues
                   WHERE resolved_at IS NULL
                   ORDER BY severity DESC, issue_type, first_detected"""
            )
        else:
            # Only un-notified issues
            issues = await conn.fetch(
                """SELECT * FROM guild_identity.audit_issues
                   WHERE resolved_at IS NULL AND notified_at IS NULL
                   ORDER BY severity DESC, issue_type, first_detected"""
            )
        
        if not issues:
            logger.info("No new audit issues to report.")
            return 0
        
        # Group by issue type
        grouped = {}
        for issue in issues:
            itype = issue['issue_type']
            if itype not in grouped:
                grouped[itype] = []
            grouped[itype].append(issue)
        
        # Build embeds (Discord has a 6000 char limit per message, 10 embeds per message)
        embeds = []
        
        # Header embed
        header = discord.Embed(
            title="🔍 Guild Identity Audit Report",
            description=(
                f"**{len(issues)} {'total' if force_full else 'new'} issue(s) detected**\n"
                f"Run at: <t:{int(datetime.now(timezone.utc).timestamp())}:F>"
            ),
            color=0xD4A84B,  # PATT gold
        )
        embeds.append(header)
        
        # One embed per issue type
        for itype, type_issues in grouped.items():
            emoji = ISSUE_EMOJI.get(itype, "❓")
            type_name = ISSUE_TYPE_NAMES.get(itype, itype)
            
            # Determine color from highest severity in this group
            severities = [i['severity'] for i in type_issues]
            if "critical" in severities:
                color = SEVERITY_COLORS["critical"]
            elif "warning" in severities:
                color = SEVERITY_COLORS["warning"]
            else:
                color = SEVERITY_COLORS["info"]
            
            # Build the description (truncate if too many)
            lines = []
            for issue in type_issues[:20]:  # Cap at 20 per type
                lines.append(f"• {issue['summary']}")
            
            if len(type_issues) > 20:
                lines.append(f"*...and {len(type_issues) - 20} more*")
            
            description = "\n".join(lines)
            if len(description) > 4000:
                description = description[:3990] + "\n*...truncated*"
            
            embed = discord.Embed(
                title=f"{emoji} {type_name} ({len(type_issues)})",
                description=description,
                color=color,
            )
            embeds.append(embed)
        
        # Send in batches of 10 embeds (Discord limit)
        for i in range(0, len(embeds), 10):
            batch = embeds[i:i + 10]
            await channel.send(embeds=batch)
        
        # Mark all reported issues as notified
        issue_ids = [i['id'] for i in issues]
        now = datetime.now(timezone.utc)
        
        await conn.execute(
            """UPDATE guild_identity.audit_issues SET notified_at = $1
               WHERE id = ANY($2)""",
            now, issue_ids,
        )
        
        logger.info("Reported %d issues to #audit-channel", len(issues))
        return len(issues)


async def send_sync_summary(
    channel: discord.TextChannel,
    source: str,
    stats: dict,
    duration: float,
):
    """
    Send a brief sync summary to #audit-channel (only on notable changes).
    
    Only sends if there were new members, departures, or issues found.
    """
    notable = (
        stats.get("new", 0) > 0
        or stats.get("removed", 0) > 0
        or stats.get("departed", 0) > 0
        or stats.get("total_new", 0) > 0
    )
    
    if not notable:
        return
    
    embed = discord.Embed(
        title=f"📡 Sync Complete: {source}",
        color=0x2ECC71,  # Green
    )
    
    summary_parts = []
    if stats.get("found"):
        summary_parts.append(f"**{stats['found']}** total characters")
    if stats.get("new"):
        summary_parts.append(f"**{stats['new']}** new")
    if stats.get("removed") or stats.get("departed"):
        count = stats.get("removed", 0) + stats.get("departed", 0)
        summary_parts.append(f"**{count}** departed")
    if stats.get("total_new"):
        summary_parts.append(f"**{stats['total_new']}** new issues")
    
    embed.description = " | ".join(summary_parts)
    embed.set_footer(text=f"Completed in {duration:.1f}s")
    
    await channel.send(embed=embed)
```

## Task 5: Scheduler

Orchestrates all sync operations on a schedule.

```python
# sv_common/guild_sync/scheduler.py

"""
Scheduler for periodic guild sync operations.

Uses APScheduler to run:
- Blizzard API sync: every 6 hours (4x/day)
- Discord member sync: every 15 minutes
- Matching engine: after each Blizzard or addon sync
- Integrity check: after matching
- Report: after integrity check (only if new issues)

The Discord bot also handles real-time events (joins, leaves, role changes)
which don't need scheduling.
"""

import logging
import os
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

import asyncpg
import discord

from .blizzard_client import BlizzardClient
from .db_sync import sync_blizzard_roster
from .discord_sync import sync_discord_members
from .identity_engine import run_matching
from .integrity_checker import run_integrity_check
from .reporter import send_new_issues_report, send_sync_summary
from .sync_logger import SyncLogEntry

logger = logging.getLogger(__name__)


class GuildSyncScheduler:
    """Manages all scheduled guild sync tasks."""
    
    def __init__(
        self,
        db_pool: asyncpg.Pool,
        discord_bot: discord.Client,
        audit_channel_id: int,
    ):
        self.db_pool = db_pool
        self.discord_bot = discord_bot
        self.audit_channel_id = audit_channel_id
        
        self.blizzard_client = BlizzardClient(
            client_id=os.environ["BLIZZARD_CLIENT_ID"],
            client_secret=os.environ["BLIZZARD_CLIENT_SECRET"],
            realm_slug=os.environ.get("PATT_GUILD_REALM_SLUG", "senjin"),
            guild_slug=os.environ.get("PATT_GUILD_NAME_SLUG", "pull-all-the-things"),
        )
        
        self.scheduler = AsyncIOScheduler()
    
    async def start(self):
        """Initialize clients and start the scheduler."""
        await self.blizzard_client.initialize()
        
        # Blizzard sync: 4x/day (every 6 hours, offset to avoid midnight)
        self.scheduler.add_job(
            self.run_blizzard_sync,
            CronTrigger(hour="1,7,13,19", minute=0),
            id="blizzard_sync",
            name="Blizzard API Guild Roster Sync",
            misfire_grace_time=3600,
        )
        
        # Discord member sync: every 15 minutes
        self.scheduler.add_job(
            self.run_discord_sync,
            IntervalTrigger(minutes=15),
            id="discord_sync",
            name="Discord Member Sync",
            misfire_grace_time=300,
        )
        
        self.scheduler.start()
        logger.info("Guild sync scheduler started")
    
    async def stop(self):
        """Shut down scheduler and clients."""
        self.scheduler.shutdown()
        await self.blizzard_client.close()
    
    def _get_audit_channel(self) -> discord.TextChannel:
        """Get the #audit-channel from the bot."""
        return self.discord_bot.get_channel(self.audit_channel_id)
    
    async def run_blizzard_sync(self):
        """Full Blizzard API sync pipeline."""
        channel = self._get_audit_channel()
        
        async with SyncLogEntry(self.db_pool, "blizzard_api") as log:
            start = time.time()
            
            # Step 1: Fetch and store roster
            characters = await self.blizzard_client.sync_full_roster()
            sync_stats = await sync_blizzard_roster(self.db_pool, characters)
            log.stats = sync_stats
            
            # Step 2: Run matching engine
            match_stats = await run_matching(self.db_pool)
            
            # Step 3: Run integrity check
            integrity_stats = await run_integrity_check(self.db_pool)
            
            # Step 4: Report new issues
            if channel and integrity_stats.get("total_new", 0) > 0:
                await send_new_issues_report(self.db_pool, channel)
            
            duration = time.time() - start
            
            # Send sync summary if notable
            if channel:
                combined_stats = {**sync_stats, **integrity_stats}
                await send_sync_summary(channel, "Blizzard API", combined_stats, duration)
    
    async def run_discord_sync(self):
        """Discord member sync pipeline."""
        async with SyncLogEntry(self.db_pool, "discord_bot") as log:
            # Get the guild object
            guild = None
            for g in self.discord_bot.guilds:
                # Find the PATT guild — use the one that has our audit channel
                if self.discord_bot.get_channel(self.audit_channel_id) in g.channels:
                    guild = g
                    break
            
            if not guild:
                logger.error("Could not find Discord guild with audit channel")
                return
            
            sync_stats = await sync_discord_members(self.db_pool, guild)
            log.stats = sync_stats
    
    async def run_addon_sync(self, addon_data: list[dict]):
        """Process addon upload and run downstream pipeline."""
        channel = self._get_audit_channel()
        
        async with SyncLogEntry(self.db_pool, "addon_upload") as log:
            start = time.time()
            
            from .db_sync import sync_addon_data
            sync_stats = await sync_addon_data(self.db_pool, addon_data)
            log.stats = {"found": sync_stats["processed"], "updated": sync_stats["updated"]}
            
            # Re-run matching (addon notes might reveal new links)
            match_stats = await run_matching(self.db_pool)
            
            # Re-run integrity check
            integrity_stats = await run_integrity_check(self.db_pool)
            
            duration = time.time() - start
            
            if channel and integrity_stats.get("total_new", 0) > 0:
                await send_new_issues_report(self.db_pool, channel)
            
            if channel:
                combined_stats = {**sync_stats, **integrity_stats}
                await send_sync_summary(channel, "WoW Addon Upload", combined_stats, duration)
    
    async def trigger_full_report(self):
        """Manual trigger: send a full report of ALL unresolved issues."""
        channel = self._get_audit_channel()
        if channel:
            await send_new_issues_report(self.db_pool, channel, force_full=True)
```

## Task 6: API Routes

```python
# sv_common/guild_sync/api/routes.py

"""
FastAPI routes for the guild identity & sync system.

Mount at /api/guild-sync/ and /api/identity/ on the main FastAPI app.
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

# These would be imported from your sv_common app context
# from sv_common.deps import get_db_pool, get_sync_scheduler

guild_sync_router = APIRouter(prefix="/api/guild-sync", tags=["Guild Sync"])
identity_router = APIRouter(prefix="/api/identity", tags=["Identity"])


# --- Request/Response Models ---

class AddonUploadRequest(BaseModel):
    characters: list[dict]
    addon_version: str = "1.0"
    uploaded_by: str = "unknown"


class ManualLinkRequest(BaseModel):
    wow_character_id: int
    discord_member_id: int
    confirmed_by: str = "manual"


class LinkConfirmRequest(BaseModel):
    link_id: int
    confirmed_by: str = "manual"


# --- Auth helper ---

ADDON_API_KEY = os.environ.get("PATT_API_KEY", "")


async def verify_addon_key(x_api_key: str = Header(None)):
    """Simple API key auth for addon uploads."""
    if not ADDON_API_KEY:
        raise HTTPException(500, "PATT_API_KEY not configured")
    if x_api_key != ADDON_API_KEY:
        raise HTTPException(401, "Invalid API key")


# --- Guild Sync Routes ---

@guild_sync_router.post("/blizzard/trigger")
async def trigger_blizzard_sync(
    # db_pool = Depends(get_db_pool),
    # scheduler = Depends(get_sync_scheduler),
):
    """Manually trigger a full Blizzard API sync."""
    # await scheduler.run_blizzard_sync()
    return {"status": "sync_triggered"}


@guild_sync_router.post("/addon-upload", dependencies=[Depends(verify_addon_key)])
async def addon_upload(
    payload: AddonUploadRequest,
    # db_pool = Depends(get_db_pool),
    # scheduler = Depends(get_sync_scheduler),
):
    """
    Receive guild roster data from the WoW addon companion app.
    
    The companion app watches SavedVariables and POSTs here
    when new data is detected.
    """
    if not payload.characters:
        raise HTTPException(400, "No character data provided")
    
    # await scheduler.run_addon_sync(payload.characters)
    
    return {
        "status": "processed",
        "characters_received": len(payload.characters),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@guild_sync_router.get("/addon-upload/status")
async def addon_upload_status(
    # db_pool = Depends(get_db_pool),
):
    """Get the timestamp of the last addon upload."""
    # last = await db_pool.fetchrow(
    #     "SELECT completed_at FROM guild_identity.sync_log "
    #     "WHERE source = 'addon_upload' AND status = 'success' "
    #     "ORDER BY completed_at DESC LIMIT 1"
    # )
    return {"last_upload": None}  # Replace with actual query


@guild_sync_router.get("/status")
async def sync_status(
    # db_pool = Depends(get_db_pool),
):
    """Overall sync status — last run times for each source."""
    # Query sync_log for latest successful run per source
    return {
        "blizzard_api": {"last_sync": None},
        "discord_bot": {"last_sync": None},
        "addon_upload": {"last_sync": None},
    }


@guild_sync_router.post("/report/trigger")
async def trigger_report(
    # scheduler = Depends(get_sync_scheduler),
):
    """Force a full integrity report to #audit-channel."""
    # await scheduler.trigger_full_report()
    return {"status": "report_triggered"}


# --- Identity Routes ---

@identity_router.get("/persons")
async def list_persons(
    # db_pool = Depends(get_db_pool),
):
    """List all known persons with their linked characters and Discord accounts."""
    # Query persons with JOINs to wow_characters and discord_members
    return {"persons": []}


@identity_router.get("/orphans/wow")
async def orphan_wow_characters(
    # db_pool = Depends(get_db_pool),
):
    """WoW characters in the guild with no Discord link."""
    return {"orphans": []}


@identity_router.get("/orphans/discord")
async def orphan_discord_members(
    # db_pool = Depends(get_db_pool),
):
    """Discord members with guild roles but no WoW character link."""
    return {"orphans": []}


@identity_router.get("/mismatches")
async def role_mismatches(
    # db_pool = Depends(get_db_pool),
):
    """Role mismatches between in-game rank and Discord role."""
    return {"mismatches": []}


@identity_router.post("/link")
async def create_manual_link(
    req: ManualLinkRequest,
    # db_pool = Depends(get_db_pool),
):
    """Manually link a WoW character to a Discord member."""
    # Create person if needed, then create identity_links
    return {"status": "linked"}


@identity_router.post("/confirm")
async def confirm_link(
    req: LinkConfirmRequest,
    # db_pool = Depends(get_db_pool),
):
    """Confirm an auto-suggested link."""
    return {"status": "confirmed"}


@identity_router.delete("/link/{link_id}")
async def remove_link(
    link_id: int,
    # db_pool = Depends(get_db_pool),
):
    """Remove an incorrect identity link."""
    return {"status": "removed"}
```

## Testing Requirements for Phase 2.5B

1. **Discord sync tests:**
   - Test get_highest_guild_role with various role combinations
   - Test sync creates new members, updates existing, marks departed
   - Test real-time event handlers (join, leave, role change)

2. **Identity engine tests:**
   - Test exact name matching (case-insensitive)
   - Test guild note parsing with various patterns ("Discord: name", "DC: name", "@name")
   - Test officer note parsing
   - Test fuzzy matching scores
   - Test person creation and linking
   - Test that existing links are respected (don't double-link)
   - Test alt grouping via notes ("alt of X")

3. **Integrity checker tests:**
   - Test orphan_wow detection
   - Test orphan_discord detection  
   - Test role mismatch detection (GM vs Guild Leader, etc.)
   - Test stale character detection
   - Test auto-resolution when problems are fixed
   - Test deduplication (same issue doesn't create duplicate entries)

4. **Reporter tests:**
   - Test embed formatting
   - Test grouping by issue type
   - Test truncation for large reports
   - Test notified_at gets set after reporting
   - Test force_full mode

5. **Scheduler tests:**
   - Test pipeline orchestration (sync → match → check → report)
   - Test that only new issues trigger reports
