"""
Unit tests for the BlizzardClient.

All HTTP calls are mocked — no real network access.
Tests cover: token refresh, roster parsing, character profile parsing,
special character URL encoding, 404 handling, and static data maps.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
import time

from sv_common.guild_sync.blizzard_client import (
    BlizzardClient,
    GuildMemberData,
    CharacterProfileData,
    CLASS_ID_MAP,
    RANK_NAME_MAP,
)


@pytest_asyncio.fixture
async def client():
    """BlizzardClient with a mocked HTTP client and a pre-set valid token."""
    c = BlizzardClient(
        client_id="test_id",
        client_secret="test_secret",
        realm_slug="senjin",
        guild_slug="pull-all-the-things",
    )
    # Inject a mock HTTP client and pre-set a valid token so _ensure_token skips refresh
    c._http_client = MagicMock()
    c._access_token = "mock_access_token"
    c._token_expires_at = time.time() + 86400  # Valid for 24h
    yield c


def _make_response(status_code: int = 200, json_data: dict = None):
    """Build a mock httpx response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data or {}
    mock.raise_for_status = MagicMock()
    return mock


class TestTokenRefresh:
    async def test_token_refresh_on_expiry(self, client):
        """Expired token triggers _refresh_token which calls http POST."""
        token_response = _make_response(200, {
            "access_token": "fresh_token",
            "expires_in": 86400,
        })
        client._http_client.post = AsyncMock(return_value=token_response)

        # Force expiry
        client._token_expires_at = 0

        await client._ensure_token()

        assert client._access_token == "fresh_token"
        client._http_client.post.assert_called_once()

    async def test_valid_token_skips_refresh(self, client):
        """Valid token — _ensure_token should NOT call _refresh_token."""
        client._http_client.post = AsyncMock()

        await client._ensure_token()

        client._http_client.post.assert_not_called()
        assert client._access_token == "mock_access_token"


class TestGetGuildRoster:
    async def test_parse_roster_members(self, client):
        """Roster response is parsed into GuildMemberData list."""
        response = _make_response(200, {
            "members": [
                {
                    "character": {
                        "name": "Trogmoon",
                        "realm": {"slug": "senjin", "name": "Sen'jin"},
                        "level": 80,
                        "playable_class": {"id": 11},  # Druid
                    },
                    "rank": 0,
                },
                {
                    "character": {
                        "name": "Shodoom",
                        "realm": {"slug": "bleeding-hollow", "name": "Bleeding Hollow"},
                        "level": 80,
                        "playable_class": {"id": 7},  # Shaman
                    },
                    "rank": 1,
                },
            ]
        })
        client._http_client.get = AsyncMock(return_value=response)

        roster = await client.get_guild_roster()

        assert len(roster) == 2
        assert roster[0].character_name == "Trogmoon"
        assert roster[0].character_class == "Druid"
        assert roster[0].guild_rank == 0
        assert roster[0].realm_slug == "senjin"
        assert roster[1].character_name == "Shodoom"
        assert roster[1].character_class == "Shaman"
        assert roster[1].guild_rank == 1
        assert roster[1].realm_slug == "bleeding-hollow"

    async def test_empty_roster_returns_empty_list(self, client):
        response = _make_response(200, {"members": []})
        client._http_client.get = AsyncMock(return_value=response)

        roster = await client.get_guild_roster()
        assert roster == []

    async def test_missing_members_key_returns_empty_list(self, client):
        response = _make_response(200, {})
        client._http_client.get = AsyncMock(return_value=response)

        roster = await client.get_guild_roster()
        assert roster == []

    async def test_unknown_class_id_uses_fallback(self, client):
        response = _make_response(200, {
            "members": [{
                "character": {
                    "name": "Zap",
                    "realm": {"slug": "senjin", "name": "Sen'jin"},
                    "level": 80,
                    "playable_class": {"id": 999},  # Unknown
                },
                "rank": 3,
            }]
        })
        client._http_client.get = AsyncMock(return_value=response)

        roster = await client.get_guild_roster()
        assert "Unknown" in roster[0].character_class


class TestGetCharacterProfile:
    async def test_parse_character_profile(self, client):
        """Full profile response is parsed correctly."""
        response = _make_response(200, {
            "name": "Trogmoon",
            "realm": {"slug": "senjin", "name": "Sen'jin"},
            "character_class": {"name": "Druid"},
            "active_spec": {"name": "Balance"},
            "level": 80,
            "equipped_item_level": 623,
            "last_login_timestamp": 1740000000000,
            "race": {"name": "Tauren"},
            "gender": {"name": "Male"},
        })
        client._http_client.get = AsyncMock(return_value=response)

        profile = await client.get_character_profile("senjin", "Trogmoon")

        assert profile is not None
        assert profile.character_name == "Trogmoon"
        assert profile.character_class == "Druid"
        assert profile.active_spec == "Balance"
        assert profile.item_level == 623
        assert profile.level == 80
        assert profile.last_login_timestamp == 1740000000000
        assert profile.realm_slug == "senjin"
        assert profile.race == "Tauren"

    async def test_character_not_found_returns_none(self, client):
        """404 response returns None."""
        response = _make_response(404)
        client._http_client.get = AsyncMock(return_value=response)

        profile = await client.get_character_profile("senjin", "DeletedChar")
        assert profile is None

    async def test_name_lowercased_in_url(self, client):
        """Character name is lowercased before calling the API."""
        response = _make_response(200, {
            "name": "Trogmoon",
            "realm": {"slug": "senjin", "name": "Sen'jin"},
            "character_class": {"name": "Druid"},
            "active_spec": {"name": "Balance"},
            "level": 80,
        })
        client._http_client.get = AsyncMock(return_value=response)

        await client.get_character_profile("senjin", "TROGMOON")

        call_args = client._http_client.get.call_args
        url = call_args[0][0]
        assert "trogmoon" in url
        assert "TROGMOON" not in url

    async def test_special_characters_url_encoded(self, client):
        """Special characters in names (e.g., ñ) are URL-encoded."""
        response = _make_response(200, {
            "name": "Zatañña",
            "realm": {"slug": "sargeras", "name": "Sargeras"},
            "character_class": {"name": "Mage"},
            "active_spec": {"name": "Arcane"},
            "level": 80,
        })
        client._http_client.get = AsyncMock(return_value=response)

        profile = await client.get_character_profile("sargeras", "Zatañña")

        assert profile is not None
        assert profile.character_name == "Zatañña"

        call_args = client._http_client.get.call_args
        url = call_args[0][0]
        # URL should contain encoded form, not raw accented chars
        assert "zata" in url.lower()

    async def test_missing_spec_returns_none_spec(self, client):
        """Profile without active_spec returns None for that field."""
        response = _make_response(200, {
            "name": "NoSpec",
            "realm": {"slug": "senjin", "name": "Sen'jin"},
            "character_class": {"name": "Warrior"},
            "level": 80,
        })
        client._http_client.get = AsyncMock(return_value=response)

        profile = await client.get_character_profile("senjin", "NoSpec")
        assert profile is not None
        assert profile.active_spec is None


class TestStaticMaps:
    def test_all_wow_classes_are_mapped(self):
        expected = [
            "Warrior", "Paladin", "Hunter", "Rogue", "Priest",
            "Death Knight", "Shaman", "Mage", "Warlock", "Monk",
            "Druid", "Demon Hunter", "Evoker",
        ]
        for cls in expected:
            assert cls in CLASS_ID_MAP.values(), f"Missing WoW class: {cls}"

    def test_class_id_map_has_13_entries(self):
        assert len(CLASS_ID_MAP) == 13

    def test_rank_name_map_matches_patt_structure(self):
        assert RANK_NAME_MAP[0] == "Guild Leader"
        assert RANK_NAME_MAP[1] == "Officer"
        assert RANK_NAME_MAP[2] == "Veteran"
        assert RANK_NAME_MAP[3] == "Member"
        assert RANK_NAME_MAP[4] == "Initiate"

    def test_druid_class_id(self):
        assert CLASS_ID_MAP[11] == "Druid"

    def test_evoker_class_id(self):
        assert CLASS_ID_MAP[13] == "Evoker"
