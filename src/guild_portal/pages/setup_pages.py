"""Setup wizard page routes — GET handlers for each wizard step."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from guild_portal.deps import get_db
from guild_portal.templating import templates
from sv_common.config_cache import get_site_config
from sv_common.db.models import DiscordConfig, GuildRank, RankWowMapping, SiteConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup-pages"])

STEPS = [
    ("welcome",        "/setup",                  "Welcome"),
    ("guild-identity", "/setup/guild-identity",   "Guild Identity"),
    ("discord",        "/setup/discord",           "Discord"),
    ("blizzard",       "/setup/blizzard",          "Blizzard"),
    ("ranks",          "/setup/ranks",             "Ranks"),
    ("discord-roles",  "/setup/discord-roles",     "Discord Roles"),
    ("channels",       "/setup/channels",          "Channels"),
    ("admin-account",  "/setup/admin-account",     "Admin Account"),
    ("complete",       "/setup/complete",          "Complete"),
]


def _setup_context(current_step: str, request: Request) -> dict:
    steps = [
        {"key": key, "url": url, "label": label, "active": key == current_step}
        for key, url, label in STEPS
    ]
    return {"request": request, "steps": steps, "current_step": current_step}


def _block_if_complete():
    """Return a RedirectResponse to /admin/players if setup is already complete."""
    if get_site_config().get("setup_complete"):
        return RedirectResponse("/admin/players", status_code=302)
    return None


@router.get("/setup", response_class=HTMLResponse)
async def setup_welcome(request: Request):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    ctx = _setup_context("welcome", request)
    return templates.TemplateResponse("setup/welcome.html", ctx)


@router.get("/setup/guild-identity", response_class=HTMLResponse)
async def setup_guild_identity(request: Request, db: AsyncSession = Depends(get_db)):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    result = await db.execute(select(SiteConfig).limit(1))
    sc = result.scalar_one_or_none()
    ctx = _setup_context("guild-identity", request)
    ctx["config"] = sc
    return templates.TemplateResponse("setup/guild_identity.html", ctx)


@router.get("/setup/discord", response_class=HTMLResponse)
async def setup_discord(request: Request):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    ctx = _setup_context("discord", request)
    return templates.TemplateResponse("setup/discord.html", ctx)


@router.get("/setup/blizzard", response_class=HTMLResponse)
async def setup_blizzard(request: Request, db: AsyncSession = Depends(get_db)):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    result = await db.execute(select(SiteConfig).limit(1))
    sc = result.scalar_one_or_none()
    ctx = _setup_context("blizzard", request)
    ctx["config"] = sc
    return templates.TemplateResponse("setup/blizzard.html", ctx)


@router.get("/setup/ranks", response_class=HTMLResponse)
async def setup_ranks(request: Request, db: AsyncSession = Depends(get_db)):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level.desc()))
    ranks = ranks_result.scalars().all()
    mappings_result = await db.execute(select(RankWowMapping))
    existing_mappings = {m.wow_rank_index: m.guild_rank_id for m in mappings_result.scalars().all()}
    ctx = _setup_context("ranks", request)
    ctx["ranks"] = ranks
    ctx["existing_mappings"] = existing_mappings
    return templates.TemplateResponse("setup/ranks.html", ctx)


@router.get("/setup/discord-roles", response_class=HTMLResponse)
async def setup_discord_roles(request: Request, db: AsyncSession = Depends(get_db)):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level.desc()))
    ranks = ranks_result.scalars().all()
    ctx = _setup_context("discord-roles", request)
    ctx["ranks"] = ranks
    return templates.TemplateResponse("setup/discord_roles.html", ctx)


@router.get("/setup/channels", response_class=HTMLResponse)
async def setup_channels(request: Request, db: AsyncSession = Depends(get_db)):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    result = await db.execute(select(DiscordConfig).limit(1))
    dc = result.scalar_one_or_none()
    ctx = _setup_context("channels", request)
    ctx["discord_config"] = dc
    return templates.TemplateResponse("setup/channels.html", ctx)


@router.get("/setup/admin-account", response_class=HTMLResponse)
async def setup_admin_account(request: Request):
    redirect = _block_if_complete()
    if redirect:
        return redirect
    ctx = _setup_context("admin-account", request)
    return templates.TemplateResponse("setup/admin_account.html", ctx)


@router.get("/setup/complete", response_class=HTMLResponse)
async def setup_complete_page(request: Request, db: AsyncSession = Depends(get_db)):
    # This page is accessible even after complete (to show the summary)
    result = await db.execute(select(SiteConfig).limit(1))
    sc = result.scalar_one_or_none()
    ctx = _setup_context("complete", request)
    ctx["config"] = sc
    return templates.TemplateResponse("setup/complete.html", ctx)
