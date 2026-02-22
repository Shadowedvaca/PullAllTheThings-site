"""
Unit tests for discord_sync role helpers.

get_highest_guild_role and get_all_guild_roles are pure functions
that operate on mock Discord Member objects — no DB, no network.
"""

import pytest
from unittest.mock import MagicMock

from sv_common.guild_sync.discord_sync import (
    get_highest_guild_role,
    get_all_guild_roles,
    GUILD_ROLE_PRIORITY,
    DISCORD_TO_INGAME_RANK,
)


def _make_member(role_names: list[str], username: str = "testuser", nick: str = None, bot: bool = False):
    """Create a mock discord.Member with the given role names."""
    member = MagicMock()
    member.bot = bot
    member.name = username
    member.nick = nick
    member.display_name = nick or username
    member.id = 12345
    member.joined_at = None

    roles = []
    # @everyone is always present
    everyone = MagicMock()
    everyone.name = "@everyone"
    roles.append(everyone)

    for rn in role_names:
        role = MagicMock()
        role.name = rn
        roles.append(role)

    member.roles = roles
    return member


class TestGetHighestGuildRole:
    def test_gm_is_highest_even_with_other_roles(self):
        member = _make_member(["Member", "GM", "Officer"])
        assert get_highest_guild_role(member) == "GM"

    def test_officer_beats_veteran(self):
        member = _make_member(["Veteran", "Officer"])
        assert get_highest_guild_role(member) == "Officer"

    def test_veteran_beats_member(self):
        member = _make_member(["Member", "Veteran"])
        assert get_highest_guild_role(member) == "Veteran"

    def test_member_only(self):
        member = _make_member(["Member"])
        assert get_highest_guild_role(member) == "Member"

    def test_initiate(self):
        member = _make_member(["Initiate"])
        assert get_highest_guild_role(member) == "Initiate"

    def test_no_guild_role_returns_none(self):
        member = _make_member(["Booster", "Nitro", "Server Booster"])
        assert get_highest_guild_role(member) is None

    def test_no_roles_returns_none(self):
        member = _make_member([])
        assert get_highest_guild_role(member) is None

    def test_case_insensitive_matching(self):
        """Role names in Discord may be lowercase — matching is case-insensitive."""
        member = _make_member(["officer"])  # lowercase
        assert get_highest_guild_role(member) == "Officer"

    def test_case_insensitive_gm(self):
        member = _make_member(["gm"])
        assert get_highest_guild_role(member) == "GM"

    def test_bot_can_have_roles(self):
        """The function doesn't filter bots — callers do."""
        member = _make_member(["Member"], bot=True)
        assert get_highest_guild_role(member) == "Member"


class TestGetAllGuildRoles:
    def test_multiple_guild_roles(self):
        member = _make_member(["Member", "Officer", "Veteran"])
        roles = get_all_guild_roles(member)
        assert "Officer" in roles
        assert "Veteran" in roles
        assert "Member" in roles

    def test_returns_in_priority_order(self):
        member = _make_member(["Initiate", "Member", "Officer"])
        roles = get_all_guild_roles(member)
        # Should be in GUILD_ROLE_PRIORITY order (Officer first)
        assert roles.index("Officer") < roles.index("Member")
        assert roles.index("Member") < roles.index("Initiate")

    def test_non_guild_roles_excluded(self):
        member = _make_member(["Member", "Booster", "Verified"])
        roles = get_all_guild_roles(member)
        assert "Booster" not in roles
        assert "Verified" not in roles

    def test_no_guild_roles_returns_empty_list(self):
        member = _make_member(["Booster", "Nitro"])
        assert get_all_guild_roles(member) == []

    def test_gm_role(self):
        member = _make_member(["GM"])
        roles = get_all_guild_roles(member)
        assert "GM" in roles
        assert len(roles) == 1


class TestStaticData:
    def test_guild_role_priority_order(self):
        """Priority list must go from highest to lowest authority."""
        priority = GUILD_ROLE_PRIORITY
        assert priority[0] == "GM"
        assert "Officer" in priority
        assert "Initiate" in priority
        # GM should appear before Officer
        assert priority.index("GM") < priority.index("Officer")
        assert priority.index("Officer") < priority.index("Veteran")

    def test_discord_to_ingame_rank_mapping(self):
        """Every Discord role should map to an in-game rank."""
        assert DISCORD_TO_INGAME_RANK["GM"] == "Guild Leader"
        assert DISCORD_TO_INGAME_RANK["Officer"] == "Officer"
        assert DISCORD_TO_INGAME_RANK["Veteran"] == "Veteran"
        assert DISCORD_TO_INGAME_RANK["Member"] == "Member"
        assert DISCORD_TO_INGAME_RANK["Initiate"] == "Initiate"
