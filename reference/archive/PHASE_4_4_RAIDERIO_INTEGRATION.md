# Phase 4.4 — Raider.IO Integration

## Goal

Integrate the Raider.IO public API to pull M+ scores, recent runs, best runs, and raid
progression summaries for all guild characters. No API key required. Data displayed on
the roster page and available for admin dashboards.

---

## Prerequisites

- Phase 4.3 complete (last-login optimization in place, `progression_sync.py` exists)
- `guild_identity.wow_characters` table populated with active characters

---

## Database Migration: 0033_raiderio_profiles

### New Table: `guild_identity.raiderio_profiles`

```sql
CREATE TABLE guild_identity.raiderio_profiles (
    id                  SERIAL PRIMARY KEY,
    character_id        INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    season              VARCHAR(30) NOT NULL,       -- e.g., "season-tww-2"
    overall_score       DECIMAL(7,1) DEFAULT 0,
    dps_score           DECIMAL(7,1) DEFAULT 0,
    healer_score        DECIMAL(7,1) DEFAULT 0,
    tank_score          DECIMAL(7,1) DEFAULT 0,
    score_color         VARCHAR(7),                 -- Hex color for the score tier
    raid_progression    VARCHAR(50),                -- e.g., "8/8 H 3/8 M"
    best_runs           JSONB DEFAULT '[]',         -- Array of {dungeon, level, timed, score, affixes}
    recent_runs         JSONB DEFAULT '[]',         -- Array of {dungeon, level, timed, score, date}
    profile_url         VARCHAR(255),               -- Link to raider.io profile
    last_synced         TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, season)
);
CREATE INDEX idx_rio_char ON guild_identity.raiderio_profiles(character_id);
CREATE INDEX idx_rio_season ON guild_identity.raiderio_profiles(season);
CREATE INDEX idx_rio_score ON guild_identity.raiderio_profiles(overall_score DESC);
```

---

## Task 1: Raider.IO Client

### New File: `src/sv_common/guild_sync/raiderio_client.py`

```python
"""
Raider.IO API client.

Free, public, no authentication required.
Rate limit: ~300 requests/minute (community observed, not officially published).
Batching: 30 concurrent requests with 1s delay between batches.
"""

import asyncio
from dataclasses import dataclass
from urllib.parse import quote

import httpx

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
    raid_progression: str | None    # "8/8 H 3/8 M"
    best_runs: list[dict]
    recent_runs: list[dict]
    gear_ilvl: int | None
    profile_url: str | None
    achievement_points: int | None


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

    async def get_character_profile(
        self, realm_slug: str, character_name: str
    ) -> RaiderIOProfile | None:
        """Fetch a character's M+ and raid data from Raider.IO."""
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
        except (httpx.HTTPError, KeyError):
            return None

    def _parse_profile(self, data: dict) -> RaiderIOProfile:
        # Extract M+ scores from current season
        scores = {"all": 0, "dps": 0, "healer": 0, "tank": 0}
        score_color = None
        for season in data.get("mythic_plus_scores_by_season", []):
            s = season.get("scores", {})
            scores["all"] = s.get("all", 0)
            scores["dps"] = s.get("dps", 0)
            scores["healer"] = s.get("healer", 0)
            scores["tank"] = s.get("tank", 0)
            # Color from segments
            segments = season.get("segments", {})
            all_seg = segments.get("all", {})
            color = all_seg.get("color")
            if color:
                score_color = "#{:02x}{:02x}{:02x}".format(
                    color.get("r", 0), color.get("g", 0), color.get("b", 0)
                )

        # Extract raid progression
        raid_prog = None
        for rp in data.get("raid_progression", {}).values():
            raid_prog = rp.get("summary")  # Takes the last (most current) tier

        # Parse best runs
        best_runs = [
            {
                "dungeon": r.get("dungeon"),
                "short_name": r.get("short_name"),
                "level": r.get("mythic_level"),
                "timed": r.get("num_keystone_upgrades", 0) > 0,
                "score": r.get("score", 0),
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
                "score": r.get("score", 0),
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
        self, realm_slug: str, characters: list[dict], batch_size: int = 30
    ) -> dict[int, RaiderIOProfile]:
        """Fetch profiles for many characters with batching."""
        results = {}
        for i in range(0, len(characters), batch_size):
            batch = characters[i : i + batch_size]
            profiles = await asyncio.gather(
                *[
                    self.get_character_profile(
                        c.get("realm_slug", realm_slug), c["name"]
                    )
                    for c in batch
                ],
                return_exceptions=True,
            )
            for char, profile in zip(batch, profiles):
                if isinstance(profile, RaiderIOProfile):
                    results[char["id"]] = profile
            if i + batch_size < len(characters):
                await asyncio.sleep(1.0)  # Respect rate limits
        return results
```

---

## Task 2: Sync Integration

### File: `src/sv_common/guild_sync/progression_sync.py`

Add Raider.IO sync function:

```python
async def sync_raiderio_profiles(
    pool, raiderio_client: RaiderIOClient, characters: list[dict], realm_slug: str
) -> dict:
    """Fetch Raider.IO data and store in raiderio_profiles table."""
    profiles = await raiderio_client.get_guild_profiles(realm_slug, characters)

    async with pool.acquire() as conn:
        for char_id, profile in profiles.items():
            await conn.execute("""
                INSERT INTO guild_identity.raiderio_profiles
                    (character_id, season, overall_score, dps_score, healer_score,
                     tank_score, score_color, raid_progression, best_runs,
                     recent_runs, profile_url, last_synced)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, NOW())
                ON CONFLICT (character_id, season) DO UPDATE SET
                    overall_score = EXCLUDED.overall_score,
                    dps_score = EXCLUDED.dps_score,
                    healer_score = EXCLUDED.healer_score,
                    tank_score = EXCLUDED.tank_score,
                    score_color = EXCLUDED.score_color,
                    raid_progression = EXCLUDED.raid_progression,
                    best_runs = EXCLUDED.best_runs,
                    recent_runs = EXCLUDED.recent_runs,
                    profile_url = EXCLUDED.profile_url,
                    last_synced = NOW()
            """, char_id, "current", profile.overall_score, ...)

    return {"synced": len(profiles), "total": len(characters)}
```

### Scheduler Integration

Add to `run_blizzard_sync()` pipeline, after M+ data:

```python
# Step: Raider.IO sync (uses last-login filtered characters)
rio_client = RaiderIOClient(region="us")
await rio_client.initialize()
try:
    rio_stats = await sync_raiderio_profiles(
        self.db_pool, rio_client, active_characters, realm_slug
    )
finally:
    await rio_client.close()
```

The last-login optimization applies — only fetch profiles for characters that have
been active since the last sync.

---

## Task 3: Roster Page Integration

### File: `src/patt/pages/public_pages.py` (roster endpoint)

Update the roster API to include Raider.IO data:

```python
# In the roster query, LEFT JOIN raiderio_profiles:
"""
LEFT JOIN guild_identity.raiderio_profiles rio
    ON rio.character_id = wc.id AND rio.season = 'current'
"""
```

### File: `src/patt/templates/public/roster.html`

Add M+ Score column to the Full Roster tab:

| Column | Data | Display |
|--------|------|---------|
| M+ Score | `rio.overall_score` | Number with score color (e.g., `<span style="color: #ff8000">2450</span>`) |
| Raid Prog | `rio.raid_progression` | e.g., "8/8 H 3/8 M" |
| R.IO Link | `rio.profile_url` | Small external link icon |

### Roster Composition Tab

Add average M+ score per role in the composition breakdown.

---

## Task 4: API Endpoint

### New Route: `GET /api/v1/guild/progression`

Returns aggregated progression data for the guild:

```json
{
    "ok": true,
    "data": {
        "mythic_plus": {
            "average_score": 2150.3,
            "median_score": 2050.0,
            "top_10": [
                {"name": "Trogmoon", "score": 2650.5, "color": "#ff8000"}
            ]
        },
        "raid_progression": {
            "guild_best": "8/8 H 6/8 M",
            "heroic_clearers": 18,
            "mythic_progressed": 12
        }
    }
}
```

Public endpoint (no auth required) — same as roster.

---

## Rate Limit Considerations

| Guild Size | Characters | API Calls/Sync | Time | Daily (4 syncs) |
|-----------|-----------|---------------|------|-----------------|
| Small (100) | ~40 active | 40 | ~2s | 160 |
| Medium (300) | ~120 active | 120 | ~4s | 480 |
| Large (500) | ~200 active | 200 | ~7s | 800 |

All well within Raider.IO's ~300/min observed limit. The 1-second delay between batches
of 30 keeps us at ~30 req/sec peak, well under any reasonable threshold.

---

## Tests

- Unit test `RaiderIOClient._parse_profile()` with sample API response
- Unit test `get_character_profile()` with mock httpx (success, 400, timeout)
- Unit test `sync_raiderio_profiles()` with mock client and DB
- Unit test `should_sync_character()` integration with Raider.IO path
- Verify roster page renders M+ score column
- Verify score colors render correctly
- All existing tests pass

---

## Deliverables Checklist

- [ ] Migration 0033 (raiderio_profiles table)
- [ ] ORM model for RaiderIOProfile
- [ ] `raiderio_client.py` with full client implementation
- [ ] `sync_raiderio_profiles()` in progression_sync.py
- [ ] Scheduler integration (after Blizzard sync, uses last-login filter)
- [ ] Roster page: M+ Score and Raid Prog columns
- [ ] Score color rendering
- [ ] `GET /api/v1/guild/progression` endpoint
- [ ] Raider.IO profile URL links on roster
- [ ] Tests
