"""
Battle.net OAuth2 account linking routes.

GET  /auth/battlenet           — Redirect user to Blizzard authorization page
GET  /auth/battlenet/callback  — Exchange code for tokens, store, redirect to profile
DELETE /api/v1/auth/battlenet  — Unlink Battle.net account
"""

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.config import get_settings
from guild_portal.deps import get_current_player, get_db, get_page_member
from sv_common.config_cache import get_site_config
from sv_common.crypto import decrypt_secret, encrypt_bnet_token
from sv_common.db.models import BattlenetAccount, Player, PlayerCharacter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bnet-auth"])

BNET_AUTHORIZE_URL = "https://oauth.battle.net/authorize"
BNET_TOKEN_URL = "https://oauth.battle.net/token"
BNET_USERINFO_URL = "https://oauth.battle.net/userinfo"

_STATE_COOKIE = "bnet_oauth_state"


def _get_blizzard_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) from site_config, falling back to env vars.

    Raises RuntimeError if credentials are not available from either source.
    """
    settings = get_settings()
    cfg = get_site_config()

    client_id = cfg.get("blizzard_client_id", "") or settings.blizzard_client_id
    encrypted_secret = cfg.get("blizzard_client_secret_encrypted", "")

    if encrypted_secret:
        client_secret = decrypt_secret(encrypted_secret, settings.jwt_secret_key)
    else:
        client_secret = settings.blizzard_client_secret

    if not client_id or not client_secret:
        raise RuntimeError("Blizzard API credentials not configured")
    return client_id, client_secret


def _check_bnet_key_configured() -> None:
    """Raise RuntimeError if BNET_TOKEN_ENCRYPTION_KEY is not set."""
    settings = get_settings()
    if not settings.bnet_token_encryption_key:
        raise RuntimeError("BNET_TOKEN_ENCRYPTION_KEY is not set")


# ---------------------------------------------------------------------------
# GET /auth/battlenet — initiate OAuth flow
# ---------------------------------------------------------------------------


@router.get("/auth/battlenet", include_in_schema=False)
async def bnet_auth_start(
    request: Request,
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/auth/battlenet", status_code=302)

    try:
        client_id, _ = _get_blizzard_credentials()
        _check_bnet_key_configured()
    except RuntimeError as exc:
        logger.error("Battle.net OAuth not available: %s", exc)
        return RedirectResponse(
            url="/profile?error=Battle.net+connection+is+not+configured.+Contact+an+officer.",
            status_code=302,
        )

    state = secrets.token_hex(16)
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/battlenet/callback"

    params = (
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=openid+wow.profile"
        f"&state={state}"
    )
    authorize_url = BNET_AUTHORIZE_URL + params

    response = RedirectResponse(url=authorize_url, status_code=302)
    response.set_cookie(
        key=_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


# ---------------------------------------------------------------------------
# GET /auth/battlenet/callback — handle Blizzard redirect
# ---------------------------------------------------------------------------


@router.get("/auth/battlenet/callback", include_in_schema=False)
async def bnet_auth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/auth/battlenet", status_code=302)

    # User denied on Blizzard's page
    if request.query_params.get("error"):
        response = RedirectResponse(
            url="/profile?error=Battle.net+connection+cancelled", status_code=302
        )
        response.delete_cookie(_STATE_COOKIE)
        return response

    # Validate state to prevent CSRF
    returned_state = request.query_params.get("state", "")
    cookie_state = request.cookies.get(_STATE_COOKIE, "")
    if not returned_state or not cookie_state or returned_state != cookie_state:
        logger.warning(
            "Battle.net OAuth state mismatch for player %s", current_member.id
        )
        response = RedirectResponse(
            url="/profile?error=Battle.net+connection+failed.+Please+try+again.",
            status_code=302,
        )
        response.delete_cookie(_STATE_COOKIE)
        return response

    code = request.query_params.get("code", "")
    if not code:
        return RedirectResponse(
            url="/profile?error=Battle.net+connection+failed.+No+authorization+code+received.",
            status_code=302,
        )

    try:
        client_id, client_secret = _get_blizzard_credentials()
    except RuntimeError:
        return RedirectResponse(
            url="/profile?error=Battle.net+credentials+not+configured.",
            status_code=302,
        )

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/battlenet/callback"

    # Exchange authorization code for tokens
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            token_resp = await http.post(
                BNET_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=(client_id, client_secret),
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            access_token = token_data.get("access_token", "")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in")

            # Fetch userinfo to get bnet_id (sub) and battletag
            userinfo_resp = await http.get(
                BNET_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()

    except httpx.HTTPStatusError as exc:
        logger.error("Blizzard token exchange failed: %s", exc)
        response = RedirectResponse(
            url="/profile?error=Battle.net+connection+failed.+Please+try+again.",
            status_code=302,
        )
        response.delete_cookie(_STATE_COOKIE)
        return response
    except Exception as exc:
        logger.error("Unexpected error during Battle.net OAuth: %s", exc)
        response = RedirectResponse(
            url="/profile?error=Battle.net+connection+failed.+Please+try+again.",
            status_code=302,
        )
        response.delete_cookie(_STATE_COOKIE)
        return response

    bnet_id = str(userinfo.get("sub", ""))
    battletag = userinfo.get("battletag", "")

    if not bnet_id:
        response = RedirectResponse(
            url="/profile?error=Could+not+retrieve+Battle.net+account+info.",
            status_code=302,
        )
        response.delete_cookie(_STATE_COOKIE)
        return response

    # Check if bnet_id is already claimed by a different player
    existing_result = await db.execute(
        select(BattlenetAccount).where(BattlenetAccount.bnet_id == bnet_id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None and existing.player_id != current_member.id:
        logger.warning(
            "Battle.net bnet_id %s already claimed by player %s (attempted by player %s)",
            bnet_id, existing.player_id, current_member.id,
        )
        response = RedirectResponse(
            url=(
                "/profile?error=This+Battle.net+account+is+already+linked+to+another+guild+member."
                "+Contact+an+officer+if+you+believe+this+is+a+mistake."
            ),
            status_code=302,
        )
        response.delete_cookie(_STATE_COOKIE)
        return response

    # Encrypt tokens
    encrypted_access = encrypt_bnet_token(access_token)
    encrypted_refresh = encrypt_bnet_token(refresh_token) if refresh_token else None

    # Calculate token expiry
    token_expires_at = None
    if expires_in:
        from datetime import datetime, timezone, timedelta
        token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    # Upsert into battlenet_accounts (delete old row for this player if exists, then insert)
    await db.execute(
        delete(BattlenetAccount).where(BattlenetAccount.player_id == current_member.id)
    )

    bnet_account = BattlenetAccount(
        player_id=current_member.id,
        bnet_id=bnet_id,
        battletag=battletag,
        access_token_encrypted=encrypted_access,
        refresh_token_encrypted=encrypted_refresh,
        token_expires_at=token_expires_at,
    )
    db.add(bnet_account)
    await db.flush()

    logger.info(
        "Battle.net account linked: player_id=%s battletag=%s",
        current_member.id,
        battletag,
    )

    response = RedirectResponse(
        url="/profile?success=Battle.net+account+linked+successfully",
        status_code=302,
    )
    response.delete_cookie(_STATE_COOKIE)
    return response


# ---------------------------------------------------------------------------
# DELETE /api/v1/auth/battlenet — unlink
# ---------------------------------------------------------------------------


@router.delete("/api/v1/auth/battlenet")
async def bnet_unlink(
    db: AsyncSession = Depends(get_db),
    current_member: Player = Depends(get_current_player),
):
    """Unlink the current player's Battle.net account.

    Removes the battlenet_accounts row and any player_characters rows with
    link_source='battlenet_oauth'. Manual links are unaffected.
    """
    # Remove OAuth-sourced character links for this player
    await db.execute(
        delete(PlayerCharacter).where(
            PlayerCharacter.player_id == current_member.id,
            PlayerCharacter.link_source == "battlenet_oauth",
        )
    )

    # Remove the battlenet_accounts row
    result = await db.execute(
        select(BattlenetAccount).where(BattlenetAccount.player_id == current_member.id)
    )
    bnet_account = result.scalar_one_or_none()
    if bnet_account:
        await db.delete(bnet_account)

    await db.flush()
    logger.info("Battle.net account unlinked: player_id=%s", current_member.id)
    return {"ok": True}
