"""Public page routes: landing page."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from guild_portal.deps import get_db, get_page_member
from guild_portal.services import campaign_service
from guild_portal.templating import templates
from sv_common.config_cache import is_guild_quotes_enabled
from sv_common.db.models import (
    GuildRank,
    GuildQuote,
    GuildQuoteTitle,
    QuoteSubject,
    Player,
    RecurringEvent,
    WowCharacter,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["public-pages"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROLE_TARGETS = {
    "Tank": 2,
    "Healer": 4,
    "Melee DPS": 7,
    "Ranged DPS": 7,
}
_DPS_TOTAL_TARGET = 14  # sum of Melee + Ranged targets

CLASS_EMOJIS = {
    "Druid": "🌿",
    "Paladin": "⚔️",
    "Warlock": "👁️",
    "Priest": "✨",
    "Mage": "🔮",
    "Hunter": "🏹",
    "Warrior": "⚔️",
    "Shaman": "⚡",
    "Monk": "☯️",
    "Death Knight": "💀",
    "Demon Hunter": "🦅",
    "Evoker": "🐉",
    "Rogue": "🗡️",
}

ROLE_EMOJIS = {
    "Tank": "🛡️",
    "Healer": "💚",
    "Melee DPS": "⚔️",
    "Ranged DPS": "🏹",
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Helper queries
# ---------------------------------------------------------------------------


async def _get_officers(db) -> list[dict[str, Any]]:
    """Query officers (guild_rank.level >= 4), eagerly load char + class + spec."""
    result = await db.execute(
        select(Player)
        .join(GuildRank, Player.guild_rank_id == GuildRank.id)
        .where(GuildRank.level >= 4, Player.is_active == True)
        .order_by(GuildRank.level.desc(), Player.display_name.asc())
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.main_character).selectinload(WowCharacter.wow_class),
            selectinload(Player.main_character).selectinload(WowCharacter.active_spec),
        )
    )
    players = result.unique().scalars().all()

    officers = []
    for p in players:
        char = p.main_character
        class_name = char.wow_class.name if (char and char.wow_class) else None
        class_color = (
            f"#{char.wow_class.color_hex}" if (char and char.wow_class and char.wow_class.color_hex) else "#d4a84b"
        )
        armory_url = None
        if char:
            armory_url = (
                f"https://worldofwarcraft.blizzard.com/en-us/character/us"
                f"/{char.realm_slug}/{char.character_name.lower()}"
            )
        officers.append(
            {
                "display_name": p.display_name,
                "guild_rank": p.guild_rank,
                "main_character": char,
                "class_emoji": CLASS_EMOJIS.get(class_name, "⚔️") if class_name else "⚔️",
                "class_color": class_color,
                "armory_url": armory_url,
            }
        )
    return officers


async def _get_recruiting_needs(db) -> dict[str, int]:
    """Count active roster players by main role vs targets; return roles where count < target."""
    rows = await db.execute(
        text(
            """
            SELECT r.name AS role_name, COUNT(p.id) AS cnt
            FROM guild_identity.players p
            JOIN guild_identity.wow_characters wc ON wc.id = p.main_character_id
            JOIN ref.specializations s
              ON s.id = COALESCE(p.main_spec_id, wc.active_spec_id)
            JOIN guild_identity.roles r ON r.id = s.default_role_id
            JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
            WHERE p.is_active = TRUE
              AND p.on_raid_hiatus IS NOT TRUE
              AND gr.level > 1
              AND wc.in_guild = TRUE
            GROUP BY r.name
            """
        )
    )
    current_counts: dict[str, int] = {row.role_name: row.cnt for row in rows}

    # Balancing: if one DPS type exceeds 7, the other's target shrinks to keep total = 14
    effective_targets = dict(ROLE_TARGETS)
    melee = current_counts.get("Melee DPS", 0)
    ranged = current_counts.get("Ranged DPS", 0)
    if melee > ROLE_TARGETS["Melee DPS"]:
        effective_targets["Ranged DPS"] = max(0, _DPS_TOTAL_TARGET - melee)
    elif ranged > ROLE_TARGETS["Ranged DPS"]:
        effective_targets["Melee DPS"] = max(0, _DPS_TOTAL_TARGET - ranged)

    needs = {}
    for role, target in effective_targets.items():
        current = current_counts.get(role, 0)
        if current < target:
            needs[role] = target - current
    return needs


async def _get_event_days(db) -> list[RecurringEvent]:
    """Load public-visible active recurring events, ordered by day_of_week."""
    result = await db.execute(
        select(RecurringEvent)
        .where(RecurringEvent.display_on_public == True, RecurringEvent.is_active == True)
        .order_by(RecurringEvent.day_of_week.asc())
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------


def _rank_level(member: Player | None) -> int:
    if member is None:
        return 0
    return member.guild_rank.level if member.guild_rank else 0


@router.get("/", response_class=HTMLResponse)
async def landing_page(
    request: Request,
    db=Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    viewer_level = _rank_level(current_member)

    # Load live campaigns visible to this viewer
    all_campaigns = await campaign_service.list_campaigns(db)
    live_campaigns = [
        c for c in all_campaigns
        if c.status == "live"
        and (c.min_rank_to_view is None or viewer_level >= c.min_rank_to_view)
    ]
    # Also show recently closed
    closed_campaigns = [
        c for c in all_campaigns
        if c.status == "closed"
        and (c.min_rank_to_view is None or viewer_level >= c.min_rank_to_view)
    ][:3]

    # Random guild quote and title from DB (only loaded when feature is enabled)
    guild_quote = None
    guild_quote_title = None
    guild_quote_subject = None  # display_name of the subject for attribution
    if is_guild_quotes_enabled():
        try:
            # Pick a random active subject first
            subj_result = await db.execute(
                select(QuoteSubject)
                .where(QuoteSubject.active.is_(True))
                .order_by(func.random())
                .limit(1)
            )
            subj = subj_result.scalar_one_or_none()

            if subj:
                # Quote from that subject's pool
                q_result = await db.execute(
                    select(GuildQuote)
                    .where(GuildQuote.subject_id == subj.id)
                    .order_by(func.random())
                    .limit(1)
                )
                quote_row = q_result.scalar_one_or_none()
                if quote_row:
                    guild_quote = quote_row.quote
                    guild_quote_subject = subj.display_name

                # Title from that subject's pool
                t_result = await db.execute(
                    select(GuildQuoteTitle)
                    .where(GuildQuoteTitle.subject_id == subj.id)
                    .order_by(func.random())
                    .limit(1)
                )
                title_row = t_result.scalar_one_or_none()
                if title_row:
                    guild_quote_title = title_row.title
            else:
                # Fallback: unassigned quotes (subject_id IS NULL)
                q_result = await db.execute(
                    select(GuildQuote)
                    .where(GuildQuote.subject_id.is_(None))
                    .order_by(func.random())
                    .limit(1)
                )
                quote_row = q_result.scalar_one_or_none()
                if quote_row:
                    guild_quote = quote_row.quote

                t_result = await db.execute(
                    select(GuildQuoteTitle)
                    .where(GuildQuoteTitle.subject_id.is_(None))
                    .order_by(func.random())
                    .limit(1)
                )
                title_row = t_result.scalar_one_or_none()
                if title_row:
                    guild_quote_title = title_row.title
        except Exception:
            logger.warning("Could not load guild quote/title from DB", exc_info=True)

    # Load dynamic index data
    officers = []
    recruiting_needs: dict[str, int] = {}
    event_days = []
    try:
        officers = await _get_officers(db)
    except Exception:
        logger.warning("Could not load officers from DB", exc_info=True)
    try:
        recruiting_needs = await _get_recruiting_needs(db)
    except Exception:
        logger.warning("Could not load recruiting needs from DB", exc_info=True)
    try:
        event_days = await _get_event_days(db)
    except Exception:
        logger.warning("Could not load event days from DB", exc_info=True)
    ah_prices = []
    viewer_realm_id = 0
    available_realms = []
    try:
        pool = getattr(request.app.state, "guild_sync_pool", None)
        if pool:
            from sv_common.guild_sync.ah_service import get_prices_for_realm, get_available_realms
            from sv_common.config_cache import get_site_config as _get_site_config
            _cfg = _get_site_config()
            home_realm_slug = _cfg.get("home_realm_slug", "")
            home_connected_realm_id = _cfg.get("connected_realm_id") or 0

            # Default to home realm prices for the index page
            viewer_realm_id = home_connected_realm_id

            # If logged in and main char is on home realm, use home realm prices
            if current_member:
                try:
                    from sqlalchemy import select as sa_select
                    from sv_common.db.models import WowCharacter as _WowChar
                    mc_id = current_member.main_character_id
                    if mc_id:
                        mc_res = await db.execute(sa_select(_WowChar).where(_WowChar.id == mc_id))
                        main_char = mc_res.scalar_one_or_none()
                        if main_char and main_char.realm_slug and main_char.realm_slug == home_realm_slug:
                            viewer_realm_id = home_connected_realm_id
                except Exception:
                    pass

            raw_prices = await get_prices_for_realm(pool, viewer_realm_id)
            ah_prices = [p for p in raw_prices if p.get("min_buyout") is not None]
            available_realms = await get_available_realms(pool)
    except Exception:
        logger.warning("Could not load AH prices from DB", exc_info=True)

    ctx = {
        "request": request,
        "current_member": current_member,
        "active_campaigns": live_campaigns,
        "live_campaigns": live_campaigns,
        "closed_campaigns": closed_campaigns,
        "guild_quote": guild_quote,
        "guild_quote_title": guild_quote_title,
        "guild_quote_subject": guild_quote_subject,
        "officers": officers,
        "recruiting_needs": recruiting_needs,
        "event_days": event_days,
        "class_emojis": CLASS_EMOJIS,
        "role_emojis": ROLE_EMOJIS,
        "day_names": DAY_NAMES,
        "ah_prices": ah_prices,
        "viewer_realm_id": viewer_realm_id,
        "available_realms": available_realms,
    }
    return templates.TemplateResponse("public/index.html", ctx)


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(
    request: Request,
    current_member: Player | None = Depends(get_page_member),
):
    prefill_contact = current_member.display_name if current_member else None
    return templates.TemplateResponse(
        "feedback.html",
        {
            "request": request,
            "current_member": current_member,
            "is_authenticated": current_member is not None,
            "prefill_contact": prefill_contact,
        },
    )


@router.get("/crafting-corner", response_class=HTMLResponse)
async def crafting_corner_page(
    request: Request,
    current_member: Player | None = Depends(get_page_member),
):
    """Public crafting corner page — no auth required to browse."""
    return templates.TemplateResponse(
        "public/crafting_corner.html",
        {
            "request": request,
            "current_member": current_member,
            "active_campaigns": [],
        },
    )


@router.get("/roster", response_class=HTMLResponse)
async def roster_page(
    request: Request,
    db=Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    """Public roster view — no auth required."""
    return templates.TemplateResponse(
        "public/roster.html",
        {
            "request": request,
            "current_member": current_member,
            "active_campaigns": [],
        },
    )
