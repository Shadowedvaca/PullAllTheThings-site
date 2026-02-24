"""Integration tests for Discord role sync.

All Discord API calls are mocked. Tests verify that platform player ranks
are correctly updated based on Discord role changes, and that DiscordUser
records are created/updated for new Discord members.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import DiscordUser, GuildRank, Player


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GUILD_ID = "987654321012345678"


async def _setup_ranks(db: AsyncSession) -> dict[str, GuildRank]:
    """Create Initiate(1), Member(2), Officer(4) ranks with Discord role IDs."""
    ranks = {
        "initiate": GuildRank(name="Initiate_rs", level=1, discord_role_id="111111111111111111"),
        "member": GuildRank(name="Member_rs", level=2, discord_role_id="222222222222222222"),
        "officer": GuildRank(name="Officer_rs", level=4, discord_role_id="444444444444444444"),
    }
    for r in ranks.values():
        db.add(r)
    await db.flush()
    return ranks


async def _setup_discord_user_and_player(
    db: AsyncSession, discord_id: str, username: str, rank_id: int
) -> tuple[DiscordUser, Player]:
    """Create a DiscordUser and linked Player for testing."""
    du = DiscordUser(
        discord_id=discord_id,
        username=username,
        display_name=username,
        is_present=True,
    )
    db.add(du)
    await db.flush()

    player = Player(
        display_name=username,
        discord_user_id=du.id,
        guild_rank_id=rank_id,
        guild_rank_source="manual",
    )
    db.add(player)
    await db.flush()
    return du, player


def _make_discord_member(discord_id: str, role_ids: list[str], name: str = "testuser"):
    """Build a mock discord.Member with the given roles."""
    member = MagicMock()
    member.id = int(discord_id)
    member.bot = False
    member.__str__ = MagicMock(return_value=name)
    member.display_name = name

    roles = []
    for role_id in role_ids:
        role = MagicMock()
        role.id = int(role_id)
        roles.append(role)
    member.roles = roles
    return member


def _make_bot_with_members(discord_members: list) -> MagicMock:
    """Build a mock discord.Client whose guild.members returns the given list."""
    guild = MagicMock()
    guild.members = discord_members

    async def _async_iter():
        for m in discord_members:
            yield m

    guild.fetch_members = MagicMock(return_value=_async_iter())

    bot = MagicMock()
    bot.get_guild = MagicMock(return_value=guild)
    return bot


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRoleSync:
    async def test_role_sync_promotes_player_when_discord_role_added(
        self, db_session: AsyncSession
    ):
        """Player currently Initiate gains Officer role in Discord → rank updated."""
        from sv_common.discord.role_sync import sync_discord_roles

        ranks = await _setup_ranks(db_session)

        _, player = await _setup_discord_user_and_player(
            db_session, "555000000000000001", "to_be_promoted", ranks["initiate"].id
        )

        # Discord says this user now has the Officer role
        discord_member = _make_discord_member(
            "555000000000000001",
            role_ids=["444444444444444444"],  # Officer role_id
            name="to_be_promoted",
        )
        bot = _make_bot_with_members([discord_member])

        factory = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await sync_discord_roles(bot, factory, _GUILD_ID)

        await db_session.refresh(player)
        assert player.guild_rank_id == ranks["officer"].id
        assert player.guild_rank_source == "discord_sync"

    async def test_role_sync_demotes_player_when_discord_role_removed(
        self, db_session: AsyncSession
    ):
        """Player was Officer; Discord role changed to Initiate → rank updated."""
        from sv_common.discord.role_sync import sync_discord_roles

        ranks = await _setup_ranks(db_session)

        _, player = await _setup_discord_user_and_player(
            db_session, "555000000000000002", "to_be_demoted", ranks["officer"].id
        )

        # Discord says this user now has only Initiate role
        discord_member = _make_discord_member(
            "555000000000000002",
            role_ids=["111111111111111111"],  # Initiate role_id
            name="to_be_demoted",
        )
        bot = _make_bot_with_members([discord_member])

        factory = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await sync_discord_roles(bot, factory, _GUILD_ID)

        await db_session.refresh(player)
        assert player.guild_rank_id == ranks["initiate"].id
        assert player.guild_rank_source == "discord_sync"

    async def test_role_sync_creates_new_discord_user_for_unknown_discord_member(
        self, db_session: AsyncSession
    ):
        """Discord member not in platform → new DiscordUser record created."""
        from sv_common.discord.role_sync import sync_discord_roles

        await _setup_ranks(db_session)

        discord_member = _make_discord_member(
            "555000000000000003",
            role_ids=["222222222222222222"],  # Member role
            name="brand_new_user",
        )
        bot = _make_bot_with_members([discord_member])

        factory = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        stats = await sync_discord_roles(bot, factory, _GUILD_ID)

        result = await db_session.execute(
            select(DiscordUser).where(DiscordUser.discord_id == "555000000000000003")
        )
        new_du = result.scalar_one_or_none()
        assert new_du is not None
        assert stats["created"] == 1

    async def test_role_sync_skips_player_when_rank_unchanged(
        self, db_session: AsyncSession
    ):
        """Existing player with correct rank and no role change is skipped."""
        from sv_common.discord.role_sync import sync_discord_roles

        ranks = await _setup_ranks(db_session)

        _, player = await _setup_discord_user_and_player(
            db_session, "555000000000000004", "stable_member", ranks["member"].id
        )
        original_rank_id = player.guild_rank_id

        # Discord matches current rank — no change expected
        discord_member = _make_discord_member(
            "555000000000000004",
            role_ids=["222222222222222222"],  # Member role — same as current
            name="stable_member",
        )
        bot = _make_bot_with_members([discord_member])

        factory = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await sync_discord_roles(bot, factory, _GUILD_ID)

        await db_session.refresh(player)
        assert player.guild_rank_id == original_rank_id

    async def test_role_sync_sets_source_to_discord_sync(
        self, db_session: AsyncSession
    ):
        """guild_rank_source is set to 'discord_sync' on any rank update."""
        from sv_common.discord.role_sync import sync_discord_roles

        ranks = await _setup_ranks(db_session)

        _, player = await _setup_discord_user_and_player(
            db_session, "555000000000000005", "source_test_user", ranks["initiate"].id
        )

        discord_member = _make_discord_member(
            "555000000000000005",
            role_ids=["222222222222222222"],  # Member role
            name="source_test_user",
        )
        bot = _make_bot_with_members([discord_member])

        factory = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await sync_discord_roles(bot, factory, _GUILD_ID)

        await db_session.refresh(player)
        assert player.guild_rank_source == "discord_sync"

    async def test_role_sync_skips_bot_accounts(
        self, db_session: AsyncSession
    ):
        """Bot accounts in Discord guild should not create DiscordUser records."""
        from sv_common.discord.role_sync import sync_discord_roles

        await _setup_ranks(db_session)

        bot_member = _make_discord_member(
            "555000000000000006",
            role_ids=[],
            name="SomeBot#0000",
        )
        bot_member.bot = True  # Mark as bot account

        bot = _make_bot_with_members([bot_member])

        factory = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await sync_discord_roles(bot, factory, _GUILD_ID)

        result = await db_session.execute(
            select(DiscordUser).where(DiscordUser.discord_id == "555000000000000006")
        )
        assert result.scalar_one_or_none() is None
