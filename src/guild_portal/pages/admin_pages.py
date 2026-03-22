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

from guild_portal.deps import get_db, get_page_member
from guild_portal.nav import get_min_rank_for_screen, load_nav_items
from guild_portal.services import campaign_service, vote_service
from guild_portal.templating import templates
from sv_common.db.models import (
    AuditIssue, DiscordUser, GuildRank, Player, PlayerActionLog,
    ScreenPermission, Specialization, User, WowCharacter, PlayerCharacter,
)
from sv_common.identity import members as member_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-pages"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_screen(
    screen_key: str, request: Request, db: AsyncSession
) -> Player | None:
    """Return player if they have the required rank for this screen, else None."""
    player = await get_page_member(request, db)
    if player is None:
        return None
    min_level = await get_min_rank_for_screen(db, screen_key)
    rank_level = player.guild_rank.level if player.guild_rank else 0
    if rank_level < min_level:
        return None
    return player


# Keep _require_admin as a convenience alias (Officer+ check, used by
# API-style sub-routes that don't map to a single screen).
async def _require_admin(request: Request, db: AsyncSession) -> Player | None:
    return await _require_screen("player_manager", request, db)


_PATH_TO_SCREEN: list[tuple[str, str]] = [
    ("/admin/campaigns",       "campaigns"),
    ("/admin/players",         "player_manager"),
    ("/admin/users",           "users"),
    ("/admin/availability",    "availability"),
    ("/admin/raid-tools",      "raid_tools"),
    ("/admin/reference-tables","reference_tables"),
    ("/admin/bot-settings",    "bot_settings"),
    ("/admin/crafting-sync",   "crafting_sync"),
    ("/admin/data-quality",    "data_quality"),
    ("/admin/audit-log",       "audit_log"),
    ("/admin/drift",           "data_quality"),
    ("/admin/matching",        "data_quality"),
    ("/admin/site-config",     "site_config"),
    ("/admin/progression",     "progression"),
    ("/admin/warcraft-logs",   "warcraft_logs"),
    ("/admin/ah-pricing",      "ah_pricing"),
    ("/admin/attendance",      "attendance_report"),
    ("/admin/quotes",          "quotes"),
    ("/admin/error-routing",   "error_routing"),
]


def _screen_for_path(path: str) -> str:
    for prefix, key in _PATH_TO_SCREEN:
        if path.startswith(prefix):
            return key
    return ""


async def _base_ctx(request: Request, player: Player, db: AsyncSession) -> dict:
    active = await campaign_service.list_campaigns(db, status="live")
    nav_items = await load_nav_items(db, player)
    return {
        "request": request,
        "current_member": player,
        "active_campaigns": active,
        "nav_items": nav_items,
        "current_screen": _screen_for_path(request.url.path),
    }


async def _compute_best_rank(db: AsyncSession, player_id: int) -> "tuple[GuildRank, str] | tuple[None, None]":
    """Return the GuildRank the player qualifies for.

    Priority:
      1. Highest rank across all linked WoW characters (primary source of truth)
      2. Discord user's highest_guild_role (fallback — only if no characters have ranks)

    Does NOT override admin_override source — callers must check.
    """
    p_result = await db.execute(
        select(Player)
        .options(selectinload(Player.discord_user), selectinload(Player.characters))
        .where(Player.id == player_id)
    )
    p = p_result.scalar_one_or_none()
    if not p:
        return None, None

    # WoW character ranks are primary source of truth
    if p.characters:
        char_ids = [pc.character_id for pc in p.characters]
        chars_result = await db.execute(
            select(WowCharacter).where(WowCharacter.id.in_(char_ids))
        )
        chars = list(chars_result.scalars().all())
        rank_ids = {c.guild_rank_id for c in chars if c.guild_rank_id}
        if rank_ids:
            ranks_result = await db.execute(
                select(GuildRank).where(GuildRank.id.in_(rank_ids))
            )
            char_ranks = list(ranks_result.scalars().all())
            if char_ranks:
                return max(char_ranks, key=lambda r: r.level), "wow_character"

    # Discord is fallback only (no linked characters with a known rank)
    if p.discord_user and p.discord_user.highest_guild_role:
        dr_result = await db.execute(
            select(GuildRank).where(
                sa_func.lower(GuildRank.name) == p.discord_user.highest_guild_role.lower()
            )
        )
        dr = dr_result.scalar_one_or_none()
        if dr:
            return dr, "discord_sync"

    return None, None


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
        from guild_portal.config import get_settings
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
            pc.player_id, pc.link_source,
            wc.guild_note, wc.officer_note,
            gr.name AS guild_rank_name,
            (wc.id IS NOT NULL) AS in_wow_scan,
            CASE WHEN p.main_character_id = wc.id AND p.offspec_character_id = wc.id THEN 'main+offspec'
                 WHEN p.main_character_id = wc.id THEN 'main'
                 WHEN p.offspec_character_id = wc.id THEN 'offspec'
                 ELSE 'alt' END AS main_alt
        FROM guild_identity.wow_characters wc
        LEFT JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
        LEFT JOIN guild_identity.players p ON p.id = pc.player_id
        LEFT JOIN guild_identity.classes cl ON cl.id = wc.class_id
        LEFT JOIN guild_identity.specializations sp ON sp.id = wc.active_spec_id
        LEFT JOIN guild_identity.roles ro ON ro.id = sp.default_role_id
        LEFT JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
        WHERE wc.removed_at IS NULL AND wc.in_guild = TRUE
        ORDER BY wc.character_name
    """))
    chars = chars_result.mappings().all()

    # Load aliases grouped by player_id
    aliases_result = await db.execute(text("""
        SELECT id, player_id, alias, source
        FROM guild_identity.player_note_aliases
        ORDER BY alias
    """))
    aliases_by_player: dict = {}
    for ar in aliases_result.mappings().all():
        pid = ar["player_id"]
        if pid not in aliases_by_player:
            aliases_by_player[pid] = []
        aliases_by_player[pid].append({"id": ar["id"], "alias": ar["alias"], "source": ar["source"]})

    # Build set of bnet-verified player IDs
    bnet_result = await db.execute(
        text("SELECT player_id FROM guild_identity.battlenet_accounts")
    )
    bnet_verified_ids = {row[0] for row in bnet_result.all()}

    # Attendance status per player (feature-gated)
    attendance_by_player: dict = {}
    try:
        att_cfg = await db.execute(text(
            """
            SELECT attendance_feature_enabled, attendance_min_pct, attendance_trailing_events
            FROM common.discord_config LIMIT 1
            """
        ))
        att_cfg_row = att_cfg.mappings().first()
        if att_cfg_row and att_cfg_row["attendance_feature_enabled"]:
            min_pct = att_cfg_row["attendance_min_pct"] or 75
            trailing = att_cfg_row["attendance_trailing_events"] or 8
            att_rows = await db.execute(text(
                """
                SELECT ra.player_id, ra.attended, ra.noted_absence
                FROM patt.raid_attendance ra
                JOIN patt.raid_events re ON re.id = ra.event_id
                WHERE re.attendance_processed_at IS NOT NULL
                ORDER BY ra.player_id, re.start_time_utc DESC
                """
            ))
            # Group by player_id, take last N
            from collections import defaultdict
            raw: dict = defaultdict(list)
            for row in att_rows.mappings().all():
                raw[row["player_id"]].append(row)
            for pid, rows in raw.items():
                recent = rows[:trailing]
                attended = sum(1 for r in recent if r["attended"] or r["noted_absence"])
                total = len(recent)
                summary = f"{attended}/{total} raids"
                if total < 3:
                    status = "new"
                elif attended / total * 100 >= min_pct:
                    status = "good"
                elif attended / total * 100 >= 50:
                    status = "at_risk"
                else:
                    status = "concern"
                attendance_by_player[pid] = {"status": status, "summary": summary}
    except Exception as e:
        logger.warning("Could not load attendance status: %s", e)

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
                    "on_raid_hiatus": p.on_raid_hiatus,
                    "bnet_verified": p.id in bnet_verified_ids,
                    "aliases": aliases_by_player.get(p.id, []),
                    "attendance_status": attendance_by_player.get(p.id, {}).get("status", "none"),
                    "attendance_summary": attendance_by_player.get(p.id, {}).get("summary", ""),
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
                    "link_source": c["link_source"] or "",
                    "guild_note": c["guild_note"] or "",
                    "officer_note": c["officer_note"] or "",
                    "guild_rank_name": c["guild_rank_name"] or "",
                    "in_wow_scan": True,
                }
                for c in chars
            ],
        },
    })


@router.get("/players-search")
async def admin_players_search(
    q: str = "",
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """Lightweight player search for autocomplete (e.g. add quote subject modal)."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    if not q or len(q) < 2:
        return JSONResponse({"ok": True, "data": []})

    result = await db.execute(
        select(Player)
        .where(Player.display_name.ilike(f"%{q}%"))
        .order_by(Player.display_name)
        .limit(20)
    )
    players = result.scalars().all()
    return JSONResponse({
        "ok": True,
        "data": [{"id": p.id, "display_name": p.display_name} for p in players],
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

    # Null out main/offspec pointers on any player that currently owns this character,
    # so the pointer is cleared before the bridge row is removed.
    await db.execute(
        text("UPDATE guild_identity.players SET main_character_id = NULL WHERE main_character_id = :cid"),
        {"cid": char_id},
    )
    await db.execute(
        text("UPDATE guild_identity.players SET offspec_character_id = NULL WHERE offspec_character_id = :cid"),
        {"cid": char_id},
    )

    # Remove existing bridge row
    await db.execute(
        text("DELETE FROM guild_identity.player_characters WHERE character_id = :cid"),
        {"cid": char_id},
    )

    player_name = "Unlinked"
    p = None
    if player_id:
        bridge = PlayerCharacter(
            player_id=player_id,
            character_id=char_id,
            link_source="manual",
            confidence="confirmed",
        )
        db.add(bridge)
        p_result = await db.execute(select(Player).where(Player.id == player_id))
        p = p_result.scalar_one_or_none()
        if p:
            player_name = p.display_name

    await db.commit()

    # Re-compute rank after character assignment
    rank_updated = False
    new_rank_name = None
    if player_id and p and p.guild_rank_source != "admin_override":
        best_rank, best_source = await _compute_best_rank(db, player_id)
        if best_rank:
            p.guild_rank_id = best_rank.id
            p.guild_rank_source = best_source
            await db.commit()
            rank_updated = True
            new_rank_name = best_rank.name

    return JSONResponse({
        "ok": True,
        "data": {
            "char_id": char_id,
            "char_name": char.character_name,
            "player_id": player_id,
            "player_name": player_name,
            "rank_updated": rank_updated,
            "new_rank": new_rank_name,
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
        if not du:
            return JSONResponse(
                {"ok": False, "error": f"Discord user {discord_id} not found in database. Run a Discord sync first."},
                status_code=404,
            )
        p.discord_user_id = du.id

        # Upgrade any low-confidence character links for this player
        # (stub players had confidence='low'; now that Discord is linked, bump to 'medium')
        if du:
            await db.execute(
                text(
                    """UPDATE guild_identity.player_characters
                       SET confidence = 'medium'
                       WHERE player_id = :pid AND confidence = 'low'"""
                ),
                {"pid": player_id},
            )
    else:
        p.discord_user_id = None

    await db.commit()

    # Re-compute rank after Discord link change
    rank_updated = False
    new_rank_name = None
    if p.guild_rank_source != "admin_override":
        best_rank, best_source = await _compute_best_rank(db, player_id)
        if best_rank:
            p.guild_rank_id = best_rank.id
            p.guild_rank_source = best_source
            await db.commit()
            rank_updated = True
            new_rank_name = best_rank.name

    return JSONResponse({
        "ok": True,
        "data": {
            "player_id": player_id,
            "discord_id": discord_id,
            "rank_updated": rank_updated,
            "new_rank": new_rank_name,
        },
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

    if p.website_user_id is not None:
        return JSONResponse({
            "ok": False,
            "error": "This player has a registered account. Delete their user account first (Admin → Users).",
            "registered": True,
        }, status_code=409)

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


@router.patch("/players/{player_id}/raid-hiatus")
async def admin_toggle_raid_hiatus(
    request: Request,
    player_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    try:
        body = await request.json()
        enabled = bool(body.get("enabled", False))
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if player is None:
        return JSONResponse({"ok": False, "error": "Player not found"}, status_code=404)

    player.on_raid_hiatus = enabled
    await db.commit()
    return JSONResponse({"ok": True, "data": {"on_raid_hiatus": enabled}})


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
    from sv_common.db.models import CharacterParse, CharacterRaidProgress, GuideSite, Role, WowClass, Specialization
    from sv_common.identity import ranks as rank_service
    from guild_portal.services import season_service
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

    screen_perms_result = await db.execute(
        select(ScreenPermission)
        .order_by(ScreenPermission.category_order, ScreenPermission.nav_order)
    )
    screen_permissions = list(screen_perms_result.scalars().all())

    guide_sites_result = await db.execute(
        select(GuideSite).order_by(GuideSite.sort_order, GuideSite.id)
    )
    guide_sites = list(guide_sites_result.scalars().all())

    known_raids_result = await db.execute(
        select(CharacterRaidProgress.raid_name, CharacterRaidProgress.raid_id)
        .distinct()
        .order_by(CharacterRaidProgress.raid_id.desc())
    )
    known_raids = [{"name": row[0], "id": row[1]} for row in known_raids_result.all()]

    # Which raid IDs are already assigned to any season
    all_assigned_raid_ids: set[int] = set()
    for s in seasons:
        if s.current_raid_ids:
            all_assigned_raid_ids.update(s.current_raid_ids)

    # WCL zones discovered from synced parse data
    known_wcl_zones_result = await db.execute(
        select(CharacterParse.zone_name, CharacterParse.zone_id)
        .distinct()
        .order_by(CharacterParse.zone_id.desc())
    )
    known_wcl_zones = [{"name": row[0], "id": row[1]} for row in known_wcl_zones_result.all()]

    all_assigned_wcl_zone_ids: set[int] = set()
    for s in seasons:
        if s.current_wcl_zone_ids:
            all_assigned_wcl_zone_ids.update(s.current_wcl_zone_ids)

    ctx = await _base_ctx(request, player, db)
    ctx.update({
        "ranks": ranks,
        "roles": roles,
        "classes": classes,
        "seasons": seasons,
        "screen_permissions": screen_permissions,
        "guide_sites": guide_sites,
        "known_raids": known_raids,
        "all_assigned_raid_ids": all_assigned_raid_ids,
        "known_wcl_zones": known_wcl_zones,
        "all_assigned_wcl_zone_ids": all_assigned_wcl_zone_ids,
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
    ctx["has_bot_token"] = bool(discord_config and discord_config.bot_token_encrypted)
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

    player = await _require_screen("raid_tools", request, db)
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
    show: str = "open",  # "open", "resolved", or "claims"
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/audit-log")

    issues = []
    claims = []

    if show == "claims":
        claims_result = await db.execute(
            select(PlayerActionLog)
            .options(
                selectinload(PlayerActionLog.player),
                selectinload(PlayerActionLog.character),
            )
            .order_by(PlayerActionLog.created_at.desc())
            .limit(200)
        )
        claims = list(claims_result.scalars().all())
    else:
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
        "claims": claims,
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



@router.post("/players/{player_id}/aliases")
async def admin_add_player_alias(
    request: Request,
    player_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Add a manual alias for a player (Player Manager)."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    body = await request.json()
    alias = (body.get("alias") or "").strip().lower()
    if not alias:
        return JSONResponse({"ok": False, "error": "alias required"}, status_code=400)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO guild_identity.player_note_aliases (player_id, alias, source)
               VALUES ($1, $2, 'manual')
               ON CONFLICT (player_id, alias) DO UPDATE SET source = EXCLUDED.source
               RETURNING id, player_id, alias, source""",
            player_id, alias,
        )
    return JSONResponse({"ok": True, "alias": {"id": row["id"], "alias": row["alias"], "source": row["source"]}})


@router.delete("/players/aliases/{alias_id}")
async def admin_delete_player_alias(
    request: Request,
    alias_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a player alias by id (Player Manager)."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM guild_identity.player_note_aliases WHERE id = $1 RETURNING id",
            alias_id,
        )
    if not deleted:
        return JSONResponse({"ok": False, "error": "Alias not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.delete("/data-quality/aliases/{alias_id}")
async def admin_delete_note_alias(
    request: Request,
    alias_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single player note alias."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM guild_identity.player_note_aliases WHERE id = $1 RETURNING id",
            alias_id,
        )
    if not deleted:
        return JSONResponse({"ok": False, "error": "Alias not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.post("/data-quality/aliases")
async def admin_add_note_alias(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a manual player note alias."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    body = await request.json()
    player_id = body.get("player_id")
    alias = (body.get("alias") or "").strip().lower()
    if not player_id or not alias:
        return JSONResponse({"ok": False, "error": "player_id and alias required"}, status_code=400)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO guild_identity.player_note_aliases (player_id, alias, source)
               VALUES ($1, $2, 'manual')
               ON CONFLICT (player_id, alias) DO UPDATE SET source = EXCLUDED.source
               RETURNING id, player_id, alias, source""",
            player_id, alias,
        )
    return JSONResponse({"ok": True, "alias": {"id": row["id"], "alias": row["alias"], "source": row["source"]}})


@router.get("/matching/coverage")
async def admin_matching_coverage(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Coverage metrics for the matching engine — Admin only."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        total_chars = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.wow_characters WHERE removed_at IS NULL AND in_guild = TRUE"
        )
        matched_chars = await conn.fetchval(
            """SELECT COUNT(DISTINCT wc.id)
               FROM guild_identity.wow_characters wc
               JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
               WHERE wc.removed_at IS NULL AND wc.in_guild = TRUE"""
        )
        total_discord = await conn.fetchval(
            """SELECT COUNT(*) FROM guild_identity.discord_users
               WHERE is_present = TRUE AND highest_guild_role IS NOT NULL"""
        )
        matched_discord = await conn.fetchval(
            """SELECT COUNT(DISTINCT du.id)
               FROM guild_identity.discord_users du
               JOIN guild_identity.players p ON p.discord_user_id = du.id
               WHERE du.is_present = TRUE AND du.highest_guild_role IS NOT NULL"""
        )
        total_players = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.players WHERE is_active = TRUE"
        )
        players_with_discord = await conn.fetchval(
            """SELECT COUNT(*) FROM guild_identity.players
               WHERE is_active = TRUE AND discord_user_id IS NOT NULL"""
        )
        source_rows = await conn.fetch(
            """SELECT link_source, COUNT(*) AS cnt
               FROM guild_identity.player_characters
               GROUP BY link_source ORDER BY cnt DESC"""
        )
        by_link_source = {r["link_source"]: r["cnt"] for r in source_rows}
        conf_rows = await conn.fetch(
            """SELECT confidence, COUNT(*) AS cnt
               FROM guild_identity.player_characters
               GROUP BY confidence ORDER BY cnt DESC"""
        )
        by_confidence = {r["confidence"]: r["cnt"] for r in conf_rows}

        unmatched_char_rows = await conn.fetch(
            """SELECT wc.id, wc.character_name,
                      wc.realm_name, wc.realm_slug,
                      gr.name AS guild_rank,
                      wc.guild_note,
                      wc.last_login_timestamp
               FROM guild_identity.wow_characters wc
               LEFT JOIN common.guild_ranks gr ON gr.id = wc.guild_rank_id
               WHERE wc.removed_at IS NULL
                 AND wc.id NOT IN (SELECT character_id FROM guild_identity.player_characters)
               ORDER BY wc.character_name"""
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        unmatched_characters = []
        for r in unmatched_char_rows:
            last_login_days = None
            if r["last_login_timestamp"]:
                diff_ms = now_ms - r["last_login_timestamp"]
                last_login_days = max(0, diff_ms // 86_400_000)
            unmatched_characters.append({
                "id": r["id"],
                "character_name": r["character_name"],
                "realm": r["realm_name"] or r["realm_slug"],
                "guild_rank": r["guild_rank"],
                "guild_note": r["guild_note"] or "",
                "last_login_days_ago": last_login_days,
            })

        unmatched_discord_rows = await conn.fetch(
            """SELECT du.id, du.username, du.display_name,
                      du.highest_guild_role, du.joined_server_at
               FROM guild_identity.discord_users du
               WHERE du.is_present = TRUE
                 AND du.highest_guild_role IS NOT NULL
                 AND du.id NOT IN (
                     SELECT discord_user_id FROM guild_identity.players
                     WHERE discord_user_id IS NOT NULL
                 )
               ORDER BY du.username"""
        )
        unmatched_discord_users = [
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": r["display_name"],
                "highest_guild_role": r["highest_guild_role"],
                "joined_server_at": r["joined_server_at"].isoformat() if r["joined_server_at"] else None,
            }
            for r in unmatched_discord_rows
        ]

    def pct(matched: int, total: int) -> float:
        return round(matched / total * 100, 1) if total else 0.0

    unmatched_chars_count = (total_chars or 0) - (matched_chars or 0)
    unmatched_discord_count = (total_discord or 0) - (matched_discord or 0)
    players_without_discord = (total_players or 0) - (players_with_discord or 0)

    return JSONResponse({
        "ok": True,
        "data": {
            "summary": {
                "total_characters": total_chars or 0,
                "matched_characters": matched_chars or 0,
                "unmatched_characters": unmatched_chars_count,
                "character_coverage_pct": pct(matched_chars or 0, total_chars or 0),
                "total_discord_users": total_discord or 0,
                "matched_discord_users": matched_discord or 0,
                "unmatched_discord_users": unmatched_discord_count,
                "discord_coverage_pct": pct(matched_discord or 0, total_discord or 0),
                "total_players": total_players or 0,
                "players_with_discord": players_with_discord or 0,
                "players_without_discord": players_without_discord,
                "discord_link_pct": pct(players_with_discord or 0, total_players or 0),
            },
            "by_link_source": by_link_source,
            "by_confidence": by_confidence,
            "unmatched_characters": unmatched_characters,
            "unmatched_discord_users": unmatched_discord_users,
        },
    })


@router.post("/drift/scan")
async def admin_drift_scan(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Run a drift scan now and return results synchronously."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.drift_scanner import run_drift_scan
    try:
        results = await run_drift_scan(pool)
        return JSONResponse({"ok": True, "data": results})
    except Exception as exc:
        logger.error("Drift scan failed: %s", exc, exc_info=True)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/drift/summary")
async def admin_drift_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return open issue counts for all drift rule types."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.drift_scanner import DRIFT_RULE_TYPES
    async with pool.acquire() as conn:
        count_rows = await conn.fetch(
            """SELECT
                   issue_type,
                   COUNT(*) FILTER (WHERE resolved_at IS NULL)            AS open_count,
                   COUNT(*) FILTER (WHERE resolved_at IS NOT NULL
                                      AND resolved_at > NOW() - INTERVAL '30 days') AS resolved_30d,
                   MAX(created_at) AS last_triggered
               FROM guild_identity.audit_issues
               WHERE issue_type = ANY($1::text[])
               GROUP BY issue_type""",
            list(DRIFT_RULE_TYPES),
        )
        log_rows = await conn.fetch(
            """SELECT id, issue_type, severity, summary,
                      created_at, resolved_at, resolved_by
               FROM guild_identity.audit_issues
               WHERE issue_type = ANY($1::text[])
                 AND (resolved_at IS NULL
                      OR resolved_at > NOW() - INTERVAL '30 days')
               ORDER BY created_at DESC
               LIMIT 100""",
            list(DRIFT_RULE_TYPES),
        )
    by_type = {r["issue_type"]: r for r in count_rows}
    summary = {}
    for issue_type in DRIFT_RULE_TYPES:
        r = by_type.get(issue_type)
        summary[issue_type] = {
            "open_count": r["open_count"] if r else 0,
            "resolved_30d": r["resolved_30d"] if r else 0,
            "last_triggered": r["last_triggered"].isoformat() if r and r["last_triggered"] else None,
        }
    log = [
        {
            "id": r["id"],
            "issue_type": r["issue_type"],
            "severity": r["severity"],
            "summary": r["summary"],
            "created_at": r["created_at"].isoformat(),
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
            "resolved_by": r["resolved_by"],
        }
        for r in log_rows
    ]
    return JSONResponse({"ok": True, "data": {"summary": summary, "log": log}})


@router.get("/oauth-coverage")
async def admin_oauth_coverage(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """OAuth verification coverage — verified vs unverified active players."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        # Total active players with Discord (these are the members we expect to verify)
        total = await conn.fetchval(
            """SELECT COUNT(*) FROM guild_identity.players p
               JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               WHERE p.is_active = TRUE AND du.is_present = TRUE"""
        )
        # Verified: players with a battlenet_accounts row
        verified = await conn.fetchval(
            """SELECT COUNT(DISTINCT p.id) FROM guild_identity.players p
               JOIN guild_identity.battlenet_accounts ba ON ba.player_id = p.id
               JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               WHERE p.is_active = TRUE AND du.is_present = TRUE"""
        )
        # Unverified member details
        unverified_rows = await conn.fetch(
            """SELECT p.id AS player_id, p.display_name,
                      du.username AS discord_username, du.display_name AS discord_display_name,
                      gr.name AS rank_name,
                      COUNT(pc.character_id) AS char_count
               FROM guild_identity.players p
               JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               LEFT JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
               LEFT JOIN guild_identity.player_characters pc ON pc.player_id = p.id
               WHERE p.is_active = TRUE
                 AND du.is_present = TRUE
                 AND p.id NOT IN (
                     SELECT player_id FROM guild_identity.battlenet_accounts
                 )
               GROUP BY p.id, p.display_name, du.username, du.display_name, gr.name
               ORDER BY p.display_name"""
        )

    unverified_members = [
        {
            "player_id": r["player_id"],
            "display_name": r["display_name"],
            "discord_username": r["discord_username"],
            "rank_name": r["rank_name"],
            "char_count": r["char_count"],
        }
        for r in unverified_rows
    ]

    return JSONResponse({
        "ok": True,
        "data": {
            "total": total or 0,
            "verified": verified or 0,
            "unverified": (total or 0) - (verified or 0),
            "unverified_members": unverified_members,
        },
    })


@router.post("/players/{player_id}/send-oauth-reminder")
async def admin_send_oauth_reminder(
    request: Request,
    player_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Send an OAuth reminder DM to an unverified player."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    # Load the player's Discord ID
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT du.discord_id FROM guild_identity.players p
               JOIN guild_identity.discord_users du ON du.id = p.discord_user_id
               WHERE p.id = $1""",
            player_id,
        )
    if not row:
        return JSONResponse({"ok": False, "error": "Player not found or not linked to Discord"}, status_code=404)

    # Send DM via bot
    try:
        from sv_common.discord.bot import get_bot
        from sv_common.config_cache import get_app_url
        bot = get_bot()
        if not bot or bot.is_closed():
            return JSONResponse({"ok": False, "error": "Bot not available"}, status_code=503)

        discord_user = await bot.fetch_user(int(row["discord_id"]))
        if not discord_user:
            return JSONResponse({"ok": False, "error": "Discord user not found"}, status_code=404)

        app_url = get_app_url() or ""
        oauth_url = f"{app_url}/auth/battlenet"
        msg = (
            "Hey! An officer has sent you a reminder to connect your Battle.net account "
            "to the guild website.\n\n"
            f"Click here to verify: {oauth_url}\n\n"
            "This links your characters automatically and confirms your guild membership."
        )
        await discord_user.send(msg)
        return JSONResponse({"ok": True})
    except Exception as exc:
        logger.error("send-oauth-reminder failed for player %d: %s", player_id, exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


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


# ---------------------------------------------------------------------------
# User Accounts page
# ---------------------------------------------------------------------------


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_admin(request, db)
    if player is None:
        return _redirect_login("/admin/users")

    rows = await db.execute(
        text("""
            SELECT u.id, u.email, u.is_active, u.created_at,
                   p.id                    AS player_id,
                   p.display_name,
                   gr.name                 AS rank_name,
                   ba.battletag            AS battletag,
                   ba.last_character_sync  AS last_bnet_sync,
                   ba.token_expires_at     AS bnet_token_expires_at
            FROM common.users u
            LEFT JOIN guild_identity.players p ON p.website_user_id = u.id
            LEFT JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
            LEFT JOIN guild_identity.battlenet_accounts ba ON ba.player_id = p.id
            ORDER BY u.created_at DESC
        """)
    )
    now = datetime.now(timezone.utc)
    users = []
    for r in rows:
        u = dict(r._mapping)
        expires_at = u.get("bnet_token_expires_at")
        u["bnet_token_expired"] = bool(
            expires_at and expires_at <= now
        )
        users.append(u)

    ctx = await _base_ctx(request, player, db)
    ctx["users"] = users
    return templates.TemplateResponse("admin/users.html", ctx)


@router.post("/users/{user_id}/bnet-sync")
async def admin_bnet_sync_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Trigger Battle.net character sync for a specific user."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if pool is None:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    result = await db.execute(
        text("""
            SELECT p.id AS player_id
            FROM common.users u
            JOIN guild_identity.players p ON p.website_user_id = u.id
            JOIN guild_identity.battlenet_accounts ba ON ba.player_id = p.id
            WHERE u.id = :user_id
        """),
        {"user_id": user_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return JSONResponse(
            {"ok": False, "error": "User not found or no Battle.net account linked"},
            status_code=404,
        )

    player_id = row["player_id"]

    from sv_common.guild_sync.bnet_character_sync import (
        get_valid_access_token,
        sync_bnet_characters,
    )

    access_token = await get_valid_access_token(pool, player_id)
    if access_token is None:
        from sv_common.errors import report_error
        await report_error(
            pool,
            "bnet_token_expired",
            "info",
            "Battle.net token expired — player must re-link their Battle.net account.",
            "admin_bnet_sync",
            details={"user_id": user_id, "player_id": player_id},
            identifier=str(player_id),
        )
        return JSONResponse(
            {"ok": False, "error": "Could not retrieve a valid access token — the token may have expired"},
            status_code=422,
        )

    stats = await sync_bnet_characters(pool, player_id, access_token)

    from sv_common.errors import resolve_issue
    await resolve_issue(pool, "bnet_token_expired", identifier=str(player_id))
    await resolve_issue(pool, "bnet_sync_error", identifier=str(player_id))

    return JSONResponse({"ok": True, "data": stats})


@router.post("/users/bnet-sync-all")
async def admin_bnet_sync_all(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trigger Battle.net character sync for every user with a linked account."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if pool is None:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.bnet_character_sync import (
        get_valid_access_token,
        sync_bnet_characters,
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT player_id FROM guild_identity.battlenet_accounts")
    player_ids = [r["player_id"] for r in rows]

    synced = 0
    failed = 0
    total_linked = 0

    from sv_common.errors import report_error, resolve_issue

    for player_id in player_ids:
        try:
            access_token = await get_valid_access_token(pool, player_id)
            if access_token is None:
                failed += 1
                await report_error(
                    pool,
                    "bnet_token_expired",
                    "info",
                    f"Battle.net token expired for player {player_id} — player must re-link.",
                    "admin_bnet_sync",
                    details={"player_id": player_id},
                    identifier=str(player_id),
                )
                continue
            stats = await sync_bnet_characters(pool, player_id, access_token)
            synced += 1
            total_linked += stats.get("linked", 0)
            await resolve_issue(pool, "bnet_token_expired", identifier=str(player_id))
            await resolve_issue(pool, "bnet_sync_error", identifier=str(player_id))
        except Exception as exc:
            logger.error("BNet sync-all: failed for player %s: %s", player_id, exc)
            failed += 1
            await report_error(
                pool,
                "bnet_sync_error",
                "warning",
                f"Battle.net character sync failed for player {player_id}: {exc}",
                "admin_bnet_sync",
                details={"player_id": player_id, "error": str(exc)},
                identifier=str(player_id),
            )

    return JSONResponse({"ok": True, "data": {"synced": synced, "failed": failed, "total_linked": total_linked}})


@router.patch("/users/{user_id}/toggle-active")
async def admin_toggle_user_active(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    result = await db.execute(select(User).where(User.id == user_id))
    u = result.scalar_one_or_none()
    if not u:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    u.is_active = not u.is_active
    await db.commit()
    return JSONResponse({"ok": True, "data": {"user_id": user_id, "is_active": u.is_active}})


@router.delete("/users/{user_id}")
async def admin_delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    result = await db.execute(
        select(User).options(selectinload(User.player)).where(User.id == user_id)
    )
    u = result.scalar_one_or_none()
    if not u:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    # Unlink from player before deleting
    player_name = None
    if u.player:
        player_name = u.player.display_name
        u.player.website_user_id = None
        await db.flush()

    await db.delete(u)
    await db.commit()
    return JSONResponse({"ok": True, "data": {"user_id": user_id, "player_display_name": player_name}})


# ---------------------------------------------------------------------------
# Site Config (GL-only)
# ---------------------------------------------------------------------------


@router.get("/site-config", response_class=HTMLResponse)
async def site_config_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    player = await _require_screen("site_config", request, db)
    if player is None:
        return RedirectResponse(url="/login")

    # Load current site_config from DB
    from sqlalchemy import text as sa_text
    result = await db.execute(sa_text("SELECT * FROM common.site_config LIMIT 1"))
    row = result.mappings().first()
    config = dict(row) if row else {}

    ctx = await _base_ctx(request, player, db)
    ctx["config"] = config
    return templates.TemplateResponse("admin/site_config.html", ctx)


# ---------------------------------------------------------------------------
# Progression — Phase 4.3
# ---------------------------------------------------------------------------


@router.get("/progression", response_class=HTMLResponse)
async def progression_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Admin page for managing tracked achievements and viewing progression sync status."""
    player = await _require_screen("progression", request, db)
    if player is None:
        return RedirectResponse(url="/login")

    ctx = await _base_ctx(request, player, db)
    return templates.TemplateResponse("admin/progression.html", ctx)


@router.get("/progression/tracked-achievements")
async def admin_list_tracked_achievements(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all tracked achievements."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, achievement_id, achievement_name, category, is_active
               FROM guild_identity.tracked_achievements
               ORDER BY category, achievement_name"""
        )

    achievements = [
        {
            "id": r["id"],
            "achievement_id": r["achievement_id"],
            "achievement_name": r["achievement_name"],
            "category": r["category"],
            "is_active": r["is_active"],
        }
        for r in rows
    ]
    return JSONResponse({"ok": True, "data": achievements})


@router.post("/progression/tracked-achievements")
async def admin_add_tracked_achievement(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a new tracked achievement."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    body = await request.json()
    achievement_id = body.get("achievement_id")
    achievement_name = (body.get("achievement_name") or "").strip()
    category = (body.get("category") or "general").strip()

    if not achievement_id or not achievement_name:
        return JSONResponse(
            {"ok": False, "error": "achievement_id and achievement_name required"},
            status_code=400,
        )

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO guild_identity.tracked_achievements
                       (achievement_id, achievement_name, category)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (achievement_id) DO UPDATE
                       SET achievement_name = EXCLUDED.achievement_name,
                           category         = EXCLUDED.category
                   RETURNING id, achievement_id, achievement_name, category, is_active""",
                int(achievement_id), achievement_name, category,
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return JSONResponse({
        "ok": True,
        "data": {
            "id": row["id"],
            "achievement_id": row["achievement_id"],
            "achievement_name": row["achievement_name"],
            "category": row["category"],
            "is_active": row["is_active"],
        },
    })


@router.patch("/progression/tracked-achievements/{achievement_db_id}")
async def admin_toggle_tracked_achievement(
    request: Request,
    achievement_db_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Toggle is_active on a tracked achievement."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    body = await request.json()
    is_active = body.get("is_active")
    if is_active is None:
        return JSONResponse({"ok": False, "error": "is_active required"}, status_code=400)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE guild_identity.tracked_achievements
               SET is_active = $1
               WHERE id = $2
               RETURNING id, achievement_id, achievement_name, category, is_active""",
            bool(is_active), achievement_db_id,
        )

    if not row:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    return JSONResponse({
        "ok": True,
        "data": {
            "id": row["id"],
            "achievement_id": row["achievement_id"],
            "achievement_name": row["achievement_name"],
            "category": row["category"],
            "is_active": row["is_active"],
        },
    })


@router.delete("/progression/tracked-achievements/{achievement_db_id}")
async def admin_delete_tracked_achievement(
    request: Request,
    achievement_db_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a tracked achievement."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM guild_identity.tracked_achievements WHERE id = $1 RETURNING id",
            achievement_db_id,
        )

    if not deleted:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.get("/progression/sync-stats")
async def admin_progression_sync_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return progression sync stats for the admin page."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        total_chars = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.wow_characters WHERE removed_at IS NULL AND in_guild = TRUE"
        )
        synced_chars = await conn.fetchval(
            """SELECT COUNT(*) FROM guild_identity.wow_characters
               WHERE removed_at IS NULL AND in_guild = TRUE AND last_progression_sync IS NOT NULL"""
        )
        last_sync = await conn.fetchval(
            """SELECT MAX(last_progression_sync) FROM guild_identity.wow_characters
               WHERE removed_at IS NULL AND in_guild = TRUE"""
        )
        raid_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.character_raid_progress"
        )
        mplus_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.character_mythic_plus"
        )
        ach_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.character_achievements"
        )
        snapshot_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.progression_snapshots"
        )
        latest_snapshot = await conn.fetchval(
            "SELECT MAX(snapshot_date) FROM guild_identity.progression_snapshots"
        )

    # M+ season ID: prefer active season row, fall back to site_config
    mplus_season_id = None
    async with pool.acquire() as conn:
        season_row = await conn.fetchrow(
            """SELECT blizzard_mplus_season_id FROM patt.raid_seasons
               WHERE is_active = TRUE ORDER BY start_date DESC LIMIT 1"""
        )
        if season_row and season_row["blizzard_mplus_season_id"]:
            mplus_season_id = season_row["blizzard_mplus_season_id"]
    if mplus_season_id is None:
        from sv_common.config_cache import get_site_config
        mplus_season_id = get_site_config().get("current_mplus_season_id")

    return JSONResponse({
        "ok": True,
        "data": {
            "total_chars": total_chars,
            "synced_chars": synced_chars,
            "last_progression_sync": last_sync.isoformat() if last_sync else None,
            "raid_progress_rows": raid_rows,
            "mplus_rows": mplus_rows,
            "achievement_rows": ach_rows,
            "snapshot_rows": snapshot_rows,
            "latest_snapshot_date": latest_snapshot.isoformat() if latest_snapshot else None,
            "current_mplus_season_id": mplus_season_id,
        },
    })


# ---------------------------------------------------------------------------
# Warcraft Logs — Phase 4.5
# ---------------------------------------------------------------------------


@router.get("/warcraft-logs", response_class=HTMLResponse)
async def warcraft_logs_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Admin page for Warcraft Logs configuration, reports, attendance, and parses."""
    player = await _require_screen("warcraft_logs", request, db)
    if player is None:
        return RedirectResponse(url="/login")

    ctx = await _base_ctx(request, player, db)
    return templates.TemplateResponse("admin/warcraft_logs.html", ctx)


@router.get("/warcraft-logs/config")
async def admin_wcl_get_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return current WCL config (secret masked)."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, client_id, client_secret_encrypted, wcl_guild_name,
                      wcl_server_slug, wcl_server_region, is_configured,
                      sync_enabled, last_sync, last_sync_status, last_sync_error
               FROM guild_identity.wcl_config LIMIT 1"""
        )

    if not row:
        return JSONResponse({"ok": False, "error": "WCL config not found"}, status_code=404)

    return JSONResponse({
        "ok": True,
        "data": {
            "id": row["id"],
            "client_id": row["client_id"] or "",
            "secret_configured": bool(row["client_secret_encrypted"]),
            "wcl_guild_name": row["wcl_guild_name"] or "",
            "wcl_server_slug": row["wcl_server_slug"] or "",
            "wcl_server_region": row["wcl_server_region"] or "us",
            "is_configured": row["is_configured"],
            "sync_enabled": row["sync_enabled"],
            "last_sync": row["last_sync"].isoformat() if row["last_sync"] else None,
            "last_sync_status": row["last_sync_status"],
            "last_sync_error": row["last_sync_error"],
        },
    })


@router.patch("/warcraft-logs/config")
async def admin_wcl_save_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save WCL credentials and guild info. Encrypts the client secret."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    body = await request.json()
    client_id = (body.get("client_id") or "").strip()
    client_secret = (body.get("client_secret") or "").strip()
    guild_name = (body.get("wcl_guild_name") or "").strip()
    server_slug = (body.get("wcl_server_slug") or "").strip()
    region = (body.get("wcl_server_region") or "us").strip().lower()
    sync_enabled = bool(body.get("sync_enabled", True))

    if not client_id or not guild_name or not server_slug:
        return JSONResponse(
            {"ok": False, "error": "client_id, guild_name, and server_slug are required"},
            status_code=400,
        )

    # Encrypt secret if provided
    encrypted_secret: str | None = None
    if client_secret:
        from guild_portal.config import get_settings
        from sv_common.crypto import encrypt_secret
        encrypted_secret = encrypt_secret(client_secret, get_settings().jwt_secret_key)

    async with pool.acquire() as conn:
        if encrypted_secret:
            await conn.execute(
                """UPDATE guild_identity.wcl_config SET
                       client_id = $1, client_secret_encrypted = $2,
                       wcl_guild_name = $3, wcl_server_slug = $4,
                       wcl_server_region = $5, sync_enabled = $6,
                       is_configured = TRUE, updated_at = NOW()
                   WHERE id = (SELECT id FROM guild_identity.wcl_config LIMIT 1)""",
                client_id, encrypted_secret, guild_name, server_slug, region, sync_enabled,
            )
        else:
            # Don't overwrite existing secret if blank submitted
            await conn.execute(
                """UPDATE guild_identity.wcl_config SET
                       client_id = $1, wcl_guild_name = $2,
                       wcl_server_slug = $3, wcl_server_region = $4,
                       sync_enabled = $5, is_configured = TRUE, updated_at = NOW()
                   WHERE id = (SELECT id FROM guild_identity.wcl_config LIMIT 1)""",
                client_id, guild_name, server_slug, region, sync_enabled,
            )

    return JSONResponse({"ok": True})


@router.post("/warcraft-logs/verify")
async def admin_wcl_verify(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Verify WCL credentials by attempting to fetch guild reports."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    body = await request.json()
    client_id = (body.get("client_id") or "").strip()
    client_secret = (body.get("client_secret") or "").strip()
    guild_name = (body.get("wcl_guild_name") or "").strip()
    server_slug = (body.get("wcl_server_slug") or "").strip()
    region = (body.get("wcl_server_region") or "us").strip().lower()

    if not client_id or not client_secret or not guild_name or not server_slug:
        return JSONResponse(
            {"ok": False, "error": "All fields required for verification"},
            status_code=400,
        )

    from sv_common.guild_sync.warcraftlogs_client import WarcraftLogsClient, WarcraftLogsError
    client = WarcraftLogsClient(client_id, client_secret)
    try:
        await client.initialize()
        info = await client.verify_credentials(guild_name, server_slug, region)
        return JSONResponse({"ok": True, "data": info})
    except WarcraftLogsError as exc:
        return JSONResponse({"ok": False, "error": f"WCL API error: {exc}"}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"Verification failed: {exc}"},
            status_code=400,
        )
    finally:
        await client.close()


@router.post("/warcraft-logs/trigger")
async def admin_wcl_trigger_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Force a WCL sync run."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    if scheduler is None:
        return JSONResponse({"ok": False, "error": "Scheduler not available"}, status_code=503)

    import asyncio
    asyncio.create_task(scheduler.run_wcl_sync())
    return JSONResponse({"ok": True, "message": "WCL sync started in background"})


@router.get("/warcraft-logs/reports")
async def admin_wcl_reports(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return recent raid reports."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT report_code, title, raid_date, zone_name, boss_kills,
                      wipes, duration_ms, report_url,
                      jsonb_array_length(COALESCE(attendees, '[]'::jsonb)) AS attendee_count
               FROM guild_identity.raid_reports
               ORDER BY raid_date DESC
               LIMIT 25"""
        )

    reports = [
        {
            "code": r["report_code"],
            "title": r["title"],
            "raid_date": r["raid_date"].isoformat() if r["raid_date"] else None,
            "zone_name": r["zone_name"],
            "boss_kills": r["boss_kills"] or 0,
            "wipes": r["wipes"] or 0,
            "duration_ms": r["duration_ms"],
            "report_url": r["report_url"],
            "attendee_count": r["attendee_count"] or 0,
        }
        for r in rows
    ]
    return JSONResponse({"ok": True, "data": reports})


@router.get("/warcraft-logs/attendance")
async def admin_wcl_attendance(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return attendance grid from the last 10 reports."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.wcl_sync import compute_attendance
    attendance = await compute_attendance(pool, limit_reports=10)

    # Also return the list of recent report dates for the grid header
    async with pool.acquire() as conn:
        report_rows = await conn.fetch(
            """SELECT report_code, raid_date, zone_name, attendees
               FROM guild_identity.raid_reports
               ORDER BY raid_date DESC LIMIT 10"""
        )

    reports_meta = [
        {
            "code": r["report_code"],
            "raid_date": r["raid_date"].isoformat() if r["raid_date"] else None,
            "zone_name": r["zone_name"],
        }
        for r in report_rows
    ]

    # Build per-player, per-report attendance grid
    # {player_name: [attended_report_code1, ...]}
    player_reports: dict[str, list[str]] = {}
    for row in report_rows:
        attendees = row["attendees"] or []
        for a in attendees:
            name = (a.get("name") or "").lower().strip()
            if name:
                player_reports.setdefault(name, [])
                player_reports[name].append(row["report_code"])

    grid = [
        {
            "name": name,
            "attended": set_of_reports,
            "rate": attendance.get(name, {}).get("rate", 0),
            "raids_attended": attendance.get(name, {}).get("raids_attended", 0),
        }
        for name, set_of_reports in sorted(
            player_reports.items(),
            key=lambda x: -attendance.get(x[0], {}).get("raids_attended", 0),
        )
    ]

    return JSONResponse({
        "ok": True,
        "data": {
            "reports": reports_meta,
            "grid": grid,
        },
    })


@router.get("/warcraft-logs/parses")
async def admin_wcl_parses(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return top parse records — all characters, sortable."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT cp.percentile, cp.encounter_name, cp.zone_name,
                      cp.difficulty, cp.spec, cp.amount, cp.report_code,
                      wc.character_name, cp.last_synced
               FROM guild_identity.character_parses cp
               JOIN guild_identity.wow_characters wc ON wc.id = cp.character_id
               ORDER BY cp.percentile DESC
               LIMIT 200"""
        )

    difficulty_names = {1: "LFR", 3: "Normal", 4: "Heroic", 5: "Mythic"}

    parses = [
        {
            "character_name": r["character_name"],
            "encounter_name": r["encounter_name"],
            "zone_name": r["zone_name"],
            "difficulty": difficulty_names.get(r["difficulty"], str(r["difficulty"])),
            "difficulty_id": r["difficulty"],
            "spec": r["spec"],
            "percentile": float(r["percentile"]),
            "amount": float(r["amount"]) if r["amount"] else None,
            "report_code": r["report_code"],
            "report_url": (
                f"https://www.warcraftlogs.com/reports/{r['report_code']}"
                if r["report_code"]
                else None
            ),
            "last_synced": r["last_synced"].isoformat() if r["last_synced"] else None,
        }
        for r in rows
    ]
    return JSONResponse({"ok": True, "data": parses})


# ===========================================================================
# AH Pricing — /admin/ah-pricing
# ===========================================================================


@router.get("/ah-pricing", response_class=HTMLResponse)
async def admin_ah_pricing_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """AH Pricing admin page — Officer+."""
    player = await _require_screen("ah_pricing", request, db)
    if player is None:
        return RedirectResponse("/login?next=/admin/ah-pricing")

    ctx = await _base_ctx(request, player, db)
    return templates.TemplateResponse("admin/ah_pricing.html", ctx)


@router.get("/ah-pricing/items")
async def admin_ah_items(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return tracked items with current prices (JSON)."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    from sv_common.guild_sync.ah_service import get_tracked_items_with_prices, copper_to_gold_str
    items = await get_tracked_items_with_prices(pool)

    data = [
        {
            "id": i["id"],
            "item_id": i["item_id"],
            "item_name": i["item_name"],
            "category": i["category"],
            "display_order": i["display_order"],
            "is_active": i["is_active"],
            "min_buyout": i["min_buyout"],
            "min_buyout_str": copper_to_gold_str(i["min_buyout"]),
            "median_price": i["median_price"],
            "median_price_str": copper_to_gold_str(i["median_price"]),
            "quantity_available": i["quantity_available"],
            "num_auctions": i["num_auctions"],
            "change_pct": i["change_pct"],
            "snapshot_at": i["snapshot_at"].isoformat() if i["snapshot_at"] else None,
        }
        for i in items
    ]
    return JSONResponse({"ok": True, "data": data})


@router.post("/ah-pricing/items")
async def admin_ah_add_item(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a new item to track. Body: {item_id, item_name, category}."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    body = await request.json()
    item_id = body.get("item_id")
    item_name = (body.get("item_name") or "").strip()
    category = (body.get("category") or "consumable").strip()

    if not item_id or not item_name:
        return JSONResponse({"ok": False, "error": "item_id and item_name are required"}, status_code=400)

    valid_categories = {"consumable", "enchant", "gem", "material", "gear"}
    if category not in valid_categories:
        category = "consumable"

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO guild_identity.tracked_items
                    (item_id, item_name, category, added_by_player_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (item_id) DO UPDATE
                    SET item_name = EXCLUDED.item_name,
                        category = EXCLUDED.category,
                        is_active = TRUE
                RETURNING id, item_id, item_name, category, is_active
                """,
                int(item_id),
                item_name,
                category,
                admin.id,
            )
        except Exception as exc:
            logger.error("Failed to add tracked item: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return JSONResponse({"ok": True, "data": dict(row)})


@router.delete("/ah-pricing/items/{item_id}")
async def admin_ah_remove_item(
    item_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove a tracked item (hard delete with cascade to price history)."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM guild_identity.tracked_items WHERE id = $1", item_id
        )
    deleted = int(result.split()[-1]) if result else 0
    if deleted == 0:
        return JSONResponse({"ok": False, "error": "Item not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def _get_blizzard_client(request):
    """Return a ready-to-use BlizzardClient.

    Prefers the scheduler's already-initialized client. Falls back to
    creating a temporary client from environment variables, which works
    even when the scheduler is not running (e.g. audit channel not configured).
    Returns (client, owned) where owned=True means caller must close it.
    """
    import os
    from sv_common.guild_sync.blizzard_client import BlizzardClient

    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    if scheduler and scheduler.blizzard_client:
        return scheduler.blizzard_client, False

    client_id = os.environ.get("BLIZZARD_CLIENT_ID", "")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None, False

    client = BlizzardClient(client_id, client_secret)
    await client.initialize()
    return client, True


@router.post("/ah-pricing/sync")
async def admin_ah_force_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Force an immediate AH price sync."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "DB pool not available"}, status_code=503)

    scheduler = getattr(request.app.state, "guild_sync_scheduler", None)
    if scheduler:
        # Scheduler is running — delegate to it (handles realm resolution too)
        import asyncio
        asyncio.create_task(scheduler.run_ah_sync())
        return JSONResponse({"ok": True, "message": "AH sync triggered"})

    # Scheduler not running — run sync inline with a temporary client
    blizzard_client, owned = await _get_blizzard_client(request)
    if not blizzard_client:
        return JSONResponse({"ok": False, "error": "Blizzard API credentials not configured"}, status_code=503)

    try:
        from sv_common.guild_sync.ah_sync import sync_ah_prices
        async with pool.acquire() as conn:
            cfg = await conn.fetchrow(
                "SELECT connected_realm_id, home_realm_slug FROM common.site_config LIMIT 1"
            )
        connected_realm_id = cfg["connected_realm_id"] if cfg else None
        if not connected_realm_id:
            return JSONResponse({"ok": False, "error": "Connected realm not resolved yet — use Re-Resolve first"}, status_code=400)

        import asyncio
        asyncio.create_task(sync_ah_prices(pool, blizzard_client, [connected_realm_id]))
        return JSONResponse({"ok": True, "message": "AH sync triggered"})
    except Exception as exc:
        if owned:
            await blizzard_client.close()
        raise exc


@router.post("/ah-pricing/resolve-realm")
async def admin_ah_resolve_realm(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Re-resolve the connected realm ID from the home realm slug."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "DB pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT home_realm_slug FROM common.site_config LIMIT 1")
    realm_slug = row["home_realm_slug"] if row else None
    if not realm_slug:
        return JSONResponse({"ok": False, "error": "home_realm_slug not configured"}, status_code=400)

    blizzard_client, owned = await _get_blizzard_client(request)
    if not blizzard_client:
        return JSONResponse({"ok": False, "error": "Blizzard API credentials not configured"}, status_code=503)

    try:
        connected_realm_id = await blizzard_client.get_connected_realm_id(realm_slug)
    finally:
        if owned:
            await blizzard_client.close()

    if not connected_realm_id:
        return JSONResponse({"ok": False, "error": "Could not resolve connected realm ID from Blizzard API"}, status_code=502)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE common.site_config SET connected_realm_id = $1", connected_realm_id
        )
    return JSONResponse({"ok": True, "connected_realm_id": connected_realm_id})


@router.get("/ah-pricing/status")
async def admin_ah_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return AH sync status: last snapshot time, item counts, connected realm."""
    admin = await _require_admin(request, db)
    if admin is None:
        return JSONResponse({"ok": False, "error": "Not authorized"}, status_code=403)

    pool = getattr(request.app.state, "guild_sync_pool", None)
    if not pool:
        return JSONResponse({"ok": False, "error": "Guild sync pool not available"}, status_code=503)

    async with pool.acquire() as conn:
        config_row = await conn.fetchrow(
            "SELECT connected_realm_id, home_realm_slug, active_connected_realm_ids FROM common.site_config LIMIT 1"
        )
        total_items = await conn.fetchval(
            "SELECT COUNT(*) FROM guild_identity.tracked_items WHERE is_active = TRUE"
        )
        last_snapshot = await conn.fetchval(
            "SELECT MAX(snapshot_at) FROM guild_identity.item_price_history"
        )
        items_with_prices = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT tracked_item_id)
            FROM guild_identity.item_price_history
            WHERE snapshot_at >= NOW() - INTERVAL '2 hours'
            """
        )

    active_realm_ids = []
    if config_row:
        active_realm_ids = list(config_row["active_connected_realm_ids"] or [])

    return JSONResponse({
        "ok": True,
        "data": {
            "connected_realm_id": config_row["connected_realm_id"] if config_row else None,
            "realm_slug": config_row["home_realm_slug"] if config_row else None,
            "active_realm_count": len(active_realm_ids),
            "active_realm_ids": active_realm_ids,
            "total_tracked_items": total_items or 0,
            "items_with_recent_prices": items_with_prices or 0,
            "last_snapshot": last_snapshot.isoformat() if last_snapshot else None,
        },
    })


# ===========================================================================
# Attendance — /admin/attendance
# ===========================================================================


@router.get("/attendance", response_class=HTMLResponse)
async def admin_attendance_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Voice attendance tracking admin page — Officer+."""
    player = await _require_screen("attendance_report", request, db)
    if player is None:
        return RedirectResponse("/login?next=/admin/attendance")

    ctx = await _base_ctx(request, player, db)
    return templates.TemplateResponse("admin/attendance.html", ctx)


@router.get("/quotes", response_class=HTMLResponse)
async def admin_quotes_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Guild Quotes management admin page — Officer+."""
    player = await _require_screen("quotes", request, db)
    if player is None:
        return RedirectResponse("/login?next=/admin/quotes")

    ctx = await _base_ctx(request, player, db)
    return templates.TemplateResponse("admin/quotes.html", ctx)


# ===========================================================================
# Error Routing — /admin/error-routing
# ===========================================================================


@router.get("/error-routing", response_class=HTMLResponse)
async def admin_error_routing_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Error routing admin page — Officer+."""
    player = await _require_screen("error_routing", request, db)
    if player is None:
        return RedirectResponse("/login?next=/admin/error-routing")

    pool = request.app.state.guild_sync_pool
    from sv_common.errors import get_unresolved
    errors = await get_unresolved(pool)

    from sqlalchemy import text as sa_text
    result = await db.execute(
        sa_text("""
            SELECT id, issue_type, min_severity, dest_audit_log, dest_discord,
                   first_only, enabled, notes, updated_at
              FROM common.error_routing
             ORDER BY issue_type NULLS LAST, min_severity
        """)
    )
    routing_rules = [dict(r) for r in result.mappings().all()]

    ctx = await _base_ctx(request, player, db)
    ctx["errors"] = errors
    ctx["routing_rules"] = routing_rules
    return templates.TemplateResponse("admin/error_routing.html", ctx)
