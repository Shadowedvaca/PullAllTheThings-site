"""User profile / settings page routes."""

import logging
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from patt.deps import get_db, get_page_member
from patt.nav import load_nav_items
from patt.services import campaign_service
from patt.services.availability_service import (
    clear_player_availability,
    get_player_availability,
    set_player_availability,
)
from patt.templating import templates
from sv_common.auth.passwords import hash_password, verify_password
from sv_common.db.models import (
    Player,
    PlayerActionLog,
    PlayerCharacter,
    Specialization,
    User,
    WowCharacter,
    WowClass,
)
from sv_common.identity import members as member_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["profile-pages"])

# Common US/world timezones for the dropdown
COMMON_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
    "America/Toronto",
    "America/Vancouver",
    "America/Halifax",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Stockholm",
    "Europe/Helsinki",
    "Australia/Sydney",
    "Australia/Melbourne",
    "Pacific/Auckland",
]

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


async def _base_ctx(request: Request, player: Player, db: AsyncSession) -> dict:
    active = await campaign_service.list_campaigns(db, status="live")
    nav_items = await load_nav_items(db, player)
    return {
        "request": request,
        "current_member": player,
        "active_campaigns": active,
        "nav_items": nav_items,
        "current_screen": "my_profile",
    }


async def _load_profile_data(player: Player, db: AsyncSession) -> dict:
    """Load all data needed for the profile page."""
    # Player's linked characters
    result = await db.execute(
        select(PlayerCharacter)
        .options(
            selectinload(PlayerCharacter.character).selectinload(WowCharacter.wow_class)
        )
        .where(PlayerCharacter.player_id == player.id)
        .order_by(PlayerCharacter.id)
    )
    player_chars = list(result.scalars().all())

    # Unclaimed active guild characters (not in player_characters, not removed)
    claimed_char_ids_result = await db.execute(select(PlayerCharacter.character_id))
    claimed_char_ids = set(claimed_char_ids_result.scalars().all())

    unclaimed_result = await db.execute(
        select(WowCharacter)
        .options(selectinload(WowCharacter.wow_class))
        .where(
            WowCharacter.removed_at.is_(None),
            WowCharacter.id.notin_(claimed_char_ids) if claimed_char_ids else True,
        )
        .order_by(WowCharacter.character_name)
    )
    unclaimed_chars = list(unclaimed_result.scalars().all())

    # All specs grouped by class_id for JS-driven dropdown
    spec_result = await db.execute(
        select(Specialization)
        .options(selectinload(Specialization.wow_class))
        .order_by(Specialization.class_id, Specialization.name)
    )
    all_specs = list(spec_result.scalars().all())

    # Group specs by class_id
    specs_by_class: dict[int, list[Specialization]] = {}
    for spec in all_specs:
        specs_by_class.setdefault(spec.class_id, []).append(spec)

    # Availability rows
    availability = await get_player_availability(db, player.id)
    avail_by_day = {row.day_of_week: row for row in availability}

    return {
        "player_chars": player_chars,
        "unclaimed_chars": unclaimed_chars,
        "specs_by_class": specs_by_class,
        "all_specs": all_specs,
        "avail_by_day": avail_by_day,
        "day_names": DAY_NAMES,
        "timezones": COMMON_TIMEZONES,
    }


# ---------------------------------------------------------------------------
# GET /profile
# ---------------------------------------------------------------------------


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/profile", status_code=302)

    # Reload player with all relationships
    result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.guild_rank),
            selectinload(Player.main_character).selectinload(WowCharacter.wow_class),
            selectinload(Player.main_spec).selectinload(Specialization.wow_class),
            selectinload(Player.offspec_character).selectinload(WowCharacter.wow_class),
            selectinload(Player.offspec_spec).selectinload(Specialization.wow_class),
            selectinload(Player.discord_user),
        )
        .where(Player.id == current_member.id)
    )
    player = result.scalar_one_or_none()
    if player is None:
        return RedirectResponse(url="/login", status_code=302)

    ctx = await _base_ctx(request, player, db)
    profile_data = await _load_profile_data(player, db)
    ctx.update(profile_data)
    ctx["flash_success"] = success
    ctx["flash_error"] = error

    return templates.TemplateResponse("profile/settings.html", ctx)


# ---------------------------------------------------------------------------
# POST /profile/info  — display name, timezone, auto_invite_events
# ---------------------------------------------------------------------------


@router.post("/profile/info", response_class=HTMLResponse)
async def profile_update_info(
    request: Request,
    display_name: str = Form(...),
    timezone: str = Form("America/Chicago"),
    auto_invite_events: str = Form(None),
    crafting_notifications_enabled: str = Form(None),
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/profile", status_code=302)

    display_name = display_name.strip()
    if not display_name:
        return RedirectResponse(url="/profile?error=Display+name+cannot+be+empty", status_code=302)
    if timezone not in COMMON_TIMEZONES:
        return RedirectResponse(url="/profile?error=Invalid+timezone+selected", status_code=302)

    try:
        await member_service.update_player(
            db,
            current_member.id,
            display_name=display_name,
            timezone=timezone,
            auto_invite_events=(auto_invite_events == "on"),
            crafting_notifications_enabled=(crafting_notifications_enabled == "on"),
        )
    except Exception as exc:
        logger.error("profile info update failed for player %s: %s", current_member.id, exc)
        return RedirectResponse(url="/profile?error=Failed+to+save+changes", status_code=302)

    return RedirectResponse(url="/profile?success=Identity+settings+saved", status_code=302)


# ---------------------------------------------------------------------------
# POST /profile/characters  — main/offspec character + spec
# ---------------------------------------------------------------------------


@router.post("/profile/characters", response_class=HTMLResponse)
async def profile_update_characters(
    request: Request,
    main_character_id: str = Form(None),
    main_spec_id: str = Form(None),
    offspec_character_id: str = Form(None),
    offspec_spec_id: str = Form(None),
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/profile", status_code=302)

    def _parse_id(val: str | None) -> int | None:
        if not val or val.strip() == "":
            return None
        try:
            return int(val)
        except ValueError:
            return None

    main_char_id = _parse_id(main_character_id)
    main_s_id = _parse_id(main_spec_id)
    offspec_char_id = _parse_id(offspec_character_id)
    offspec_s_id = _parse_id(offspec_spec_id)

    # Verify the selected characters actually belong to this player
    result = await db.execute(
        select(PlayerCharacter.character_id).where(PlayerCharacter.player_id == current_member.id)
    )
    owned_char_ids = {row for row in result.scalars().all()}

    if main_char_id is not None and main_char_id not in owned_char_ids:
        return RedirectResponse(url="/profile?error=Invalid+main+character+selection", status_code=302)
    if offspec_char_id is not None and offspec_char_id not in owned_char_ids:
        return RedirectResponse(url="/profile?error=Invalid+off-spec+character+selection", status_code=302)

    try:
        await member_service.update_player(
            db,
            current_member.id,
            main_character_id=main_char_id,
            main_spec_id=main_s_id,
            offspec_character_id=offspec_char_id,
            offspec_spec_id=offspec_s_id,
        )
    except Exception as exc:
        logger.error("profile characters update failed for player %s: %s", current_member.id, exc)
        return RedirectResponse(url="/profile?error=Failed+to+save+character+settings", status_code=302)

    return RedirectResponse(url="/profile?success=Character+settings+saved", status_code=302)


# ---------------------------------------------------------------------------
# POST /profile/availability  — upsert all 7 days
# ---------------------------------------------------------------------------


@router.post("/profile/availability", response_class=HTMLResponse)
async def profile_update_availability(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/profile", status_code=302)

    form = await request.form()

    try:
        # First clear all existing availability rows for this player
        await clear_player_availability(db, current_member.id)

        for day in range(7):
            available_flag = form.get(f"day_{day}_available")
            if not available_flag:
                # Day is marked unavailable — skip (already cleared)
                continue

            start_str = form.get(f"day_{day}_start", "")
            hours_str = form.get(f"day_{day}_hours", "")

            if not start_str or not hours_str:
                continue

            # Parse HH:MM time string
            try:
                h, m = start_str.split(":")
                earliest_start = time(int(h), int(m))
            except (ValueError, AttributeError):
                return RedirectResponse(
                    url=f"/profile?error=Invalid+time+for+{DAY_NAMES[day]}",
                    status_code=302,
                )

            try:
                available_hours = Decimal(hours_str)
            except InvalidOperation:
                return RedirectResponse(
                    url=f"/profile?error=Invalid+hours+for+{DAY_NAMES[day]}",
                    status_code=302,
                )

            await set_player_availability(
                db,
                player_id=current_member.id,
                day_of_week=day,
                earliest_start=earliest_start,
                available_hours=available_hours,
            )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/profile?error={str(exc).replace(' ', '+')}",
            status_code=302,
        )
    except Exception as exc:
        logger.error("profile availability update failed for player %s: %s", current_member.id, exc)
        return RedirectResponse(url="/profile?error=Failed+to+save+availability", status_code=302)

    return RedirectResponse(url="/profile?success=Availability+saved", status_code=302)


# ---------------------------------------------------------------------------
# POST /profile/password  — change password
# ---------------------------------------------------------------------------


@router.post("/profile/password", response_class=HTMLResponse)
async def profile_update_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/profile", status_code=302)

    if new_password != confirm_password:
        return RedirectResponse(url="/profile?error=New+passwords+do+not+match", status_code=302)
    if len(new_password) < 8:
        return RedirectResponse(url="/profile?error=Password+must+be+at+least+8+characters", status_code=302)

    # Load the User record for password verification
    if current_member.website_user_id is None:
        return RedirectResponse(url="/profile?error=No+website+account+linked", status_code=302)

    result = await db.execute(select(User).where(User.id == current_member.website_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return RedirectResponse(url="/profile?error=Account+not+found", status_code=302)

    if not verify_password(current_password, user.password_hash):
        return RedirectResponse(url="/profile?error=Current+password+is+incorrect", status_code=302)

    user.password_hash = hash_password(new_password)
    try:
        await db.flush()
    except Exception as exc:
        logger.error("password update failed for player %s: %s", current_member.id, exc)
        return RedirectResponse(url="/profile?error=Failed+to+update+password", status_code=302)

    return RedirectResponse(url="/profile?success=Password+updated+successfully", status_code=302)


# ---------------------------------------------------------------------------
# POST /profile/characters/claim  — self-service: link an unclaimed character
# ---------------------------------------------------------------------------


@router.post("/profile/characters/claim", response_class=HTMLResponse)
async def profile_claim_character(
    request: Request,
    character_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/profile", status_code=302)

    # Load the character — must exist and not be removed
    result = await db.execute(
        select(WowCharacter).where(
            WowCharacter.id == character_id,
            WowCharacter.removed_at.is_(None),
        )
    )
    char = result.scalar_one_or_none()
    if char is None:
        return RedirectResponse(url="/profile?error=Character+not+found", status_code=302)

    # Verify not already claimed
    existing = await db.execute(
        select(PlayerCharacter).where(PlayerCharacter.character_id == character_id)
    )
    if existing.scalar_one_or_none() is not None:
        return RedirectResponse(url="/profile?error=That+character+is+already+claimed", status_code=302)

    try:
        pc = PlayerCharacter(
            player_id=current_member.id,
            character_id=character_id,
            link_source="self_service",
            confidence="medium",
        )
        db.add(pc)

        log_entry = PlayerActionLog(
            player_id=current_member.id,
            action="claim_character",
            character_id=character_id,
            character_name=char.character_name,
            realm_slug=char.realm_slug,
            details={"link_source": "self_service", "confidence": "medium"},
        )
        db.add(log_entry)
        await db.flush()
    except Exception as exc:
        logger.error("claim_character failed for player %s char %s: %s", current_member.id, character_id, exc)
        return RedirectResponse(url="/profile?error=Failed+to+claim+character", status_code=302)

    return RedirectResponse(
        url=f"/profile?success={char.character_name}+claimed+successfully",
        status_code=302,
    )


# ---------------------------------------------------------------------------
# POST /profile/characters/unclaim  — self-service: remove a claimed character
# ---------------------------------------------------------------------------


@router.post("/profile/characters/unclaim", response_class=HTMLResponse)
async def profile_unclaim_character(
    request: Request,
    character_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
    current_member: Player | None = Depends(get_page_member),
):
    if current_member is None:
        return RedirectResponse(url="/login?next=/profile", status_code=302)

    # Verify this character belongs to this player
    result = await db.execute(
        select(PlayerCharacter).where(
            PlayerCharacter.character_id == character_id,
            PlayerCharacter.player_id == current_member.id,
        )
    )
    pc = result.scalar_one_or_none()
    if pc is None:
        return RedirectResponse(url="/profile?error=Character+not+linked+to+your+account", status_code=302)

    # Load the character for log denormalization + deletion check
    char_result = await db.execute(
        select(WowCharacter).where(WowCharacter.id == character_id)
    )
    char = char_result.scalar_one_or_none()
    char_name = char.character_name if char else "Unknown"
    char_realm = char.realm_slug if char else ""

    try:
        # Block unclaim if the character is the player's current main or offspec.
        # The player must set a different main/offspec first, then remove this character.
        player_result = await db.execute(
            select(Player).where(Player.id == current_member.id)
        )
        player = player_result.scalar_one_or_none()
        if player is not None:
            if player.main_character_id == character_id:
                return RedirectResponse(
                    url=f"/profile?error={char_name}+is+your+main+character.+Set+a+different+main+first,+then+remove+it.",
                    status_code=302,
                )
            if player.offspec_character_id == character_id:
                return RedirectResponse(
                    url=f"/profile?error={char_name}+is+your+secondary+character.+Set+a+different+secondary+first,+then+remove+it.",
                    status_code=302,
                )

        # Remove the player_characters bridge row
        await db.delete(pc)

        # If character has never been seen by Blizzard API, delete it entirely
        deleted_char = False
        if char is not None and char.blizzard_last_sync is None:
            await db.delete(char)
            deleted_char = True

        log_entry = PlayerActionLog(
            player_id=current_member.id,
            action="unclaim_character",
            character_id=None if deleted_char else character_id,
            character_name=char_name,
            realm_slug=char_realm,
            details={"character_deleted": deleted_char},
        )
        db.add(log_entry)
        await db.flush()
    except Exception as exc:
        logger.error("unclaim_character failed for player %s char %s: %s", current_member.id, character_id, exc)
        return RedirectResponse(url="/profile?error=Failed+to+unclaim+character", status_code=302)

    return RedirectResponse(
        url=f"/profile?success={char_name}+unclaimed+successfully",
        status_code=302,
    )
