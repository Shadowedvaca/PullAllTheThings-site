"""
Officer slash commands for managing onboarding sessions.

Commands:
  /onboard-status  — list all pending sessions
  /onboard-resolve — manually provision a member
  /onboard-dismiss — close a session without provisioning
  /onboard-retry   — re-run verification for one member
"""

import logging
from typing import Optional

import asyncpg
import discord
from discord import app_commands

from .provisioner import AutoProvisioner
from .deadline_checker import OnboardingDeadlineChecker

logger = logging.getLogger(__name__)

PATT_GOLD = 0xD4A84B


def register_onboarding_commands(
    tree: app_commands.CommandTree,
    db_pool: asyncpg.Pool,
    audit_channel_id: Optional[int] = None,
):
    """Register /onboard-* slash commands on the given command tree."""

    async def _require_officer(interaction: discord.Interaction) -> bool:
        """Return True if the caller has the Officer (or higher) role."""
        officer_role_names = {"Officer", "Guild Leader"}
        member_roles = {r.name for r in interaction.user.roles}
        if not member_roles.intersection(officer_role_names):
            await interaction.response.send_message(
                "❌ This command is for officers only.", ephemeral=True
            )
            return False
        return True

    @tree.command(name="onboard-status", description="List pending onboarding sessions")
    async def onboard_status(interaction: discord.Interaction):
        if not await _require_officer(interaction):
            return

        async with db_pool.acquire() as conn:
            sessions = await conn.fetch(
                """SELECT id, discord_id, reported_main_name, state,
                          verification_attempts, created_at, deadline_at, escalated_at,
                          verified_player_id
                   FROM guild_identity.onboarding_sessions
                   WHERE state NOT IN ('provisioned', 'manually_resolved', 'declined')
                   ORDER BY created_at ASC
                   LIMIT 20""",
            )

        if not sessions:
            await interaction.response.send_message(
                "✅ No pending onboarding sessions.", ephemeral=True
            )
            return

        lines = []
        for s in sessions:
            tag = f"<@{s['discord_id']}>"
            main = s["reported_main_name"] or "*(not reported)*"
            state = s["state"]
            attempts = s["verification_attempts"]
            overdue = "⚠️ OVERDUE" if s["escalated_at"] else ""
            lines.append(f"• {tag} — main: **{main}** | {state} | {attempts} attempts {overdue}")

        embed = discord.Embed(
            title=f"Pending Onboarding Sessions ({len(sessions)})",
            description="\n".join(lines),
            color=PATT_GOLD,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="onboard-resolve", description="Manually provision a pending member")
    @app_commands.describe(member="The Discord member to resolve")
    async def onboard_resolve(interaction: discord.Interaction, member: discord.Member):
        if not await _require_officer(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        async with db_pool.acquire() as conn:
            session = await conn.fetchrow(
                """SELECT id, state, discord_id
                   FROM guild_identity.onboarding_sessions
                   WHERE discord_id = $1
                     AND state NOT IN ('provisioned', 'manually_resolved', 'declined')""",
                str(member.id),
            )
            if not session:
                await interaction.followup.send(
                    f"No active onboarding session found for {member.mention}.",
                    ephemeral=True,
                )
                return

            # Look up discord_users record
            du_row = await conn.fetchrow(
                "SELECT id FROM guild_identity.discord_users WHERE discord_id = $1",
                str(member.id),
            )
            if not du_row:
                await interaction.followup.send(
                    "No discord_users record found. Unable to provision automatically.",
                    ephemeral=True,
                )
                return

            du_id = du_row["id"]

            # Check if a player exists for this discord user
            player_row = await conn.fetchrow(
                "SELECT id FROM guild_identity.players WHERE discord_user_id = $1",
                du_id,
            )
            if not player_row:
                # Create a bare player record
                player_id = await conn.fetchval(
                    """INSERT INTO guild_identity.players (display_name, discord_user_id)
                       VALUES ($1, $2) RETURNING id""",
                    member.display_name, du_id,
                )
            else:
                player_id = player_row["id"]

            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'verified',
                    verified_at = NOW(),
                    verified_player_id = $2,
                    updated_at = NOW()
                   WHERE id = $1""",
                session["id"], player_id,
            )

        # Run provisioner (with DM)
        provisioner = AutoProvisioner(db_pool, interaction.client)
        result = await provisioner.provision_player(
            player_id,
            silent=False,
            onboarding_session_id=session["id"],
        )

        async with db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'manually_resolved',
                    website_invite_sent = $2,
                    website_invite_code = $3,
                    roster_entries_created = $4,
                    discord_role_assigned = $5,
                    completed_at = NOW(),
                    updated_at = NOW()
                   WHERE id = $1""",
                session["id"],
                result["invite_code"] is not None,
                result["invite_code"],
                result["characters_linked"] > 0,
                result["discord_role_assigned"],
            )

        await interaction.followup.send(
            f"✅ {member.mention} has been provisioned.\n"
            f"• Characters linked: {result['characters_linked']}\n"
            f"• Discord role assigned: {'Yes' if result['discord_role_assigned'] else 'No'}\n"
            f"• Invite code: `{result['invite_code'] or 'N/A'}`",
            ephemeral=True,
        )
        logger.info(
            "Officer manually resolved onboarding for discord_id=%s player=%d by %s",
            member.id, player_id, interaction.user.name,
        )

    @tree.command(
        name="onboard-dismiss",
        description="Close an onboarding session without provisioning",
    )
    @app_commands.describe(member="The Discord member to dismiss")
    async def onboard_dismiss(interaction: discord.Interaction, member: discord.Member):
        if not await _require_officer(interaction):
            return

        async with db_pool.acquire() as conn:
            updated = await conn.fetchval(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'manually_resolved',
                    completed_at = NOW(),
                    updated_at = NOW()
                   WHERE discord_id = $1
                     AND state NOT IN ('provisioned', 'manually_resolved', 'declined')
                   RETURNING id""",
                str(member.id),
            )

        if updated:
            await interaction.response.send_message(
                f"✅ Dismissed onboarding session for {member.mention} (session #{updated}).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"No active session found for {member.mention}.", ephemeral=True
            )

    @tree.command(
        name="onboard-retry",
        description="Re-run roster verification for a pending member",
    )
    @app_commands.describe(member="The Discord member to retry")
    async def onboard_retry(interaction: discord.Interaction, member: discord.Member):
        if not await _require_officer(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        async with db_pool.acquire() as conn:
            session = await conn.fetchrow(
                """SELECT id FROM guild_identity.onboarding_sessions
                   WHERE discord_id = $1 AND state = 'pending_verification'""",
                str(member.id),
            )
            if not session:
                await interaction.followup.send(
                    f"No pending_verification session found for {member.mention}.",
                    ephemeral=True,
                )
                return

        checker = OnboardingDeadlineChecker(db_pool, interaction.client, audit_channel_id)
        async with db_pool.acquire() as conn:
            reported_main = await conn.fetchval(
                "SELECT reported_main_name FROM guild_identity.onboarding_sessions WHERE id = $1",
                session["id"],
            )
        verified = await checker._retry_verification(session["id"], reported_main)

        if verified:
            await interaction.followup.send(
                f"✅ {member.mention} was verified and provisioned successfully.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"⚠️ Still couldn't match {member.mention} in the roster. "
                "The verification attempt counter was incremented.",
                ephemeral=True,
            )
