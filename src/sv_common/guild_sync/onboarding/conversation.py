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

from sv_common.config_cache import get_accent_color_int, get_app_url, get_guild_name, get_site_config

logger = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 300   # 5 min per question before saving state
DEADLINE_HOURS   = 24    # hours before #audit-channel escalation


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
        from sv_common.discord.dm import is_onboarding_dm_enabled as is_bot_dm_enabled

        async with self.db_pool.acquire() as conn:
            # Bail if an active session already exists
            existing = await conn.fetchrow(
                """SELECT id, state FROM guild_identity.onboarding_sessions
                   WHERE discord_id = $1
                     AND state NOT IN (
                         'provisioned', 'manually_resolved', 'declined',
                         'oauth_complete', 'abandoned_oauth'
                     )""",
                str(self.member.id),
            )
            if existing:
                self.session_id = existing["id"]
                return

            # Ensure discord_users row exists
            dm_id = await conn.fetchval(
                "SELECT id FROM guild_identity.discord_users WHERE discord_id = $1",
                str(self.member.id),
            )
            if not dm_id:
                dm_id = await conn.fetchval(
                    """INSERT INTO guild_identity.discord_users
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

        # Check DM gate — if disabled, leave session in awaiting_dm state
        if not await is_bot_dm_enabled(self.db_pool):
            logger.info(
                "Bot DM disabled — skipping onboarding DM for %s (session=%s)",
                self.member.name, self.session_id,
            )
            return

        try:
            await self._send_welcome()
        except discord.Forbidden:
            logger.warning("Cannot DM %s — DMs closed", self.member.name)
            await self._set_state("declined")
            await self._notify_landing_zone()

    async def _notify_landing_zone(self) -> None:
        """Post a server message @mentioning the member when DMs are closed."""
        async with self.db_pool.acquire() as conn:
            channel_id = await conn.fetchval(
                "SELECT landing_zone_channel_id FROM common.discord_config LIMIT 1"
            )
        if not channel_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            logger.warning(
                "Landing zone channel %s not in cache — cannot notify %s",
                channel_id, self.member.name,
            )
            return
        guild_name = get_guild_name()
        try:
            await channel.send(
                f"Hey {self.member.mention}! It looks like I couldn't send you a DM "
                f"(your Discord settings may be blocking messages from server members). "
                f"To get set up with a **{guild_name} website account**, just use "
                f"`/get-account` in any channel — it only takes a second! 🎮"
            )
        except discord.Forbidden:
            logger.warning(
                "Bot lacks send permission in landing zone channel %s", channel_id
            )

    async def _create_session_only(self):
        """
        Create an onboarding session in awaiting_dm state without sending a DM.
        Used when bot_dm_enabled is False — the deadline checker will resume these later.
        """
        # Session is already created in start() before the DM gate check.
        # This method is available if called externally.
        pass

    # ── Conversation steps ────────────────────────────────────────────────────

    async def _send_welcome(self):
        embed = discord.Embed(
            title=f"Welcome to {get_guild_name()}! 🎮",
            description=(
                f"Hey there! Welcome to the {get_guild_name()} Discord!\n\n"
                "**Have you already joined the guild in World of Warcraft?**\n\n"
                "Just reply **yes** or **no** — no rush!"
            ),
            color=get_accent_color_int(),
        )
        embed.set_footer(text=get_guild_name())

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
            class_name = match["class_name"] or "Unknown class"
            await dm.send(
                f"Found **{match['character_name']}** on **{match['realm_slug']}** — "
                f"{class_name}! That you? 🎉"
            )
            await self._set_field("reported_main_realm", match["realm_slug"])
        else:
            await dm.send(
                f"I don't see **{main_name}** in the roster yet — no worries! "
                f"The roster syncs a few times a day."
            )

        await self._set_field("reported_alt_names", [])
        await self._proceed_to_verification(dm)

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
        await self._proceed_to_verification(dm)

    async def _proceed_to_verification(self, dm: discord.DMChannel):
        """Send the confirmation embed and kick off verification."""
        main_name = await self._get_field("reported_main_name")

        embed = discord.Embed(
            title="Got it! You're all set on my end 👍",
            description=(
                f"**Main:** {main_name}\n\n"
                "I'm verifying this against the guild roster. Once confirmed:\n"
                "• Your Discord roles will be set\n"
                "• You'll get a website invite for pullallthethings.com\n"
                "• Your characters will be pre-loaded in the roster\n\n"
                "You'll hear from me shortly! Feel free to chat in the Discord. 🎮"
            ),
            color=get_accent_color_int(),
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
            color=get_accent_color_int(),
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
                """SELECT id, character_name, realm_slug
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

            # Check if character already belongs to a player
            existing_pc = await conn.fetchrow(
                """SELECT pc.player_id FROM guild_identity.player_characters pc
                   WHERE pc.character_id = $1""",
                char["id"],
            )

            # Get discord_users.id for this member
            du_row = await conn.fetchrow(
                "SELECT id FROM guild_identity.discord_users WHERE discord_id = $1",
                str(self.member.id),
            )
            du_id = du_row["id"] if du_row else None

            if existing_pc:
                player_id = existing_pc["player_id"]
                # Link discord to existing player if not already linked
                if du_id:
                    await conn.execute(
                        """UPDATE guild_identity.players SET discord_user_id = $1, updated_at = NOW()
                           WHERE id = $2 AND discord_user_id IS NULL""",
                        du_id, player_id,
                    )
            else:
                # Create new player
                display = self.member.nick or self.member.display_name
                player_id = await conn.fetchval(
                    """INSERT INTO guild_identity.players (display_name, discord_user_id)
                       VALUES ($1, $2) RETURNING id""",
                    display, du_id,
                )
                # Link character to player
                await conn.execute(
                    """INSERT INTO guild_identity.player_characters (player_id, character_id)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    player_id, char["id"],
                )

            # Link reported alts
            for alt_name in (session["reported_alt_names"] or []):
                alt_char = await conn.fetchrow(
                    """SELECT id FROM guild_identity.wow_characters
                       WHERE LOWER(character_name) = $1
                         AND removed_at IS NULL
                         AND id NOT IN (
                             SELECT character_id FROM guild_identity.player_characters
                         )""",
                    alt_name.lower(),
                )
                if alt_char:
                    await conn.execute(
                        """INSERT INTO guild_identity.player_characters (player_id, character_id)
                           VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                        player_id, alt_char["id"],
                    )

            # Update session
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'verified',
                    verified_at = NOW(),
                    verified_player_id = $2,
                    verification_attempts = verification_attempts + 1,
                    last_verification_at = NOW(),
                    updated_at = NOW()
                   WHERE id = $1""",
                self.session_id, player_id,
            )

        await self._auto_provision(player_id)

    async def _auto_provision(self, player_id: int):
        from .provisioner import AutoProvisioner
        provisioner = AutoProvisioner(self.db_pool, self.bot)
        result = await provisioner.provision_player(
            player_id,
            silent=False,
            onboarding_session_id=self.session_id,
        )

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.onboarding_sessions SET
                    state = 'oauth_pending',
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
                result["characters_linked"] > 0,
                result["discord_role_assigned"],
            )

        # Send the Battle.net link prompt as a follow-up DM
        await self._send_oauth_prompt()

        # Poll in the background for OAuth completion (up to 10 min)
        asyncio.create_task(self._poll_for_oauth_complete(player_id))

    # ── OAuth step ────────────────────────────────────────────────────────────

    async def _send_oauth_prompt(self) -> None:
        """Send a follow-up DM prompting the member to connect their Battle.net account."""
        from sv_common.discord.dm import is_bot_dm_enabled
        if not await is_bot_dm_enabled(self.db_pool):
            return
        site_url = get_app_url()
        if not site_url:
            try:
                from guild_portal.config import get_settings
                site_url = get_settings().app_url.rstrip("/")
            except Exception:
                pass
        oauth_url = f"{site_url}/auth/battlenet" if site_url else "/auth/battlenet"
        try:
            dm = await self.member.create_dm()
            embed = discord.Embed(
                title="One more step! 🔗",
                description=(
                    "Once you've registered, connect your Battle.net account so we can\n"
                    "automatically find your characters:\n\n"
                    f"👉 {oauth_url}\n\n"
                    "It takes about 10 seconds — click *Approve* on Blizzard's page\n"
                    "and your characters will be linked automatically."
                ),
                color=get_accent_color_int(),
            )
            embed.set_footer(text=get_guild_name())
            await dm.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Cannot DM OAuth prompt to %s — DMs closed", self.member.name)

    async def _poll_for_oauth_complete(self, player_id: int) -> None:
        """Poll every 60s for up to 10 min waiting for the member to complete OAuth."""
        for _ in range(10):
            await asyncio.sleep(60)
            async with self.db_pool.acquire() as conn:
                state = await conn.fetchval(
                    "SELECT state FROM guild_identity.onboarding_sessions WHERE id = $1",
                    self.session_id,
                )
            if state == "oauth_complete":
                await self._send_oauth_completion_dm(player_id)
                return
            if state != "oauth_pending":
                # Session was resolved by another path (e.g. officer command)
                return
        logger.info(
            "OAuth polling timed out for session=%s — deadline_checker will follow up",
            self.session_id,
        )

    async def _send_oauth_completion_dm(self, player_id: int) -> None:
        """Send the 'you're all set' DM after OAuth completes."""
        async with self.db_pool.acquire() as conn:
            char_count = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_identity.player_characters WHERE player_id = $1",
                player_id,
            ) or 0

        realm = get_site_config().get("realm_display_name") or "your realm"
        site_url = get_app_url()

        try:
            dm = await self.member.create_dm()
            embed = discord.Embed(
                title="You're all set! ✅",
                description=(
                    f"Found **{char_count}** character"
                    + ("s" if char_count != 1 else "")
                    + f" on **{realm}** linked to your profile.\n\n"
                    f"Check your roster at **{site_url}/profile**"
                ),
                color=0x4ADE80,
            )
            embed.set_footer(text=get_guild_name())
            await dm.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Cannot DM completion message to %s — DMs closed", self.member.name)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _find_char(self, name: str) -> Optional[dict]:
        async with self.db_pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT wc.id, wc.character_name, wc.realm_slug,
                          c.name as class_name
                   FROM guild_identity.wow_characters wc
                   LEFT JOIN ref.classes c ON c.id = wc.class_id
                   WHERE LOWER(wc.character_name) = $1 AND wc.removed_at IS NULL""",
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


# ---------------------------------------------------------------------------
# Module-level helpers (called by other modules, e.g. bnet_auth_routes)
# ---------------------------------------------------------------------------


async def update_onboarding_status(
    pool: asyncpg.Pool,
    player_id: int,
    new_status: str,
) -> bool:
    """
    Update the onboarding session status for a given player.

    Only updates sessions currently in ``oauth_pending`` state so that
    already-completed or manually-resolved sessions are unaffected.

    Returns True if a session was updated, False if none matched.
    """
    async with pool.acquire() as conn:
        updated_id = await conn.fetchval(
            """UPDATE guild_identity.onboarding_sessions
               SET state = $2, updated_at = NOW()
               WHERE verified_player_id = $1 AND state = 'oauth_pending'
               RETURNING id""",
            player_id,
            new_status,
        )
    return updated_id is not None
