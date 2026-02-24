"""
Blizzard Battle.net API client for WoW guild data.

Handles:
- OAuth2 client credentials flow (tokens auto-refresh)
- Guild roster fetching
- Individual character profile enrichment
- Rate limit awareness (36,000 req/hr — generous)

Usage:
    client = BlizzardClient(client_id, client_secret)
    await client.initialize()
    roster = await client.get_guild_roster()
    for member in roster:
        profile = await client.get_character_profile(member.realm_slug, member.name)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# Blizzard API endpoints
OAUTH_TOKEN_URL = "https://oauth.battle.net/token"
API_BASE_URL = "https://us.api.blizzard.com"


@dataclass
class GuildMemberData:
    """Raw guild member data from the roster endpoint."""
    character_name: str
    realm_slug: str
    realm_name: str
    character_class: str
    level: int
    guild_rank: int


@dataclass
class CharacterProfileData:
    """Enriched character data from the profile endpoint."""
    character_name: str
    realm_slug: str
    realm_name: str
    character_class: str
    active_spec: Optional[str] = None
    level: int = 0
    item_level: int = 0
    guild_rank: int = 0
    guild_rank_name: str = ""
    last_login_timestamp: Optional[int] = None
    race: Optional[str] = None
    gender: Optional[str] = None


# Blizzard class ID → class name mapping
CLASS_ID_MAP = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue",
    5: "Priest", 6: "Death Knight", 7: "Shaman", 8: "Mage",
    9: "Warlock", 10: "Monk", 11: "Druid", 12: "Demon Hunter",
    13: "Evoker",
}

# Guild rank index → rank name mapping for PATT
# WoW rank 0 is always Guild Master. Lower rank index = more access (WoW standard).
# 1 = Officer, 2 = Veteran, 3 = Member, 4 = Initiate (lowest)
RANK_NAME_MAP = {
    0: "Guild Leader",
    1: "Officer",
    2: "Veteran",
    3: "Member",
    4: "Initiate",
}


class BlizzardClient:
    """Async client for Blizzard's Battle.net API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        realm_slug: str = "senjin",
        guild_slug: str = "pull-all-the-things",
        region: str = "us",
        namespace: str = "profile-us",
        locale: str = "en_US",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.realm_slug = realm_slug
        self.guild_slug = guild_slug
        self.region = region
        self.namespace = namespace
        self.locale = locale

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._http_client: Optional[httpx.AsyncClient] = None
        self._request_count = 0
        self._request_window_start = time.time()

    async def initialize(self):
        """Create HTTP client and fetch initial token."""
        self._http_client = httpx.AsyncClient(timeout=30.0)
        await self._refresh_token()

    async def close(self):
        """Clean up HTTP client."""
        if self._http_client:
            await self._http_client.aclose()

    async def _refresh_token(self):
        """Get a new OAuth2 access token via client credentials flow."""
        logger.info("Refreshing Blizzard API access token...")

        response = await self._http_client.post(
            OAUTH_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        response.raise_for_status()
        data = response.json()

        self._access_token = data["access_token"]
        # Refresh 5 minutes before actual expiry
        self._token_expires_at = time.time() + data.get("expires_in", 86400) - 300

        logger.info("Blizzard API token refreshed, expires in %d seconds", data.get("expires_in", 0))

    async def _ensure_token(self):
        """Refresh token if expired or about to expire."""
        if time.time() >= self._token_expires_at:
            await self._refresh_token()

    async def _api_get(self, path: str, params: dict = None) -> dict:
        """Make an authenticated GET request to the Blizzard API."""
        await self._ensure_token()

        if params is None:
            params = {}
        params.setdefault("namespace", self.namespace)
        params.setdefault("locale", self.locale)

        headers = {"Authorization": f"Bearer {self._access_token}"}

        url = f"{API_BASE_URL}{path}"
        response = await self._http_client.get(url, headers=headers, params=params)

        self._request_count += 1

        if response.status_code == 404:
            logger.warning("Blizzard API 404: %s", path)
            return None

        response.raise_for_status()
        return response.json()

    async def get_guild_roster(self) -> list[GuildMemberData]:
        """
        Fetch the full guild roster.

        Endpoint: /data/wow/guild/{realmSlug}/{guildSlug}/roster
        Returns: List of GuildMemberData with basic info per character
        """
        path = f"/data/wow/guild/{self.realm_slug}/{self.guild_slug}/roster"
        data = await self._api_get(path)

        if not data or "members" not in data:
            logger.error("No roster data returned from Blizzard API")
            return []

        members = []
        for entry in data["members"]:
            char = entry.get("character", {})

            # Get class name from playable_class id
            class_id = char.get("playable_class", {}).get("id", 0)
            class_name = CLASS_ID_MAP.get(class_id, f"Unknown({class_id})")

            # Get realm info
            realm = char.get("realm", {})

            members.append(GuildMemberData(
                character_name=char.get("name", "Unknown"),
                realm_slug=realm.get("slug", self.realm_slug),
                realm_name=realm.get("name", "Unknown"),
                character_class=class_name,
                level=char.get("level", 0),
                guild_rank=entry.get("rank", 99),
            ))

        logger.info("Fetched %d guild members from Blizzard API", len(members))
        return members

    async def get_character_profile(
        self, realm_slug: str, character_name: str
    ) -> Optional[CharacterProfileData]:
        """
        Fetch detailed character profile including spec and item level.

        Endpoint: /profile/wow/character/{realmSlug}/{characterName}
        Note: Character name must be lowercase for the API.
        """
        # API requires lowercase character name
        name_lower = character_name.lower()
        # Handle special characters in names (e.g., Zatañña)
        name_encoded = quote(name_lower, safe='')

        path = f"/profile/wow/character/{realm_slug}/{name_encoded}"
        data = await self._api_get(path)

        if not data:
            return None

        # Extract active spec
        active_spec = None
        spec_data = data.get("active_spec", {})
        if spec_data:
            active_spec = spec_data.get("name")

        # Extract class
        class_name = data.get("character_class", {}).get("name", "Unknown")

        # Extract realm
        realm = data.get("realm", {})

        return CharacterProfileData(
            character_name=data.get("name", character_name),
            realm_slug=realm.get("slug", realm_slug),
            realm_name=realm.get("name", "Unknown"),
            character_class=class_name,
            active_spec=active_spec,
            level=data.get("level", 0),
            item_level=data.get("equipped_item_level", 0),
            last_login_timestamp=data.get("last_login_timestamp"),
            race=data.get("race", {}).get("name"),
            gender=data.get("gender", {}).get("name"),
        )

    async def get_character_equipment_summary(
        self, realm_slug: str, character_name: str
    ) -> Optional[int]:
        """
        Fetch just the equipped item level for a character.

        Endpoint: /profile/wow/character/{realmSlug}/{characterName}/equipment
        Returns: equipped item level or None
        """
        name_lower = character_name.lower()
        name_encoded = quote(name_lower, safe='')

        path = f"/profile/wow/character/{realm_slug}/{name_encoded}/equipment"
        data = await self._api_get(path)

        if not data:
            return None

        return data.get("equipped_item_level")

    async def sync_full_roster(self) -> list[CharacterProfileData]:
        """
        Full sync: fetch roster, then enrich each member with profile data.

        This is the main method called by the scheduler.
        Batches character profile requests to be respectful of rate limits.
        """
        roster = await self.get_guild_roster()
        if not roster:
            return []

        enriched = []
        batch_size = 10

        for i in range(0, len(roster), batch_size):
            batch = roster[i:i + batch_size]
            tasks = [
                self.get_character_profile(m.realm_slug, m.character_name)
                for m in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for member, result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Failed to fetch profile for %s: %s",
                        member.character_name, result
                    )
                    # Use basic roster data without enrichment
                    enriched.append(CharacterProfileData(
                        character_name=member.character_name,
                        realm_slug=member.realm_slug,
                        realm_name=member.realm_name,
                        character_class=member.character_class,
                        level=member.level,
                        guild_rank=member.guild_rank,
                        guild_rank_name=RANK_NAME_MAP.get(member.guild_rank, f"Rank {member.guild_rank}"),
                    ))
                elif result is not None:
                    # Merge guild rank from roster (profile doesn't include it)
                    result.guild_rank = member.guild_rank
                    result.guild_rank_name = RANK_NAME_MAP.get(member.guild_rank, f"Rank {member.guild_rank}")
                    enriched.append(result)

            # Small delay between batches to be nice
            if i + batch_size < len(roster):
                await asyncio.sleep(0.5)

        logger.info(
            "Full roster sync complete: %d members enriched out of %d",
            len(enriched), len(roster)
        )
        return enriched
