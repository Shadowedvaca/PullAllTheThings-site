"""
Raider.IO API client.

Free, public, no authentication required.
Rate limit: ~300 requests/minute (community observed, not officially published).
Batching: 30 concurrent requests with 1s delay between batches.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://raider.io/api/v1"

PROFILE_FIELDS = ",".join([
    "mythic_plus_scores_by_season:current",
    "mythic_plus_recent_runs",
    "mythic_plus_best_runs",
    "raid_progression",
    "gear",
])


@dataclass
class RaiderIOProfile:
    name: str
    realm: str
    region: str
    overall_score: float
    dps_score: float
    healer_score: float
    tank_score: float
    score_color: str | None
    raid_progression: str | None    # e.g. "8/8 H 3/8 M"
    best_runs: list[dict] = field(default_factory=list)
    recent_runs: list[dict] = field(default_factory=list)
    gear_ilvl: int | None = None
    profile_url: str | None = None
    achievement_points: int | None = None


class RaiderIOClient:
    def __init__(self, region: str = "us"):
        self.region = region
        self._client: httpx.AsyncClient | None = None

    async def initialize(self):
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=15.0,
            headers={"Accept": "application/json"},
        )

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_character_profile(
        self, realm_slug: str, character_name: str
    ) -> RaiderIOProfile | None:
        """Fetch a character's M+ and raid data from Raider.IO."""
        if not self._client:
            raise RuntimeError("RaiderIOClient not initialized — call initialize() first")
        try:
            resp = await self._client.get(
                "/characters/profile",
                params={
                    "region": self.region,
                    "realm": realm_slug,
                    "name": character_name.lower(),
                    "fields": PROFILE_FIELDS,
                },
            )
            if resp.status_code == 400:
                return None  # Character not found on Raider.IO
            resp.raise_for_status()
            data = resp.json()
            return self._parse_profile(data)
        except (httpx.HTTPError, KeyError, Exception) as exc:
            logger.debug("Raider.IO fetch failed for %s-%s: %s", character_name, realm_slug, exc)
            return None

    def _parse_profile(self, data: dict) -> RaiderIOProfile:
        # Extract M+ scores from current season
        scores = {"all": 0.0, "dps": 0.0, "healer": 0.0, "tank": 0.0}
        score_color = None
        for season_entry in data.get("mythic_plus_scores_by_season", []):
            s = season_entry.get("scores", {})
            scores["all"] = float(s.get("all", 0) or 0)
            scores["dps"] = float(s.get("dps", 0) or 0)
            scores["healer"] = float(s.get("healer", 0) or 0)
            scores["tank"] = float(s.get("tank", 0) or 0)
            # Color from segments
            segments = season_entry.get("segments", {})
            all_seg = segments.get("all", {})
            color = all_seg.get("color")
            if color and isinstance(color, dict):
                score_color = "#{:02x}{:02x}{:02x}".format(
                    color.get("r", 0), color.get("g", 0), color.get("b", 0)
                )

        # Extract raid progression — use the last (most current) raid tier
        raid_prog = None
        for rp in data.get("raid_progression", {}).values():
            summary = rp.get("summary")
            if summary:
                raid_prog = summary

        # Parse best runs
        best_runs = [
            {
                "dungeon": r.get("dungeon"),
                "short_name": r.get("short_name"),
                "level": r.get("mythic_level"),
                "timed": r.get("num_keystone_upgrades", 0) > 0,
                "score": float(r.get("score", 0) or 0),
                "affixes": [a.get("name") for a in r.get("affixes", [])],
            }
            for r in data.get("mythic_plus_best_runs", [])
        ]

        # Parse recent runs
        recent_runs = [
            {
                "dungeon": r.get("dungeon"),
                "short_name": r.get("short_name"),
                "level": r.get("mythic_level"),
                "timed": r.get("num_keystone_upgrades", 0) > 0,
                "score": float(r.get("score", 0) or 0),
                "completed_at": r.get("completed_at"),
            }
            for r in data.get("mythic_plus_recent_runs", [])
        ]

        gear = data.get("gear", {})

        return RaiderIOProfile(
            name=data.get("name", ""),
            realm=data.get("realm", ""),
            region=data.get("region", ""),
            overall_score=scores["all"],
            dps_score=scores["dps"],
            healer_score=scores["healer"],
            tank_score=scores["tank"],
            score_color=score_color,
            raid_progression=raid_prog,
            best_runs=best_runs,
            recent_runs=recent_runs,
            gear_ilvl=gear.get("item_level_equipped"),
            profile_url=data.get("profile_url"),
            achievement_points=data.get("achievement_points"),
        )

    async def get_guild_profiles(
        self, characters: list[dict], default_realm_slug: str, batch_size: int = 30
    ) -> dict[int, RaiderIOProfile]:
        """Fetch profiles for many characters with batching.

        Each character dict must have: id, name, realm_slug (optional — falls back to default_realm_slug).
        Returns {character_id: RaiderIOProfile} for successful fetches.
        """
        results: dict[int, RaiderIOProfile] = {}
        for i in range(0, len(characters), batch_size):
            batch = characters[i: i + batch_size]
            profiles = await asyncio.gather(
                *[
                    self.get_character_profile(
                        c.get("realm_slug", default_realm_slug), c["name"]
                    )
                    for c in batch
                ],
                return_exceptions=True,
            )
            for char, profile in zip(batch, profiles):
                if isinstance(profile, RaiderIOProfile):
                    results[char["id"]] = profile
                elif isinstance(profile, Exception):
                    logger.debug("Unexpected error for character %s: %s", char.get("name"), profile)
            if i + batch_size < len(characters):
                await asyncio.sleep(1.0)  # Respect rate limits between batches

        logger.info(
            "Raider.IO: fetched %d/%d profiles", len(results), len(characters)
        )
        return results
