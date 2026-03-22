"""
Warcraft Logs v2 API client.

GraphQL API at https://www.warcraftlogs.com/api/v2/client
OAuth2 client credentials flow (same pattern as Blizzard).
Rate limit: ~3600 points/hour. Character parse queries cost ~1 point each.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"


class WarcraftLogsError(Exception):
    pass


class WarcraftLogsClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_expires: float = 0
        self._client: httpx.AsyncClient | None = None

    async def initialize(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        await self._refresh_token()

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _refresh_token(self):
        """OAuth2 client credentials grant."""
        resp = await self._client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 3600) - 300

    async def _query(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query. Returns the data dict."""
        if not self._client:
            raise RuntimeError(
                "WarcraftLogsClient not initialized — call initialize() first"
            )
        if time.time() >= self._token_expires:
            await self._refresh_token()
        resp = await self._client.post(
            API_URL,
            json={"query": query, "variables": variables or {}},
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        result = resp.json()
        if "errors" in result:
            raise WarcraftLogsError(result["errors"])
        return result.get("data", {})

    # --- Character Queries ---

    async def get_character_parses(
        self,
        name: str,
        server_slug: str,
        server_region: str = "us",
        zone_id: int | None = None,
    ) -> dict:
        """Get best parse percentiles for a character across all encounters in a zone."""
        query = """
        query ($name: String!, $server: String!, $region: String!, $zoneID: Int) {
            characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                    zoneRankings(zoneID: $zoneID)
                }
            }
        }
        """
        variables: dict = {
            "name": name,
            "server": server_slug,
            "region": server_region,
        }
        if zone_id is not None:
            variables["zoneID"] = zone_id
        return await self._query(query, variables)

    async def get_character_rankings_for_encounter(
        self,
        name: str,
        server_slug: str,
        encounter_id: int,
        difficulty: int = 4,
        server_region: str = "us",
    ) -> dict:
        """Get a character's best parse for a specific encounter."""
        query = """
        query ($name: String!, $server: String!, $region: String!,
               $encounterID: Int!, $difficulty: Int!) {
            characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                    encounterRankings(
                        encounterID: $encounterID,
                        difficulty: $difficulty
                    )
                }
            }
        }
        """
        return await self._query(query, {
            "name": name,
            "server": server_slug,
            "region": server_region,
            "encounterID": encounter_id,
            "difficulty": difficulty,
        })

    async def get_world_zones(self) -> dict[int, str]:
        """Fetch all WCL zone IDs and names from worldData.

        Returns {zone_id: zone_name}.
        """
        query = """
        query {
            worldData {
                zones {
                    id
                    name
                }
            }
        }
        """
        data = await self._query(query)
        zones = data.get("worldData", {}).get("zones") or []
        return {z["id"]: z["name"] for z in zones if z.get("id") and z.get("name")}

    # --- Guild Queries ---

    async def get_guild_reports(
        self,
        guild_name: str,
        server_slug: str,
        server_region: str = "us",
        limit: int = 25,
    ) -> dict:
        """Get recent raid reports for the guild."""
        query = """
        query ($name: String!, $server: String!, $region: String!, $limit: Int!) {
            reportData {
                reports(guildName: $name, guildServerSlug: $server,
                        guildServerRegion: $region, limit: $limit) {
                    data {
                        code
                        title
                        startTime
                        endTime
                        zone { id name }
                        owner { name }
                    }
                    total
                }
            }
        }
        """
        return await self._query(query, {
            "name": guild_name,
            "server": server_slug,
            "region": server_region,
            "limit": limit,
        })

    async def get_report_fights(self, report_code: str) -> dict:
        """Get fight details and attendance from a specific report."""
        query = """
        query ($code: String!) {
            reportData {
                report(code: $code) {
                    code
                    title
                    startTime
                    endTime
                    zone { id name }
                    owner { name }
                    masterData {
                        actors(type: "Player") {
                            name
                            type
                            subType
                            server
                        }
                    }
                    fights(killType: Kills) {
                        id
                        name
                        kill
                        startTime
                        endTime
                        difficulty
                    }
                }
            }
        }
        """
        return await self._query(query, {"code": report_code})

    async def verify_credentials(
        self,
        guild_name: str,
        server_slug: str,
        server_region: str = "us",
    ) -> dict:
        """Verify credentials and return basic guild info.

        Returns dict with guild_name, server, region, report_count, latest_report_date.
        Raises WarcraftLogsError or httpx.HTTPStatusError on failure.
        """
        data = await self.get_guild_reports(
            guild_name, server_slug, server_region, limit=1
        )
        reports_data = data.get("reportData", {}).get("reports", {})
        reports_list = reports_data.get("data", [])
        total = reports_data.get("total", 0)

        latest_date = None
        if reports_list:
            start_ms = reports_list[0].get("startTime", 0)
            if start_ms:
                latest_date = start_ms / 1000  # ms → seconds epoch

        return {
            "guild_name": guild_name,
            "server": server_slug,
            "region": server_region,
            "report_count": total,
            "latest_report_date": latest_date,
        }
