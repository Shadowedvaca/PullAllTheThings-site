"""Admin page routes: campaign management and roster management."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_db, get_page_member
from patt.services import campaign_service, vote_service
from patt.templating import templates
from sv_common.db.models import GuildMember, GuildRank
from sv_common.identity import members as member_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-pages"])

MIN_ADMIN_RANK = 4  # Officer+


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_admin(request: Request, db: AsyncSession) -> GuildMember | None:
    """Return member if Officer+, else None (caller handles redirect)."""
    member = await get_page_member(request, db)
    if member is None:
        return None
    if not member.rank or member.rank.level < MIN_ADMIN_RANK:
        return None
    return member


async def _base_ctx(request: Request, member: GuildMember, db: AsyncSession) -> dict:
    active = await campaign_service.list_campaigns(db, status="live")
    return {
        "request": request,
        "current_member": member,
        "active_campaigns": active,
    }


def _redirect_login(url: str) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={url}", status_code=302)


def _redirect_forbidden() -> HTMLResponse:
    return RedirectResponse(url="/", status_code=302)


# ---------------------------------------------------------------------------
# Admin root → campaigns
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def admin_root():
    return RedirectResponse(url="/admin/campaigns", status_code=302)


# ---------------------------------------------------------------------------
# Campaign list
# ---------------------------------------------------------------------------


@router.get("/campaigns", response_class=HTMLResponse)
async def admin_campaigns(
    request: Request,
    db: AsyncSession = Depends(get_db),
    success: str | None = None,
    error: str | None = None,
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login("/admin/campaigns")

    campaigns = await campaign_service.list_campaigns(db)
    # Sort: live first, then draft, then closed/archived
    order = {"live": 0, "draft": 1, "closed": 2, "archived": 3}
    campaigns.sort(key=lambda c: order.get(c.status, 9))

    ctx = await _base_ctx(request, member, db)
    ctx.update({
        "campaigns": campaigns,
        "flash_success": success,
        "flash_error": error,
    })
    return templates.TemplateResponse("admin/campaigns.html", ctx)


# ---------------------------------------------------------------------------
# Create campaign
# ---------------------------------------------------------------------------


@router.get("/campaigns/new", response_class=HTMLResponse)
async def admin_campaign_new(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login("/admin/campaigns/new")

    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level))
    ranks = list(ranks_result.scalars().all())

    ctx = await _base_ctx(request, member, db)
    ctx.update({"ranks": ranks, "campaign": None, "error": None, "form": {}})
    return templates.TemplateResponse("admin/campaign_edit.html", ctx)


@router.post("/campaigns/new", response_class=HTMLResponse)
async def admin_campaign_new_post(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    min_rank_to_vote: int = Form(...),
    min_rank_to_view: str = Form(""),
    start_at: str = Form(...),
    duration_hours: int = Form(...),
    discord_channel_id: str = Form(""),
    early_close_if_all_voted: str = Form("on"),
    picks_per_voter: int = Form(3),
    agent_enabled: str = Form("on"),
    agent_chattiness: str = Form("normal"),
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login("/admin/campaigns")

    try:
        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        start_dt = datetime.now(timezone.utc)

    try:
        campaign = await campaign_service.create_campaign(
            db,
            title=title,
            description=description or None,
            min_rank_to_vote=min_rank_to_vote,
            min_rank_to_view=int(min_rank_to_view) if min_rank_to_view else None,
            start_at=start_dt,
            duration_hours=duration_hours,
            discord_channel_id=discord_channel_id or None,
            early_close_if_all_voted=(early_close_if_all_voted == "on"),
            picks_per_voter=picks_per_voter,
            created_by=member.id,
            agent_enabled=(agent_enabled == "on"),
            agent_chattiness=agent_chattiness if agent_chattiness in ("quiet", "normal", "hype") else "normal",
        )
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign.id}/edit",
            status_code=302,
        )
    except Exception as e:
        logger.error("Create campaign error: %s", e)
        ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level))
        ranks = list(ranks_result.scalars().all())
        ctx = await _base_ctx(request, member, db)
        ctx.update({
            "ranks": ranks,
            "campaign": None,
            "error": str(e),
            "form": {
                "title": title,
                "description": description,
                "min_rank_to_vote": min_rank_to_vote,
            },
        })
        return templates.TemplateResponse("admin/campaign_edit.html", ctx, status_code=400)


# ---------------------------------------------------------------------------
# Edit campaign
# ---------------------------------------------------------------------------


@router.get("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
async def admin_campaign_edit(
    request: Request,
    campaign_id: int,
    success: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    campaign = await campaign_service.get_campaign(db, campaign_id)
    if campaign is None:
        return RedirectResponse(url="/admin/campaigns", status_code=302)

    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level))
    ranks = list(ranks_result.scalars().all())

    # Load all members for the "associated member" dropdown on entries
    members_result = await db.execute(
        select(GuildMember).options(selectinload(GuildMember.rank)).order_by(GuildMember.discord_username)
    )
    all_members = list(members_result.scalars().all())

    # Load vote stats if live
    vote_stats = None
    if campaign.status == "live":
        try:
            vote_stats = await vote_service.get_vote_stats(db, campaign_id)
        except Exception:
            pass

    ctx = await _base_ctx(request, member, db)
    ctx.update({
        "campaign": campaign,
        "ranks": ranks,
        "all_members": all_members,
        "vote_stats": vote_stats,
        "flash_success": success,
        "flash_error": error,
        "error": None,
        "form": {},
    })
    return templates.TemplateResponse("admin/campaign_edit.html", ctx)


@router.post("/campaigns/{campaign_id}/edit", response_class=HTMLResponse)
async def admin_campaign_edit_post(
    request: Request,
    campaign_id: int,
    title: str = Form(...),
    description: str = Form(""),
    min_rank_to_vote: int = Form(...),
    min_rank_to_view: str = Form(""),
    start_at: str = Form(...),
    duration_hours: int = Form(...),
    discord_channel_id: str = Form(""),
    early_close_if_all_voted: str = Form("off"),
    picks_per_voter: int = Form(3),
    agent_enabled: str = Form("off"),
    agent_chattiness: str = Form("normal"),
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    try:
        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        start_dt = datetime.now(timezone.utc)

    try:
        await campaign_service.update_campaign(
            db,
            campaign_id,
            title=title,
            description=description or None,
            min_rank_to_vote=min_rank_to_vote,
            min_rank_to_view=int(min_rank_to_view) if min_rank_to_view else None,
            start_at=start_dt,
            duration_hours=duration_hours,
            discord_channel_id=discord_channel_id or None,
            early_close_if_all_voted=(early_close_if_all_voted == "on"),
            picks_per_voter=picks_per_voter,
            agent_enabled=(agent_enabled == "on"),
            agent_chattiness=agent_chattiness if agent_chattiness in ("quiet", "normal", "hype") else "normal",
        )
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?success=Campaign+updated.",
            status_code=302,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?error={e}",
            status_code=302,
        )


# ---------------------------------------------------------------------------
# Entry management
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/entries", response_class=HTMLResponse)
async def admin_add_entry(
    request: Request,
    campaign_id: int,
    name: str = Form(...),
    description: str = Form(""),
    image_url: str = Form(""),
    associated_member_id: str = Form(""),
    sort_order: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    try:
        await campaign_service.add_entry(
            db,
            campaign_id,
            name=name,
            description=description or None,
            image_url=image_url or None,
            associated_member_id=int(associated_member_id) if associated_member_id else None,
            sort_order=sort_order,
        )
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?success=Entry+added.",
            status_code=302,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?error={e}",
            status_code=302,
        )


@router.post("/campaigns/{campaign_id}/entries/{entry_id}/delete", response_class=HTMLResponse)
async def admin_delete_entry(
    request: Request,
    campaign_id: int,
    entry_id: int,
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    try:
        await campaign_service.remove_entry(db, campaign_id, entry_id)
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?success=Entry+removed.",
            status_code=302,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?error={e}",
            status_code=302,
        )


# ---------------------------------------------------------------------------
# Campaign lifecycle actions
# ---------------------------------------------------------------------------


@router.post("/campaigns/{campaign_id}/activate", response_class=HTMLResponse)
async def admin_activate(
    request: Request,
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    try:
        await campaign_service.activate_campaign(db, campaign_id)
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?success=Campaign+is+now+live!",
            status_code=302,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?error={e}",
            status_code=302,
        )


@router.post("/campaigns/{campaign_id}/close", response_class=HTMLResponse)
async def admin_close(
    request: Request,
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    try:
        await campaign_service.close_campaign(db, campaign_id)
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?success=Campaign+closed.+Results+calculated.",
            status_code=302,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/admin/campaigns/{campaign_id}/edit?error={e}",
            status_code=302,
        )


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


@router.get("/players", response_class=HTMLResponse)
async def admin_players(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login("/admin/players")

    ctx = await _base_ctx(request, member, db)
    return templates.TemplateResponse("admin/players.html", ctx)


# ---------------------------------------------------------------------------
# Player Manager JSON API — cookie-auth so browser fetch() works
# ---------------------------------------------------------------------------


@router.get("/players-data")
async def admin_players_data(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    members_result = await db.execute(
        select(GuildMember)
        .options(selectinload(GuildMember.rank))
        .order_by(GuildMember.discord_username)
    )
    members = list(members_result.scalars().all())

    # Build set of discord_ids already linked to a player
    linked_discord_ids = {m.discord_id for m in members if m.discord_id}

    # Join common.characters with guild_identity notes+rank by name+realm
    chars_result = await db.execute(text("""
        SELECT c.id, c.name, c.realm, c.class, c.spec, c.role, c.main_alt, c.member_id,
               wc.guild_note, wc.officer_note, wc.guild_rank_name,
               (wc.id IS NOT NULL) AS in_wow_scan
        FROM common.characters c
        LEFT JOIN guild_identity.wow_characters wc
            ON LOWER(wc.character_name) = LOWER(c.name)
            AND LOWER(wc.realm_slug) = LOWER(REPLACE(c.realm, '''', ''))
        ORDER BY c.name
    """))
    chars = chars_result.mappings().all()

    # Main character info per member (for display_name and role fallbacks)
    main_char_by_member = {}   # member_id -> {name, role}
    for c in chars:
        if c["main_alt"] == "main" and c["member_id"] and c["member_id"] not in main_char_by_member:
            main_char_by_member[c["member_id"]] = {"name": c["name"], "role": c["role"]}

    # Build rank role_id → rank_name lookup for Discord role matching
    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level.desc()))
    all_ranks = list(ranks_result.scalars().all())
    role_id_to_rank = {r.discord_role_id: r for r in all_ranks if r.discord_role_id}

    # Get Discord server member list from bot
    discord_users = []
    try:
        from sv_common.discord.bot import get_bot
        from patt.config import get_settings
        settings = get_settings()
        bot = get_bot()
        if bot and not bot.is_closed() and settings.discord_guild_id:
            guild = bot.get_guild(int(settings.discord_guild_id))
            if guild:
                for dm in guild.members:
                    if dm.bot:
                        continue
                    # Find highest guild rank from Discord roles
                    highest_rank = None
                    for role in dm.roles:
                        r = role_id_to_rank.get(str(role.id))
                        if r and (highest_rank is None or r.level > highest_rank.level):
                            highest_rank = r
                    discord_users.append({
                        "id": str(dm.id),
                        "username": dm.name,
                        "display_name": dm.display_name,
                        "linked": str(dm.id) in linked_discord_ids,
                        "rank_name": highest_rank.name if highest_rank else None,
                        "rank_level": highest_rank.level if highest_rank else 0,
                    })
                discord_users.sort(key=lambda u: u["display_name"].lower())
    except Exception as e:
        logger.warning("Could not load Discord members: %s", e)

    return JSONResponse({
        "ok": True,
        "data": {
            "discord_users": discord_users,
            "players": [
                {
                    "id": m.id,
                    "discord_username": m.discord_username,
                    "display_name": m.display_name,
                    "discord_id": m.discord_id,
                    "rank_name": m.rank.name if m.rank else "Unknown",
                    "rank_level": m.rank.level if m.rank else 0,
                    "registered": m.user_id is not None,
                    "main_char_name": (main_char_by_member.get(m.id) or {}).get("name"),
                    "main_char_role": (main_char_by_member.get(m.id) or {}).get("role"),
                }
                for m in members
            ],
            "characters": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "realm": c["realm"],
                    "class": c["class"],
                    "spec": c["spec"],
                    "role": c["role"],
                    "main_alt": c["main_alt"],
                    "member_id": c["member_id"],
                    "guild_note": c["guild_note"] or "",
                    "officer_note": c["officer_note"] or "",
                    "guild_rank_name": c["guild_rank_name"] or "",
                    "in_wow_scan": bool(c["in_wow_scan"]),
                }
                for c in chars
            ],
        },
    })


@router.patch("/characters/{char_id}/assign")
async def admin_assign_character(
    request: Request,
    char_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        member_id = body.get("member_id")  # may be None (unlink)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    from sv_common.db.models import Character
    result = await db.execute(select(Character).where(Character.id == char_id))
    char = result.scalar_one_or_none()
    if not char:
        return JSONResponse({"ok": False, "error": f"Character {char_id} not found"}, status_code=404)

    char.member_id = member_id
    await db.commit()

    member_name = "Unlinked"
    if member_id:
        m_result = await db.execute(
            select(GuildMember).where(GuildMember.id == member_id)
        )
        m = m_result.scalar_one_or_none()
        if m:
            member_name = m.display_name or m.discord_username

    return JSONResponse({
        "ok": True,
        "data": {
            "char_id": char_id,
            "char_name": char.name,
            "member_id": member_id,
            "member_name": member_name,
        },
    })


@router.delete("/characters/{char_id}")
async def admin_delete_character(
    request: Request,
    char_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    from sv_common.db.models import Character
    result = await db.execute(select(Character).where(Character.id == char_id))
    char = result.scalar_one_or_none()
    if not char:
        return JSONResponse({"ok": False, "error": f"Character {char_id} not found"}, status_code=404)

    name = char.name
    await db.delete(char)
    await db.commit()
    return JSONResponse({"ok": True, "data": {"deleted": True, "char_name": name}})


@router.patch("/characters/{char_id}/main-alt")
async def admin_toggle_main_alt(
    request: Request,
    char_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        main_alt = body.get("main_alt")
        if main_alt not in ("main", "alt"):
            raise ValueError("invalid")
    except Exception:
        return JSONResponse({"ok": False, "error": "main_alt must be 'main' or 'alt'"}, status_code=400)

    from sv_common.db.models import Character
    result = await db.execute(select(Character).where(Character.id == char_id))
    char = result.scalar_one_or_none()
    if not char:
        return JSONResponse({"ok": False, "error": f"Character {char_id} not found"}, status_code=404)

    char.main_alt = main_alt
    await db.commit()

    return JSONResponse({"ok": True, "data": {"char_id": char_id, "char_name": char.name, "main_alt": main_alt}})


@router.post("/players/create")
async def admin_create_player(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new guild_member record (no Discord or registration required)."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        display_name = (body.get("display_name") or "").strip()
        if not display_name:
            return JSONResponse({"ok": False, "error": "display_name required"}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    # Use a placeholder discord_username from display_name (can be updated later)
    import re
    username_slug = re.sub(r'[^a-z0-9_]', '_', display_name.lower()).strip('_') or "player"

    # Get default rank (Initiate = level 1)
    rank_result = await db.execute(select(GuildRank).order_by(GuildRank.level).limit(1))
    default_rank = rank_result.scalar_one_or_none()
    if not default_rank:
        return JSONResponse({"ok": False, "error": "No ranks configured"}, status_code=500)

    new_member = GuildMember(
        discord_username=username_slug,
        display_name=display_name,
        rank_id=default_rank.id,
        rank_source="manual",
    )
    db.add(new_member)
    await db.commit()
    await db.refresh(new_member)

    return JSONResponse({
        "ok": True,
        "data": {
            "id": new_member.id,
            "discord_username": new_member.discord_username,
            "display_name": new_member.display_name,
            "discord_id": None,
            "rank_name": default_rank.name,
            "rank_level": default_rank.level,
            "registered": False,
        },
    })


@router.patch("/players/{member_id}/link-discord")
async def admin_link_discord(
    request: Request,
    member_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Link (or unlink) a Discord user to a guild member player."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        discord_id = body.get("discord_id")      # None = unlink
        discord_username = body.get("discord_username", "")
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    result = await db.execute(select(GuildMember).where(GuildMember.id == member_id))
    m = result.scalar_one_or_none()
    if not m:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    m.discord_id = discord_id or None
    if discord_username and discord_id:
        # Update discord_username to match Discord display if we have it
        m.discord_username = discord_username
    await db.commit()

    return JSONResponse({
        "ok": True,
        "data": {"member_id": member_id, "discord_id": discord_id},
    })


@router.delete("/players/{member_id}")
async def admin_delete_player(
    request: Request,
    member_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    result = await db.execute(select(GuildMember).where(GuildMember.id == member_id))
    m = result.scalar_one_or_none()
    if not m:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    name = m.display_name or m.discord_username
    # Use raw SQL to let DB-level cascades handle dependent rows cleanly,
    # bypassing SQLAlchemy ORM's attempt to NULL foreign keys first.
    await db.execute(text("DELETE FROM common.guild_members WHERE id = :id"), {"id": member_id})
    await db.commit()
    return JSONResponse({"ok": True, "data": {"deleted": True, "name": name}})


@router.patch("/players/{member_id}/display-name")
async def admin_update_display_name(
    request: Request,
    member_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        display_name = (body.get("display_name") or "").strip()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    result = await db.execute(select(GuildMember).where(GuildMember.id == member_id))
    m = result.scalar_one_or_none()
    if not m:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    m.display_name = display_name or None  # None clears it, falls back to discord_username in UI
    await db.commit()
    return JSONResponse({"ok": True, "data": {"member_id": member_id, "display_name": m.display_name}})


@router.get("/roster", response_class=HTMLResponse)
async def admin_roster(
    request: Request,
    db: AsyncSession = Depends(get_db),
    success: str | None = None,
    error: str | None = None,
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login("/admin/roster")

    members_result = await db.execute(
        select(GuildMember)
        .options(
            selectinload(GuildMember.rank),
            selectinload(GuildMember.characters),
            selectinload(GuildMember.invite_codes),
        )
        .order_by(GuildMember.discord_username)
    )
    all_members = list(members_result.scalars().all())

    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level))
    ranks = list(ranks_result.scalars().all())

    ctx = await _base_ctx(request, member, db)
    ctx.update({
        "members": all_members,
        "ranks": ranks,
        "flash_success": success,
        "flash_error": error,
        "now": datetime.now(timezone.utc),
    })
    return templates.TemplateResponse("admin/roster.html", ctx)


@router.post("/roster/add", response_class=HTMLResponse)
async def admin_roster_add(
    request: Request,
    discord_username: str = Form(...),
    discord_id: str = Form(""),
    display_name: str = Form(""),
    rank_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    member = await _require_admin(request, db)
    if member is None:
        return _redirect_login("/admin/roster")

    try:
        from sv_common.identity.members import create_member
        await create_member(
            db,
            discord_username=discord_username,
            discord_id=discord_id or None,
            display_name=display_name or None,
            rank_id=rank_id,
        )
        return RedirectResponse(url="/admin/roster?success=Member+added.", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/admin/roster?error={e}", status_code=302)


@router.post("/roster/{member_id}/update", response_class=HTMLResponse)
async def admin_roster_update(
    request: Request,
    member_id: int,
    discord_id: str = Form(""),
    display_name: str = Form(""),
    rank_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return _redirect_login("/admin/roster")

    try:
        from sv_common.identity.members import update_member
        updates = {"rank_id": rank_id}
        if discord_id:
            updates["discord_id"] = discord_id
        if display_name:
            updates["display_name"] = display_name
        await update_member(db, member_id, **updates)
        return RedirectResponse(url="/admin/roster?success=Member+updated.", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/admin/roster?error={e}", status_code=302)


@router.post("/roster/{member_id}/invite", response_class=HTMLResponse)
async def admin_roster_invite(
    request: Request,
    member_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return _redirect_login("/admin/roster")

    try:
        from sv_common.auth.invite_codes import generate_invite_code
        from sv_common.db.models import InviteCode
        from datetime import timedelta

        code = generate_invite_code()
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        invite = InviteCode(
            code=code,
            member_id=member_id,
            created_by=admin.id,
            expires_at=expires,
        )
        db.add(invite)
        await db.flush()

        # Try to send Discord DM
        dm_sent = False
        try:
            from sv_common.discord.bot import get_bot
            bot = get_bot()
            if bot is not None:
                target = await db.get(GuildMember, member_id)
                if target and target.discord_id:
                    from sv_common.discord.dm import send_invite_dm
                    await send_invite_dm(bot, target.discord_id, code)
                    dm_sent = True
        except Exception as dm_err:
            logger.warning("DM send failed: %s", dm_err)

        msg = f"Invite+code+{code}+created"
        if dm_sent:
            msg += "+and+sent+via+Discord."
        else:
            msg += ".+DM+not+sent+(member+may+not+have+Discord+ID)."
        return RedirectResponse(url=f"/admin/roster?success={msg}", status_code=302)
    except Exception as e:
        logger.error("Invite error: %s", e)
        return RedirectResponse(url=f"/admin/roster?error={e}", status_code=302)
