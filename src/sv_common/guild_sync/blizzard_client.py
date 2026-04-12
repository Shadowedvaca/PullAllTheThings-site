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
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import asyncpg
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
    blizzard_character_id: Optional[int] = None


@dataclass
class CharacterProfessionData:
    """Professions data from the character professions endpoint."""
    character_name: str
    realm_slug: str
    professions: list[dict]  # Raw profession+tier+recipe structure


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
    blizzard_character_id: Optional[int] = None


@dataclass
class CharacterEquipmentSlot:
    """Per-slot equipment data from the Blizzard equipment endpoint."""
    slot: str                           # normalised: head, neck, shoulder, …
    blizzard_item_id: int
    item_name: str
    item_level: int
    quality_track: Optional[str]        # V / C / H / M, or None
    bonus_ids: list[int] = field(default_factory=list)
    enchant_id: Optional[int] = None
    gem_ids: list[int] = field(default_factory=list)


# Blizzard class ID → class name mapping
CLASS_ID_MAP = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue",
    5: "Priest", 6: "Death Knight", 7: "Shaman", 8: "Mage",
    9: "Warlock", 10: "Monk", 11: "Druid", 12: "Demon Hunter",
    13: "Evoker",
}

# Guild rank index → rank name mapping (fallback when DB is unavailable)
# WoW rank 0 is always Guild Master. Lower rank index = more access (WoW standard).
RANK_NAME_MAP = {
    0: "Guild Leader",
    1: "Officer",
    2: "Veteran",
    3: "Member",
    4: "Initiate",
}


async def get_rank_name_map(pool: asyncpg.Pool) -> dict[int, str]:
    """Load WoW rank index → platform rank name mapping from DB (common.rank_wow_mapping).

    Returns a dict like {0: "Guild Leader", 1: "Officer", ...}.
    Falls back to the hardcoded RANK_NAME_MAP if the table is empty or unavailable.
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT rwm.wow_rank_index, gr.name
                FROM common.rank_wow_mapping rwm
                JOIN common.guild_ranks gr ON gr.id = rwm.guild_rank_id
                """
            )
        if rows:
            return {row["wow_rank_index"]: row["name"] for row in rows}
    except Exception as exc:
        logger.warning("Could not load rank_wow_mapping from DB: %s — using fallback", exc)
    return dict(RANK_NAME_MAP)


def should_sync_character(
    last_login_ts: Optional[int],
    last_sync: Optional[datetime],
    force_full: bool = False,
) -> bool:
    """Return True if character needs a progression or profession sync.

    Compares the character's Blizzard last_login_timestamp (ms since epoch)
    against when we last synced that category. If the character hasn't logged
    in since our last sync, nothing can have changed — skip it.

    Args:
        last_login_ts: Blizzard last_login_timestamp in milliseconds, or None.
        last_sync: When we last synced this category (last_progression_sync or
                   last_profession_sync), or None if never synced.
        force_full: If True, always sync regardless of timestamps.
    """
    if force_full:
        return True
    if last_login_ts is None:
        return True  # No login data — sync to be safe
    if last_sync is None:
        return True  # Never synced — must sync
    last_login_dt = datetime.fromtimestamp(last_login_ts / 1000, tz=timezone.utc)
    return last_login_dt > last_sync


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

    async def _api_get(
        self, path: str, params: dict = None, _retries: int = 3
    ) -> dict:
        """Make an authenticated GET request to the Blizzard API.

        Retries up to _retries times on HTTP 429 (rate limit), backing off
        using the Retry-After header value plus random jitter to spread
        concurrent callers.
        """
        await self._ensure_token()

        if params is None:
            params = {}
        params.setdefault("namespace", self.namespace)
        params.setdefault("locale", self.locale)

        headers = {"Authorization": f"Bearer {self._access_token}"}

        url = f"{API_BASE_URL}{path}"
        response = await self._http_client.get(url, headers=headers, params=params)

        self._request_count += 1

        if response.status_code == 429:
            if _retries > 0:
                retry_after = float(response.headers.get("Retry-After", "1"))
                wait = retry_after + random.uniform(0.1, 1.0)
                logger.warning(
                    "Blizzard API 429 on %s — retrying in %.1fs (%d retries left)",
                    path, wait, _retries - 1,
                )
                await asyncio.sleep(wait)
                return await self._api_get(path, params, _retries - 1)
            logger.warning("Blizzard API 429 on %s — no retries left", path)
            return None

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
                blizzard_character_id=char.get("id"),
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
            blizzard_character_id=data.get("id"),
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

    async def get_character_equipment(
        self, realm_slug: str, character_name: str
    ) -> Optional[list["CharacterEquipmentSlot"]]:
        """
        Fetch full per-slot equipment data for a character.

        Endpoint: /profile/wow/character/{realmSlug}/{characterName}/equipment
        Returns: list of CharacterEquipmentSlot (one per tracked slot), or None.
        """
        from .quality_track import (
            normalize_slot, detect_quality_track,
            track_from_display_string, is_crafted_item,
        )

        name_lower = character_name.lower()
        name_encoded = quote(name_lower, safe='')

        path = f"/profile/wow/character/{realm_slug}/{name_encoded}/equipment"
        data = await self._api_get(path)

        if not data or "equipped_items" not in data:
            return None

        slots: list[CharacterEquipmentSlot] = []
        for item in data["equipped_items"]:
            slot_raw = item.get("slot", {}).get("type", "")
            slot = normalize_slot(slot_raw)
            if not slot:
                continue  # TABARD, SHIRT, etc.

            blizzard_item_id = item.get("item", {}).get("id")
            if not blizzard_item_id:
                continue

            item_name = item.get("name", "")
            item_level = item.get("level", {}).get("value", 0)

            # Quality track from display string
            display_string = (
                item.get("name_description", {}).get("display_string") or ""
            )
            bonus_ids = item.get("bonus_list") or []
            quality_track = detect_quality_track(display_string, bonus_ids)

            # Crafted items don't carry a display_string or standard track bonus IDs.
            # Fall back to the item API with bonus IDs applied — Blizzard returns the
            # correct name_description.display_string (e.g. "Heroic") when bonus IDs
            # are provided, letting us detect track without hardcoding any IDs.
            if quality_track is None and is_crafted_item(bonus_ids):
                crafted_ds = await self.get_item_preview(blizzard_item_id, bonus_ids)
                if crafted_ds:
                    quality_track = track_from_display_string(crafted_ds)

            # Enchant
            enchant_id = None
            enchant_data = item.get("enchantments")
            if enchant_data:
                enchant_id = enchant_data[0].get("enchantment_id")

            # Gems
            gem_ids = [
                s.get("item", {}).get("id", 0)
                for s in item.get("sockets", [])
                if s.get("item")
            ]

            slots.append(CharacterEquipmentSlot(
                slot=slot,
                blizzard_item_id=blizzard_item_id,
                item_name=item_name,
                item_level=item_level,
                quality_track=quality_track,
                bonus_ids=[b for b in bonus_ids if b],
                enchant_id=enchant_id,
                gem_ids=[g for g in gem_ids if g],
            ))

        return slots

    async def get_character_professions(
        self, realm_slug: str, character_name: str
    ) -> Optional["CharacterProfessionData"]:
        """
        Fetch profession data including known recipes for a character.

        Endpoint: /profile/wow/character/{realmSlug}/{characterName}/professions
        Returns: CharacterProfessionData or None if character not found / no professions
        """
        name_lower = character_name.lower()
        name_encoded = quote(name_lower, safe='')

        path = f"/profile/wow/character/{realm_slug}/{name_encoded}/professions"
        data = await self._api_get(path)

        if not data:
            return None

        professions = []
        for section in ("primaries", "secondaries"):
            for entry in data.get(section, []):
                prof = entry.get("profession", {})
                tiers = entry.get("tiers", [])

                # Skip professions with no recipe tiers (gathering profs)
                recipe_tiers = [t for t in tiers if t.get("known_recipes")]
                if not recipe_tiers:
                    continue

                professions.append({
                    "profession_name": prof.get("name"),
                    "profession_id": prof.get("id"),
                    "is_primary": section == "primaries",
                    "tiers": [
                        {
                            "tier_name": t["tier"]["name"],
                            "tier_id": t["tier"]["id"],
                            "skill_points": t.get("skill_points", 0),
                            "max_skill_points": t.get("max_skill_points", 0),
                            "known_recipes": [
                                {"name": r["name"], "id": r["id"]}
                                for r in t.get("known_recipes", [])
                            ],
                        }
                        for t in recipe_tiers
                    ],
                })

        if not professions:
            return None

        return CharacterProfessionData(
            character_name=character_name,
            realm_slug=realm_slug,
            professions=professions,
        )

    async def get_character_encounters_raids(
        self, realm_slug: str, character_name: str
    ) -> Optional[dict]:
        """
        Fetch raid encounter progress (boss kill counts per difficulty).

        Endpoint: /profile/wow/character/{realm}/{name}/encounters/raids
        Returns the raw API response dict, or None if the character is not found.
        """
        name_encoded = quote(character_name.lower(), safe="")
        path = f"/profile/wow/character/{realm_slug}/{name_encoded}/encounters/raids"
        return await self._api_get(path)

    async def get_character_mythic_keystone_profile(
        self, realm_slug: str, character_name: str, season_id: Optional[int] = None
    ) -> Optional[dict]:
        """
        Fetch Mythic+ keystone profile (overall rating + best runs per dungeon).

        Endpoint: /profile/wow/character/{realm}/{name}/mythic-keystone-profile
        Optional season-specific endpoint when season_id is provided.
        Returns None if character not found or has no M+ data.
        """
        name_encoded = quote(character_name.lower(), safe="")
        path = f"/profile/wow/character/{realm_slug}/{name_encoded}/mythic-keystone-profile"
        if season_id:
            path += f"/season/{season_id}"
        return await self._api_get(path)

    async def get_character_achievements(
        self, realm_slug: str, character_name: str
    ) -> Optional[dict]:
        """
        Fetch all earned achievements for a character.

        WARNING: Large payload — filter to tracked_achievements IDs only when storing.
        Endpoint: /profile/wow/character/{realm}/{name}/achievements
        Returns None if character not found.
        """
        name_encoded = quote(character_name.lower(), safe="")
        path = f"/profile/wow/character/{realm_slug}/{name_encoded}/achievements"
        return await self._api_get(path)

    # ------------------------------------------------------------------
    # Journal API (static-us namespace)
    # ------------------------------------------------------------------

    async def get_journal_expansion_index(self) -> list[dict]:
        """GET /data/wow/journal-expansion/index — all expansion tiers.

        Returns a list of dicts like: [{"id": N, "name": "...", "key": {...}}]
        Sorted ascending by id; most recent expansion has the highest id.
        """
        data = await self._api_get(
            "/data/wow/journal-expansion/index",
            params={"namespace": "static-us", "locale": self.locale},
        )
        if not data:
            return []
        return data.get("tiers", [])

    async def get_journal_expansion(self, expansion_id: int) -> Optional[dict]:
        """GET /data/wow/journal-expansion/{id} — dungeon/raid instances.

        Returns a dict with "dungeons" and "raids" lists.
        """
        return await self._api_get(
            f"/data/wow/journal-expansion/{expansion_id}",
            params={"namespace": "static-us", "locale": self.locale},
        )

    async def get_journal_instance(self, instance_id: int) -> Optional[dict]:
        """GET /data/wow/journal-instance/{id} — encounters + metadata.

        Response includes encounters.encounters list and category.type
        ("DUNGEON" or "RAID").
        """
        return await self._api_get(
            f"/data/wow/journal-instance/{instance_id}",
            params={"namespace": "static-us", "locale": self.locale},
        )

    async def get_journal_encounter(self, encounter_id: int) -> Optional[dict]:
        """GET /data/wow/journal-encounter/{id} — items dropped by encounter.

        Response includes an "items" list where each entry has an "item.id"
        field with the Blizzard item ID and an optional top-level "name".
        """
        return await self._api_get(
            f"/data/wow/journal-encounter/{encounter_id}",
            params={"namespace": "static-us", "locale": self.locale},
        )

    async def get_recipe_detail(self, recipe_id: int) -> Optional[dict]:
        """GET /data/wow/recipe/{id} — crafted_item.id/name and reagents.

        Note: for new expansions (e.g. Midnight) Blizzard may not yet populate
        the crafted_item field.  Use search_items_by_name as a fallback.
        """
        return await self._api_get(
            f"/data/wow/recipe/{recipe_id}",
            params={"namespace": "static-us", "locale": self.locale},
        )

    async def search_items_by_name(self, name: str, page_size: int = 10) -> list[dict]:
        """GET /data/wow/search/item — find items by name.

        Returns a list of result 'data' dicts (empty list on no results/error).
        Each dict includes: id, name (locale dict), inventory_type, item_subclass.
        Note: item_subclass is a locale dict here, unlike get_item() which returns
        {"name": "...", "id": N}.  Use item_subclass.get("en_US") to read it.
        """
        data = await self._api_get(
            "/data/wow/search/item",
            params={
                "name.en_US": name,
                "namespace": "static-us",
                "_pageSize": str(page_size),
                "_page": "1",
            },
        )
        if not data:
            return []
        return [r["data"] for r in data.get("results", [])]

    async def get_item(self, item_id: int) -> Optional[dict]:
        """GET /data/wow/item/{id} — static item metadata (name, item_set, etc.)."""
        return await self._api_get(
            f"/data/wow/item/{item_id}",
            params={"namespace": "static-us", "locale": self.locale},
        )

    async def get_item_set(self, set_id: int) -> Optional[dict]:
        """GET /data/wow/item-set/{id} — all items in a tier set.

        Returns a dict with an 'items' list, each entry having 'id' (blizzard_item_id)
        and optionally 'name'.  Returns None on 404 or error.
        """
        return await self._api_get(
            f"/data/wow/item-set/{set_id}",
            params={"namespace": "static-us", "locale": self.locale},
        )

    async def get_item_media(self, item_id: int) -> Optional[str]:
        """GET /data/wow/media/item/{id} — returns the CDN icon URL, or None.

        The Blizzard static API has icon data for brand-new expansion items
        before Wowhead has indexed them, making this a reliable fallback when
        enrich_unenriched_items() gets no icon from the Wowhead tooltip API.
        """
        data = await self._api_get(
            f"/data/wow/media/item/{item_id}",
            params={"namespace": "static-us"},
        )
        if not data:
            return None
        for asset in data.get("assets", []):
            if asset.get("key") == "icon":
                return asset.get("value")
        return None

    async def get_item_preview(
        self, item_id: int, bonus_ids: list[int]
    ) -> Optional[str]:
        """Return the name_description.display_string for an item with bonus IDs applied.

        Calls GET /data/wow/item/{id}?bl={bonus_ids} which makes Blizzard return a
        preview_item block reflecting those bonus IDs (e.g. "Heroic" for a Hero-crest
        crafted item).  Used to detect quality track for crafted items without
        maintaining a hardcoded bonus-ID map.

        Returns the raw display_string (e.g. "Heroic", "Mythic") or None.
        """
        bl = ":".join(str(b) for b in bonus_ids)
        data = await self._api_get(
            f"/data/wow/item/{item_id}",
            params={"namespace": "static-us", "locale": self.locale, "bl": bl},
        )
        if not data:
            return None
        preview = data.get("preview_item", {})
        return preview.get("name_description", {}).get("display_string") or None

    async def get_connected_realm_id(self, realm_slug: str) -> int | None:
        """
        Resolve a realm slug to its connected realm ID.

        GET /data/wow/realm/{realmSlug}
        The response includes a connected_realm href from which we extract the ID.
        """
        path = f"/data/wow/realm/{realm_slug}"
        data = await self._api_get(path, params={"namespace": "dynamic-us", "locale": self.locale})
        if data and "connected_realm" in data:
            href = data["connected_realm"]["href"]
            match = re.search(r"/connected-realm/(\d+)", href)
            if match:
                return int(match.group(1))
        return None

    async def get_auctions(self, connected_realm_id: int) -> dict | None:
        """
        GET /data/wow/connected-realm/{connectedRealmId}/auctions

        Returns all non-commodity auctions on the connected realm.
        WARNING: Large response (can be 10+ MB for busy realms).
        """
        path = f"/data/wow/connected-realm/{connected_realm_id}/auctions"
        return await self._api_get(path, params={"namespace": "dynamic-us", "locale": self.locale})

    async def get_commodities(self) -> dict | None:
        """
        GET /data/wow/auctions/commodities

        Returns region-wide commodity auctions (flasks, enchants, gems, mats).
        Commodities are sold region-wide since patch 9.2.7.
        """
        return await self._api_get(
            "/data/wow/auctions/commodities",
            params={"namespace": "dynamic-us", "locale": self.locale},
        )

    async def get_journal_instance(self, instance_id: int) -> dict | None:
        """GET /data/wow/journal-instance/{id} — authoritative encounter list for a raid/dungeon.

        Uses the static-us namespace (not player-dependent). The response
        includes an encounters.encounters list with one entry per boss.
        """
        return await self._api_get(
            f"/data/wow/journal-instance/{instance_id}",
            params={"namespace": "static-us", "locale": self.locale},
        )

    async def sync_full_roster(
        self,
        rank_map: dict[int, str] | None = None,
    ) -> list[CharacterProfileData]:
        """
        Full sync: fetch roster, then enrich each member with profile data.

        This is the main method called by the scheduler.
        Batches character profile requests to be respectful of rate limits.

        Args:
            rank_map: WoW rank index → rank name mapping. If None, falls back
                      to the hardcoded RANK_NAME_MAP. Load from DB via
                      get_rank_name_map(pool) before calling for full config support.
        """
        effective_rank_map = rank_map if rank_map is not None else RANK_NAME_MAP

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
                    # Use basic roster data without enrichment; stable ID from roster
                    enriched.append(CharacterProfileData(
                        character_name=member.character_name,
                        realm_slug=member.realm_slug,
                        realm_name=member.realm_name,
                        character_class=member.character_class,
                        level=member.level,
                        guild_rank=member.guild_rank,
                        guild_rank_name=effective_rank_map.get(
                            member.guild_rank, f"Rank {member.guild_rank}"
                        ),
                        blizzard_character_id=member.blizzard_character_id,
                    ))
                elif result is not None:
                    # Merge guild rank from roster (profile doesn't include it).
                    # Profile ID takes precedence; fall back to roster ID if missing.
                    result.guild_rank = member.guild_rank
                    result.guild_rank_name = effective_rank_map.get(
                        member.guild_rank, f"Rank {member.guild_rank}"
                    )
                    if result.blizzard_character_id is None:
                        result.blizzard_character_id = member.blizzard_character_id
                    enriched.append(result)

            # Small delay between batches to be nice
            if i + batch_size < len(roster):
                await asyncio.sleep(0.5)

        logger.info(
            "Full roster sync complete: %d members enriched out of %d",
            len(enriched), len(roster)
        )
        return enriched
