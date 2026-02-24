"""
Discord reporter — sends formatted integrity reports to #audit-channel.

Only reports NEW issues (not previously notified).
Groups issues by type for readability.
Uses Discord embeds with color-coding by severity.
"""

import logging
from datetime import datetime, timezone

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
                   ORDER BY severity DESC, issue_type, created_at"""
            )
        else:
            # Only un-notified issues
            issues = await conn.fetch(
                """SELECT * FROM guild_identity.audit_issues
                   WHERE resolved_at IS NULL AND notified_at IS NULL
                   ORDER BY severity DESC, issue_type, created_at"""
            )

        if not issues:
            logger.info("No new audit issues to report.")
            return 0

        # Group by issue type
        grouped = {}
        for issue in issues:
            itype = issue["issue_type"]
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
            severities = [i["severity"] for i in type_issues]
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
        issue_ids = [i["id"] for i in issues]
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
