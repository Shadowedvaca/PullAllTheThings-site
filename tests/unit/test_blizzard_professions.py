"""
Unit tests for BlizzardClient.get_character_professions().

All HTTP calls are mocked — no real network access.
Tests cover: profession parsing, gathering prof filtering, None returns.
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from sv_common.guild_sync.blizzard_client import BlizzardClient, CharacterProfessionData


@pytest.fixture
def client():
    c = BlizzardClient(
        client_id="test_id",
        client_secret="test_secret",
    )
    c._http_client = MagicMock()
    c._access_token = "mock_token"
    c._token_expires_at = time.time() + 86400
    return c


def _make_response(status_code=200, json_data=None):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data or {}
    mock.raise_for_status = MagicMock()
    return mock


_SAMPLE_PROFESSION_RESPONSE = {
    "primaries": [
        {
            "profession": {"name": "Blacksmithing", "id": 164},
            "tiers": [
                {
                    "skill_points": 100,
                    "max_skill_points": 100,
                    "tier": {"name": "Khaz Algar Blacksmithing", "id": 2872},
                    "known_recipes": [
                        {"name": "Everforged Breastplate", "id": 453287},
                        {"name": "Tempered Alloy Gauntlets", "id": 453291},
                    ],
                },
                {
                    "skill_points": 175,
                    "max_skill_points": 175,
                    "tier": {"name": "Dragon Isles Blacksmithing", "id": 2751},
                    "known_recipes": [
                        {"name": "Primal Molten Breastplate", "id": 375537},
                    ],
                },
            ],
        },
        {
            "profession": {"name": "Mining", "id": 186},
            "tiers": [
                {
                    "skill_points": 100,
                    "max_skill_points": 100,
                    "tier": {"name": "Khaz Algar Mining", "id": 2873},
                    # No known_recipes — gathering profession
                },
            ],
        },
    ],
    "secondaries": [
        {
            "profession": {"name": "Cooking", "id": 185},
            "tiers": [
                {
                    "skill_points": 50,
                    "max_skill_points": 100,
                    "tier": {"name": "Khaz Algar Cooking", "id": 2880},
                    "known_recipes": [
                        {"name": "Algari Feast", "id": 461480},
                    ],
                }
            ],
        }
    ],
}


class TestGetCharacterProfessions:
    async def test_parses_primary_professions(self, client):
        client._http_client.get = AsyncMock(
            return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
        )
        result = await client.get_character_professions("senjin", "Trogmoon")

        assert result is not None
        assert isinstance(result, CharacterProfessionData)
        assert result.character_name == "Trogmoon"
        assert result.realm_slug == "senjin"

        # Blacksmithing should be present (2 tiers with recipes)
        prof_names = [p["profession_name"] for p in result.professions]
        assert "Blacksmithing" in prof_names

    def test_gathering_profession_excluded(self, client):
        """Mining has no known_recipes — should be excluded from result."""
        import asyncio

        async def _run():
            client._http_client.get = AsyncMock(
                return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
            )
            result = await client.get_character_professions("senjin", "Trogmoon")
            prof_names = [p["profession_name"] for p in result.professions]
            assert "Mining" not in prof_names

        asyncio.get_event_loop().run_until_complete(_run())

    async def test_secondaries_included_if_recipes(self, client):
        client._http_client.get = AsyncMock(
            return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
        )
        result = await client.get_character_professions("senjin", "Trogmoon")
        prof_names = [p["profession_name"] for p in result.professions]
        assert "Cooking" in prof_names

    async def test_recipe_count(self, client):
        client._http_client.get = AsyncMock(
            return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
        )
        result = await client.get_character_professions("senjin", "Trogmoon")

        bs = next(p for p in result.professions if p["profession_name"] == "Blacksmithing")
        all_recipes = [r for t in bs["tiers"] for r in t["known_recipes"]]
        assert len(all_recipes) == 3  # 2 KA + 1 DI

    async def test_recipe_has_id_and_name(self, client):
        client._http_client.get = AsyncMock(
            return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
        )
        result = await client.get_character_professions("senjin", "Trogmoon")
        bs = next(p for p in result.professions if p["profession_name"] == "Blacksmithing")
        tier = bs["tiers"][0]
        recipe = tier["known_recipes"][0]
        assert "id" in recipe
        assert "name" in recipe
        assert recipe["id"] == 453287
        assert recipe["name"] == "Everforged Breastplate"

    async def test_is_primary_flag(self, client):
        client._http_client.get = AsyncMock(
            return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
        )
        result = await client.get_character_professions("senjin", "Trogmoon")
        bs = next(p for p in result.professions if p["profession_name"] == "Blacksmithing")
        cooking = next(p for p in result.professions if p["profession_name"] == "Cooking")
        assert bs["is_primary"] is True
        assert cooking["is_primary"] is False

    async def test_returns_none_on_404(self, client):
        client._http_client.get = AsyncMock(return_value=_make_response(404))
        result = await client.get_character_professions("senjin", "DeletedChar")
        assert result is None

    async def test_returns_none_when_all_gathering(self, client):
        """Character only has gathering professions — should return None."""
        no_crafting = {
            "primaries": [
                {
                    "profession": {"name": "Mining", "id": 186},
                    "tiers": [
                        {
                            "tier": {"name": "Khaz Algar Mining", "id": 2873},
                            "skill_points": 100,
                        }
                    ],
                }
            ],
            "secondaries": [],
        }
        client._http_client.get = AsyncMock(return_value=_make_response(200, no_crafting))
        result = await client.get_character_professions("senjin", "Miner")
        assert result is None

    async def test_name_lowercased_in_url(self, client):
        client._http_client.get = AsyncMock(
            return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
        )
        await client.get_character_professions("senjin", "TROGMOON")
        call_args = client._http_client.get.call_args
        url = call_args[0][0]
        assert "trogmoon" in url
        assert "TROGMOON" not in url

    async def test_tier_skill_data_preserved(self, client):
        client._http_client.get = AsyncMock(
            return_value=_make_response(200, _SAMPLE_PROFESSION_RESPONSE)
        )
        result = await client.get_character_professions("senjin", "Trogmoon")
        bs = next(p for p in result.professions if p["profession_name"] == "Blacksmithing")
        tier = bs["tiers"][0]
        assert tier["skill_points"] == 100
        assert tier["max_skill_points"] == 100
        assert tier["tier_id"] == 2872
        assert tier["tier_name"] == "Khaz Algar Blacksmithing"
