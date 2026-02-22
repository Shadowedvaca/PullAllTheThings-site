"""Auth page routes: login, register, logout."""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import COOKIE_NAME, get_db, get_page_member
from patt.templating import templates
from sv_common.db.models import GuildMember, InviteCode, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth-pages"])

COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


def _clear_auth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _base_ctx(request: Request, member: GuildMember | None) -> dict:
    return {
        "request": request,
        "current_member": member,
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
    current_member: GuildMember | None = Depends(get_page_member),
):
    if current_member:
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

    # Look up the member by discord_username
    result = await db.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank), selectinload(GuildMember.user))
        .where(GuildMember.discord_username == discord_username)
    )
    member = result.scalar_one_or_none()

    if member is None or member.user is None:
        return render_error("Invalid username or password.")

    if not member.user.is_active:
        return render_error("Account is inactive. Contact an officer.")

    if not verify_password(password, member.user.password_hash):
        return render_error("Invalid username or password.")

    token = create_access_token(
        user_id=member.user.id,
        member_id=member.id,
        rank_level=member.rank.level if member.rank else 0,
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
    current_member: GuildMember | None = Depends(get_page_member),
):
    if current_member:
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
        .options(selectinload(InviteCode.member))
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

    if invite.member is None:
        return render_error("Invite code is not associated with a member.")

    if invite.member.discord_username.lower() != discord_username.strip().lower():
        return render_error("Discord username does not match the invite code.")

    member = invite.member
    if member.user_id is not None:
        return render_error("This account is already registered.")

    # Create the user account
    password_hash = hash_password(password)
    user = User(password_hash=password_hash, is_active=True)
    db.add(user)
    await db.flush()

    # Link user to member
    member.user_id = user.id
    member.registered_at = now

    # Consume the invite code
    invite.used_at = now

    await db.flush()

    # Load rank for token
    await db.refresh(member, ["rank"])

    token = create_access_token(
        user_id=user.id,
        member_id=member.id,
        rank_level=member.rank.level if member.rank else 0,
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
