"""
Mitigation functions for data quality rules.

Each function takes (pool, issue_row) and returns True if the issue was resolved.
Successful mitigations mark the audit_issue as resolved with a descriptive resolved_by.

Also contains run_auto_mitigations() which processes all pending auto-mitigate issues.
"""

import logging
from datetime import datetime, timezone

import asyncpg

from .identity_engine import _extract_note_key, _find_discord_for_key, _note_still_matches_player
from .integrity_checker import upsert_note_alias

logger = logging.getLogger(__name__)


async def mitigate_note_mismatch(pool: asyncpg.Pool, issue_row: dict) -> bool:
    """
    Unlink character from wrong player; re-link to correct one if found.

    1. Check if the note key still matches the current player (false alarm → resolve).
    2. Unlink character from its current player.
    3. Try to find the correct player by note key via Discord username matching.
    4. If found: create new player_characters link.
    5. Resolve the audit issue regardless (orphan_wow will be raised if no new player found).
    """
    char_id = issue_row.get("wow_character_id")
    if not char_id:
        logger.warning("note_mismatch issue %d has no wow_character_id", issue_row["id"])
        return False

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
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

        if not row:
            logger.warning("Character %d not found for note_mismatch mitigation", char_id)
            return False

        note_key = _extract_note_key(dict(row))
        old_player_id = row["player_id"]

        # Check if the note key still matches the current player (false alarm)
        if old_player_id and note_key:
            still_matches = _note_still_matches_player(
                note_key,
                row["player_display_name"],
                row["discord_username"],
                row["discord_display_name"],
            )
            if still_matches:
                logger.info(
                    "note_mismatch '%s': note key '%s' still matches player '%s' — false alarm",
                    row["character_name"], note_key, row["player_display_name"],
                )
                await conn.execute(
                    """UPDATE guild_identity.audit_issues SET
                        resolved_at = $1, resolved_by = 'mitigate_note_mismatch:no_change'
                       WHERE id = $2""",
                    now, issue_row["id"],
                )
                return True

        # Unlink from current player
        if old_player_id:
            async with conn.transaction():
                await conn.execute(
                    """UPDATE guild_identity.players
                       SET main_character_id = NULL
                       WHERE id = $1 AND main_character_id = $2""",
                    old_player_id, char_id,
                )
                await conn.execute(
                    """UPDATE guild_identity.players
                       SET offspec_character_id = NULL
                       WHERE id = $1 AND offspec_character_id = $2""",
                    old_player_id, char_id,
                )
                await conn.execute(
                    "DELETE FROM guild_identity.player_characters WHERE character_id = $1",
                    char_id,
                )
            logger.info(
                "note_mismatch: unlinked '%s' from player '%s' (note key: '%s')",
                row["character_name"], row["player_display_name"], note_key,
            )

        # Try to find the correct player by note key
        new_player_id = None
        if note_key:
            all_discord = await conn.fetch(
                """SELECT du.id, du.discord_id, du.username, du.display_name,
                          p.id AS player_id
                   FROM guild_identity.discord_users du
                   JOIN guild_identity.players p ON p.discord_user_id = du.id
                   WHERE du.is_present = TRUE"""
            )
            discord_match = _find_discord_for_key(note_key, [dict(r) for r in all_discord])
            if discord_match and discord_match.get("player_id"):
                new_player_id = discord_match["player_id"]
                await conn.execute(
                    """INSERT INTO guild_identity.player_characters (player_id, character_id)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    new_player_id, char_id,
                )
                await upsert_note_alias(conn, new_player_id, note_key, source="mitigation")
                logger.info(
                    "note_mismatch: re-linked '%s' to player %d via note key '%s'",
                    row["character_name"], new_player_id, note_key,
                )

        resolved_by = (
            f"mitigate_note_mismatch:relinked_to_{new_player_id}"
            if new_player_id
            else "mitigate_note_mismatch:unlinked_orphan"
        )
        await conn.execute(
            """UPDATE guild_identity.audit_issues SET
                resolved_at = $1, resolved_by = $2
               WHERE id = $3""",
            now, resolved_by, issue_row["id"],
        )
        return True


async def mitigate_orphan_wow(pool: asyncpg.Pool, issue_row: dict) -> bool:
    """
    Attempt note-key match to existing player. Returns True if linked.

    Looks at the character's guild_note and tries to find a player whose
    Discord username or display_name matches the note key.
    """
    char_id = issue_row.get("wow_character_id")
    if not char_id:
        return False

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        char_row = await conn.fetchrow(
            """SELECT id, character_name, guild_note
               FROM guild_identity.wow_characters
               WHERE id = $1 AND removed_at IS NULL""",
            char_id,
        )
        if not char_row:
            return False

        # Already linked — resolve as stale issue
        existing = await conn.fetchval(
            "SELECT player_id FROM guild_identity.player_characters WHERE character_id = $1",
            char_id,
        )
        if existing:
            await conn.execute(
                """UPDATE guild_identity.audit_issues SET
                    resolved_at = $1, resolved_by = 'mitigate_orphan_wow:already_linked'
                   WHERE id = $2""",
                now, issue_row["id"],
            )
            return True

        note_key = _extract_note_key(dict(char_row))
        if not note_key:
            logger.info(
                "orphan_wow: '%s' has no guild note — cannot auto-match",
                char_row["character_name"],
            )
            return False

        # Find a Discord user who has a linked player
        all_discord = await conn.fetch(
            """SELECT du.id, du.discord_id, du.username, du.display_name,
                      p.id AS player_id
               FROM guild_identity.discord_users du
               JOIN guild_identity.players p ON p.discord_user_id = du.id
               WHERE du.is_present = TRUE"""
        )
        discord_match = _find_discord_for_key(note_key, [dict(r) for r in all_discord])
        if not discord_match or not discord_match.get("player_id"):
            logger.info(
                "orphan_wow: '%s' note key '%s' — no matching player found",
                char_row["character_name"], note_key,
            )
            return False

        player_id = discord_match["player_id"]
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO guild_identity.player_characters (player_id, character_id)
                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                player_id, char_id,
            )
            await conn.execute(
                """UPDATE guild_identity.audit_issues SET
                    resolved_at = $1, resolved_by = $2
                   WHERE id = $3""",
                now, f"mitigate_orphan_wow:linked_to_{player_id}", issue_row["id"],
            )
        await upsert_note_alias(conn, player_id, note_key, source="note_match")

        logger.info(
            "orphan_wow: linked '%s' to player %d via note key '%s'",
            char_row["character_name"], player_id, note_key,
        )
        return True


async def mitigate_orphan_discord(pool: asyncpg.Pool, issue_row: dict) -> bool:
    """
    Attempt to match Discord username/display_name to unlinked character note keys.

    If matching characters are found, creates a player and links them.
    """
    discord_member_id = issue_row.get("discord_member_id")
    if not discord_member_id:
        return False

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        du_row = await conn.fetchrow(
            """SELECT id, discord_id, username, display_name
               FROM guild_identity.discord_users WHERE id = $1""",
            discord_member_id,
        )
        if not du_row:
            return False

        # Already linked — resolve as stale issue
        existing = await conn.fetchval(
            "SELECT id FROM guild_identity.players WHERE discord_user_id = $1",
            discord_member_id,
        )
        if existing:
            await conn.execute(
                """UPDATE guild_identity.audit_issues SET
                    resolved_at = $1, resolved_by = 'mitigate_orphan_discord:already_linked'
                   WHERE id = $2""",
                now, issue_row["id"],
            )
            return True

        # Find unlinked characters whose note key matches this Discord user
        unlinked_chars = await conn.fetch(
            """SELECT id, character_name, guild_note, guild_rank_id
               FROM guild_identity.wow_characters
               WHERE removed_at IS NULL
                 AND id NOT IN (SELECT character_id FROM guild_identity.player_characters)"""
        )

        discord_username = (du_row["username"] or "").lower().strip()
        discord_display = (du_row["display_name"] or "").lower().strip()

        matched_chars = []
        for char in unlinked_chars:
            note_key = _extract_note_key(dict(char))
            if not note_key:
                continue
            if _note_still_matches_player(
                note_key,
                discord_display,
                discord_username,
                discord_display,
            ):
                matched_chars.append(char)

        if not matched_chars:
            logger.info(
                "orphan_discord: Discord '%s' — no matching unlinked characters found",
                du_row["username"],
            )
            return False

        display = du_row["display_name"] or du_row["username"]
        char_rank_ids = [c["guild_rank_id"] for c in matched_chars if c.get("guild_rank_id")]
        best_rank_id = None
        if char_rank_ids:
            best_rank_id = await conn.fetchval(
                """SELECT id FROM common.guild_ranks
                   WHERE id = ANY($1::int[])
                   ORDER BY level DESC LIMIT 1""",
                char_rank_ids,
            )

        async with conn.transaction():
            player_id = await conn.fetchval(
                """INSERT INTO guild_identity.players
                       (display_name, discord_user_id, guild_rank_id, guild_rank_source)
                   VALUES ($1, $2, $3, $4) RETURNING id""",
                display, discord_member_id, best_rank_id,
                "wow_character" if best_rank_id else None,
            )
            for char in matched_chars:
                await conn.execute(
                    """INSERT INTO guild_identity.player_characters (player_id, character_id)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    player_id, char["id"],
                )
            await conn.execute(
                """UPDATE guild_identity.audit_issues SET
                    resolved_at = $1, resolved_by = $2
                   WHERE id = $3""",
                now, f"mitigate_orphan_discord:created_player_{player_id}", issue_row["id"],
            )

        # Record note aliases for all linked characters
        for char in matched_chars:
            char_note_key = _extract_note_key(dict(char))
            if char_note_key:
                await upsert_note_alias(conn, player_id, char_note_key, source="note_match")

        logger.info(
            "orphan_discord: created player '%s' (id=%d) for Discord '%s', linked %d char(s)",
            display, player_id, du_row["username"], len(matched_chars),
        )
        return True


async def mitigate_role_mismatch(pool: asyncpg.Pool, issue_row: dict) -> bool:
    """
    Update Discord role to match in-game rank via bot.

    Requires the Discord bot to be running and the expected_discord_role in details.
    """
    import discord as discord_lib
    from sv_common.discord.bot import get_bot
    from sv_common.guild_sync.discord_sync import DISCORD_TO_INGAME_RANK

    discord_member_id = issue_row.get("discord_member_id")
    details = issue_row.get("details") or {}
    expected_role = details.get("expected_discord_role")

    if not discord_member_id or not expected_role:
        logger.warning(
            "role_mismatch issue %d missing discord_member_id or expected_discord_role",
            issue_row["id"],
        )
        return False

    bot = get_bot()
    if not bot or bot.is_closed():
        logger.warning("Discord bot not running — cannot mitigate role_mismatch")
        return False

    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        du_row = await conn.fetchrow(
            "SELECT discord_id FROM guild_identity.discord_users WHERE id = $1",
            discord_member_id,
        )
        if not du_row:
            return False

        try:
            from patt.config import get_settings
            settings = get_settings()
            if not settings.discord_guild_id:
                return False

            guild = bot.get_guild(int(settings.discord_guild_id))
            if not guild:
                return False

            member = guild.get_member(int(du_row["discord_id"]))
            if not member:
                return False

            target_role = discord_lib.utils.get(guild.roles, name=expected_role)
            if not target_role:
                logger.warning("Discord role '%s' not found in guild", expected_role)
                return False

            # Remove existing guild rank roles and add the correct one
            old_rank_roles = [r for r in member.roles if r.name in DISCORD_TO_INGAME_RANK]
            await member.remove_roles(*old_rank_roles, reason="Data Quality: role_mismatch fix")
            await member.add_roles(target_role, reason="Data Quality: role_mismatch fix")

            await conn.execute(
                """UPDATE guild_identity.audit_issues SET
                    resolved_at = $1, resolved_by = 'mitigate_role_mismatch:discord_updated'
                   WHERE id = $2""",
                now, issue_row["id"],
            )
            logger.info(
                "role_mismatch: updated Discord role for user %s to '%s'",
                du_row["discord_id"], expected_role,
            )
            return True

        except Exception as exc:
            logger.error(
                "role_mismatch mitigation failed for discord_member_id %d: %s",
                discord_member_id, exc,
            )
            return False


async def run_auto_mitigations(pool: asyncpg.Pool) -> dict:
    """
    Process all unresolved audit issues where the rule has auto_mitigate=True.

    Returns stats: {processed, resolved, failed}
    """
    from .rules import RULES

    auto_rules = {k: v for k, v in RULES.items() if v.auto_mitigate and v.mitigate_fn}
    if not auto_rules:
        return {"processed": 0, "resolved": 0, "failed": 0}

    stats = {"processed": 0, "resolved": 0, "failed": 0}

    async with pool.acquire() as conn:
        issues = await conn.fetch(
            """SELECT id, issue_type, severity, wow_character_id, discord_member_id,
                      summary, details, issue_hash, created_at, resolved_at, resolved_by
               FROM guild_identity.audit_issues
               WHERE resolved_at IS NULL
                 AND issue_type = ANY($1::text[])
               ORDER BY created_at""",
            list(auto_rules.keys()),
        )

    for issue in issues:
        issue_type = issue["issue_type"]
        rule = auto_rules.get(issue_type)
        if not rule:
            continue

        stats["processed"] += 1
        try:
            resolved = await rule.mitigate_fn(pool, dict(issue))
            if resolved:
                stats["resolved"] += 1
                logger.info("Auto-mitigated %s issue %d", issue_type, issue["id"])
            else:
                stats["failed"] += 1
        except Exception as exc:
            stats["failed"] += 1
            logger.error(
                "Auto-mitigation error for %s issue %d: %s",
                issue_type, issue["id"], exc,
            )

    if stats["processed"]:
        logger.info(
            "Auto-mitigations complete: %d processed, %d resolved, %d failed",
            stats["processed"], stats["resolved"], stats["failed"],
        )
    return stats
