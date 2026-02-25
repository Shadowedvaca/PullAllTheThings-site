"""Admin page routes: campaign management and roster management."""

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_db, get_page_member
from patt.services import campaign_service, vote_service
from patt.templating import templates
from sv_common.db.models import (
    AuditIssue, DiscordUser, GuildRank, Player, Specialization, WowCharacter, PlayerCharacter,
)
from sv_common.identity import members as member_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-pages"])

MIN_ADMIN_RANK = 4  # Officer+


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_admin(request: Request, db: AsyncSession) -> Player | None:
    """Return player if Officer+, else None."""
    player = await get_page_member(request, db)
    if player is None:
        return None
    if not player.guild_rank or player.guild_rank.level < MIN_ADMIN_RANK:
        return None
    return player


async def _base_ctx(request: Request, player: Player, db: AsyncSession) -> dict:
    active = await campaign_service.list_campaigns(db, status="live")
    return {
        "request": request,
        "current_member": player,
        "active_campaigns": active,
    }


def _player_tz_from_name(tz_name: str) -> ZoneInfo:
    """Return ZoneInfo for a timezone name string, falling back to UTC."""
    try:
        return ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("UTC")


def _player_tz(player: "Player") -> ZoneInfo:
    """Return the player's ZoneInfo, falling back to UTC on invalid names."""
    try:
        return ZoneInfo(player.timezone or "UTC")
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("UTC")


# Google Drive URL → uc?id=FILE_ID&export=view normalizer
_DRIVE_FILE_ID_RE = re.compile(
    r"drive\.google\.com"
    r"(?:/file/d/([A-Za-z0-9_-]+)"
    r"|/open\?[^'\"\s]*id=([A-Za-z0-9_-]+)"
    r"|/uc\?[^'\"\s]*id=([A-Za-z0-9_-]+))"
)
_BARE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{25,}$")


def _normalize_image_url(url: str) -> str:
    """Convert any Google Drive URL format to the thumbnail embed form.

    Uses drive.google.com/thumbnail?id={id}&sz=w2000 which is reliable for
    'anyone with the link' files and bypasses Google's virus-scan redirect.

    Accepts:
      - drive.google.com/file/d/{id}/view
      - drive.google.com/open?id={id}
      - drive.google.com/uc?id={id}
      - drive.google.com/thumbnail?id={id}  (updates sz if missing)
      - Bare file IDs (25+ alphanumeric/_/- chars)
      - Non-Drive URLs are returned unchanged.
    """
    if not url:
        return url
    url = url.strip()
    m = _DRIVE_FILE_ID_RE.search(url)
    if m:
        file_id = m.group(1) or m.group(2) or m.group(3)
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w2000"
    if _BARE_FILE_ID_RE.match(url):
        return f"https://drive.google.com/thumbnail?id={url}&sz=w2000"
    return url


def _redirect_login(url: str) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={url}", status_code=302)


def _redirect_forbidden() -> RedirectResponse:
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
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/campaigns")

    campaigns = await campaign_service.list_campaigns(db)
    order = {"live": 0, "draft": 1, "closed": 2, "archived": 3}
    campaigns.sort(key=lambda c: order.get(c.status, 9))

    ctx = await _base_ctx(request, player, db)
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
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/campaigns/new")

    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level))
    ranks = list(ranks_result.scalars().all())

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "ranks": ranks,
        "campaign": None,
        "error": None,
        "form": {},
        "user_timezone": player.timezone or "UTC",
        "start_at_local": None,
    })
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
    user_timezone: str = Form("UTC"),
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/campaigns")

    try:
        tz = _player_tz_from_name(user_timezone)
        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tz)
        start_dt = start_dt.astimezone(timezone.utc)
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
            created_by=player.id,
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
        ctx = await _base_ctx(request, player, db)
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
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    campaign = await campaign_service.get_campaign(db, campaign_id)
    if campaign is None:
        return RedirectResponse(url="/admin/campaigns", status_code=302)

    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level))
    ranks = list(ranks_result.scalars().all())

    # Load all players for the "associated player" dropdown on entries
    players_result = await db.execute(
        select(Player).options(selectinload(Player.guild_rank)).order_by(Player.display_name)
    )
    all_players = list(players_result.scalars().all())

    vote_stats = None
    if campaign.status == "live":
        try:
            vote_stats = await vote_service.get_vote_stats(db, campaign_id)
        except Exception:
            pass

    user_tz = _player_tz(player)
    start_at_local = (
        campaign.start_at.astimezone(user_tz) if campaign and campaign.start_at else None
    )

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "campaign": campaign,
        "ranks": ranks,
        "all_members": all_players,
        "vote_stats": vote_stats,
        "flash_success": success,
        "flash_error": error,
        "error": None,
        "form": {},
        "user_timezone": player.timezone or "UTC",
        "start_at_local": start_at_local,
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
    user_timezone: str = Form("UTC"),
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    try:
        tz = _player_tz_from_name(user_timezone)
        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tz)
        start_dt = start_dt.astimezone(timezone.utc)
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
# Delete campaign
# ---------------------------------------------------------------------------


@router.delete("/campaigns/{campaign_id}")
async def admin_campaign_delete(
    request: Request,
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)
    try:
        deleted = await campaign_service.delete_campaign(db, campaign_id)
        if not deleted:
            return JSONResponse({"ok": False, "error": "Campaign not found"}, status_code=404)
        return JSONResponse({"ok": True, "data": {"deleted": True}})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


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
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login(f"/admin/campaigns/{campaign_id}/edit")

    try:
        await campaign_service.add_entry(
            db,
            campaign_id,
            name=name,
            description=description or None,
            image_url=_normalize_image_url(image_url) or None,
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


@router.post(
    "/campaigns/{campaign_id}/entries/{entry_id}/delete", response_class=HTMLResponse
)
async def admin_delete_entry(
    request: Request,
    campaign_id: int,
    entry_id: int,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
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
    player = await _require_admin(request, db)
    if player is None:
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
    player = await _require_admin(request, db)
    if player is None:
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
# Player Manager page
# ---------------------------------------------------------------------------


@router.get("/players", response_class=HTMLResponse)
async def admin_players(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/players")

    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level.desc()))
    ranks = list(ranks_result.scalars().all())

    ctx = await _base_ctx(request, player, db)
    ctx["guild_ranks"] = ranks
    return templates.TemplateResponse("admin/players.html", ctx)


# ---------------------------------------------------------------------------
# Player Manager JSON API — cookie-auth so browser fetch() works
# ---------------------------------------------------------------------------


@router.get("/players-data")
async def admin_players_data(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    players_result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.discord_user),
            selectinload(Player.characters),
            selectinload(Player.main_spec),
            selectinload(Player.offspec_spec),
        )
        .order_by(Player.display_name)
    )
    players = list(players_result.scalars().all())

    linked_discord_ids = {
        p.discord_user.discord_id
        for p in players
        if p.discord_user
    }

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

    # Build character list from player_characters bridge + wow_characters
    chars_result = await db.execute(text("""
        SELECT
            wc.id, wc.character_name AS name, wc.realm_slug AS realm,
            cl.name AS class, sp.name AS spec, ro.name AS role,
            pc.player_id,
            wc.guild_note, wc.officer_note,
            gr.name AS guild_rank_name,
            (wc.id IS NOT NULL) AS in_wow_scan,
            CASE WHEN p.main_character_id = wc.id THEN 'main'
                 WHEN p.offspec_character_id = wc.id THEN 'offspec'
                 ELSE 'alt' END AS main_alt
        FROM guild_identity.wow_characters wc
        LEFT JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
        LEFT JOIN guild_identity.players p ON p.id = pc.player_id
        LEFT JOIN guild_identity.classes cl ON cl.id = wc.class_id
        LEFT JOIN guild_identity.specializations sp ON sp.id = wc.active_spec_id
        LEFT JOIN guild_identity.roles ro ON ro.id = sp.default_role_id
        LEFT JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
        WHERE wc.removed_at IS NULL
        ORDER BY wc.character_name
    """))
    chars = chars_result.mappings().all()

    return JSONResponse({
        "ok": True,
        "data": {
            "discord_users": discord_users,
            "players": [
                {
                    "id": p.id,
                    "display_name": p.display_name,
                    "discord_id": p.discord_user.discord_id if p.discord_user else None,
                    "discord_username": p.discord_user.username if p.discord_user else None,
                    "rank_name": p.guild_rank.name if p.guild_rank else "Unknown",
                    "rank_level": p.guild_rank.level if p.guild_rank else 0,
                    "registered": p.website_user_id is not None,
                    "timezone": p.timezone or "UTC",
                    "main_character_id": p.main_character_id,
                    "offspec_character_id": p.offspec_character_id,
                    "main_spec_name": p.main_spec.name if p.main_spec else None,
                    "offspec_spec_name": p.offspec_spec.name if p.offspec_spec else None,
                    "auto_invite_events": p.auto_invite_events,
                    "crafting_notifications_enabled": p.crafting_notifications_enabled,
                }
                for p in players
            ],
            "characters": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "realm": c["realm"],
                    "class": c["class"] or "",
                    "spec": c["spec"] or "",
                    "role": c["role"] or "",
                    "main_alt": c["main_alt"],
                    "player_id": c["player_id"],
                    "guild_note": c["guild_note"] or "",
                    "officer_note": c["officer_note"] or "",
                    "guild_rank_name": c["guild_rank_name"] or "",
                    "in_wow_scan": True,
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
        player_id = body.get("player_id") or body.get("member_id")  # support old key
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    result = await db.execute(
        select(WowCharacter).where(WowCharacter.id == char_id)
    )
    char = result.scalar_one_or_none()
    if not char:
        return JSONResponse(
            {"ok": False, "error": f"Character {char_id} not found"}, status_code=404
        )

    # Remove existing bridge row
    await db.execute(
        text("DELETE FROM guild_identity.player_characters WHERE character_id = :cid"),
        {"cid": char_id},
    )

    player_name = "Unlinked"
    if player_id:
        bridge = PlayerCharacter(player_id=player_id, character_id=char_id)
        db.add(bridge)
        p_result = await db.execute(select(Player).where(Player.id == player_id))
        p = p_result.scalar_one_or_none()
        if p:
            player_name = p.display_name

    await db.commit()

    return JSONResponse({
        "ok": True,
        "data": {
            "char_id": char_id,
            "char_name": char.character_name,
            "player_id": player_id,
            "player_name": player_name,
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

    result = await db.execute(
        select(WowCharacter).where(WowCharacter.id == char_id)
    )
    char = result.scalar_one_or_none()
    if not char:
        return JSONResponse(
            {"ok": False, "error": f"Character {char_id} not found"}, status_code=404
        )

    name = char.character_name
    await db.delete(char)
    await db.commit()
    return JSONResponse({"ok": True, "data": {"deleted": True, "char_name": name}})


@router.patch("/characters/{char_id}/main-alt")
async def admin_set_main(
    request: Request,
    char_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Set a character as the player's main or offspec, or clear it."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        main_alt = body.get("main_alt")
        if main_alt not in ("main", "offspec", "alt"):
            raise ValueError("invalid")
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "main_alt must be 'main', 'offspec', or 'alt'"},
            status_code=400,
        )

    # Find who owns this character
    bridge_result = await db.execute(
        select(PlayerCharacter).where(PlayerCharacter.character_id == char_id)
    )
    bridge = bridge_result.scalar_one_or_none()
    if not bridge:
        return JSONResponse(
            {"ok": False, "error": "Character not linked to a player"}, status_code=404
        )

    p_result = await db.execute(select(Player).where(Player.id == bridge.player_id))
    player = p_result.scalar_one_or_none()
    if not player:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    if main_alt == "main":
        player.main_character_id = char_id
    elif main_alt == "offspec":
        player.offspec_character_id = char_id
    else:
        # alt — clear if it was main or offspec
        if player.main_character_id == char_id:
            player.main_character_id = None
        if player.offspec_character_id == char_id:
            player.offspec_character_id = None

    await db.commit()

    return JSONResponse({
        "ok": True,
        "data": {"char_id": char_id, "main_alt": main_alt},
    })


@router.post("/players/create")
async def admin_create_player(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        display_name = (body.get("display_name") or "").strip()
        if not display_name:
            return JSONResponse(
                {"ok": False, "error": "display_name required"}, status_code=400
            )
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    rank_result = await db.execute(select(GuildRank).order_by(GuildRank.level).limit(1))
    default_rank = rank_result.scalar_one_or_none()
    if not default_rank:
        return JSONResponse({"ok": False, "error": "No ranks configured"}, status_code=500)

    new_player = Player(
        display_name=display_name,
        guild_rank_id=default_rank.id,
        guild_rank_source="manual",
    )
    db.add(new_player)
    await db.commit()
    await db.refresh(new_player)

    return JSONResponse({
        "ok": True,
        "data": {
            "id": new_player.id,
            "display_name": new_player.display_name,
            "discord_id": None,
            "rank_name": default_rank.name,
            "rank_level": default_rank.level,
            "registered": False,
        },
    })


@router.patch("/players/{player_id}/link-discord")
async def admin_link_discord(
    request: Request,
    player_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        discord_id = body.get("discord_id")  # None = unlink
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    result = await db.execute(select(Player).where(Player.id == player_id))
    p = result.scalar_one_or_none()
    if not p:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    if discord_id:
        from sv_common.db.models import DiscordUser
        du_result = await db.execute(
            select(DiscordUser).where(DiscordUser.discord_id == discord_id)
        )
        du = du_result.scalar_one_or_none()
        p.discord_user_id = du.id if du else None
    else:
        p.discord_user_id = None

    await db.commit()
    return JSONResponse({
        "ok": True,
        "data": {"player_id": player_id, "discord_id": discord_id},
    })


@router.delete("/players/{player_id}")
async def admin_delete_player(
    request: Request,
    player_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    result = await db.execute(select(Player).where(Player.id == player_id))
    p = result.scalar_one_or_none()
    if not p:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    name = p.display_name
    await db.execute(
        text("DELETE FROM guild_identity.players WHERE id = :id"), {"id": player_id}
    )
    await db.commit()
    return JSONResponse({"ok": True, "data": {"deleted": True, "name": name}})


@router.patch("/players/{player_id}/display-name")
async def admin_update_display_name(
    request: Request,
    player_id: int,
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

    result = await db.execute(select(Player).where(Player.id == player_id))
    p = result.scalar_one_or_none()
    if not p:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    p.display_name = display_name or p.display_name
    await db.commit()
    return JSONResponse({
        "ok": True,
        "data": {"player_id": player_id, "display_name": p.display_name},
    })


@router.post("/players/{player_id}/send-invite")
async def admin_send_invite_json(
    request: Request,
    player_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Generate an invite code and optionally DM it — returns JSON for the Player Manager."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        from sv_common.auth.invite_codes import generate_invite_code

        code = await generate_invite_code(db, player_id=player_id, created_by_id=admin.id)

        result = await db.execute(
            select(Player).where(Player.id == player_id).options(selectinload(Player.discord_user))
        )
        target = result.scalar_one_or_none()
        dm_sent = False
        if target and target.discord_user:
            try:
                from sv_common.discord.bot import get_bot
                from sv_common.discord.dm import send_invite_dm
                from sv_common.db.models import DiscordConfig
                bot = get_bot()
                cfg_result = await db.execute(select(DiscordConfig).limit(1))
                cfg = cfg_result.scalar_one_or_none()
                dm_enabled = cfg and cfg.bot_dm_enabled and cfg.feature_invite_dm
                if bot is not None and dm_enabled:
                    base_url = str(request.base_url).rstrip("/")
                    register_url = f"{base_url}/register?code={code}"
                    await send_invite_dm(bot, target.discord_user.discord_id, code, register_url)
                    dm_sent = True
            except Exception as dm_err:
                logger.warning("DM send failed for player %d: %s", player_id, dm_err)

        return JSONResponse({
            "ok": True,
            "code": code,
            "dm_sent": dm_sent,
            "has_discord": bool(target and target.discord_user),
        })
    except Exception as e:
        logger.error("Invite error for player %d: %s", player_id, e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Reference Tables page
# ---------------------------------------------------------------------------


@router.get("/reference-tables", response_class=HTMLResponse)
async def admin_reference_tables(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from sv_common.db.models import Role, WowClass, Specialization
    from sv_common.identity import ranks as rank_service
    from patt.services import season_service
    from sqlalchemy.orm import selectinload

    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/reference-tables")

    ranks = await rank_service.get_all_ranks(db)
    seasons = await season_service.get_all_seasons(db)

    roles_result = await db.execute(select(Role).order_by(Role.id))
    roles = list(roles_result.scalars().all())

    classes_result = await db.execute(
        select(WowClass)
        .options(selectinload(WowClass.specializations).selectinload(Specialization.default_role))
        .order_by(WowClass.name)
    )
    classes = list(classes_result.scalars().all())

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "ranks": ranks,
        "roles": roles,
        "classes": classes,
        "seasons": seasons,
    })
    return templates.TemplateResponse("admin/reference_tables.html", ctx)


# ---------------------------------------------------------------------------
# Roster page (legacy — kept for reference; redirects to /admin/players)
# ---------------------------------------------------------------------------


@router.get("/roster", response_class=HTMLResponse)
async def admin_roster(
    request: Request,
    db: AsyncSession = Depends(get_db),
    success: str | None = None,
    error: str | None = None,
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/roster")

    players_result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.characters),
            selectinload(Player.invite_codes),
        )
        .order_by(Player.display_name)
    )
    all_players = list(players_result.scalars().all())

    ranks_result = await db.execute(select(GuildRank).order_by(GuildRank.level))
    ranks = list(ranks_result.scalars().all())

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "members": all_players,
        "ranks": ranks,
        "flash_success": success,
        "flash_error": error,
        "now": datetime.now(timezone.utc),
    })
    return templates.TemplateResponse("admin/roster.html", ctx)


@router.post("/roster/add", response_class=HTMLResponse)
async def admin_roster_add(
    request: Request,
    display_name: str = Form(...),
    rank_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/roster")

    try:
        await member_service.create_player(
            db,
            display_name=display_name,
            guild_rank_id=rank_id,
        )
        return RedirectResponse(url="/admin/roster?success=Player+added.", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/admin/roster?error={e}", status_code=302)


@router.post("/roster/{player_id}/update", response_class=HTMLResponse)
async def admin_roster_update(
    request: Request,
    player_id: int,
    display_name: str = Form(""),
    rank_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return _redirect_login("/admin/roster")

    try:
        updates: dict = {"guild_rank_id": rank_id}
        if display_name:
            updates["display_name"] = display_name
        await member_service.update_player(db, player_id, **updates)
        return RedirectResponse(
            url="/admin/roster?success=Player+updated.", status_code=302
        )
    except Exception as e:
        return RedirectResponse(url=f"/admin/roster?error={e}", status_code=302)


@router.post("/roster/{player_id}/invite", response_class=HTMLResponse)
async def admin_roster_invite(
    request: Request,
    player_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return _redirect_login("/admin/roster")

    try:
        from sv_common.auth.invite_codes import generate_invite_code
        from sv_common.db.models import InviteCode
        from datetime import timedelta

        code = await generate_invite_code(db, player_id=player_id, created_by_id=admin.id)

        dm_sent = False
        target = await db.get(Player, player_id)
        if target and target.discord_user:
            try:
                from sv_common.discord.bot import get_bot
                from sv_common.discord.dm import send_invite_dm, is_invite_dm_enabled
                bot = get_bot()
                pool = getattr(request.app.state, "guild_sync_pool", None)
                invite_ok = pool and await is_invite_dm_enabled(pool)
                if bot is not None and invite_ok:
                    await send_invite_dm(bot, target.discord_user.discord_id, code)
                    dm_sent = True
            except Exception as dm_err:
                logger.warning("DM send failed: %s", dm_err)

        msg = f"Invite+code+{code}+created"
        if dm_sent:
            msg += "+and+sent+via+Discord."
        elif target and target.discord_user:
            msg += ".+DM+not+sent+(Invite+DMs+are+disabled+in+Bot+Settings)."
        else:
            msg += ".+DM+not+sent+(no+Discord+linked)."
        return RedirectResponse(url=f"/admin/roster?success={msg}", status_code=302)
    except Exception as e:
        logger.error("Invite error: %s", e)
        return RedirectResponse(url=f"/admin/roster?error={e}", status_code=302)


# ---------------------------------------------------------------------------
# Bot settings
# ---------------------------------------------------------------------------


@router.get("/bot-settings", response_class=HTMLResponse)
async def admin_bot_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/bot-settings")

    from sqlalchemy import select as sa_select, text
    from sv_common.db.models import DiscordConfig

    result = await db.execute(sa_select(DiscordConfig).limit(1))
    discord_config = result.scalar_one_or_none()

    ctx = await _base_ctx(request, player, db)
    ctx["discord_config"] = discord_config
    return templates.TemplateResponse("admin/bot_settings.html", ctx)


# ---------------------------------------------------------------------------
# Availability page (Phase 3.1)
# ---------------------------------------------------------------------------


@router.get("/availability", response_class=HTMLResponse)
async def admin_availability(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from sv_common.db.models import Player, PlayerAvailability, RecurringEvent, Specialization
    from sqlalchemy.orm import selectinload

    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/availability")

    # Total active players
    total_result = await db.execute(
        select(sa_func.count(Player.id)).where(Player.is_active.is_(True))
    )
    total_active = total_result.scalar() or 0

    # All recurring events keyed by day_of_week (all, not just active — to populate table)
    events_result = await db.execute(
        select(RecurringEvent).order_by(RecurringEvent.day_of_week)
    )
    events_by_day = {e.day_of_week: e for e in events_result.scalars().all()}

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days = []
    for dow in range(7):
        avail_result = await db.execute(
            select(PlayerAvailability)
            .options(
                selectinload(PlayerAvailability.player).selectinload(Player.guild_rank),
                selectinload(PlayerAvailability.player)
                    .selectinload(Player.main_spec)
                    .selectinload(Specialization.default_role),
            )
            .where(PlayerAvailability.day_of_week == dow)
        )
        avail_rows = list(avail_result.scalars().all())

        available_count = len(avail_rows)
        pct = round(available_count / total_active * 100, 1) if total_active else 0.0
        weighted_score = sum(
            r.player.guild_rank.scheduling_weight if r.player.guild_rank else 0
            for r in avail_rows
        )

        if pct >= 70:
            bar_class = "bar--green"
        elif pct >= 40:
            bar_class = "bar--amber"
        else:
            bar_class = "bar--red"

        player_list = []
        for row in avail_rows:
            p = row.player
            main_role = None
            if p.main_spec and p.main_spec.default_role:
                main_role = p.main_spec.default_role.name
            player_list.append({
                "display_name": p.display_name,
                "rank": p.guild_rank.name if p.guild_rank else "—",
                "main_role": main_role,
            })

        days.append({
            "dow": dow,
            "day_name": day_names[dow],
            "available_count": available_count,
            "availability_pct": pct,
            "weighted_score": weighted_score,
            "bar_class": bar_class,
            "players": player_list,
            "event": events_by_day.get(dow),
        })

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "days": days,
        "total_active": total_active,
        "events_by_day": events_by_day,
    })
    return templates.TemplateResponse("admin/availability.html", ctx)


# ---------------------------------------------------------------------------
# Raid Tools page (Phase 3.4)
# ---------------------------------------------------------------------------


@router.get("/raid-tools", response_class=HTMLResponse)
async def admin_raid_tools(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from sv_common.db.models import DiscordConfig

    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/raid-tools")

    cfg_result = await db.execute(select(DiscordConfig).limit(1))
    discord_config = cfg_result.scalar_one_or_none()

    # Active players with main characters for roster preview
    players_result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.main_character),
            selectinload(Player.main_spec).selectinload(Specialization.default_role),
        )
        .where(Player.is_active.is_(True), Player.main_character_id.is_not(None))
        .order_by(Player.display_name)
    )
    roster_players = list(players_result.scalars().all())

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "discord_config": discord_config,
        "roster_players": roster_players,
    })
    return templates.TemplateResponse("admin/raid_tools.html", ctx)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/audit-log", response_class=HTMLResponse)
async def admin_audit_log(
    request: Request,
    show: str = "open",  # "open" or "resolved"
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/audit-log")

    q = (
        select(AuditIssue)
        .options(
            selectinload(AuditIssue.wow_character),
            selectinload(AuditIssue.discord_member),
        )
        .order_by(AuditIssue.created_at.desc())
    )
    if show == "resolved":
        q = q.where(AuditIssue.resolved_at.is_not(None))
    else:
        q = q.where(AuditIssue.resolved_at.is_(None))

    result = await db.execute(q)
    issues = list(result.scalars().all())

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "issues": issues,
        "show": show,
    })
    return templates.TemplateResponse("admin/audit_log.html", ctx)


@router.get("/crafting-sync", response_class=HTMLResponse)
async def admin_crafting_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/crafting-sync")

    ctx = await _base_ctx(request, player, db)
    return templates.TemplateResponse("admin/crafting_sync.html", ctx)


# ---------------------------------------------------------------------------
# Data Quality page
# ---------------------------------------------------------------------------


@router.get("/data-quality", response_class=HTMLResponse)
async def admin_data_quality(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from sv_common.guild_sync.rules import RULES

    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/data-quality")

    pool = getattr(request.app.state, "guild_sync_pool", None)
    rules_with_stats = []
    recent_issues = []

    if pool:
        async with pool.acquire() as conn:
            stats_rows = await conn.fetch(
                """SELECT
                       issue_type,
                       COUNT(*) FILTER (WHERE resolved_at IS NULL)         AS open_count,
                       COUNT(*) FILTER (WHERE resolved_at IS NOT NULL
                                          AND resolved_at > NOW() - INTERVAL '30 days') AS resolved_30d,
                       MAX(created_at) AS last_triggered
                   FROM guild_identity.audit_issues
                   WHERE issue_type = ANY($1::text[])
                   GROUP BY issue_type""",
                list(RULES.keys()),
            )
            stats_by_type = {r["issue_type"]: r for r in stats_rows}

            recent_rows = await conn.fetch(
                """SELECT ai.id, ai.issue_type, ai.severity, ai.summary,
                          ai.created_at, ai.resolved_at, ai.resolved_by,
                          wc.character_name,
                          du.display_name AS discord_display_name,
                          du.username     AS discord_username
                   FROM guild_identity.audit_issues ai
                   LEFT JOIN guild_identity.wow_characters wc ON wc.id = ai.wow_character_id
                   LEFT JOIN guild_identity.discord_users  du ON du.id = ai.discord_member_id
                   ORDER BY ai.created_at DESC
                   LIMIT 50"""
            )
            recent_issues = [dict(r) for r in recent_rows]

        for issue_type, rule in RULES.items():
            s = stats_by_type.get(issue_type)
            rules_with_stats.append({
                "rule": rule,
                "open_count": s["open_count"] if s else 0,
                "resolved_30d": s["resolved_30d"] if s else 0,
                "last_triggered": s["last_triggered"] if s else None,
            })

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "rules_with_stats": rules_with_stats,
        "recent_issues": recent_issues,
        "pool_available": pool is not None,
    })
    return templates.TemplateResponse("admin/data_quality.html", ctx)


@router.post("/data-quality/scan")
async def admin_data_quality_scan_all(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Run all detection rules now."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.integrity_checker import run_integrity_check
    import asyncio
    asyncio.create_task(run_integrity_check(pool))
    return JSONResponse({"ok": True, "status": "scan_started"})


@router.post("/data-quality/scan/{issue_type}")
async def admin_data_quality_scan_type(
    request: Request,
    issue_type: str,
    db: AsyncSession = Depends(get_db),
):
    """Run detection for a single rule type."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.rules import RULES
    from sv_common.guild_sync.integrity_checker import DETECT_FUNCTIONS

    if issue_type not in RULES:
        return JSONResponse({"ok": False, "error": f"Unknown issue type: {issue_type}"}, status_code=400)

    if issue_type == "role_mismatch":
        # role_mismatch uses a combined detect function
        from sv_common.guild_sync.integrity_checker import detect_role_mismatch
        import asyncio

        async def _run():
            async with pool.acquire() as conn:
                await detect_role_mismatch(conn)
        asyncio.create_task(_run())
        return JSONResponse({"ok": True, "status": "scan_started", "issue_type": issue_type})

    detect_fn = DETECT_FUNCTIONS.get(issue_type)
    if not detect_fn:
        return JSONResponse({"ok": False, "error": f"No detect function for: {issue_type}"}, status_code=400)

    import asyncio

    async def _run():
        async with pool.acquire() as conn:
            await detect_fn(conn)

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "status": "scan_started", "issue_type": issue_type})


@router.post("/data-quality/fix/{issue_id}")
async def admin_data_quality_fix_one(
    request: Request,
    issue_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Run mitigation for a specific issue."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.rules import RULES

    # Load the issue
    async with pool.acquire() as conn:
        issue_row = await conn.fetchrow(
            """SELECT id, issue_type, severity, wow_character_id, discord_member_id,
                      summary, details, issue_hash, created_at, resolved_at, resolved_by
               FROM guild_identity.audit_issues WHERE id = $1""",
            issue_id,
        )

    if not issue_row:
        return JSONResponse({"ok": False, "error": "Issue not found"}, status_code=404)

    if issue_row["resolved_at"] is not None:
        return JSONResponse({"ok": False, "error": "Issue already resolved"}, status_code=400)

    rule = RULES.get(issue_row["issue_type"])
    if not rule or not rule.mitigate_fn:
        return JSONResponse(
            {"ok": False, "error": f"No mitigation available for {issue_row['issue_type']}"},
            status_code=400,
        )

    import asyncio
    asyncio.create_task(rule.mitigate_fn(pool, dict(issue_row)))
    return JSONResponse({"ok": True, "status": "fix_started", "issue_id": issue_id})


@router.post("/data-quality/fix-all/{issue_type}")
async def admin_data_quality_fix_all_type(
    request: Request,
    issue_type: str,
    db: AsyncSession = Depends(get_db),
):
    """Run mitigation for all open issues of a given type."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.rules import RULES

    rule = RULES.get(issue_type)
    if not rule:
        return JSONResponse({"ok": False, "error": f"Unknown issue type: {issue_type}"}, status_code=400)
    if not rule.mitigate_fn:
        return JSONResponse(
            {"ok": False, "error": f"No mitigation available for {issue_type}"},
            status_code=400,
        )

    import asyncio

    async def _run_all():
        async with pool.acquire() as conn:
            issues = await conn.fetch(
                """SELECT id, issue_type, severity, wow_character_id, discord_member_id,
                          summary, details, issue_hash, created_at, resolved_at, resolved_by
                   FROM guild_identity.audit_issues
                   WHERE issue_type = $1 AND resolved_at IS NULL
                   ORDER BY created_at""",
                issue_type,
            )
        resolved = 0
        for issue in issues:
            try:
                ok = await rule.mitigate_fn(pool, dict(issue))
                if ok:
                    resolved += 1
            except Exception as exc:
                logger.error("fix-all %s issue %d error: %s", issue_type, issue["id"], exc)
        logger.info("fix-all %s: %d/%d resolved", issue_type, resolved, len(issues))

    asyncio.create_task(_run_all())
    return JSONResponse({"ok": True, "status": "fix_all_started", "issue_type": issue_type})


@router.post("/audit-log/{issue_id}/resolve", response_class=HTMLResponse)
async def admin_audit_resolve(
    request: Request,
    issue_id: int,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/audit-log")

    result = await db.execute(select(AuditIssue).where(AuditIssue.id == issue_id))
    issue = result.scalar_one_or_none()
    if issue and issue.resolved_at is None:
        issue.resolved_at = datetime.now(timezone.utc)
        issue.resolved_by = player.display_name

    return RedirectResponse(url="/admin/audit-log", status_code=302)
