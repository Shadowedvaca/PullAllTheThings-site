"""Auth page routes: login, register, logout."""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import COOKIE_NAME, get_db, get_page_member
from patt.templating import templates
from sv_common.db.models import InviteCode, Player, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth-pages"])

COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    from patt.config import get_settings
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


def _clear_auth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _base_ctx(request: Request, player: Player | None) -> dict:
    return {
        "request": request,
        "current_member": player,
        "active_campaigns": [],
    }


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/",
    db: AsyncSession = Depends(get_db),
    current_player: Player | None = Depends(get_page_member),
):
    if current_player:
        return RedirectResponse(url=next, status_code=302)
    return templates.TemplateResponse(
        "auth/login.html",
        {**_base_ctx(request, None), "next": next, "error": None, "username": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    discord_username: str = Form(...),
    password: str = Form(...),
    next: str = "/",
    db: AsyncSession = Depends(get_db),
):
    from sv_common.auth.passwords import verify_password
    from sv_common.auth.jwt import create_access_token

    def render_error(msg: str):
        return templates.TemplateResponse(
            "auth/login.html",
            {
                **_base_ctx(request, None),
                "next": next,
                "error": msg,
                "username": discord_username,
            },
            status_code=400,
        )

    # Look up user by email (discord_username stored as email at registration)
    login_email = discord_username.lower().strip()
    user_result = await db.execute(
        select(User).where(User.email == login_email)
    )
    user = user_result.scalar_one_or_none()

    if user is None:
        return render_error("Invalid username or password.")

    if not user.is_active:
        return render_error("Account is inactive. Contact an officer.")

    if not verify_password(password, user.password_hash):
        return render_error("Invalid username or password.")

    # Find the player linked to this user
    player_result = await db.execute(
        select(Player)
        .options(selectinload(Player.guild_rank))
        .where(Player.website_user_id == user.id)
    )
    player = player_result.scalar_one_or_none()
    if player is None:
        return render_error("No player account linked to this login.")

    token = create_access_token(
        user_id=user.id,
        member_id=player.id,
        rank_level=player.guild_rank.level if player.guild_rank else 0,
    )

    safe_next = next if next.startswith("/") else "/"
    response = RedirectResponse(url=safe_next, status_code=302)
    _set_auth_cookie(response, token)
    return response


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_player: Player | None = Depends(get_page_member),
):
    if current_player:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "auth/register.html",
        {**_base_ctx(request, None), "error": None, "form": {}},
    )


@router.post("/register", response_class=HTMLResponse)
async def register_post(
    request: Request,
    code: str = Form(...),
    discord_username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    from sv_common.auth.passwords import hash_password
    from sv_common.auth.jwt import create_access_token
    from datetime import datetime, timezone

    form_data = {"code": code, "discord_username": discord_username}

    def render_error(msg: str):
        return templates.TemplateResponse(
            "auth/register.html",
            {**_base_ctx(request, None), "error": msg, "form": form_data},
            status_code=400,
        )

    if password != password2:
        return render_error("Passwords do not match.")

    if len(password) < 8:
        return render_error("Password must be at least 8 characters.")

    # Look up invite code
    code_upper = code.strip().upper()
    invite_result = await db.execute(
        select(InviteCode)
        .options(selectinload(InviteCode.player))
        .where(InviteCode.code == code_upper)
    )
    invite = invite_result.scalar_one_or_none()

    if invite is None:
        return render_error("Invalid invite code.")

    if invite.used_at is not None:
        return render_error("This invite code has already been used.")

    now = datetime.now(timezone.utc)
    if invite.expires_at and invite.expires_at < now:
        return render_error("This invite code has expired.")

    if invite.player is None:
        return render_error("Invite code is not associated with a player.")

    player = invite.player
    if player.website_user_id is not None:
        return render_error("This account is already registered.")

    # Use discord_username as the login key (stored as User.email)
    login_email = discord_username.lower().strip()

    # Check for duplicate email
    existing_result = await db.execute(
        select(User).where(User.email == login_email)
    )
    if existing_result.scalar_one_or_none() is not None:
        return render_error("An account with this Discord username already exists.")

    # Create the user account
    user = User(email=login_email, password_hash=hash_password(password), is_active=True)
    db.add(user)
    await db.flush()

    # Link user to player
    player.website_user_id = user.id

    # Consume the invite code
    invite.used_at = now

    await db.flush()

    # Load rank for token
    await db.refresh(player, ["guild_rank"])

    token = create_access_token(
        user_id=user.id,
        member_id=player.id,
        rank_level=player.guild_rank.level if player.guild_rank else 0,
    )

    response = RedirectResponse(url="/", status_code=302)
    _set_auth_cookie(response, token)
    return response


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=302)
    _clear_auth_cookie(response)
    return response
