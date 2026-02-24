# Phase 2.5A: Database Schema & Blizzard API Client

## Prerequisites
- Phase 1 complete (PostgreSQL running, Python environment set up)
- Phase 2 complete (sv_common package exists, FastAPI app running)
- Blizzard API credentials registered at https://develop.battle.net

## Task 1: PostgreSQL Schema

Create the `guild_identity` schema in PostgreSQL. All tables go in this schema.

```sql
-- schema: guild_identity

CREATE SCHEMA IF NOT EXISTS guild_identity;

-- Persons: abstract "people" that characters and discord accounts link to
CREATE TABLE guild_identity.persons (
    id SERIAL PRIMARY KEY,
    display_name VARCHAR(100) NOT NULL,  -- Friendly name, e.g., "Mike" or "Trog"
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- WoW Characters: populated from Blizzard API + addon data
CREATE TABLE guild_identity.wow_characters (
    id SERIAL PRIMARY KEY,
    person_id INTEGER REFERENCES guild_identity.persons(id) ON DELETE SET NULL,
    
    -- From Blizzard API (guild roster endpoint)
    character_name VARCHAR(50) NOT NULL,
    realm_slug VARCHAR(50) NOT NULL,
    realm_name VARCHAR(100),
    character_class VARCHAR(30),         -- "Warrior", "Druid", etc.
    active_spec VARCHAR(50),             -- "Protection", "Balance", etc.
    level INTEGER,
    item_level INTEGER,
    guild_rank INTEGER,                  -- Rank index (0 = Guild Leader, 4 = Initiate)
    guild_rank_name VARCHAR(50),         -- "Guild Leader", "Officer", etc.
    last_login_timestamp BIGINT,         -- Unix timestamp from Blizzard
    
    -- From addon (SavedVariables upload)
    guild_note TEXT,
    officer_note TEXT,
    addon_last_seen TIMESTAMPTZ,
    
    -- Metadata
    is_main BOOLEAN DEFAULT FALSE,       -- Migrated from existing sheet Main/Alt
    role_category VARCHAR(10),           -- "Tank", "Healer", "Melee", "Ranged"
    blizzard_last_sync TIMESTAMPTZ,
    addon_last_sync TIMESTAMPTZ,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    removed_at TIMESTAMPTZ,              -- NULL = still in guild, set when they leave
    
    UNIQUE(character_name, realm_slug)
);

CREATE INDEX idx_wow_chars_person ON guild_identity.wow_characters(person_id);
CREATE INDEX idx_wow_chars_rank ON guild_identity.wow_characters(guild_rank);
CREATE INDEX idx_wow_chars_removed ON guild_identity.wow_characters(removed_at);
CREATE INDEX idx_wow_chars_name_lower ON guild_identity.wow_characters(LOWER(character_name));

-- Discord Members: populated from Discord bot
CREATE TABLE guild_identity.discord_members (
    id SERIAL PRIMARY KEY,
    person_id INTEGER REFERENCES guild_identity.persons(id) ON DELETE SET NULL,
    
    discord_id VARCHAR(25) NOT NULL UNIQUE,  -- Discord snowflake ID
    username VARCHAR(50) NOT NULL,            -- Discord username (new system, no discriminator)
    display_name VARCHAR(50),                 -- Server nickname if set
    
    -- Role tracking (highest guild-relevant role)
    highest_guild_role VARCHAR(30),           -- "GM", "Officer", "Veteran", "Member", "Initiate"
    all_guild_roles TEXT[],                   -- Array of all guild-relevant roles
    
    -- Metadata
    joined_server_at TIMESTAMPTZ,
    last_sync TIMESTAMPTZ,
    is_present BOOLEAN DEFAULT TRUE,         -- FALSE if they left the server
    removed_at TIMESTAMPTZ,
    
    first_seen TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_discord_members_person ON guild_identity.discord_members(person_id);
CREATE INDEX idx_discord_members_username ON guild_identity.discord_members(LOWER(username));
CREATE INDEX idx_discord_members_display ON guild_identity.discord_members(LOWER(display_name));

-- Character-to-Person links with confidence tracking
CREATE TABLE guild_identity.identity_links (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES guild_identity.persons(id) ON DELETE CASCADE,
    
    -- One of these will be set (not both)
    wow_character_id INTEGER REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    discord_member_id INTEGER REFERENCES guild_identity.discord_members(id) ON DELETE CASCADE,
    
    -- How this link was created
    link_source VARCHAR(30) NOT NULL,  -- "exact_name_match", "guild_note", "officer_note", 
                                       -- "migrated_sheet", "manual", "fuzzy_match"
    confidence VARCHAR(10) NOT NULL DEFAULT 'high',  -- "high", "medium", "low"
    is_confirmed BOOLEAN DEFAULT FALSE,  -- TRUE = human verified
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    confirmed_by VARCHAR(50),  -- Who confirmed it
    
    UNIQUE(wow_character_id),   -- A character can only link to one person
    UNIQUE(discord_member_id),  -- A discord account can only link to one person
    
    -- Ensure at least one target is set
    CHECK (wow_character_id IS NOT NULL OR discord_member_id IS NOT NULL)
);

-- Audit Issues: tracked integrity problems
CREATE TABLE guild_identity.audit_issues (
    id SERIAL PRIMARY KEY,
    issue_type VARCHAR(50) NOT NULL,
    -- Types: "orphan_wow", "orphan_discord", "role_mismatch", 
    --        "auto_link_suggestion", "stale_character", "rank_change"
    
    severity VARCHAR(10) NOT NULL DEFAULT 'info',  -- "critical", "warning", "info"
    
    -- References (nullable, depends on issue type)
    wow_character_id INTEGER REFERENCES guild_identity.wow_characters(id) ON DELETE CASCADE,
    discord_member_id INTEGER REFERENCES guild_identity.discord_members(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES guild_identity.persons(id) ON DELETE SET NULL,
    
    -- Details
    summary TEXT NOT NULL,           -- Human-readable one-liner
    details JSONB,                   -- Structured data about the issue
    
    -- Lifecycle
    first_detected TIMESTAMPTZ DEFAULT NOW(),
    last_detected TIMESTAMPTZ DEFAULT NOW(),
    notified_at TIMESTAMPTZ,         -- When we sent Discord notification
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(50),
    resolution_note TEXT,
    
    -- Prevent duplicate active issues
    issue_hash VARCHAR(64) NOT NULL,  -- Hash of type + relevant IDs for dedup
    UNIQUE(issue_hash, resolved_at)   -- Can have same hash if previous one resolved
);

CREATE INDEX idx_audit_issues_type ON guild_identity.audit_issues(issue_type);
CREATE INDEX idx_audit_issues_unresolved ON guild_identity.audit_issues(resolved_at) WHERE resolved_at IS NULL;
CREATE INDEX idx_audit_issues_unnotified ON guild_identity.audit_issues(notified_at) WHERE notified_at IS NULL AND resolved_at IS NULL;

-- Sync Log: track when each data source was last synced
CREATE TABLE guild_identity.sync_log (
    id SERIAL PRIMARY KEY,
    source VARCHAR(30) NOT NULL,  -- "blizzard_api", "discord_bot", "addon_upload"
    status VARCHAR(20) NOT NULL,  -- "success", "partial", "error"
    
    characters_found INTEGER,
    characters_updated INTEGER,
    characters_new INTEGER,
    characters_removed INTEGER,
    
    error_message TEXT,
    duration_seconds FLOAT,
    
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_sync_log_source ON guild_identity.sync_log(source, started_at DESC);
```

### Migration Script: Import Existing Google Sheet Data

Create a one-time migration script that reads from the existing Google Sheet (or an exported CSV)
and seeds the identity tables. The existing sheet has:
- `Characters` tab: Discord, Character, Class, Spec, Role, Main/Alt
- `DiscordIDs` tab: Discord username → Discord User ID mapping

```python
# sv_common/guild_sync/migration.py

"""
One-time migration from Google Sheets CSV exports to PostgreSQL.

Usage:
  python -m sv_common.guild_sync.migration \
    --characters characters.csv \
    --discord-ids discord_ids.csv
    
CSV formats:
  characters.csv: Discord,Character,Class,Spec,Role,MainAlt
  discord_ids.csv: Discord,DiscordID
"""

import csv
import hashlib
from datetime import datetime, timezone
from typing import Optional
import asyncpg

# Role category mapping from spec
ROLE_MAP = {
    # Tanks
    "Protection": "Tank",  # Warrior or Paladin
    "Guardian": "Tank",
    "Blood": "Tank",
    "Vengeance": "Tank",
    "Brewmaster": "Tank",
    # Healers
    "Restoration": "Healer",  # Druid or Shaman
    "Holy": "Healer",  # Priest or Paladin
    "Discipline": "Healer",
    "Mistweaver": "Healer",
    "Preservation": "Healer",
    # Melee DPS
    "Arms": "Melee", "Fury": "Melee",
    "Retribution": "Melee",
    "Enhancement": "Melee",
    "Feral": "Melee",
    "Windwalker": "Melee",
    "Havoc": "Melee",
    "Assassination": "Melee", "Outlaw": "Melee", "Subtlety": "Melee",
    "Unholy": "Melee", "Frost": "Melee",  # DK Frost — context-dependent
    "Survival": "Melee",
    # Ranged DPS
    "Balance": "Ranged",
    "Elemental": "Ranged",
    "Shadow": "Ranged",
    "Arcane": "Ranged", "Fire": "Ranged",  # Mage Frost handled below
    "Affliction": "Ranged", "Demonology": "Ranged", "Destruction": "Ranged",
    "Beast Mastery": "Ranged", "Marksmanship": "Ranged",
    "Devastation": "Ranged",
    "Augmentation": "Ranged",
}

# Special cases where spec name is shared across classes
def get_role_category(wow_class: str, spec: str, explicit_role: str = "") -> str:
    """Determine role category from class + spec, falling back to explicit role."""
    if explicit_role in ("Tank", "Healer", "Melee", "Ranged"):
        return explicit_role
    
    # Handle ambiguous specs
    spec_lower = spec.lower().strip()
    class_lower = wow_class.lower().strip()
    
    if spec_lower == "frost":
        return "Melee" if class_lower == "death knight" else "Ranged"  # Mage
    if spec_lower == "holy":
        return "Healer"  # Both Priest and Paladin Holy are healers
    if spec_lower == "protection":
        return "Tank"  # Both Warrior and Paladin Prot are tanks
    if spec_lower == "restoration":
        return "Healer"  # Both Druid and Shaman Resto are healers
    
    return ROLE_MAP.get(spec, "Ranged")  # Default to Ranged if unknown


async def migrate_from_csv(
    db_pool: asyncpg.Pool,
    characters_csv: str,
    discord_ids_csv: str
):
    """Import existing Google Sheet data into the identity system."""
    
    # Load Discord ID mappings
    discord_map = {}  # lowercase discord name → discord_id
    with open(discord_ids_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('Discord', '').strip().lower()
            did = row.get('DiscordID', '').strip()
            if name and did:
                discord_map[name] = did
    
    # Group characters by discord name to create persons
    persons = {}  # lowercase discord name → list of character rows
    with open(characters_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            discord_name = row.get('Discord', '').strip()
            if not discord_name:
                continue
            key = discord_name.lower()
            if key not in persons:
                persons[key] = {"discord_name": discord_name, "characters": []}
            persons[key]["characters"].append(row)
    
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for discord_key, data in persons.items():
                discord_name = data["discord_name"]
                chars = data["characters"]
                
                # Create person
                person_id = await conn.fetchval(
                    """INSERT INTO guild_identity.persons (display_name)
                       VALUES ($1) RETURNING id""",
                    discord_name
                )
                
                # Create discord member if we have an ID
                discord_id = discord_map.get(discord_key)
                if discord_id:
                    dm_id = await conn.fetchval(
                        """INSERT INTO guild_identity.discord_members 
                           (person_id, discord_id, username)
                           VALUES ($1, $2, $3) RETURNING id""",
                        person_id, discord_id, discord_name
                    )
                    # Create identity link for discord
                    await conn.execute(
                        """INSERT INTO guild_identity.identity_links
                           (person_id, discord_member_id, link_source, confidence, is_confirmed)
                           VALUES ($1, $2, 'migrated_sheet', 'high', TRUE)""",
                        person_id, dm_id
                    )
                
                # Create characters
                for char_row in chars:
                    char_name = char_row.get('Character', '').strip()
                    wow_class = char_row.get('Class', '').strip()
                    spec = char_row.get('Spec', '').strip()
                    role = char_row.get('Role', '').strip()
                    main_alt = char_row.get('MainAlt', char_row.get('Main/Alt', '')).strip()
                    
                    role_cat = get_role_category(wow_class, spec, role)
                    is_main = main_alt.lower() == 'main'
                    
                    wc_id = await conn.fetchval(
                        """INSERT INTO guild_identity.wow_characters
                           (person_id, character_name, realm_slug, character_class,
                            active_spec, role_category, is_main)
                           VALUES ($1, $2, 'unknown', $3, $4, $5, $6)
                           ON CONFLICT (character_name, realm_slug) DO UPDATE
                           SET person_id = $1, character_class = $3, active_spec = $4,
                               role_category = $5, is_main = $6
                           RETURNING id""",
                        person_id, char_name, wow_class, spec, role_cat, is_main
                    )
                    
                    # Create identity link for character
                    await conn.execute(
                        """INSERT INTO guild_identity.identity_links
                           (person_id, wow_character_id, link_source, confidence, is_confirmed)
                           VALUES ($1, $2, 'migrated_sheet', 'high', TRUE)""",
                        person_id, wc_id
                    )
    
    print(f"Migrated {len(persons)} persons with characters and Discord links.")
```

## Task 2: Blizzard API Client

This client handles OAuth2 authentication and all Blizzard API calls.

```python
# sv_common/guild_sync/blizzard_client.py

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
```

## Task 3: Database Sync Layer

This module writes Blizzard API results into PostgreSQL, tracking changes.

```python
# sv_common/guild_sync/db_sync.py

"""
Writes Blizzard API and addon data into the guild_identity PostgreSQL schema.

Handles:
- Upsert of character data (new characters, updated specs/levels, departures)
- Tracking of who left the guild vs. who's still present
- Marking characters as removed when they disappear from the roster
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from .blizzard_client import CharacterProfileData, RANK_NAME_MAP
from .migration import get_role_category

logger = logging.getLogger(__name__)


async def sync_blizzard_roster(
    pool: asyncpg.Pool,
    characters: list[CharacterProfileData],
) -> dict:
    """
    Sync a full Blizzard API roster pull into the database.
    
    Returns stats dict: {found, updated, new, removed}
    """
    now = datetime.now(timezone.utc)
    stats = {"found": len(characters), "updated": 0, "new": 0, "removed": 0}
    
    # Build set of current character keys
    current_keys = set()
    for char in characters:
        current_keys.add((char.character_name.lower(), char.realm_slug.lower()))
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            
            # Upsert each character
            for char in characters:
                role_cat = get_role_category(
                    char.character_class,
                    char.active_spec or "",
                    ""
                )
                
                existing = await conn.fetchrow(
                    """SELECT id, active_spec, level, item_level, guild_rank, removed_at
                       FROM guild_identity.wow_characters
                       WHERE LOWER(character_name) = $1 AND LOWER(realm_slug) = $2""",
                    char.character_name.lower(), char.realm_slug.lower()
                )
                
                if existing:
                    # Update existing character
                    await conn.execute(
                        """UPDATE guild_identity.wow_characters SET
                            character_class = $3,
                            active_spec = $4,
                            level = $5,
                            item_level = $6,
                            guild_rank = $7,
                            guild_rank_name = $8,
                            last_login_timestamp = $9,
                            role_category = $10,
                            blizzard_last_sync = $11,
                            removed_at = NULL,
                            realm_name = $12
                           WHERE id = $1""",
                        existing['id'],
                        # Skip $2
                        char.character_class,
                        char.active_spec,
                        char.level,
                        char.item_level,
                        char.guild_rank,
                        RANK_NAME_MAP.get(char.guild_rank, f"Rank {char.guild_rank}"),
                        char.last_login_timestamp,
                        role_cat,
                        now,
                        char.realm_name,
                    )
                    
                    if existing['removed_at'] is not None:
                        logger.info("Character %s has returned to the guild", char.character_name)
                    
                    stats["updated"] += 1
                else:
                    # New character
                    await conn.execute(
                        """INSERT INTO guild_identity.wow_characters
                           (character_name, realm_slug, realm_name, character_class,
                            active_spec, level, item_level, guild_rank, guild_rank_name,
                            last_login_timestamp, role_category, blizzard_last_sync)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                        char.character_name, char.realm_slug, char.realm_name,
                        char.character_class, char.active_spec, char.level,
                        char.item_level, char.guild_rank,
                        RANK_NAME_MAP.get(char.guild_rank, f"Rank {char.guild_rank}"),
                        char.last_login_timestamp, role_cat, now,
                    )
                    logger.info("New guild member detected: %s (%s)", char.character_name, char.character_class)
                    stats["new"] += 1
            
            # Mark characters as removed if they're no longer in the roster
            all_active = await conn.fetch(
                """SELECT id, character_name, realm_slug
                   FROM guild_identity.wow_characters
                   WHERE removed_at IS NULL"""
            )
            
            for row in all_active:
                key = (row['character_name'].lower(), row['realm_slug'].lower())
                if key not in current_keys:
                    await conn.execute(
                        """UPDATE guild_identity.wow_characters
                           SET removed_at = $2
                           WHERE id = $1""",
                        row['id'], now
                    )
                    logger.info("Character %s has left the guild", row['character_name'])
                    stats["removed"] += 1
    
    logger.info(
        "Blizzard sync stats: %d found, %d updated, %d new, %d removed",
        stats["found"], stats["updated"], stats["new"], stats["removed"]
    )
    return stats


async def sync_addon_data(
    pool: asyncpg.Pool,
    addon_characters: list[dict],
) -> dict:
    """
    Sync data from the WoW addon upload (guild notes, officer notes).
    
    addon_characters format:
    [
        {
            "name": "Trogmoon",
            "realm": "Sen'jin",
            "guild_note": "GM / Mike",
            "officer_note": "Discord: Trog",
            "rank": 0,
            "rank_name": "Guild Leader",
            "class": "Druid",
            "level": 80,
            "last_online": "0d 2h 15m",
        },
        ...
    ]
    """
    now = datetime.now(timezone.utc)
    stats = {"processed": 0, "updated": 0, "not_found": 0}
    
    async with pool.acquire() as conn:
        for char_data in addon_characters:
            name = char_data.get("name", "").strip()
            if not name:
                continue
            
            stats["processed"] += 1
            
            # Try to find the character in our DB
            # Use case-insensitive match since addon might have different casing
            row = await conn.fetchrow(
                """SELECT id FROM guild_identity.wow_characters
                   WHERE LOWER(character_name) = $1 AND removed_at IS NULL""",
                name.lower()
            )
            
            if row:
                await conn.execute(
                    """UPDATE guild_identity.wow_characters SET
                        guild_note = $2,
                        officer_note = $3,
                        addon_last_sync = $4
                       WHERE id = $1""",
                    row['id'],
                    char_data.get("guild_note", ""),
                    char_data.get("officer_note", ""),
                    now,
                )
                stats["updated"] += 1
            else:
                logger.warning("Addon data for character '%s' not found in DB", name)
                stats["not_found"] += 1
    
    logger.info(
        "Addon sync stats: %d processed, %d updated, %d not found",
        stats["processed"], stats["updated"], stats["not_found"]
    )
    return stats
```

## Task 4: Sync Log Helper

```python
# sv_common/guild_sync/sync_logger.py

"""Helper for recording sync operations in the sync_log table."""

import logging
import time
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


class SyncLogEntry:
    """Context manager that logs a sync operation."""
    
    def __init__(self, pool: asyncpg.Pool, source: str):
        self.pool = pool
        self.source = source
        self.log_id = None
        self.start_time = None
        self.stats = {}
    
    async def __aenter__(self):
        self.start_time = time.time()
        async with self.pool.acquire() as conn:
            self.log_id = await conn.fetchval(
                """INSERT INTO guild_identity.sync_log (source, status)
                   VALUES ($1, 'running') RETURNING id""",
                self.source
            )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        status = "error" if exc_type else "success"
        error_msg = str(exc_val) if exc_val else None
        
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE guild_identity.sync_log SET
                    status = $2,
                    characters_found = $3,
                    characters_updated = $4,
                    characters_new = $5,
                    characters_removed = $6,
                    error_message = $7,
                    duration_seconds = $8,
                    completed_at = $9
                   WHERE id = $1""",
                self.log_id,
                status,
                self.stats.get("found"),
                self.stats.get("updated"),
                self.stats.get("new"),
                self.stats.get("removed"),
                error_msg,
                duration,
                datetime.now(timezone.utc),
            )
        
        if exc_type:
            logger.error("Sync %s failed after %.1fs: %s", self.source, duration, exc_val)
        else:
            logger.info("Sync %s completed in %.1fs", self.source, duration)
        
        return False  # Don't suppress exceptions
```

## Testing Requirements for Phase 2.5A

1. **Schema tests:** Verify all tables create correctly, constraints work (unique, foreign keys, check)
2. **Blizzard client tests:**
   - Mock OAuth token flow
   - Mock guild roster response, verify parsing
   - Mock character profile response, verify spec/ilvl extraction
   - Test special character handling in names (Zatañña)
   - Test 404 handling for deleted characters
   - Test token refresh when expired
3. **DB sync tests:**
   - Test upsert: new character creates row
   - Test upsert: existing character updates fields
   - Test removal detection: character missing from roster gets marked removed
   - Test return-to-guild: removed character reappears, removed_at cleared
4. **Migration tests:**
   - Test CSV import creates persons, characters, and links correctly
   - Test role category detection for ambiguous specs (Frost DK vs Frost Mage)
   - Test duplicate handling
