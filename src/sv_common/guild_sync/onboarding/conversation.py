"""
Discord DM onboarding conversation for new guild members.

Flow:
  1. Welcome message — "Have you joined the guild in WoW?"
  2a. YES → "What's your main character's name?" → "Any alts?"
  2b. NO  → "Reply when you do, or type /pattsync"
  3. Store self-reported data → attempt immediate verification
  4. If verified → auto-provision → welcome DM
  5. If not → schedule 24h deadline → escalate if unresolved

Design:
  - Non-blocking: timeouts save state; scheduler follows up later
  - Officers can check /onboard-status for pending sessions
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
import discord

logger = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 300   # 5 min per question before saving state
DEADLINE_HOURS   = 24    # hours before #audit-channel escalation
PATT_GOLD        = 0xD4A84B


class OnboardingConversation:
    """Manages a single new member's onboarding DM conversation."""

    def __init__(
        self,
        bot: discord.Client,
        member: discord.Member,
        db_pool: asyncpg.Pool,
    ):
        self.bot      = bot
        self.member   = member
        self.db_pool  = db_pool
        self.session_id: Optional[int] = None

    async def start(self):
        """Begin onboarding. Called from on_member_join."""
        async with self.db_pool.acquire() as conn:
            # Bail if an active session already exists
            existing = await conn.fetchrow(
                """SELECT id, state FROM guild_identity.onboarding_sessions
                   WHERE discord_id = $1
                     AND state NOT IN ('provisioned', 'manually_resolved', 'declined')""",
                str(self.member.id),
            )
            if existing:
                self.session_id = existing["id"]
                return

            # Ensure discord_member row exists
            dm_id = await conn.fetchval(
                "SELECT id FROM guild_identity.discord_members WHERE discord_id = $1",
                str(self.member.id),
            )
            if not dm_id:
                dm_id = await conn.fetchval(
                    """INSERT INTO guild_identity.discord_members
                       (discord_id, username, display_name, is_present, joined_server_at)
                       VALUES ($1, $2, $3, TRUE, $4)
                       ON CONFLICT (discord_id) DO UPDATE SET is_present = TRUE
                       RETURNING id""",
                    str(self.member.id),
                    self.member.name,
                    self.member.nick or self.member.display_name,
                    self.member.joined_at,
                )

            self.session_id = await conn.fetchval(
                """INSERT INTO guild_identity.onboarding_sessions
                   (discord_member_id, discord_id, state)
                   VALUES ($1, $2, 'awaiting_dm')
                   ON CONFLICT (discord_id) DO UPDATE SET state = 'awaiting_dm', updated_at = NOW()
                   RETURNING id""",
                dm_id,
                str(self.member.id),
            )

        try:
            await self._send_welcome()
        except discord.Forbidden:
            logger.warning("Cannot DM %s — DMs closed", self.member.name)
            await self._set_state("declined")

    # ── Conversation steps ────────────────────────────────────────────────────

    async def _send_welcome(self):
        embed = discord.Embed(
            title="Welcome to Pull All The Things! 🎮",
            description=(
                "Hey there! Welcome to the PATT Discord!\n\n"
                "**Have you already joined the guild in World of Warcraft?**\n\n"
                "Just reply **yes** or **no** — no rush!"
            ),
            color=PATT_GOLD,
        )
        embed.set_footer(text="Pull All The Things • Sen'jin")

        dm = await self.member.create_dm()
        await dm.send(embed=embed)
        await self._set_state("asked_in_guild", set_dm_sent=True)

        response = await self._wait_for_response(dm)
        if response is None:
            return

        answer = response.content.strip().lower()
        if answer in ("yes", "y", "yeah", "yep", "yea", "si", "ye", "yup"):
            await self._set_field("is_in_guild", True)
            await self._ask_main(dm)
        elif answer in ("no", "n", "nah", "nope", "not yet"):
            await self._set_field("is_in_guild", False)
            await self._handle_not_in_guild(dm)
        else:
            await dm.send("I'll take that as a yes! 😄 Let's get you set up.")
            await self._set_field("is_in_guild", True)
            await self._ask_main(dm)

    async def _ask_main(self, dm: discord.DMChannel):
        await dm.send(
            "Awesome! **What's your main character's name?**\n"
            "Just the character name — I'll find them in the roster."
        )
        await self._set_state("asked_main")

        response = await self._wait_for_response(dm)
        if response is None:
            return

        # Strip realm suffixes like "Trogmoon-Senjin" or "(Druid)"
        main_name = response.content.strip().split("-")[0].split("(")[0].strip()
        await self._set_field("reported_main_name", main_name)

        # Try to find them in the scan
        match = await self._find_char(main_name)
        if match:
            await dm.send(
                f"Found **{match['character_name']}** on **{match['realm_name']}** — "
                f"{match['character_class']}! That you? 🎉\n\n"
                f"**Do you have any alts in the guild?** "
                f"List them separated by commas, or say **none**."
            )
            await self._set_field("reported_main_realm", match["realm_slug"])
        else:
            await dm.send(
                f"I don't see **{main_name}** in the roster yet — no worries! "
                f"The roster syncs a few times a day.\n\n"
                f"**Any alts in the guild?** List them or say **none**."
            )

        await self._ask_alts(dm)

    async def _ask_alts(self, dm: discord.DMChannel):
        await self._set_state("asked_alts")

        response = await self._wait_for_response(dm)
        if response is None:
            return

        answer = response.content.strip()
        if answer.lower() in ("none", "no", "nope", "n/a", "na", "0", "-"):
            alt_names = []
        else:
            alt_names = [
                n.strip().split("-")[0].split("(")[0].strip()
                for n in answer.split(",")
                if n.strip()
            ]

        await self._set_field("reported_alt_names", alt_names)

        main_name = await self._get_field("reported_main_name")
        char_list = f"**Main:** {main_name}"
        if alt_names:
            char_list += f"\n**Alts:** {', '.join(alt_names)}"

        embed = discord.Embed(
            title="Got it! You're all set on my end 👍",
            description=(
                f"{char_list}\n\n"
                "I'm verifying this against the guild roster. Once confirmed:\n"
                "• Your Discord roles will be set\n"
                "• You'll get a website invite for pullallthethings.com\n"
                "• Your characters will be pre-loaded in the roster\n\n"
                "You'll hear from me shortly! Feel free to chat in the Discord. 🎮"
            ),
            color=PATT_GOLD,
        )
        await dm.send(embed=embed)

        now = datetime.now(timezone.utc)
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'pending_verification',
                    dm_completed_at = $2,
                    deadline_at = $3,
                    updated_at = NOW()
                   WHERE id = $1""",
                self.session_id,
                now,
                now + timedelta(hours=DEADLINE_HOURS),
            )

        await self._attempt_verification()

    async def _handle_not_in_guild(self, dm: discord.DMChannel):
        embed = discord.Embed(
            title="No worries! 👋",
            description=(
                "Whenever you join the guild in WoW, just reply here with "
                "your character name, or type **/pattsync** in any channel.\n\n"
                "If you need a guild invite, ask any officer — they'll sort you out. 🎮\n\n"
                "*I'll check back in with you in about 24 hours!*"
            ),
            color=PATT_GOLD,
        )
        await dm.send(embed=embed)

        now = datetime.now(timezone.utc)
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'pending_verification',
                    dm_completed_at = $2,
                    deadline_at = $3,
                    updated_at = NOW()
                   WHERE id = $1""",
                self.session_id,
                now,
                now + timedelta(hours=DEADLINE_HOURS),
            )

    # ── Verification & provisioning ───────────────────────────────────────────

    async def _attempt_verification(self):
        """
        Try to match the self-reported main character to the guild roster.
        Called immediately after DM and again by the deadline checker on each sync.
        """
        async with self.db_pool.acquire() as conn:
            session = await conn.fetchrow(
                "SELECT * FROM guild_identity.onboarding_sessions WHERE id = $1",
                self.session_id,
            )
            if not session or session["state"] != "pending_verification":
                return

            main_name = session["reported_main_name"]
            if not main_name:
                return

            char = await conn.fetchrow(
                """SELECT id, character_name, realm_slug, person_id
                   FROM guild_identity.wow_characters
                   WHERE LOWER(character_name) = $1 AND removed_at IS NULL""",
                main_name.lower(),
            )
            if not char:
                await conn.execute(
                    """UPDATE guild_identity.onboarding_sessions SET
                        verification_attempts = verification_attempts + 1,
                        last_verification_at = NOW(),
                        updated_at = NOW()
                       WHERE id = $1""",
                    self.session_id,
                )
                return

            # Found — ensure they have a person record
            person_id = char["person_id"]
            if not person_id:
                person_id = await conn.fetchval(
                    "INSERT INTO guild_identity.persons (display_name) VALUES ($1) RETURNING id",
                    main_name,
                )
                await conn.execute(
                    "UPDATE guild_identity.wow_characters SET person_id = $1 WHERE id = $2",
                    person_id, char["id"],
                )

            # Link Discord member to person
            dm_row = await conn.fetchrow(
                "SELECT id FROM guild_identity.discord_members WHERE discord_id = $1",
                session["discord_id"],
            )
            if dm_row:
                await conn.execute(
                    "UPDATE guild_identity.discord_members SET person_id = $1 WHERE id = $2",
                    person_id, dm_row["id"],
                )

            # Try to link reported alts
            for alt_name in (session["reported_alt_names"] or []):
                await conn.execute(
                    """UPDATE guild_identity.wow_characters SET person_id = $1
                       WHERE LOWER(character_name) = $2
                         AND removed_at IS NULL AND person_id IS NULL""",
                    person_id, alt_name.lower(),
                )

            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'verified',
                    verified_at = NOW(),
                    verified_person_id = $2,
                    updated_at = NOW()
                   WHERE id = $1""",
                self.session_id, person_id,
            )

        await self._auto_provision(person_id)

    async def _auto_provision(self, person_id: int):
        from .provisioner import AutoProvisioner
        provisioner = AutoProvisioner(self.db_pool, self.bot)
        result = await provisioner.provision_person(
            person_id,
            silent=False,
            onboarding_session_id=self.session_id,
        )

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'provisioned',
                    website_invite_sent = $2,
                    website_invite_code = $3,
                    roster_entries_created = $4,
                    discord_role_assigned = $5,
                    completed_at = NOW(),
                    updated_at = NOW()
                   WHERE id = $1""",
                self.session_id,
                result["invite_code"] is not None,
                result["invite_code"],
                result["characters_created"] > 0,
                result["discord_role_assigned"],
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _find_char(self, name: str) -> Optional[dict]:
        async with self.db_pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT character_name, realm_slug, realm_name, character_class
                   FROM guild_identity.wow_characters
                   WHERE LOWER(character_name) = $1 AND removed_at IS NULL""",
                name.lower(),
            )

    async def _wait_for_response(self, dm: discord.DMChannel) -> Optional[discord.Message]:
        def check(m):
            return m.author == self.member and m.channel == dm
        try:
            return await self.bot.wait_for("message", check=check, timeout=RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            return None

    async def _set_state(self, state: str, set_dm_sent: bool = False) -> None:
        async with self.db_pool.acquire() as conn:
            if set_dm_sent:
                await conn.execute(
                    """UPDATE guild_identity.onboarding_sessions SET
                        state = $2, dm_sent_at = NOW(), updated_at = NOW()
                       WHERE id = $1""",
                    self.session_id, state,
                )
            else:
                await conn.execute(
                    """UPDATE guild_identity.onboarding_sessions SET
                        state = $2, updated_at = NOW()
                       WHERE id = $1""",
                    self.session_id, state,
                )

    async def _set_field(self, field: str, value) -> None:
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                f"UPDATE guild_identity.onboarding_sessions SET {field} = $2, updated_at = NOW() WHERE id = $1",
                self.session_id, value,
            )

    async def _get_field(self, field: str):
        async with self.db_pool.acquire() as conn:
            return await conn.fetchval(
                f"SELECT {field} FROM guild_identity.onboarding_sessions WHERE id = $1",
                self.session_id,
            )
