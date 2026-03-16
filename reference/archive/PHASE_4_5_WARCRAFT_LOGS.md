# Phase 4.5 — Warcraft Logs Integration

## Goal

Integrate the Warcraft Logs v2 GraphQL API to pull character parse percentiles, guild raid
reports, and attendance data. Each guild provides their own WCL credentials via the setup
wizard or admin UI. Data displayed on admin dashboards and optionally on player profiles.

---

## Prerequisites

- Phase 4.0 complete (site_config, config_cache)
- Guild has a Warcraft Logs account with uploaded combat logs
- OAuth2 client credentials pattern established (reuse from Blizzard client)

---

## Database Migration: 0034_warcraft_logs

### New Table: `guild_identity.wcl_config`

Single-row configuration (same pattern as `crafting_sync_config`):

```sql
CREATE TABLE guild_identity.wcl_config (
    id                      SERIAL PRIMARY KEY,
    client_id               VARCHAR(100),
    client_secret_encrypted VARCHAR(500),   -- Fernet-encrypted
    wcl_guild_name          VARCHAR(100),   -- Guild name as it appears on WCL
    wcl_server_slug         VARCHAR(50),    -- e.g., "senjin"
    wcl_server_region       VARCHAR(5) DEFAULT 'us',
    is_configured           BOOLEAN NOT NULL DEFAULT FALSE,
    last_sync               TIMESTAMP,
    last_sync_status        VARCHAR(20),    -- 'success', 'error', 'partial'
    last_sync_error         TEXT,
    sync_enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### New Table: `guild_identity.character_parses`

```sql
CREATE TABLE guild_identity.character_parses (
    id                  SERIAL PRIMARY KEY,
    character_id        INTEGER NOT NULL REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    encounter_id        INTEGER NOT NULL,           -- WCL encounter ID
    encounter_name      VARCHAR(100) NOT NULL,
    zone_id             INTEGER NOT NULL,            -- WCL zone (raid) ID
    zone_name           VARCHAR(100) NOT NULL,
    difficulty          INTEGER NOT NULL,             -- 3=Normal, 4=Heroic, 5=Mythic
    spec                VARCHAR(50) NOT NULL,
    percentile          DECIMAL(5,1) NOT NULL,       -- 0.0–100.0
    amount              DECIMAL(12,1),               -- DPS or HPS value
    report_code         VARCHAR(20),                 -- WCL report code for deep link
    fight_id            INTEGER,                     -- Fight ID within report
    fight_date          TIMESTAMP,
    last_synced         TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, encounter_id, difficulty, spec)
);
CREATE INDEX idx_parses_char ON guild_identity.character_parses(character_id);
CREATE INDEX idx_parses_zone ON guild_identity.character_parses(zone_id);
CREATE INDEX idx_parses_percentile ON guild_identity.character_parses(percentile DESC);
```

### New Table: `guild_identity.raid_reports`

```sql
CREATE TABLE guild_identity.raid_reports (
    id                  SERIAL PRIMARY KEY,
    report_code         VARCHAR(20) NOT NULL UNIQUE,  -- WCL report code (e.g., "a1b2c3d4")
    title               VARCHAR(200),
    raid_date           TIMESTAMP NOT NULL,
    zone_id             INTEGER,
    zone_name           VARCHAR(100),
    owner_name          VARCHAR(50),                   -- Who uploaded the log
    boss_kills          INTEGER DEFAULT 0,
    wipes               INTEGER DEFAULT 0,
    duration_ms         BIGINT,                        -- Total raid duration
    attendees           JSONB DEFAULT '[]',            -- Array of {name, class, spec, server}
    report_url          VARCHAR(255),                  -- Direct link to WCL report
    last_synced         TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_reports_date ON guild_identity.raid_reports(raid_date DESC);
CREATE INDEX idx_reports_zone ON guild_identity.raid_reports(zone_id);
```

---

## Task 1: Warcraft Logs Client

### New File: `src/sv_common/guild_sync/warcraftlogs_client.py`

```python
"""
Warcraft Logs v2 API client.

GraphQL API at https://www.warcraftlogs.com/api/v2/client
OAuth2 client credentials flow (same as Blizzard).
Rate limit: ~3600 points/hour.
"""

import httpx

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"


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

    async def _query(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query."""
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
    ) -> list[dict]:
        """Get best parse percentiles for a character."""
        query = """
        query ($name: String!, $server: String!, $region: String!, $zoneID: Int) {
            characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                    encounterRankings(zoneID: $zoneID)
                    zoneRankings(zoneID: $zoneID)
                }
            }
        }
        """
        variables = {
            "name": name,
            "server": server_slug,
            "region": server_region,
        }
        if zone_id:
            variables["zoneID"] = zone_id
        return await self._query(query, variables)

    # --- Guild Queries ---

    async def get_guild_reports(
        self,
        guild_name: str,
        server_slug: str,
        server_region: str = "us",
        limit: int = 25,
    ) -> list[dict]:
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

    async def get_character_rankings_for_encounter(
        self,
        name: str,
        server_slug: str,
        encounter_id: int,
        difficulty: int = 4,
        server_region: str = "us",
    ) -> dict | None:
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


class WarcraftLogsError(Exception):
    pass
```

---

## Task 2: Sync Functions

### File: `src/sv_common/guild_sync/wcl_sync.py` (new file)

```python
async def sync_guild_reports(pool, wcl_client, guild_name, server_slug, region) -> dict:
    """Fetch recent guild reports and store in raid_reports table."""
    data = await wcl_client.get_guild_reports(guild_name, server_slug, region)
    reports = data.get("reportData", {}).get("reports", {}).get("data", [])

    stats = {"new_reports": 0, "updated": 0}
    for report in reports:
        # Check if report already exists
        # If new: fetch fight details + attendance
        fight_data = await wcl_client.get_report_fights(report["code"])
        # Parse attendees from masterData.actors
        # Count boss kills from fights
        # Upsert into raid_reports
    return stats


async def sync_character_parses(pool, wcl_client, characters, server_slug, region) -> dict:
    """Fetch parse percentiles for characters from recent reports."""
    # For each character, get encounterRankings for current zone
    # Store best parse per encounter+difficulty+spec
    # Rate limit: space queries to stay under 3600 points/hour
    stats = {"synced": 0, "errors": 0}
    for batch in chunk(characters, 5):  # Smaller batches — WCL has tighter limits
        results = await asyncio.gather(
            *[wcl_client.get_character_parses(c["name"], server_slug, region)
              for c in batch],
            return_exceptions=True,
        )
        # Parse and upsert results
        await asyncio.sleep(2.0)  # ~2.5 req/sec to stay under 3600/hr
    return stats
```

---

## Task 3: Scheduler Integration

### File: `src/sv_common/guild_sync/scheduler.py`

New pipeline — runs independently of Blizzard sync:

```python
async def run_wcl_sync(self):
    """Warcraft Logs sync pipeline. Runs daily at 5 AM UTC."""
    # 1. Load WCL config from guild_identity.wcl_config
    # 2. If not configured or not enabled, skip
    # 3. Initialize WarcraftLogsClient
    # 4. Sync guild reports (last 25)
    # 5. For new reports: extract attendance, fight data
    # 6. Sync character parses for active characters
    # 7. Update wcl_config.last_sync
    # 8. Post summary to audit channel
```

Add to scheduler:

```python
scheduler.add_job(self.run_wcl_sync, "cron", hour=5, minute=0)  # Daily at 5 AM UTC
```

### Rate Limit Management

WCL allows ~3600 points/hour. Character parse queries cost ~1 point each.
For a 320-character guild with last-login filtering (~128 active):

- Guild reports query: ~5 points (25 reports + fight details)
- Character parses: ~128 points
- **Total per sync: ~133 points** — well under limit

Add a `remaining_points` tracker that reads from response headers if available.

---

## Task 4: Setup Wizard Integration

### File: `src/patt/pages/setup_pages.py`

Add an **optional** Step 4b after Blizzard setup:

```
Step 4b: Warcraft Logs (Optional)
─────────────────────────────────
Want to track parse rankings and raid attendance?

1. Go to Warcraft Logs → [link]
2. Click your profile → API Clients → Create Client
3. Copy Client ID and Client Secret

   Client ID: [________________]
   Client Secret: [________________] [Verify]

   ✅ Connected! Found guild "Pull All The Things" on Sen'jin

[Skip for now] [Save & Continue]
```

Skippable — can be configured later at `/admin/warcraft-logs`.

### Verify Endpoint: `POST /api/v1/setup/verify-wcl`

- Attempt OAuth2 token with provided credentials
- If success, query for the guild name + server to confirm it exists on WCL
- Return guild name, server, region, latest report date

---

## Task 5: Admin Page

### New Route: `GET /admin/warcraft-logs`

| Section | Content |
|---------|---------|
| **Configuration** | WCL client ID, secret (masked), guild name, server, region. Edit button. |
| **Sync Status** | Last sync time, status, error message if any. Force sync button. |
| **Recent Reports** | Table of last 25 reports: date, title, zone, boss kills, attendees count, link to WCL |
| **Attendance Summary** | Grid: player names × raid dates. Shows who attended which raids. |
| **Top Parses** | Table: character, encounter, difficulty, spec, percentile. Sortable. |

### New Route: `POST /admin/warcraft-logs/trigger`

Force sync. Returns results inline.

### Nav Entry

Add "Warcraft Logs" to admin sidebar. Only visible when `wcl_config.is_configured = TRUE`
or always visible with a "Configure" prompt.

---

## Task 6: Attendance Tracking

The most valuable WCL data for guild management is **attendance** — who showed up to raid.

### Deriving Attendance from Reports

```python
async def compute_attendance(pool) -> dict:
    """Compute attendance rates from stored raid_reports."""
    # Query last N reports (e.g., last 30 days)
    # For each report, parse attendees JSONB array
    # Cross-reference with guild_identity.wow_characters by name+server
    # Return: {character_id: {raids_attended: int, raids_possible: int, rate: float}}
```

### Display

On the admin Warcraft Logs page, show an attendance grid:

```
                  Mar 4    Mar 7    Mar 11   Rate
Trogmoon          ✓        ✓        ✓       100%
AltPlayer         ✓        ✗        ✓       67%
NewRecruit        ✗        ✓        ✓       67%
```

---

## Task 7: Public API

### Route: `GET /api/v1/guild/parses`

Returns aggregate parse data (public, no auth):

```json
{
    "ok": true,
    "data": {
        "zone": "Nerub-ar Palace",
        "difficulty": "Heroic",
        "characters": [
            {
                "name": "Trogmoon",
                "spec": "Balance",
                "encounters": [
                    {"boss": "Ulgrax", "percentile": 89.2, "amount": 1250000}
                ]
            }
        ]
    }
}
```

---

## WCL Difficulty IDs

| ID | Difficulty |
|----|-----------|
| 1 | LFR |
| 3 | Normal |
| 4 | Heroic |
| 5 | Mythic |

---

## Tests

- Unit test `WarcraftLogsClient._refresh_token()` with mock httpx
- Unit test `get_guild_reports()` GraphQL query + response parsing
- Unit test `get_report_fights()` attendance extraction
- Unit test `sync_character_parses()` with mock client
- Unit test attendance computation
- Unit test WCL credential verification endpoint
- Integration test: full sync pipeline with mock responses
- All existing tests pass

---

## Deliverables Checklist

- [ ] Migration 0034 (wcl_config, character_parses, raid_reports)
- [ ] ORM models
- [ ] `warcraftlogs_client.py` (OAuth2 + GraphQL)
- [ ] `wcl_sync.py` (report sync + parse sync + attendance)
- [ ] Scheduler: daily WCL pipeline
- [ ] Setup wizard: optional WCL step
- [ ] Admin page: `/admin/warcraft-logs`
- [ ] Attendance grid display
- [ ] Force sync endpoint
- [ ] `GET /api/v1/guild/parses` public endpoint
- [ ] Tests
