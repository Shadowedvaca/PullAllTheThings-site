"""Setup wizard API endpoints — accessible only when setup_complete is FALSE."""

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.config import get_settings
from guild_portal.deps import get_db
from sv_common.auth.passwords import hash_password
from sv_common.config_cache import get_site_config, set_site_config
from sv_common.crypto import decrypt_secret, encrypt_secret
from sv_common.db.models import DiscordConfig, GuildRank, Player, RankWowMapping, SiteConfig, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/setup", tags=["setup"])

DISCORD_API = "https://discord.com/api/v10"
BLIZZARD_TOKEN_URL = "https://oauth.battle.net/token"
BLIZZARD_API_BASE = "https://us.api.blizzard.com"


def _require_setup_incomplete():
    """Raise 404 if setup is already complete."""
    if get_site_config().get("setup_complete"):
        raise HTTPException(status_code=404, detail="Not found")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class GuildIdentityBody(BaseModel):
    guild_name: str
    guild_tagline: Optional[str] = None
    guild_mission: Optional[str] = None
    accent_color_hex: str = "#d4a84b"
    logo_url: Optional[str] = None


class VerifyDiscordTokenBody(BaseModel):
    token: str


class VerifyDiscordGuildBody(BaseModel):
    guild_id: str


class VerifyBlizzardBody(BaseModel):
    client_id: str
    client_secret: str
    realm_slug: str
    guild_slug: str


class RankMappingEntry(BaseModel):
    wow_rank_index: int
    guild_rank_id: int


class RankNamesBody(BaseModel):
    rank_names: dict[str, str]  # level (str) -> name
    wow_mappings: list[RankMappingEntry]


class DiscordRoleEntry(BaseModel):
    guild_rank_id: int
    discord_role_id: Optional[str] = None


class DiscordRolesBody(BaseModel):
    roles: list[DiscordRoleEntry]


class ChannelsBody(BaseModel):
    audit_channel_id: Optional[str] = None
    crafters_corner_channel_id: Optional[str] = None
    raid_announcement_channel_id: Optional[str] = None


class CreateAdminBody(BaseModel):
    display_name: str
    discord_username: str
    password: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_create_site_config(db: AsyncSession) -> SiteConfig:
    result = await db.execute(select(SiteConfig).limit(1))
    sc = result.scalar_one_or_none()
    if sc is None:
        sc = SiteConfig(guild_name="My Guild", accent_color_hex="#d4a84b")
        db.add(sc)
        await db.flush()
    return sc


async def _get_or_create_discord_config(db: AsyncSession) -> DiscordConfig:
    result = await db.execute(select(DiscordConfig).limit(1))
    dc = result.scalar_one_or_none()
    if dc is None:
        dc = DiscordConfig(guild_discord_id="0")
        db.add(dc)
        await db.flush()
    return dc


async def _get_stored_bot_token(db: AsyncSession) -> Optional[str]:
    """Retrieve and decrypt the stored Discord bot token, or None."""
    settings = get_settings()
    dc = await _get_or_create_discord_config(db)
    if not dc.bot_token_encrypted:
        return None
    try:
        return decrypt_secret(dc.bot_token_encrypted, settings.jwt_secret_key)
    except Exception:
        return None


async def _get_stored_blizzard_creds(db: AsyncSession) -> tuple[Optional[str], Optional[str]]:
    """Return (client_id, client_secret) or (None, None)."""
    settings = get_settings()
    sc = await _get_or_create_site_config(db)
    if not sc.blizzard_client_id or not sc.blizzard_client_secret_encrypted:
        return None, None
    try:
        secret = decrypt_secret(sc.blizzard_client_secret_encrypted, settings.jwt_secret_key)
        return sc.blizzard_client_id, secret
    except Exception:
        return None, None


async def _get_blizzard_token(client_id: str, client_secret: str) -> Optional[str]:
    """Request a Blizzard OAuth2 client credentials token."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            BLIZZARD_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("access_token")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/guild-identity")
async def save_guild_identity(
    body: GuildIdentityBody, db: AsyncSession = Depends(get_db)
):
    _require_setup_incomplete()
    sc = await _get_or_create_site_config(db)
    sc.guild_name = body.guild_name.strip() or "My Guild"
    sc.guild_tagline = body.guild_tagline
    sc.guild_mission = body.guild_mission
    sc.accent_color_hex = body.accent_color_hex or "#d4a84b"
    sc.logo_url = body.logo_url
    await db.flush()
    # Refresh cache with current values (don't set setup_complete yet)
    result = await db.execute(select(SiteConfig).limit(1))
    updated = result.scalar_one_or_none()
    if updated:
        config_dict = {
            col.key: getattr(updated, col.key)
            for col in SiteConfig.__table__.columns
        }
        set_site_config(config_dict)
    return {"ok": True}


@router.post("/verify-discord-token")
async def verify_discord_token(body: VerifyDiscordTokenBody):
    _require_setup_incomplete()
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bot {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail="Discord token is invalid. Make sure you copied the full bot token.",
        )

    bot_data = resp.json()
    bot_id = bot_data.get("id", "")
    bot_username = bot_data.get("username", "Unknown Bot")

    # Permissions: VIEW_CHANNEL + SEND_MESSAGES + MANAGE_ROLES + READ_MESSAGE_HISTORY
    invite_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={bot_id}&permissions=268437504&scope=bot"
    )

    return {
        "ok": True,
        "bot_username": bot_username,
        "bot_id": bot_id,
        "invite_url": invite_url,
    }


@router.post("/store-discord-token")
async def store_discord_token(
    body: VerifyDiscordTokenBody, db: AsyncSession = Depends(get_db)
):
    """Persist the verified Discord bot token (encrypted) to discord_config."""
    _require_setup_incomplete()
    settings = get_settings()
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token required")

    encrypted = encrypt_secret(token, settings.jwt_secret_key)
    dc = await _get_or_create_discord_config(db)
    dc.bot_token_encrypted = encrypted
    await db.flush()
    return {"ok": True}


@router.get("/discord-guilds")
async def list_discord_guilds(db: AsyncSession = Depends(get_db)):
    """List the Discord servers this bot token can see."""
    _require_setup_incomplete()
    token = await _get_stored_bot_token(db)
    if not token:
        raise HTTPException(status_code=400, detail="Discord token not configured yet")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={"Authorization": f"Bot {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch guild list from Discord")

    guilds = [{"id": g["id"], "name": g["name"]} for g in resp.json()]
    return {"ok": True, "guilds": guilds}


@router.post("/verify-discord-guild")
async def verify_discord_guild(
    body: VerifyDiscordGuildBody, db: AsyncSession = Depends(get_db)
):
    """Confirm the bot can access the selected guild and store guild_discord_id."""
    _require_setup_incomplete()
    token = await _get_stored_bot_token(db)
    if not token:
        raise HTTPException(status_code=400, detail="Discord token not configured yet")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{body.guild_id}?with_counts=true",
            headers={"Authorization": f"Bot {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail="Cannot access that server. Make sure the bot is added to it first.",
        )

    guild_data = resp.json()
    member_count = guild_data.get("approximate_member_count", 0)
    guild_name = guild_data.get("name", "Unknown")

    dc = await _get_or_create_discord_config(db)
    dc.guild_discord_id = body.guild_id
    await db.flush()

    return {"ok": True, "guild_name": guild_name, "member_count": member_count}


@router.post("/verify-blizzard")
async def verify_blizzard(
    body: VerifyBlizzardBody, db: AsyncSession = Depends(get_db)
):
    """Verify Blizzard credentials and fetch guild info to confirm realm/guild slug."""
    _require_setup_incomplete()
    settings = get_settings()

    access_token = await _get_blizzard_token(body.client_id, body.client_secret)
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="Blizzard credentials are invalid. Check your Client ID and Secret.",
        )

    realm_slug = body.realm_slug.strip().lower().replace(" ", "-").replace("'", "")
    guild_slug = body.guild_slug.strip().lower().replace(" ", "-").replace("'", "")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{BLIZZARD_API_BASE}/data/wow/guild/{realm_slug}/{guild_slug}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"namespace": "profile-us", "locale": "en_US"},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Guild '{body.guild_slug}' not found on realm '{body.realm_slug}'. Check your realm and guild name.",
        )

    guild_data = resp.json()
    guild_name = guild_data.get("name", "Unknown")
    member_count = guild_data.get("member_count", 0)

    # Store credentials
    encrypted_secret = encrypt_secret(body.client_secret, settings.jwt_secret_key)
    sc = await _get_or_create_site_config(db)
    sc.blizzard_client_id = body.client_id
    sc.blizzard_client_secret_encrypted = encrypted_secret
    sc.home_realm_slug = realm_slug
    sc.guild_name_slug = guild_slug
    await db.flush()

    return {
        "ok": True,
        "guild_name": guild_name,
        "member_count": member_count,
        "realm_slug": realm_slug,
        "guild_slug": guild_slug,
    }


@router.get("/wow-ranks")
async def get_wow_ranks(db: AsyncSession = Depends(get_db)):
    """Fetch WoW guild roster to get rank index -> sample character names."""
    _require_setup_incomplete()
    client_id, client_secret = await _get_stored_blizzard_creds(db)
    if not client_id:
        raise HTTPException(status_code=400, detail="Blizzard credentials not configured yet")

    sc = await _get_or_create_site_config(db)
    realm_slug = sc.home_realm_slug or ""
    guild_slug = sc.guild_name_slug or ""

    if not realm_slug or not guild_slug:
        raise HTTPException(status_code=400, detail="Realm and guild not configured")

    access_token = await _get_blizzard_token(client_id, client_secret)
    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to get Blizzard access token")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{BLIZZARD_API_BASE}/data/wow/guild/{realm_slug}/{guild_slug}/roster",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"namespace": "profile-us", "locale": "en_US"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch guild roster from Blizzard")

    data = resp.json()
    members = data.get("members", [])

    # Aggregate by rank index, collect up to 3 sample names
    rank_map: dict[int, list[str]] = {}
    for m in members:
        idx = m.get("rank", 99)
        name = m.get("character", {}).get("name", "Unknown")
        if idx not in rank_map:
            rank_map[idx] = []
        if len(rank_map[idx]) < 3:
            rank_map[idx].append(name)

    ranks = [
        {"wow_rank_index": idx, "sample_names": names}
        for idx, names in sorted(rank_map.items())
    ]
    return {"ok": True, "ranks": ranks}


@router.post("/ranks")
async def save_ranks(body: RankNamesBody, db: AsyncSession = Depends(get_db)):
    """Save platform rank names and WoW rank mappings."""
    _require_setup_incomplete()

    # Update rank names
    result = await db.execute(select(GuildRank))
    all_ranks = result.scalars().all()
    for rank in all_ranks:
        level_str = str(rank.level)
        if level_str in body.rank_names:
            new_name = body.rank_names[level_str].strip()
            if new_name:
                rank.name = new_name

    await db.flush()

    # Upsert WoW rank mappings
    for mapping in body.wow_mappings:
        existing = await db.execute(
            select(RankWowMapping).where(
                RankWowMapping.wow_rank_index == mapping.wow_rank_index
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = RankWowMapping(
                wow_rank_index=mapping.wow_rank_index,
                guild_rank_id=mapping.guild_rank_id,
            )
            db.add(row)
        else:
            row.guild_rank_id = mapping.guild_rank_id
    await db.flush()

    return {"ok": True}


@router.get("/discord-roles")
async def get_discord_roles(db: AsyncSession = Depends(get_db)):
    """Fetch Discord roles for the configured guild."""
    _require_setup_incomplete()
    token = await _get_stored_bot_token(db)
    dc = await _get_or_create_discord_config(db)
    guild_id = dc.guild_discord_id

    if not token or not guild_id or guild_id == "0":
        raise HTTPException(status_code=400, detail="Discord not configured yet")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/roles",
            headers={"Authorization": f"Bot {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch Discord roles")

    roles = [
        {"id": r["id"], "name": r["name"]}
        for r in resp.json()
        if r["name"] != "@everyone"
    ]
    roles.sort(key=lambda r: r["name"])
    return {"ok": True, "roles": roles}


@router.post("/discord-roles")
async def save_discord_roles(body: DiscordRolesBody, db: AsyncSession = Depends(get_db)):
    """Save Discord role IDs for each platform rank."""
    _require_setup_incomplete()

    for entry in body.roles:
        result = await db.execute(
            select(GuildRank).where(GuildRank.id == entry.guild_rank_id)
        )
        rank = result.scalar_one_or_none()
        if rank:
            rank.discord_role_id = entry.discord_role_id or None
    await db.flush()

    return {"ok": True}


@router.get("/discord-channels")
async def get_discord_channels(db: AsyncSession = Depends(get_db)):
    """Fetch Discord text channels for the configured guild."""
    _require_setup_incomplete()
    token = await _get_stored_bot_token(db)
    dc = await _get_or_create_discord_config(db)
    guild_id = dc.guild_discord_id

    if not token or not guild_id or guild_id == "0":
        raise HTTPException(status_code=400, detail="Discord not configured yet")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/channels",
            headers={"Authorization": f"Bot {token}"},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch Discord channels")

    channels = [
        {"id": c["id"], "name": c["name"]}
        for c in resp.json()
        if c.get("type") == 0  # text channels only
    ]
    channels.sort(key=lambda c: c["name"])
    return {"ok": True, "channels": channels}


@router.post("/channels")
async def save_channels(body: ChannelsBody, db: AsyncSession = Depends(get_db)):
    """Save Discord channel assignments."""
    _require_setup_incomplete()

    dc = await _get_or_create_discord_config(db)
    if body.audit_channel_id is not None:
        dc.audit_channel_id = body.audit_channel_id or None
    if body.raid_announcement_channel_id is not None:
        dc.default_announcement_channel_id = body.raid_announcement_channel_id or None
    await db.flush()

    if body.crafters_corner_channel_id is not None:
        await db.execute(
            text(
                "UPDATE guild_identity.crafting_sync_config "
                "SET crafters_corner_channel_id = :cid"
            ),
            {"cid": body.crafters_corner_channel_id or None},
        )

    return {"ok": True}


@router.post("/create-admin")
async def create_admin(body: CreateAdminBody, db: AsyncSession = Depends(get_db)):
    """Create the first admin (Guild Leader) account."""
    _require_setup_incomplete()

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    login_email = body.discord_username.strip().lower()
    if not login_email:
        raise HTTPException(status_code=400, detail="Discord username is required")
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="Display name is required")

    # Check no duplicate
    existing = await db.execute(select(User).where(User.email == login_email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="An account with this Discord username already exists.",
        )

    # Find Guild Leader rank (level 5)
    rank_result = await db.execute(
        select(GuildRank).where(GuildRank.level == 5)
    )
    gl_rank = rank_result.scalar_one_or_none()
    if gl_rank is None:
        # Fall back to highest available rank
        rank_result = await db.execute(
            select(GuildRank).order_by(GuildRank.level.desc()).limit(1)
        )
        gl_rank = rank_result.scalar_one_or_none()

    if gl_rank is None:
        raise HTTPException(status_code=500, detail="No ranks configured. Run database setup first.")

    user = User(email=login_email, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()

    player = Player(
        display_name=body.display_name.strip(),
        guild_rank_id=gl_rank.id,
        website_user_id=user.id,
    )
    db.add(player)
    await db.flush()

    return {"ok": True, "user_id": user.id, "player_id": player.id}


@router.post("/complete")
async def complete_setup(db: AsyncSession = Depends(get_db)):
    """Mark setup as complete. Triggers config cache refresh."""
    _require_setup_incomplete()

    sc = await _get_or_create_site_config(db)
    sc.setup_complete = True
    await db.flush()

    # Refresh config cache
    result = await db.execute(select(SiteConfig).limit(1))
    updated = result.scalar_one_or_none()
    if updated:
        config_dict = {
            col.key: getattr(updated, col.key)
            for col in SiteConfig.__table__.columns
        }
        set_site_config(config_dict)

    return {"ok": True}
