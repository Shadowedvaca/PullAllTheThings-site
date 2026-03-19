"""
Battle.net character sync.

Fetches the character list from /profile/user/wow using a player's stored OAuth
access token, then creates or updates player_characters links for all characters
on the guild's home realm.

link_source = 'battlenet_oauth', confidence = 'high' (Blizzard confirmed ownership directly).
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from sv_common.config_cache import get_site_config

logger = logging.getLogger(__name__)

BNET_PROFILE_URL = "https://us.api.blizzard.com/profile/user/wow"
BNET_TOKEN_URL = "https://oauth.battle.net/token"


async def get_valid_access_token(pool, player_id: int) -> str | None:
    """
    Return a valid access token for the player. Refreshes if expired.
    Returns None if the account is not linked or the token cannot be refreshed.
    """
    from sv_common.crypto import decrypt_bnet_token, encrypt_bnet_token

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT access_token_encrypted, refresh_token_encrypted, token_expires_at
               FROM guild_identity.battlenet_accounts
               WHERE player_id = $1""",
            player_id,
        )

    if row is None:
        return None

    now = datetime.now(timezone.utc)

    # Check token expiry (with 60-second buffer)
    expires = row["token_expires_at"]
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now + timedelta(seconds=60):
            # Expired — attempt refresh
            return await _refresh_token(pool, player_id, row, now)

    # Token still valid
    try:
        return decrypt_bnet_token(row["access_token_encrypted"])
    except Exception as exc:
        logger.error("Failed to decrypt access token for player %s: %s", player_id, exc)
        return None


async def _refresh_token(pool, player_id: int, row, now: datetime) -> str | None:
    """Attempt to refresh an expired access token. Returns new token or None."""
    from sv_common.crypto import decrypt_bnet_token, encrypt_bnet_token

    if not row["refresh_token_encrypted"]:
        logger.warning(
            "No refresh token for player %s — cannot refresh expired OAuth token", player_id
        )
        from sv_common.errors import report_error
        await report_error(
            pool,
            "bnet_token_expired",
            "info",
            f"Battle.net token expired for player {player_id} — no refresh token stored. "
            f"Player must re-link their Battle.net account.",
            "bnet_character_sync",
            details={"player_id": player_id},
            identifier=str(player_id),
        )
        return None

    try:
        refresh_token = decrypt_bnet_token(row["refresh_token_encrypted"])
    except Exception as exc:
        logger.error("Failed to decrypt refresh token for player %s: %s", player_id, exc)
        return None

    # Get Blizzard client credentials
    from sv_common.crypto import decrypt_secret

    cfg = get_site_config()
    client_id = cfg.get("blizzard_client_id", "") or os.environ.get("BLIZZARD_CLIENT_ID", "")

    encrypted_secret = cfg.get("blizzard_client_secret_encrypted", "")
    if encrypted_secret:
        jwt_secret = os.environ.get("JWT_SECRET_KEY", "")
        try:
            client_secret = decrypt_secret(encrypted_secret, jwt_secret)
        except Exception:
            client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET", "")
    else:
        client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        logger.error("Blizzard credentials not available for token refresh (player %s)", player_id)
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                BNET_TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                auth=(client_id, client_secret),
            )
            resp.raise_for_status()
            token_data = resp.json()
    except Exception as exc:
        logger.error("Token refresh failed for player %s: %s", player_id, exc)
        from sv_common.errors import report_error
        await report_error(
            pool,
            "bnet_token_expired",
            "info",
            f"Battle.net token refresh failed for player {player_id}: {exc}",
            "bnet_character_sync",
            details={"player_id": player_id, "error": str(exc)},
            identifier=str(player_id),
        )
        return None

    new_access_token = token_data.get("access_token", "")
    if not new_access_token:
        logger.error("Token refresh returned empty access_token for player %s", player_id)
        return None

    new_refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")

    encrypted_access = encrypt_bnet_token(new_access_token)
    encrypted_refresh = encrypt_bnet_token(new_refresh_token) if new_refresh_token else None
    new_expires_at = (now + timedelta(seconds=int(expires_in))) if expires_in else None

    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE guild_identity.battlenet_accounts SET
               access_token_encrypted = $1,
               refresh_token_encrypted = COALESCE($2, refresh_token_encrypted),
               token_expires_at = $3,
               last_refreshed = NOW()
               WHERE player_id = $4""",
            encrypted_access,
            encrypted_refresh,
            new_expires_at,
            player_id,
        )

    logger.info("OAuth token refreshed for player %s", player_id)
    return new_access_token


async def sync_bnet_characters(pool, player_id: int, access_token: str) -> dict:
    """
    Fetch the character list for a player's Battle.net account and upsert
    player_characters entries with link_source='battlenet_oauth'.

    Captures every character (level 10+) on the account regardless of realm.
    New characters discovered via BNet are created with in_guild=FALSE.
    Characters already in the guild roster (in_guild=TRUE) keep their value.

    Returns: {"linked": int, "new_characters": int, "skipped": int}
    """

    # Fetch character list from Blizzard profile API
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(
                BNET_PROFILE_URL,
                params={"namespace": "profile-us", "locale": "en_US"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error(
            "Failed to fetch Blizzard character list for player %s: %s", player_id, exc
        )
        return {"linked": 0, "new_characters": 0, "skipped": 0}

    linked = 0
    new_characters = 0
    skipped = 0

    wow_accounts = data.get("wow_accounts", [])

    async with pool.acquire() as conn:
        for account in wow_accounts:
            for char_data in account.get("characters", []):
                realm_slug = char_data.get("realm", {}).get("slug", "")
                level = char_data.get("level", 0)
                char_name = char_data.get("name", "")

                # Filter: level 10+ (no bank alts or trial chars)
                if level < 10:
                    skipped += 1
                    continue
                if not char_name:
                    skipped += 1
                    continue

                # Check if character already exists in wow_characters.
                # Used to track whether this is a new character row (for new_characters counter).
                existing_char = await conn.fetchrow(
                    """SELECT id FROM guild_identity.wow_characters
                       WHERE character_name = $1 AND realm_slug = $2""",
                    char_name, realm_slug,
                )

                class_name = char_data.get("playable_class", {}).get("name", "")

                # Resolve class_id from reference table
                class_id = None
                if class_name:
                    class_row = await conn.fetchrow(
                        "SELECT id FROM guild_identity.classes WHERE LOWER(name) = LOWER($1)",
                        class_name,
                    )
                    class_id = class_row["id"] if class_row else None

                is_new_char = existing_char is None

                # Upsert into wow_characters using the character's actual realm slug.
                # New rows get in_guild=FALSE; existing rows keep their in_guild value
                # (guild roster sync is responsible for setting in_guild=TRUE).
                char_row = await conn.fetchrow(
                    """INSERT INTO guild_identity.wow_characters
                       (character_name, realm_slug, level, class_id, in_guild)
                       VALUES ($1, $2, $3, $4, FALSE)
                       ON CONFLICT (character_name, realm_slug) DO UPDATE SET
                           level = EXCLUDED.level,
                           class_id = COALESCE(EXCLUDED.class_id,
                                               guild_identity.wow_characters.class_id),
                           removed_at = NULL
                           -- in_guild is intentionally NOT updated on conflict:
                           -- if the char is already in the guild roster (TRUE), keep it TRUE
                       RETURNING id""",
                    char_name, realm_slug, level, class_id,
                )
                char_id = char_row["id"]

                if is_new_char:
                    new_characters += 1

                # If character already linked to a different player, warn and displace
                existing_link = await conn.fetchrow(
                    """SELECT player_id, link_source
                       FROM guild_identity.player_characters
                       WHERE character_id = $1""",
                    char_id,
                )

                if existing_link and existing_link["player_id"] != player_id:
                    old_player_id = existing_link["player_id"]
                    old_source = existing_link["link_source"]
                    logger.warning(
                        "Battle.net OAuth claim for player %s displaces existing %s link "
                        "for character '%s' (char_id=%s) from player %s. "
                        "Officers should investigate if unexpected.",
                        player_id, old_source, char_name, char_id, old_player_id,
                    )
                    await conn.execute(
                        "DELETE FROM guild_identity.player_characters WHERE character_id = $1",
                        char_id,
                    )

                # Upsert player_characters with battlenet_oauth (confidence='high')
                await conn.execute(
                    """INSERT INTO guild_identity.player_characters
                       (player_id, character_id, link_source, confidence)
                       VALUES ($1, $2, 'battlenet_oauth', 'high')
                       ON CONFLICT (player_id, character_id) DO UPDATE SET
                           link_source = 'battlenet_oauth',
                           confidence = 'high'""",
                    player_id, char_id,
                )
                linked += 1

        # Stamp last_character_sync
        await conn.execute(
            """UPDATE guild_identity.battlenet_accounts
               SET last_character_sync = NOW()
               WHERE player_id = $1""",
            player_id,
        )

    logger.info(
        "Battle.net character sync for player %s complete: linked=%d new_chars=%d skipped=%d",
        player_id, linked, new_characters, skipped,
    )
    return {"linked": linked, "new_characters": new_characters, "skipped": skipped}
