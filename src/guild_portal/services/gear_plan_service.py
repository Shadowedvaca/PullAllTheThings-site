"""Gear Plan service — plan CRUD, BIS population, upgrade computation.

Works with asyncpg pool (raw SQL) for consistency with item_service.py and
the broader guild_sync pattern.  Character ownership must be verified by
the caller before invoking any mutating function.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import asyncpg

from sv_common.guild_sync.quality_track import detect_crafted_track, is_crafted_item
from sv_common.guild_sync.simc_parser import (
    SimcSlot,
    export_gear_plan,
    parse_gear_slots,
)
from sv_common.guild_sync.source_config import (
    get_display_name as _get_display_name,
    get_tracks as _get_tracks,
    get_track_label as _get_track_label,
    track_to_label as _track_to_label,
)

logger = logging.getLogger(__name__)

# Canonical WoW slot order (16 slots)
# ── Slot metadata — loaded from ref.gear_plan_slots at startup ────────────────
# WOW_SLOTS kept as exported name so existing route guards work without changes.
WOW_SLOTS:      set[str]        = set()   # valid plan_slot keys
_SLOT_META:     dict[str, dict] = {}      # plan_slot → row dict
_SLOTS_ORDERED: list[str]       = []      # plan_slots in canonical slot_order sequence

# Quality track ranking (lowest to highest)
TRACK_ORDER: dict[str, int] = {"V": 0, "C": 1, "H": 2, "M": 3}


# Maps each quality track to the next track up (M stays at M — already maxed).
NEXT_TRACK: dict[str, str] = {"V": "C", "C": "H", "H": "M", "M": "M"}


def _noncrafted_target_ilvl(
    is_bis: bool,
    equipped_ilvl: Optional[int],
    equipped_track: Optional[str],
    quality_ilvl_map: dict,
) -> Optional[int]:
    """Phase 2C ilvl display rule for non-crafted items (raid, dungeon, tier).

    BIS slot: show at the next quality track's max ilvl (V→C, C→H, H→M, M→M).
    Not BIS:  show at the player's actual equipped ilvl.
              If below Veteran track or slot empty, show Veteran max instead.
    """
    if not quality_ilvl_map:
        return None
    if is_bis:
        if not equipped_track or equipped_track not in NEXT_TRACK:
            return quality_ilvl_map.get("V", {}).get("max")
        return quality_ilvl_map.get(NEXT_TRACK[equipped_track], {}).get("max")
    else:
        eq_rank = TRACK_ORDER.get(equipped_track or "", -1)
        v_rank  = TRACK_ORDER.get("V", 0)
        if not equipped_ilvl or eq_rank < v_rank:
            return quality_ilvl_map.get("V", {}).get("max")
        return equipped_ilvl


def _crafted_target_ilvl(
    is_bis: bool,
    equipped_track: Optional[str],
    crafted_ilvl_map: dict,
) -> Optional[int]:
    """Phase 2C ilvl display rule for crafted items.

    3d. Equipped at M (any BIS)   → M max crafted
    3b. BIS + equipped at H       → M max crafted
    3a. BIS + equipped below H    → H max crafted
    3c. Not BIS + H or below      → H max crafted
    """
    if not crafted_ilvl_map:
        return None
    h_max = crafted_ilvl_map.get("H", {}).get("max")
    m_max = crafted_ilvl_map.get("M", {}).get("max")
    if equipped_track == "M":          # 3d — always M regardless of BIS
        return m_max
    if is_bis and equipped_track == "H":  # 3b
        return m_max
    return h_max                       # 3a, 3c, or empty/unknown


TRACK_COLORS: dict[str, str] = {
    "V": "#1eff00",
    "C": "#0070dd",
    "H": "#a335ee",
    "M": "#ff8000",
}

# ── Class eligibility constants (Phase 1E.4) ──────────────────────────────────

# Class → armor type worn (for filtering armor slot items)
CLASS_ARMOR_TYPE: dict[str, str] = {
    "Death Knight": "plate",
    "Demon Hunter": "leather",
    "Druid":        "leather",
    "Evoker":       "mail",
    "Hunter":       "mail",
    "Mage":         "cloth",
    "Monk":         "leather",
    "Paladin":      "plate",
    "Priest":       "cloth",
    "Rogue":        "leather",
    "Shaman":       "mail",
    "Warlock":      "cloth",
    "Warrior":      "plate",
}


# ── Slot metadata helpers ─────────────────────────────────────────────────────

async def _ensure_slot_meta(conn: asyncpg.Connection) -> None:
    """Load ref.gear_plan_slots into the module-level cache if not already loaded."""
    if _SLOT_META:
        return
    rows = await conn.fetch(
        """SELECT plan_slot, display_name, slot_order, enrichment_slot_type,
                  paired_slot, is_armor_slot, is_weapon_slot,
                  is_tier_catalyst_slot, is_main_tier_slot
             FROM ref.gear_plan_slots ORDER BY slot_order"""
    )
    _SLOT_META.update({r["plan_slot"]: dict(r) for r in rows})
    _SLOTS_ORDERED.extend(r["plan_slot"] for r in rows)
    WOW_SLOTS.update(_SLOT_META)


async def load_slot_meta(pool: asyncpg.Pool) -> None:
    """Pre-warm the slot metadata cache. Idempotent — safe to call repeatedly."""
    async with pool.acquire() as conn:
        await _ensure_slot_meta(conn)


def is_valid_slot(slot: str) -> bool:
    """True when slot is a known plan slot (requires cache to be loaded)."""
    return slot in _SLOT_META


# The 4 catalyst slots (back/wrist/waist/feet) have no Journal encounter and
# therefore no item_sources rows.  They are found via name-suffix matching
# against the current season's main tier anchor.

# Wowhead tooltip HTML armor-type marker: <!--scstart4:{subclass_id}-->
# Used when wow_items.armor_type is NULL (items seeded via Journal API don't
# have armor_type populated; the type is embedded in the tooltip HTML instead).
_ARMOR_TYPE_MARKER: dict[str, str] = {
    "cloth":   "%<!--scstart4:1-->%",
    "leather": "%<!--scstart4:2-->%",
    "mail":    "%<!--scstart4:3-->%",
    "plate":   "%<!--scstart4:4-->%",
}

# The two weapon main-hand plan slot keys — used throughout weapon display logic.
_WEAPON_MH_SLOTS: frozenset[str] = frozenset({"main_hand_2h", "main_hand_1h"})


def _compute_weapon_display(
    bis_by_slot: dict,
    equipped_by_slot: dict,
    desired_by_slot: dict,
) -> tuple[Optional[str], bool]:
    """Determine weapon build and off-hand visibility.

    Priority order: BIS guide entries (lowest guide_order) → equipped → desired → default.

    Returns:
        weapon_build: "2h" | "1h" | None (None = no weapon data at all)
        show_off_hand: True when build is 1H or Titan's Grip (2H + off_hand BIS)
    """
    mh2h = bis_by_slot.get("main_hand_2h", [])
    mh1h = bis_by_slot.get("main_hand_1h", [])

    if mh2h or mh1h:
        min_2h = min((r.get("guide_order", 1) for r in mh2h), default=99)
        min_1h = min((r.get("guide_order", 1) for r in mh1h), default=99)
        weapon_build: Optional[str] = "2h" if min_2h <= min_1h else "1h"
    elif equipped_by_slot.get("main_hand_2h"):
        weapon_build = "2h"
    elif equipped_by_slot.get("main_hand_1h"):
        weapon_build = "1h"
    elif desired_by_slot.get("main_hand_2h"):
        weapon_build = "2h"
    elif desired_by_slot.get("main_hand_1h"):
        weapon_build = "1h"
    else:
        weapon_build = None

    # Show off_hand for 1H builds and Titan's Grip (2H build with off_hand BIS).
    # Any off_hand BIS entry alongside a 2H main hand means Titan's Grip.
    if weapon_build == "1h":
        show_off_hand = True
    elif weapon_build == "2h":
        show_off_hand = bool(bis_by_slot.get("off_hand"))
    else:
        show_off_hand = False

    return weapon_build, show_off_hand



def _upgrade_tracks(
    equipped_track: Optional[str],
    equipped_item_id: Optional[int],
    desired_item_id: Optional[int],
    available_tracks: list[str],
) -> list[str]:
    """Return which available tracks would be upgrades over the equipped item.

    Rules:
    - Empty slot → anything is an upgrade
    - Item equipped, track unknown → cannot determine upgrades (return [])
    - Same item, lower track → need strictly higher track
    - Different item → same track and above (never recommends a lower track)
    """
    if not available_tracks:
        return []
    if equipped_track is None:
        # Empty slot: anything is an upgrade.
        # Item equipped but track undetected: cannot recommend upgrades safely —
        # returning all tracks would incorrectly include Veteran as an upgrade
        # for someone wearing a non-LFR item whose display_string wasn't detected.
        return available_tracks if equipped_item_id is None else []

    eq_idx = TRACK_ORDER.get(equipped_track, -1)

    if desired_item_id and equipped_item_id == desired_item_id:
        # Same item — need strictly higher track
        return [t for t in available_tracks if TRACK_ORDER.get(t, -1) > eq_idx]
    else:
        # Different item — same track and above (never lower)
        return [t for t in available_tracks if TRACK_ORDER.get(t, -1) >= eq_idx]


def _contextual_sources(sources: list[dict], upgrade_tracks: list[str]) -> list[dict]:
    """Filter and relabel sources based on what tracks the player actually needs.

    - World boss sources are excluded when C is not in upgrade_tracks
      (world bosses only drop Champion-track loot).
    - Each source's track_label is replaced with the label for the minimum
      track from (instance_tracks ∩ upgrade_tracks), so it shows what the
      player actually needs to run, not the lowest the instance offers.
    - Sources that offer no useful upgrade tracks are excluded entirely.
    """
    if not upgrade_tracks:
        return sources  # Can't determine needs — show all with default labels

    result = []
    for src in sources:
        inst_type = src["instance_type"]
        instance_tracks = _get_tracks(inst_type)

        # World boss only drops C. Skip if player doesn't need C.
        if inst_type == "world_boss" and "C" not in upgrade_tracks:
            continue

        useful_tracks = [t for t in instance_tracks if t in upgrade_tracks]
        if not useful_tracks:
            continue

        # World boss has its own category — no track suffix
        if inst_type == "world_boss":
            result.append({**src, "track_label": ""})
            continue

        # Show the minimum useful track (closest to what the player needs now)
        min_useful = min(useful_tracks, key=lambda t: TRACK_ORDER.get(t, 99))
        result.append({**src, "track_label": _track_to_label(min_useful, inst_type)})

    return result


def _merge_paired_bis(bis_by_slot: dict, slot_a: str, slot_b: str) -> None:
    """Merge BIS recommendation lists for a paired slot (rings, trinkets).

    Both slots get the full combined pool of items so the drawer shows every
    possible ring/trinket, not just items that happened to be scraped under
    one specific slot key.  Deduplicates by (source_id, blizzard_item_id).
    Called AFTER _normalize_paired_slot so slot ordering is already settled.
    """
    recs_a = bis_by_slot.get(slot_a, [])
    recs_b = bis_by_slot.get(slot_b, [])
    if not recs_a and not recs_b:
        return
    seen: set[tuple] = set()
    merged: list[dict] = []
    for rec in recs_a + recs_b:
        key = (rec["source_id"], rec["blizzard_item_id"])
        if key not in seen:
            seen.add(key)
            merged.append(rec)
    bis_by_slot[slot_a] = merged
    bis_by_slot[slot_b] = merged


def _normalize_paired_slot(
    slot_a: str,
    slot_b: str,
    equipped_by_slot: dict,
    desired_by_slot: dict,
    bis_by_slot: dict,
    bis_source_id: Optional[int],
    slot_remapping: dict,
) -> None:
    """Normalize a paired slot (rings, trinkets) by swapping equipped items for display.

    Rules:
    1. If swapping the equipped items increases the number of equipped==desired
       matches, swap equipped only (BIS data already consistent with desired).
    2. If neither assignment produces a match, sort equipped items alphabetically
       by item name so the display is always consistent, AND swap bis_by_slot and
       desired_by_slot to match — so the BIS grid for ring_1 also shows the
       alphabetically-earlier BIS item first.  Records the swap in slot_remapping
       so callers know which DB slot key corresponds to each visual position.

    Modifies equipped_by_slot in-place; modifies bis_by_slot / desired_by_slot
    in-place for rule 2; updates slot_remapping in-place for rule 2 swaps.
    """
    eq_a = equipped_by_slot.get(slot_a)
    eq_b = equipped_by_slot.get(slot_b)
    if not eq_a or not eq_b:
        return  # nothing to normalize if either slot is empty

    eq_a_bid = eq_a["blizzard_item_id"]
    eq_b_bid = eq_b["blizzard_item_id"]

    def _desired_bid(slot: str) -> Optional[int]:
        des = desired_by_slot.get(slot)
        if des and des.get("blizzard_item_id"):
            return des["blizzard_item_id"]
        recs = bis_by_slot.get(slot, [])
        if recs and bis_source_id:
            for rec in recs:
                if rec["source_id"] == bis_source_id:
                    return rec["blizzard_item_id"]
        return None

    des_a = _desired_bid(slot_a)
    des_b = _desired_bid(slot_b)

    match_current = (1 if des_a and eq_a_bid == des_a else 0) + \
                    (1 if des_b and eq_b_bid == des_b else 0)
    match_swapped = (1 if des_a and eq_b_bid == des_a else 0) + \
                    (1 if des_b and eq_a_bid == des_b else 0)

    should_swap = match_swapped > match_current
    also_swap_bis = False
    if not should_swap and match_current == 0 and match_swapped == 0:
        # No match either way — alphabetical by item name for consistency.
        # Also swap BIS so ring_1/ring_2 BIS ordering aligns with equipped ordering.
        name_a = eq_a.get("item_name") or ""
        name_b = eq_b.get("item_name") or ""
        should_swap = name_b < name_a
        also_swap_bis = should_swap

    if should_swap:
        equipped_by_slot[slot_a] = eq_b
        equipped_by_slot[slot_b] = eq_a
        if also_swap_bis:
            # Swap BIS recs and desired items so all three stay consistent with
            # the alphabetical ordering we just applied to equipped.
            bis_by_slot[slot_a], bis_by_slot[slot_b] = (
                bis_by_slot.get(slot_b, []),
                bis_by_slot.get(slot_a, []),
            )
            des_a = desired_by_slot.get(slot_a)
            des_b = desired_by_slot.get(slot_b)
            if des_a is not None:
                desired_by_slot[slot_b] = des_a
            elif slot_b in desired_by_slot:
                del desired_by_slot[slot_b]
            if des_b is not None:
                desired_by_slot[slot_a] = des_b
            elif slot_a in desired_by_slot:
                del desired_by_slot[slot_a]
            # Record the visual→DB mapping so the frontend uses the correct DB
            # slot key when writing (e.g. the visual ring_1 position now
            # corresponds to DB slot ring_2 and vice-versa).
            slot_remapping[slot_a] = slot_b
            slot_remapping[slot_b] = slot_a


async def verify_character_ownership(
    conn: asyncpg.Connection,
    player_id: int,
    character_id: int,
) -> bool:
    """Return True if the character is linked to the player."""
    row = await conn.fetchrow(
        "SELECT 1 FROM guild_identity.player_characters"
        " WHERE player_id = $1 AND character_id = $2",
        player_id, character_id,
    )
    return row is not None


async def get_or_create_plan(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    spec_id: Optional[int] = None,
    hero_talent_id: Optional[int] = None,
    bis_source_id: Optional[int] = None,
) -> dict:
    """Get or create a gear plan row.  Returns the plan dict."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, player_id, character_id, spec_id, hero_talent_id,
                   bis_source_id, simc_profile, is_active,
                   simc_imported_at, equipped_source
              FROM guild_identity.gear_plans
             WHERE player_id = $1 AND character_id = $2
            """,
            player_id, character_id,
        )
        if row:
            return dict(row)

        # If no spec provided, try to pull from the character's active spec
        if spec_id is None:
            char_row = await conn.fetchrow(
                "SELECT active_spec_id FROM guild_identity.wow_characters WHERE id = $1",
                character_id,
            )
            if char_row:
                spec_id = char_row["active_spec_id"]

        # Pick default BIS source (first active is_default, or first active)
        if bis_source_id is None:
            src_row = await conn.fetchrow(
                """
                SELECT id FROM ref.bis_list_sources
                 WHERE is_active = TRUE
                 ORDER BY is_default DESC, sort_order
                 LIMIT 1
                """
            )
            if src_row:
                bis_source_id = src_row["id"]

        row = await conn.fetchrow(
            """
            INSERT INTO guild_identity.gear_plans
                (player_id, character_id, spec_id, hero_talent_id, bis_source_id, is_active)
            VALUES ($1, $2, $3, $4, $5, TRUE)
            RETURNING id, player_id, character_id, spec_id, hero_talent_id,
                      bis_source_id, simc_profile, is_active,
                      simc_imported_at, equipped_source
            """,
            player_id, character_id, spec_id, hero_talent_id, bis_source_id,
        )
        return dict(row)


async def update_plan_config(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    spec_id: Optional[int] = None,
    hero_talent_id: Optional[int] = None,
    bis_source_id: Optional[int] = None,
) -> bool:
    """Update plan spec/hero_talent/source configuration.  Returns True on success."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE guild_identity.gear_plans
               SET spec_id        = COALESCE($3, spec_id),
                   hero_talent_id = $4,
                   bis_source_id  = COALESCE($5, bis_source_id),
                   updated_at     = NOW()
             WHERE player_id = $1 AND character_id = $2
            """,
            player_id, character_id, spec_id, hero_talent_id, bis_source_id,
        )
        return result != "UPDATE 0"


async def update_slot(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    slot: str,
    blizzard_item_id: Optional[int],
    item_name: Optional[str] = None,
    is_locked: Optional[bool] = None,
) -> bool:
    """Upsert a gear_plan_slot row.  Pass blizzard_item_id=None to clear the slot."""
    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return False
        plan_id = plan_row["id"]

        if blizzard_item_id is None:
            if is_locked is not None:
                # Lock / unlock only — preserve the existing item
                await conn.execute(
                    "UPDATE guild_identity.gear_plan_slots SET is_locked=$1"
                    " WHERE plan_id=$2 AND slot=$3",
                    is_locked, plan_id, slot,
                )
                return True
            # Clear the slot
            await conn.execute(
                "DELETE FROM guild_identity.gear_plan_slots WHERE plan_id=$1 AND slot=$2",
                plan_id, slot,
            )
            return True

        # Resolve name from enrichment.items if not provided by caller
        if not item_name:
            name_row = await conn.fetchrow(
                "SELECT name FROM enrichment.items WHERE blizzard_item_id=$1",
                blizzard_item_id,
            )
            item_name = name_row["name"] if name_row else None
        resolved_name = item_name

        # Determine is_locked
        locked_val: bool
        if is_locked is not None:
            locked_val = is_locked
        else:
            # Preserve existing lock state, default False for new rows
            existing = await conn.fetchrow(
                "SELECT is_locked FROM guild_identity.gear_plan_slots WHERE plan_id=$1 AND slot=$2",
                plan_id, slot,
            )
            locked_val = existing["is_locked"] if existing else False

        await conn.execute(
            """
            INSERT INTO guild_identity.gear_plan_slots
                (plan_id, slot, blizzard_item_id, item_name, is_locked)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (plan_id, slot) DO UPDATE
                SET blizzard_item_id = EXCLUDED.blizzard_item_id,
                    item_name        = EXCLUDED.item_name,
                    is_locked        = EXCLUDED.is_locked
            """,
            plan_id, slot, blizzard_item_id, resolved_name, locked_val,
        )
        return True


async def populate_from_bis(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    source_id: Optional[int] = None,
    hero_talent_id: Optional[int] = None,
) -> int:
    """Populate unlocked slots from BIS entries.

    Only overwrites slots that are not locked.  Uses the plan's configured
    source_id / hero_talent_id if not specified.  Returns the number of
    slots populated.
    """
    async with pool.acquire() as conn:
        await _ensure_slot_meta(conn)
        plan_row = await conn.fetchrow(
            """
            SELECT id, spec_id, hero_talent_id, bis_source_id
              FROM guild_identity.gear_plans
             WHERE player_id=$1 AND character_id=$2
            """,
            player_id, character_id,
        )
        if not plan_row:
            return 0

        plan_id = plan_row["id"]
        spec_id = plan_row["spec_id"]
        use_source = source_id or plan_row["bis_source_id"]
        use_ht = hero_talent_id if hero_talent_id is not None else plan_row["hero_talent_id"]

        if not spec_id or not use_source:
            return 0

        # Load current slot state: locked flags and per-slot exclusions (Phase 1E.5)
        slot_data_rows = await conn.fetch(
            """
            SELECT slot, is_locked, excluded_item_ids
              FROM guild_identity.gear_plan_slots
             WHERE plan_id = $1
            """,
            plan_id,
        )
        locked_slots: set[str] = {r["slot"] for r in slot_data_rows if r["is_locked"]}
        excluded_by_slot: dict[str, set[int]] = {
            r["slot"]: set(r["excluded_item_ids"] or []) for r in slot_data_rows
        }

        # Get ALL BIS entries ordered by priority so we can skip excluded items
        # and fall through to the next-best candidate per slot.
        bis_rows = await conn.fetch(
            """
            SELECT be.slot, be.guide_order,
                   be.blizzard_item_id, i.name AS item_name
              FROM enrichment.bis_entries be
              LEFT JOIN enrichment.items i ON i.blizzard_item_id = be.blizzard_item_id
             WHERE be.source_id = $1
               AND be.spec_id = $2
               AND (be.hero_talent_id = $3 OR be.hero_talent_id IS NULL)
             ORDER BY be.slot, be.guide_order
            """,
            use_source, spec_id, use_ht,
        )

        # Group candidates by slot (already ordered by guide_order)
        by_slot: dict[str, list] = {}
        for row in bis_rows:
            by_slot.setdefault(row["slot"], []).append(row)

        # Weapon build selection: when BIS has entries for both main_hand_2h and
        # main_hand_1h (e.g. Frost DK 2H vs DW), only populate the preferred build
        # (lowest guide_order across both slots).
        weapon_slots_in_bis = _WEAPON_MH_SLOTS & set(by_slot.keys())
        if len(weapon_slots_in_bis) > 1:
            min_2h = min((r["guide_order"] for r in by_slot.get("main_hand_2h", [])), default=99)
            min_1h = min((r["guide_order"] for r in by_slot.get("main_hand_1h", [])), default=99)
            if min_2h <= min_1h:
                by_slot.pop("main_hand_1h", None)
            else:
                by_slot.pop("main_hand_2h", None)

        populated = 0
        for slot, candidates in by_slot.items():
            if not is_valid_slot(slot) or slot in locked_slots:
                continue
            # Skip excluded items, pick first non-excluded candidate
            excluded = excluded_by_slot.get(slot, set())
            chosen = next(
                (r for r in candidates if r["blizzard_item_id"] not in excluded), None
            )
            if not chosen:
                continue

            await conn.execute(
                """
                INSERT INTO guild_identity.gear_plan_slots
                    (plan_id, slot, blizzard_item_id, item_name, is_locked)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (plan_id, slot) DO UPDATE
                    SET blizzard_item_id = EXCLUDED.blizzard_item_id,
                        item_name        = EXCLUDED.item_name
                    WHERE gear_plan_slots.is_locked = FALSE
                """,
                plan_id, slot, chosen["blizzard_item_id"], chosen["item_name"],
            )
            populated += 1

        return populated


async def delete_plan(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
) -> bool:
    """Delete a gear plan and its slots (cascade).  Returns True if found."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        return result != "DELETE 0"


async def store_equipped_simc(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    simc_text: str,
) -> tuple[bool, str]:
    """Store SimC profile as the equipped gear source.

    Saves raw simc_text, stamps simc_imported_at, sets equipped_source='simc'.
    Does NOT touch gear_plan_slots — BIS goals are managed separately via
    import_simc_goals().
    Returns (success, error_code).
    """
    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return False, "not_found"

        await conn.execute(
            """
            UPDATE guild_identity.gear_plans
               SET simc_profile     = $1,
                   simc_imported_at = NOW(),
                   equipped_source  = 'simc',
                   updated_at       = NOW()
             WHERE id = $2
            """,
            simc_text, plan_row["id"],
        )
    return True, ""


async def import_simc_goals(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    simc_text: str,
) -> dict:
    """Parse SimC text and populate gear_plan_slots as BIS goals.

    Overwrites all non-locked slots with items from the SimC string.
    Does NOT change equipped_source or simc_profile — those are managed
    separately by store_equipped_simc().
    Returns {"populated": N, "skipped_locked": M, "unrecognised": K}.
    """
    slots = parse_gear_slots(simc_text)
    if not slots:
        return {"populated": 0, "skipped_locked": 0, "unrecognised": 0}

    async with pool.acquire() as conn:
        await _ensure_slot_meta(conn)
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return {"populated": 0, "skipped_locked": 0, "unrecognised": 0}
        plan_id = plan_row["id"]

        locked_rows = await conn.fetch(
            "SELECT slot FROM guild_identity.gear_plan_slots WHERE plan_id=$1 AND is_locked=TRUE",
            plan_id,
        )
        locked_slots = {r["slot"] for r in locked_rows}

        populated = 0
        skipped_locked = 0
        unrecognised = 0

        for simc_slot in slots:
            slot = simc_slot.slot
            bid = simc_slot.blizzard_item_id

            # SimC uses 'main_hand' for all weapon types; resolve to typed slot
            if slot == "main_hand":
                type_row = await conn.fetchrow(
                    "SELECT slot_type FROM enrichment.items WHERE blizzard_item_id=$1",
                    bid,
                )
                if type_row:
                    st = type_row["slot_type"] or ""
                    if st in ("two_hand", "ranged"):
                        slot = "main_hand_2h"
                    elif st == "one_hand":
                        slot = "main_hand_1h"
                    else:
                        slot = "main_hand_2h"
                else:
                    logger.error(
                        "import_simc_goals: item %s not in enrichment.items — defaulting to main_hand_2h",
                        bid,
                    )
                    slot = "main_hand_2h"

            if not is_valid_slot(slot):
                unrecognised += 1
                continue
            if slot in locked_slots:
                skipped_locked += 1
                continue

            name_row = await conn.fetchrow(
                "SELECT name FROM enrichment.items WHERE blizzard_item_id=$1",
                bid,
            )
            item_name = (name_row["name"] if name_row else None) or f"Item {bid}"

            await conn.execute(
                """
                INSERT INTO guild_identity.gear_plan_slots
                    (plan_id, slot, blizzard_item_id, item_name, is_locked)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (plan_id, slot) DO UPDATE
                    SET blizzard_item_id = EXCLUDED.blizzard_item_id,
                        item_name        = EXCLUDED.item_name
                    WHERE gear_plan_slots.is_locked = FALSE
                """,
                plan_id, slot, bid, item_name,
            )
            populated += 1

    return {"populated": populated, "skipped_locked": skipped_locked, "unrecognised": unrecognised}


async def set_goals_from_equipped(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
) -> dict:
    """Copy character_equipment rows into gear_plan_slots as BIS goals.

    Uses the Blizzard API-synced equipment (character_equipment table) as the
    source of truth.  Overwrites all non-locked slots.
    Returns {"populated": N, "skipped_locked": M}.
    """
    async with pool.acquire() as conn:
        await _ensure_slot_meta(conn)
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return {"populated": 0, "skipped_locked": 0}
        plan_id = plan_row["id"]

        locked_rows = await conn.fetch(
            "SELECT slot FROM guild_identity.gear_plan_slots WHERE plan_id=$1 AND is_locked=TRUE",
            plan_id,
        )
        locked_slots = {r["slot"] for r in locked_rows}

        equip_rows = await conn.fetch(
            """
            SELECT ce.slot, ce.blizzard_item_id, ce.item_name
              FROM guild_identity.character_equipment ce
             WHERE ce.character_id = $1
            """,
            character_id,
        )

        populated = 0
        skipped_locked = 0

        for row in equip_rows:
            slot = row["slot"]
            if not is_valid_slot(slot):
                continue
            if slot in locked_slots:
                skipped_locked += 1
                continue

            bid = row["blizzard_item_id"]
            if not bid:
                continue

            item_name = row["item_name"] or f"Item {bid}"

            await conn.execute(
                """
                INSERT INTO guild_identity.gear_plan_slots
                    (plan_id, slot, blizzard_item_id, item_name, is_locked)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (plan_id, slot) DO UPDATE
                    SET blizzard_item_id = EXCLUDED.blizzard_item_id,
                        item_name        = EXCLUDED.item_name
                    WHERE gear_plan_slots.is_locked = FALSE
                """,
                plan_id, slot, bid, item_name,
            )
            populated += 1

    return {"populated": populated, "skipped_locked": skipped_locked}


async def set_equipped_source(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    source: str,
) -> tuple[bool, Optional[str]]:
    """Switch the plan's equipped_source between 'blizzard' and 'simc'.

    Returns (success, error_message).  Switching to 'simc' requires a stored
    simc_profile; if none exists, returns (False, 'no_simc').
    """
    if source not in ("blizzard", "simc"):
        return False, "invalid_source"

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            """
            SELECT id, simc_profile
              FROM guild_identity.gear_plans
             WHERE player_id = $1 AND character_id = $2
            """,
            player_id, character_id,
        )
        if not plan_row:
            return False, "not_found"

        if source == "simc" and not plan_row["simc_profile"]:
            return False, "no_simc"

        await conn.execute(
            """
            UPDATE guild_identity.gear_plans
               SET equipped_source = $1, updated_at = NOW()
             WHERE id = $2
            """,
            source, plan_row["id"],
        )
    return True, None


async def export_simc(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
) -> Optional[str]:
    """Generate SimC profile text from gear_plan_slots + character data.

    Returns None if no plan or no slots found.
    """
    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            """
            SELECT gp.id, gp.simc_profile,
                   wc.character_name, wc.realm_slug,
                   s.name AS spec_name,
                   c.name AS class_name
              FROM guild_identity.gear_plans gp
              JOIN guild_identity.wow_characters wc ON wc.id = gp.character_id
              LEFT JOIN ref.specializations s ON s.id = gp.spec_id
              LEFT JOIN ref.classes c ON c.id = wc.class_id
             WHERE gp.player_id=$1 AND gp.character_id=$2
            """,
            player_id, character_id,
        )
        if not plan_row:
            return None

        plan_id = plan_row["id"]
        slots_rows = await conn.fetch(
            """
            SELECT gps.slot, gps.blizzard_item_id,
                   COALESCE(ei.name, gps.item_name) AS item_name,
                   ce.bonus_ids, ce.enchant_id, ce.gem_ids
              FROM guild_identity.gear_plan_slots gps
              LEFT JOIN enrichment.items ei ON ei.blizzard_item_id = gps.blizzard_item_id
              LEFT JOIN guild_identity.character_equipment ce
                     ON ce.character_id = $2 AND ce.slot = gps.slot
             WHERE gps.plan_id = $1
            """,
            plan_id, character_id,
        )

    if not slots_rows:
        return None

    plan_slots = [dict(r) for r in slots_rows]

    char_name = plan_row["character_name"] or "Unknown"
    spec_name = (plan_row["spec_name"] or "").lower()
    class_name = (plan_row["class_name"] or "").lower().replace(" ", "_")
    realm = plan_row["realm_slug"] or "unknown"

    return export_gear_plan(
        plan_slots=plan_slots,
        char_name=char_name,
        spec=spec_name,
        wow_class=class_name,
        realm=realm,
    )


async def export_equipped_simc(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
) -> Optional[str]:
    """Generate SimC profile text from the current equipped gear source.

    If equipped_source='simc', returns the stored simc_profile directly.
    If equipped_source='blizzard', builds a SimC string from character_equipment.
    Returns None if no data is available.
    """
    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            """
            SELECT gp.id, gp.equipped_source, gp.simc_profile,
                   wc.character_name, wc.realm_slug,
                   s.name AS spec_name,
                   c.name AS class_name
              FROM guild_identity.gear_plans gp
              JOIN guild_identity.wow_characters wc ON wc.id = gp.character_id
              LEFT JOIN ref.specializations s ON s.id = gp.spec_id
              LEFT JOIN ref.classes c ON c.id = wc.class_id
             WHERE gp.player_id=$1 AND gp.character_id=$2
            """,
            player_id, character_id,
        )
        if not plan_row:
            return None

        # If SimC is the equipped source, return the stored profile verbatim
        if plan_row["equipped_source"] == "simc" and plan_row["simc_profile"]:
            return plan_row["simc_profile"]

        # Otherwise build from character_equipment (Blizzard API data)
        equip_rows = await conn.fetch(
            """
            SELECT ce.slot, ce.blizzard_item_id,
                   COALESCE(ei.name, ce.item_name) AS item_name,
                   ce.bonus_ids, ce.enchant_id, ce.gem_ids
              FROM guild_identity.character_equipment ce
              LEFT JOIN enrichment.items ei ON ei.blizzard_item_id = ce.blizzard_item_id
             WHERE ce.character_id = $1
            """,
            character_id,
        )

    if not equip_rows:
        return None

    char_name = plan_row["character_name"] or "Unknown"
    spec_name = (plan_row["spec_name"] or "").lower()
    class_name = (plan_row["class_name"] or "").lower().replace(" ", "_")
    realm = plan_row["realm_slug"] or "unknown"

    return export_gear_plan(
        plan_slots=[dict(r) for r in equip_rows],
        char_name=char_name,
        spec=spec_name,
        wow_class=class_name,
        realm=realm,
    )


async def get_plan_detail(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
) -> Optional[dict]:
    """Return full plan detail for the gear plan page.

    Returns None if no plan exists.  Call get_or_create_plan first.
    """
    async with pool.acquire() as conn:
        await _ensure_slot_meta(conn)
        plan_row = await conn.fetchrow(
            """
            SELECT gp.id, gp.player_id, gp.character_id, gp.spec_id,
                   gp.hero_talent_id, gp.bis_source_id, gp.is_active,
                   gp.simc_profile, gp.simc_imported_at, gp.equipped_source,
                   s.name AS spec_name,
                   ht.name AS hero_talent_name,
                   bls.name AS bis_source_name,
                   wc.last_equipment_sync AS blizzard_synced_at,
                   c.name AS class_name,
                   s.name AS spec_name_for_stat
              FROM guild_identity.gear_plans gp
              LEFT JOIN ref.specializations s ON s.id = gp.spec_id
              LEFT JOIN ref.hero_talents ht ON ht.id = gp.hero_talent_id
              LEFT JOIN ref.bis_list_sources bls ON bls.id = gp.bis_source_id
              LEFT JOIN guild_identity.wow_characters wc ON wc.id = gp.character_id
              LEFT JOIN ref.classes c ON c.id = wc.class_id
             WHERE gp.player_id = $1 AND gp.character_id = $2
            """,
            player_id, character_id,
        )
        if not plan_row:
            return None

        plan_id = plan_row["id"]
        spec_id = plan_row["spec_id"]
        hero_talent_id = plan_row["hero_talent_id"]
        bis_source_id = plan_row["bis_source_id"]

        # Equipped gear
        equip_rows = await conn.fetch(
            """
            SELECT ce.slot, ce.blizzard_item_id, ce.item_name, ce.item_level,
                   ce.quality_track, ce.enchant_id, ce.gem_ids, ce.bonus_ids,
                   ei.icon_url, ei.slot_type
              FROM guild_identity.character_equipment ce
              LEFT JOIN enrichment.items ei ON ei.blizzard_item_id = ce.blizzard_item_id
             WHERE ce.character_id = $1
            """,
            character_id,
        )
        equipped_by_slot: dict[str, dict] = {}
        for r in equip_rows:
            d = dict(r)
            d["is_crafted"] = is_crafted_item(d.get("bonus_ids") or [])
            slot_key = r["slot"]
            # Remap legacy 'main_hand' rows not yet updated by migration 0156
            if slot_key == "main_hand":
                st = r.get("slot_type") or ""
                if st in ("two_hand", "ranged"):
                    slot_key = "main_hand_2h"
                elif st == "one_hand":
                    slot_key = "main_hand_1h"
                else:
                    slot_key = "main_hand_2h"
            equipped_by_slot[slot_key] = d

        # Phase 1E.6: If equipped_source='simc' and a stored simc_profile exists,
        # parse it and override equipped_by_slot with SimC-sourced gear data.
        # item_level is not available in SimC profiles so it shows as null in the UI.
        equipped_source = plan_row["equipped_source"] or "blizzard"
        simc_profile_text = plan_row["simc_profile"]

        if equipped_source == "simc" and simc_profile_text:
            simc_slots = parse_gear_slots(simc_profile_text)
            if simc_slots:
                simc_bids = [s.blizzard_item_id for s in simc_slots if s.blizzard_item_id]
                simc_item_cache: dict[int, dict] = {}
                if simc_bids:
                    simc_item_rows = await conn.fetch(
                        """
                        SELECT blizzard_item_id, name, icon_url
                          FROM enrichment.items
                         WHERE blizzard_item_id = ANY($1::int[])
                        """,
                        simc_bids,
                    )
                    simc_item_cache = {r["blizzard_item_id"]: dict(r) for r in simc_item_rows}

                simc_equipped: dict[str, dict] = {}
                for simc_slot in simc_slots:
                    slot_key = simc_slot.slot
                    bid = simc_slot.blizzard_item_id
                    item_info = simc_item_cache.get(bid, {})
                    is_crafted = is_crafted_item(simc_slot.bonus_ids)
                    simc_equipped[slot_key] = {
                        "slot": slot_key,
                        "blizzard_item_id": bid,
                        "item_name": item_info.get("name") or f"Item {bid}",
                        "item_level": None,
                        "quality_track": simc_slot.quality_track,
                        "enchant_id": simc_slot.enchant_id,
                        "gem_ids": simc_slot.gem_ids,
                        "bonus_ids": simc_slot.bonus_ids,
                        "icon_url": item_info.get("icon_url"),
                        "is_crafted": is_crafted,
                    }

                # Cross-reference quality_track from Blizzard API data using
                # two complementary strategies:
                #
                # Strategy A — exact item match: if the same blizzard_item_id
                #   exists in character_equipment with quality_track set (from
                #   Blizzard display_string detection), borrow it directly.
                #
                # Strategy B — bonus_id pattern learning: build a map of
                #   bonus_id → quality_track from all character_equipment rows
                #   that DO have quality_track.  Midnight Season uses new bonus
                #   IDs not yet in _DEFAULT_SIMC_BONUS_IDS; this discovers them
                #   empirically from the items we already know the quality of.
                #   For each SimC item, scan its bonus_ids against this map.
                #   First match wins (quality tier IDs are globally unique in WoW).
                blizzard_track_by_bid: dict[int, str] = {}
                bonus_id_to_track: dict[int, str] = {}
                for r in equip_rows:
                    qt = r["quality_track"]
                    if not qt:
                        continue
                    bid = r["blizzard_item_id"]
                    if bid:
                        blizzard_track_by_bid[bid] = qt
                    for bonus_id in (r.get("bonus_ids") or []):
                        if bonus_id not in bonus_id_to_track:
                            bonus_id_to_track[bonus_id] = qt

                for item_data in simc_equipped.values():
                    if item_data.get("quality_track") is not None:
                        continue
                    bid = item_data.get("blizzard_item_id")
                    # Strategy A: exact item match
                    if bid and bid in blizzard_track_by_bid:
                        item_data["quality_track"] = blizzard_track_by_bid[bid]
                        continue
                    # Strategy B: scan SimC bonus_ids for known quality markers
                    for bonus_id in (item_data.get("bonus_ids") or []):
                        if bonus_id in bonus_id_to_track:
                            item_data["quality_track"] = bonus_id_to_track[bonus_id]
                            break

                equipped_by_slot = simc_equipped

        # Phase 1F: trinket tier ratings map — all ratings for this spec, used to
        # stamp tier_badge on equipped items and source_ratings on BIS recs.
        # Deduplicates by (origin, tier) so 3 identical Wowhead rows show as one.
        _tb_map: dict[int, list[dict]] = {}
        if spec_id:
            _tb_rows = await conn.fetch(
                """
                SELECT tr.tier, tr.source_id, bls.origin AS source_origin,
                       tr.blizzard_item_id
                  FROM enrichment.trinket_ratings tr
                  JOIN ref.bis_list_sources bls ON bls.id = tr.source_id
                 WHERE tr.spec_id = $1
                   AND (tr.hero_talent_id = $2 OR tr.hero_talent_id IS NULL)
                   AND bls.is_active = TRUE
                 ORDER BY tr.sort_order
                """,
                spec_id, hero_talent_id,
            )
            _tb_seen: set = set()
            for r in _tb_rows:
                _bid = r["blizzard_item_id"]
                _key = (_bid, r["source_origin"], r["tier"])
                if _key not in _tb_seen:
                    _tb_seen.add(_key)
                    _tb_map.setdefault(_bid, []).append({
                        "source_id": r["source_id"],
                        "source_origin": r["source_origin"],
                        "tier": r["tier"],
                    })
            for _ts in ("trinket_1", "trinket_2"):
                if _ts in equipped_by_slot:
                    _eq = equipped_by_slot[_ts]
                    _eq["tier_badge"] = _tb_map.get(_eq.get("blizzard_item_id")) or None

        # Build bid → equipment data lookup BEFORE _normalize_paired_slot swaps
        # ring/trinket slot assignments.  Crafted detection must be slot-order-
        # independent: after normalization, ring_1 equipped item may have been
        # swapped with ring_2, so comparing equipped[slot].bid == desired_bid
        # would silently fail for the swapped slot.
        equipped_data_by_bid: dict[int, dict] = {}
        for eq_item in equip_rows:
            bid = eq_item.get("blizzard_item_id")
            if bid and bid not in equipped_data_by_bid:
                _bonus_ids = eq_item.get("bonus_ids") or []
                equipped_data_by_bid[bid] = {
                    "bonus_ids": _bonus_ids,
                    "item_level": eq_item.get("item_level"),
                    "is_crafted": is_crafted_item(_bonus_ids),
                }

        # Desired items (plan slots) — include excluded_item_ids for Phase 1E.5
        desired_rows = await conn.fetch(
            """
            SELECT gps.slot, gps.blizzard_item_id,
                   COALESCE(ei.name, gps.item_name) AS item_name,
                   gps.is_locked,
                   gps.excluded_item_ids,
                   ei.icon_url
              FROM guild_identity.gear_plan_slots gps
              LEFT JOIN enrichment.items ei ON ei.blizzard_item_id = gps.blizzard_item_id
             WHERE gps.plan_id = $1
            """,
            plan_id,
        )
        # Track which desired blizzard_item_ids are craftable (Wowhead tooltip has
        # "Random Stat" — crafted items have random secondary stats, drops do not).
        # Also track tier piece desired bids (tooltip has /item-set=) — these use
        # v_tier_piece_sources for source display instead of item_sources directly.
        # Strip wowhead_tooltip_html from the row before storing in desired_by_slot
        # to keep it out of the API response payload.
        craftable_desired_bids: set[int] = set()
        tier_piece_desired_bids: set[int] = set()
        desired_by_slot: dict[str, dict] = {}
        excluded_ids_by_slot: dict[str, list[int]] = {}
        for r in desired_rows:
            desired_by_slot[r["slot"]] = dict(r)
            excluded_ids_by_slot[r["slot"]] = list(r["excluded_item_ids"] or [])

        # Batch-fetch names/icons for excluded items so drawers can display them.
        # excluded_item_ids now stores blizzard_item_id values (migrated in 0144).
        all_excluded_bids: list[int] = [
            bid for bids in excluded_ids_by_slot.values() for bid in bids
        ]
        excluded_item_info: dict[int, dict] = {}
        if all_excluded_bids:
            ex_rows = await conn.fetch(
                """
                SELECT blizzard_item_id, name, icon_url
                  FROM enrichment.items
                 WHERE blizzard_item_id = ANY($1::int[])
                """,
                all_excluded_bids,
            )
            excluded_item_info = {r["blizzard_item_id"]: dict(r) for r in ex_rows}

        # exclusions are already blizzard_item_ids — no translation needed
        excluded_bids_by_slot: dict[str, set[int]] = {
            _sl: set(_ids) for _sl, _ids in excluded_ids_by_slot.items()
        }

        # BIS recommendations for this spec + hero_talent
        bis_by_slot: dict[str, list[dict]] = {}
        if spec_id:
            bis_rows = await conn.fetch(
                """
                SELECT vbr.slot, vbr.source_id, vbr.hero_talent_id, vbr.guide_order,
                       vbr.blizzard_item_id, vbr.name AS item_name, vbr.icon_url,
                       vbr.source_name, vbr.source_short_label AS short_label,
                       vbr.source_origin AS origin, vbr.content_type
                  FROM viz.bis_recommendations vbr
                  JOIN ref.bis_list_sources bls ON bls.id = vbr.source_id
                 WHERE vbr.spec_id = $1
                   AND ($2::int IS NULL OR vbr.hero_talent_id = $2 OR vbr.hero_talent_id IS NULL)
                   AND bls.is_active = TRUE
                 ORDER BY bls.sort_order, vbr.slot, vbr.guide_order
                """,
                spec_id, hero_talent_id,
            )
            for r in bis_rows:
                bis_by_slot.setdefault(r["slot"], []).append(dict(r))

        # Fetch item sources (instance/boss) for BIS items — shown in drawer list rows
        bis_sources_by_bid: dict[int, list[dict]] = {}
        all_bis_bids = list({r["blizzard_item_id"] for rlist in bis_by_slot.values() for r in rlist})
        if all_bis_bids:
            bsr = await conn.fetch(
                """
                SELECT es.blizzard_item_id, es.instance_type,
                       es.instance_name, es.encounter_name
                  FROM enrichment.item_sources es
                 WHERE es.blizzard_item_id = ANY($1::int[])
                   AND NOT es.is_junk
                """,
                all_bis_bids,
            )
            for r in bsr:
                bid = r["blizzard_item_id"]
                entry = {
                    "instance_type": r["instance_type"],
                    "instance_name": r["instance_name"] or "",
                    "encounter_name": r["encounter_name"] or "",
                }
                lst = bis_sources_by_bid.setdefault(bid, [])
                if entry not in lst:
                    lst.append(entry)

        # Popularity data for BIS items — keyed by bid -> {overall, raid, mythic_plus}
        # Aggregate across paired slots (ring_1/ring_2, trinket_1/trinket_2) so the
        # same item always shows the same % regardless of which slot it appears in.
        bis_popularity: dict[int, dict] = {}
        if all_bis_bids and spec_id:
            _bis_slots: set[str] = set()
            for sl in bis_by_slot:
                _bis_slots.add(sl)
                paired = _SLOT_META.get(sl, {}).get("paired_slot")
                if paired:
                    _bis_slots.add(paired)
            # Include legacy 'main_hand' so existing DB rows (pre-Phase 2 re-sync)
            # still match; new rows use typed slots after rebuild_item_popularity runs.
            if _bis_slots & _WEAPON_MH_SLOTS:
                _bis_slots.add("main_hand")
            _bis_slots_list = list(_bis_slots)
            pop_rows = await conn.fetch(
                """
                SELECT blizzard_item_id, content_type,
                       ROUND(SUM(count)::NUMERIC / NULLIF(SUM(total), 0) * 100, 2) AS popularity_pct
                  FROM enrichment.item_popularity ip
                  JOIN ref.bis_list_sources src ON src.id = ip.source_id
                 WHERE ip.spec_id = $1
                   AND ip.blizzard_item_id = ANY($2::int[])
                   AND ip.slot = ANY($3::text[])
                 GROUP BY blizzard_item_id, content_type
                UNION ALL
                SELECT blizzard_item_id, 'overall',
                       ROUND(SUM(count)::NUMERIC / NULLIF(SUM(total), 0) * 100, 2)
                  FROM enrichment.item_popularity
                 WHERE spec_id = $1
                   AND blizzard_item_id = ANY($2::int[])
                   AND slot = ANY($3::text[])
                 GROUP BY blizzard_item_id
                """,
                spec_id, list(all_bis_bids), _bis_slots_list,
            )
            for pr in pop_rows:
                if pr["popularity_pct"] is not None:
                    bis_popularity.setdefault(pr["blizzard_item_id"], {})[pr["content_type"]] = float(pr["popularity_pct"])

        # Collect all blizzard item IDs for source/track lookup
        all_bids: set[int] = set()
        for rows in bis_by_slot.values():
            for r in rows:
                all_bids.add(r["blizzard_item_id"])
        for d in desired_by_slot.values():
            if d.get("blizzard_item_id"):
                all_bids.add(d["blizzard_item_id"])

        # Augment craftable/tier detection via DB for items where Wowhead has no
        # tooltip data (new expansion items).  These run only when desired_by_slot
        # has entries to avoid unnecessary queries on empty plans.
        desired_bids_list = [
            d["blizzard_item_id"] for d in desired_by_slot.values()
            if d.get("blizzard_item_id")
        ]
        if desired_bids_list:
            # Craftable: any desired item that has a row in item_recipe_links
            link_rows = await conn.fetch(
                "SELECT DISTINCT blizzard_item_id FROM enrichment.item_recipes WHERE blizzard_item_id = ANY($1::int[])",
                desired_bids_list,
            )
            craftable_desired_bids |= {r["blizzard_item_id"] for r in link_rows}

            # Tier piece: desired item has item_category='tier' in enrichment
            tier_candidate_rows = await conn.fetch(
                "SELECT blizzard_item_id FROM enrichment.items WHERE blizzard_item_id = ANY($1::int[]) AND item_category = 'tier'",
                desired_bids_list,
            )
            tier_piece_desired_bids |= {r["blizzard_item_id"] for r in tier_candidate_rows}

        # Available quality tracks per blizzard_item_id (derived from source_config)
        tracks_by_item: dict[int, list[str]] = {}
        # Source location info for display
        sources_by_item: dict[int, list[dict]] = {}
        if all_bids:
            src_rows = await conn.fetch(
                """
                SELECT blizzard_item_id, instance_type, encounter_name,
                       instance_name, quality_tracks
                  FROM enrichment.item_sources
                 WHERE blizzard_item_id = ANY($1::int[])
                   AND NOT is_junk
                """,
                list(all_bids),
            )
            for r in src_rows:
                bid = r["blizzard_item_id"]
                inst_type = r["instance_type"]
                tracks = list(r["quality_tracks"] or [])
                existing_tracks = tracks_by_item.get(bid, [])
                merged = sorted(
                    set(existing_tracks) | set(tracks),
                    key=lambda t: TRACK_ORDER.get(t, 99),
                )
                tracks_by_item[bid] = merged
                sources_by_item.setdefault(bid, []).append({
                    "instance_type": inst_type,
                    "encounter_name": r["encounter_name"],
                    "instance_name": r["instance_name"],
                    "display_name": _get_display_name(r["instance_name"] or "", inst_type),
                    "track_label": _get_track_label(inst_type),
                })

        # Tier piece source lookup via v_tier_piece_sources.
        # Only runs when the player has tier piece items as their desired goal.
        # The view resolves: tier piece → matching token → token's boss source.
        # If the view doesn't exist yet (pre-migration or process_tier_tokens not
        # yet run), the query returns no rows — tier piece slots will fall through
        # to the existing item_sources data (which may be junk-flagged empty).
        if tier_piece_desired_bids:
            try:
                tier_src_rows = await conn.fetch(
                    """
                    SELECT tier_piece_blizzard_id AS blizzard_item_id,
                           instance_type,
                           boss_name AS encounter_name,
                           instance_name
                      FROM viz.tier_piece_sources
                     WHERE tier_piece_blizzard_id = ANY($1::int[])
                    """,
                    list(tier_piece_desired_bids),
                )
                for r in tier_src_rows:
                    bid = r["blizzard_item_id"]
                    inst_type = r["instance_type"]
                    tracks = _get_tracks(inst_type)
                    existing_tracks = tracks_by_item.get(bid, [])
                    merged = sorted(
                        set(existing_tracks) | set(tracks),
                        key=lambda t: TRACK_ORDER.get(t, 99),
                    )
                    tracks_by_item[bid] = merged
                    # Avoid duplicate source entries (same boss may appear via multiple
                    # token paths for 'any' slot/armor tokens like Chiming Void Curio).
                    existing_sources = sources_by_item.get(bid, [])
                    src_entry = {
                        "instance_type": inst_type,
                        "encounter_name": r["encounter_name"],
                        "instance_name": r["instance_name"],
                        "display_name": _get_display_name(r["instance_name"] or "", inst_type),
                        "track_label": _get_track_label(inst_type),
                    }
                    if src_entry not in existing_sources:
                        sources_by_item.setdefault(bid, []).append(src_entry)
            except Exception as exc:
                # View may not exist if migration hasn't run yet — degrade gracefully.
                logger.warning("viz.tier_piece_sources lookup failed: %s", exc)

        # Available BIS sources (for UI dropdowns)
        source_list = await conn.fetch(
            """
            SELECT id, name, short_label, content_type, origin, is_default, sort_order
              FROM ref.bis_list_sources
             WHERE is_active = TRUE
             ORDER BY sort_order
            """
        )

        # Which sources have hero-talent-specific BIS entries
        ht_source_ids = await conn.fetchval(
            "SELECT array_agg(DISTINCT source_id) FROM enrichment.bis_entries WHERE hero_talent_id IS NOT NULL"
        ) or []

        # Hero talents for the plan's spec (for UI dropdown)
        ht_list = []
        if spec_id:
            ht_rows = await conn.fetch(
                "SELECT id, name, slug FROM ref.hero_talents WHERE spec_id=$1 ORDER BY name",
                spec_id,
            )
            ht_list = [dict(r) for r in ht_rows]

        # Crafter lookup for craftable desired items.
        # Joins item_recipe_links → recipes → professions → character_recipes →
        # wow_characters → player_characters → players → guild_ranks.
        # DISTINCT on (item, character) so a character with multiple recipes for
        # the same item isn't double-counted.
        # Sorted by rank level DESC (Guild Leader first), then character name ASC.
        crafted_info_by_bid: dict[int, dict] = {}
        if craftable_desired_bids:
            crafter_rows = await conn.fetch(
                """
                SELECT blizzard_item_id, profession_name, character_name, rank_level,
                       COUNT(*) OVER (PARTITION BY blizzard_item_id) AS total_crafters
                  FROM viz.crafters_by_item
                 WHERE blizzard_item_id = ANY($1::int[])
                """,
                list(craftable_desired_bids),
            )
            bid_rows_map: dict[int, list] = {}
            for r in crafter_rows:
                bid_rows_map.setdefault(r["blizzard_item_id"], []).append(dict(r))
            for bid, rows in bid_rows_map.items():
                crafted_info_by_bid[bid] = {
                    "profession": rows[0]["profession_name"],
                    "crafters": [r["character_name"] for r in rows[:5]],
                    "total_crafters": rows[0]["total_crafters"],
                }

        # Phase 2C: load ilvl maps from active season for target_ilvl computation.
        season_ilvl_row = await conn.fetchrow(
            """SELECT quality_ilvl_map, crafted_ilvl_map
                 FROM patt.raid_seasons WHERE is_active = TRUE LIMIT 1"""
        )
        plan_quality_ilvl_map: dict = {}
        plan_crafted_ilvl_map: dict = {}
        if season_ilvl_row:
            plan_quality_ilvl_map = json.loads(season_ilvl_row["quality_ilvl_map"] or "{}")
            plan_crafted_ilvl_map = json.loads(season_ilvl_row["crafted_ilvl_map"] or "{}")
        # (plan_crafted_max_ilvl removed — now computed per slot via _crafted_target_ilvl)

    # Normalize ring and trinket pairs: swap equipped items to maximise BIS matches
    # and ensure consistent alphabetical ordering when no match exists.
    # slot_remapping tracks any visual→DB slot swaps so the frontend can write
    # to the correct DB slot when the user changes a goal in the detail panel.
    slot_remapping: dict[str, str] = {}
    _normalize_paired_slot("ring_1", "ring_2", equipped_by_slot, desired_by_slot, bis_by_slot, bis_source_id, slot_remapping)
    _normalize_paired_slot("trinket_1", "trinket_2", equipped_by_slot, desired_by_slot, bis_by_slot, bis_source_id, slot_remapping)

    # Merge BIS pools for paired slots so each slot's drawer shows all possible
    # ring (or trinket) items, not just those scraped under that specific slot key.
    _merge_paired_bis(bis_by_slot, "ring_1", "ring_2")
    _merge_paired_bis(bis_by_slot, "trinket_1", "trinket_2")

    # Weapon build display rules: determine active main-hand slot and off-hand visibility.
    weapon_build, show_off_hand = _compute_weapon_display(
        bis_by_slot, equipped_by_slot, desired_by_slot
    )

    # Build per-slot data
    slots_data: dict[str, dict] = {}
    for slot in _SLOTS_ORDERED:
        equipped = equipped_by_slot.get(slot)
        desired = desired_by_slot.get(slot)

        # Phase 1E.5: per-slot exclusions filter BIS recommendations
        excluded_ids = excluded_ids_by_slot.get(slot, [])
        excluded_items = [
            excluded_item_info[id_]
            for id_ in excluded_ids
            if id_ in excluded_item_info
        ]
        bis_recs = [
            r for r in bis_by_slot.get(slot, [])
            if r["blizzard_item_id"] not in excluded_bids_by_slot.get(slot, set())
        ]

        # Effective desired blizzard_item_id — only from explicit gear_plan_slots.
        # We deliberately do NOT fall back to BIS recommendations here: for paired
        # slots (rings/trinkets), after _merge_paired_bis both slots share the same
        # merged pool, so the fallback would assign the same "implied" item to both
        # slots and produce bogus is_bis / needs_upgrade flags.
        desired_bid: Optional[int] = desired["blizzard_item_id"] if desired else None

        available_tracks = tracks_by_item.get(desired_bid, []) if desired_bid else []
        item_sources = sources_by_item.get(desired_bid, []) if desired_bid else []

        # Craftable items have no item_sources rows (they're not drops), so
        # available_tracks is normally empty.  Override to ["H", "M"] — crafted
        # gear in Midnight can always be made at Hero or Mythic crest quality.
        if desired_bid and desired_bid in craftable_desired_bids and not available_tracks:
            available_tracks = ["H", "M"]

        equipped_track = equipped["quality_track"] if equipped else None
        equipped_ilvl_for_slot: Optional[int] = equipped.get("item_level") if equipped else None

        # For equipped crafted items whose quality_track wasn't detected during sync
        # (e.g. pre-fix rows with quality_track=NULL), compute it now from bonus_ids.
        # detect_crafted_track() uses track_from_bonus_ids; if those IDs aren't in
        # the map it falls through to the ilvl threshold then defaults to "H".
        # Write the computed track back into the equipped dict so the frontend
        # receives the correct value rather than null.
        if equipped and equipped_track is None and equipped.get("is_crafted"):
            equipped_track = detect_crafted_track(
                bonus_ids=equipped.get("bonus_ids") or [],
            )
            if equipped_track:
                equipped["quality_track"] = equipped_track

        equipped_bid = equipped["blizzard_item_id"] if equipped else None
        upgrade_tracks = _upgrade_tracks(equipped_track, equipped_bid, desired_bid, available_tracks)

        is_bis = bool(desired_bid and equipped_bid and equipped_bid == desired_bid)

        # Phase 2C: compute slot-level target ilvls (same rules as available-items endpoint).
        slot_noncrafted_ilvl = _noncrafted_target_ilvl(
            is_bis, equipped_ilvl_for_slot, equipped_track, plan_quality_ilvl_map
        )
        slot_crafted_ilvl = _crafted_target_ilvl(
            is_bis, equipped_track, plan_crafted_ilvl_map
        )

        # Paired-slot BIS: rings and trinkets share desired items — mark both as BIS
        _paired_slot = _SLOT_META[slot]["paired_slot"]
        _paired_desired_bid: Optional[int] = None
        if _paired_slot:
            _pd = desired_by_slot.get(_paired_slot)
            _paired_desired_bid = _pd["blizzard_item_id"] if _pd else None
        _all_desired_bids: set[int] = {b for b in (desired_bid, _paired_desired_bid) if b}

        for rec in bis_recs:
            bid = rec["blizzard_item_id"]
            if bid in craftable_desired_bids:
                rec["target_ilvl"] = slot_crafted_ilvl
            else:
                rec["target_ilvl"] = slot_noncrafted_ilvl
            # Phase 1F: EQUIPPED / BIS badges on BIS recommendations
            rec["is_equipped"] = bool(equipped_bid and bid == equipped_bid)
            rec["is_bis"]      = bool(_all_desired_bids and bid in _all_desired_bids)
            # Phase 1F: trinket tier badge on BIS recs (full-spec map fetched above)
            if slot in _TRINKET_SLOTS:
                rec["source_ratings"] = _tb_map.get(bid, [])
            # Item drop sources for drawer list display
            # Tier pieces have no enrichment.item_sources rows — use viz.tier_piece_sources data.
            rec["sources"] = bis_sources_by_bid.get(bid) or sources_by_item.get(bid, [])
            # Popularity percentages from viz.item_popularity
            rec["popularity"] = bis_popularity.get(bid, {})

        if desired and desired_bid:
            if desired_bid in craftable_desired_bids:
                desired["target_ilvl"] = slot_crafted_ilvl
            else:
                desired["target_ilvl"] = slot_noncrafted_ilvl

        # Fallback: if wearing BIS but item has no item_sources data, infer upgrade
        # tracks from the equipped quality track (strictly above current track).
        # Requires a detected quality track; crafted items with null track are excluded
        # by the equipped_track guard rather than a separate crafted check.
        if (is_bis and not upgrade_tracks and equipped_track and equipped_track != "M"):
            eq_idx = TRACK_ORDER.get(equipped_track, -1)
            upgrade_tracks = [
                t for t in ("V", "C", "H", "M") if TRACK_ORDER.get(t, -1) > eq_idx
            ]

        # Red border = has a goal but not wearing it (independent of track data).
        # Green border = wearing the desired/BIS item.
        # No border = no goal set for this slot (no data to compare).
        needs_upgrade = bool(desired_bid and not is_bis)

        # Crafted source block: fires for all desired items detected as craftable
        # (those in craftable_desired_bids via "Random Stat" Wowhead tooltip).
        # Includes profession + top-5 crafters from item_recipe_links lookup.
        crafted_source: Optional[dict] = None
        if desired_bid and desired_bid in craftable_desired_bids:
            info = crafted_info_by_bid.get(desired_bid)
            desired_name = desired["item_name"] if desired else None
            cc_url = (
                f"/crafting-corner?q={desired_name}"
                if desired_name
                else "/crafting-corner"
            )
            if info:
                crafted_source = {
                    "crafting_corner_url": cc_url,
                    "profession": info["profession"],
                    "crafters": info["crafters"],
                    "total_crafters": info["total_crafters"],
                    "no_recipe_found": False,
                }
            else:
                crafted_source = {
                    "crafting_corner_url": cc_url,
                    "profession": None,
                    "crafters": [],
                    "total_crafters": 0,
                    "no_recipe_found": True,
                }

        slots_data[slot] = {
            "slot": slot,
            "canonical_slot": slot_remapping.get(slot, slot),
            "display_name": _SLOT_META[slot]["display_name"],
            "equipped": equipped,
            "desired": desired,
            "desired_blizzard_item_id": desired_bid,
            "bis_recommendations": bis_recs,
            "item_sources": _contextual_sources(item_sources, upgrade_tracks),
            "available_tracks": available_tracks,
            "upgrade_tracks": upgrade_tracks,
            "is_bis": is_bis,
            "needs_upgrade": needs_upgrade,
            "crafted_source": crafted_source,
            "excluded_item_ids": excluded_ids,
            "excluded_items": excluded_items,
        }

    plan_dict = dict(plan_row)
    # Serialize timestamps to ISO strings for JSON
    for ts_field in ("simc_imported_at", "blizzard_synced_at"):
        val = plan_dict.get(ts_field)
        if val is not None:
            plan_dict[ts_field] = val.isoformat()

    return {
        "plan": plan_dict,
        "slots": slots_data,
        "bis_sources": [{**dict(r), "has_hero_talent_variants": r["id"] in ht_source_ids} for r in source_list],
        "hero_talents": ht_list,
        "track_colors": TRACK_COLORS,
        "weapon_build": weapon_build,
        "show_off_hand": show_off_hand,
    }


# ── Available items (Phase 1E.4) ──────────────────────────────────────────────

def _filter_by_primary_stat(items: list[dict], primary_stat: str) -> list[dict]:
    """Filter weapon items by primary stat using the pre-computed primary_stat column.

    Includes the item when uncertain (primary_stat is None).
    """
    result = []
    for item in items:
        ps = item.get("primary_stat")
        if not ps or ps == primary_stat:
            result.append(item)
    return result


async def get_available_items(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    slot: str,
) -> dict:
    """Return class-eligible scanned items grouped by source type.

    Returns {"raid": [...], "dungeon": [...], "crafted": [...]}, each list
    sorted alphabetically. Used to populate the three source sections in the
    gear plan slot drawer.

    Season filtering:
      - raid:    items from instances in patt.raid_seasons.current_raid_ids
      - dungeon: items from instances in patt.raid_seasons.current_instance_ids
      - crafted: items linked via enrichment.item_recipes (inherently current content)

    Excluded items (Phase 1E.5) are omitted from all groups.

    Eligibility rules:
      - Armor slots: filter by character's class armor type (cloth/leather/mail/plate)
      - All slots: filter by spec primary_stat (int/agi/str); items with NULL pass through
      - Accessories (neck/rings): almost all universal (NULL primary_stat), rarely filtered

    Phase D: reads from viz.slot_items (enrichment-backed) instead of guild_identity.*
    directly. item_category discriminates drop/crafted/tier/catalyst.
    """
    empty: dict = {"tier": None, "raid": [], "dungeon": [], "crafted": []}
    if not is_valid_slot(slot):
        return empty

    async with pool.acquire() as conn:
        await _ensure_slot_meta(conn)
        char_row = await conn.fetchrow(
            """
            SELECT c.name AS class_name, s.name AS spec_name,
                   c.blizzard_class_id, gp.spec_id, gp.hero_talent_id,
                   s.primary_stat AS spec_primary_stat
              FROM guild_identity.wow_characters wc
              LEFT JOIN ref.classes c ON c.id = wc.class_id
              LEFT JOIN guild_identity.gear_plans gp
                     ON gp.character_id = wc.id AND gp.player_id = $2
              LEFT JOIN ref.specializations s ON s.id = gp.spec_id
             WHERE wc.id = $1
            """,
            character_id, player_id,
        )
        if not char_row:
            return empty

        class_name     = char_row["class_name"] or ""
        spec_name      = char_row["spec_name"] or ""
        char_class_id: Optional[int] = char_row["blizzard_class_id"]
        avail_spec_id: Optional[int] = char_row["spec_id"]
        avail_ht_id:   Optional[int] = char_row["hero_talent_id"]

        # Load current season filters and ilvl maps (Phase 2C).
        season_row = await conn.fetchrow(
            """SELECT current_instance_ids, current_raid_ids,
                      quality_ilvl_map, crafted_ilvl_map
                 FROM patt.raid_seasons WHERE is_active = TRUE LIMIT 1"""
        )
        raid_ids:    list[int] = []
        dungeon_ids: list[int] = []
        quality_ilvl_map: dict = {}
        crafted_ilvl_map: dict = {}
        if season_row:
            raid_ids    = list(season_row["current_raid_ids"]    or [])
            dungeon_ids = list(season_row["current_instance_ids"] or [])
            # asyncpg returns JSONB as raw strings
            quality_ilvl_map = json.loads(season_row["quality_ilvl_map"] or "{}")
            crafted_ilvl_map = json.loads(season_row["crafted_ilvl_map"] or "{}")

        # Phase 2C: load equipped ilvl, quality track, and blizzard_item_id for this slot
        # so we can compute target_ilvl and determine is_bis.
        equip_row = await conn.fetchrow(
            """
            SELECT item_level, quality_track, blizzard_item_id
              FROM guild_identity.character_equipment
             WHERE character_id = $1 AND slot = $2
            """,
            character_id, slot,
        )
        equipped_ilvl: Optional[int]  = equip_row["item_level"]       if equip_row else None
        equipped_track: Optional[str] = equip_row["quality_track"]    if equip_row else None
        equipped_bid:   Optional[int] = equip_row["blizzard_item_id"] if equip_row else None

        # Phase 1E.5: fetch per-slot exclusions so we can hide excluded items
        excluded_ids: list[int] = []
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        desired_bid_for_slot: Optional[int] = None
        all_desired_bids: set[int] = set()
        if plan_row:
            _av_paired = _SLOT_META[slot]["paired_slot"]
            _av_slots = [slot] + ([_av_paired] if _av_paired else [])
            slot_rows = await conn.fetch(
                """
                SELECT slot, excluded_item_ids, blizzard_item_id
                  FROM guild_identity.gear_plan_slots
                 WHERE plan_id = $1 AND slot = ANY($2::text[])
                """,
                plan_row["id"], _av_slots,
            )
            for sr in slot_rows:
                if sr["slot"] == slot:
                    excluded_ids = list(sr["excluded_item_ids"] or [])
                    desired_bid_for_slot = sr["blizzard_item_id"]
                if sr["blizzard_item_id"]:
                    all_desired_bids.add(sr["blizzard_item_id"])

        # BIS = wearing any desired item from this slot's paired pool.
        is_bis: bool = bool(equipped_bid and all_desired_bids and equipped_bid in all_desired_bids)

        # Normalize paired slots to canonical enrichment.items slot_type
        slot_type = _SLOT_META[slot]["enrichment_slot_type"]

        # Armor type filter — None for accessories/weapons (no class restriction)
        armor_filter: Optional[str] = (
            CLASS_ARMOR_TYPE.get(class_name) if _SLOT_META[slot]["is_armor_slot"] else None
        )

        # excluded_item_ids stores blizzard_item_ids directly (migrated in 0144)
        excluded_blizzard_ids: list[int] = list(excluded_ids)

        # ── viz.slot_items: single query for all item categories (Phase E) ─────
        # The view's item_seasons JOIN already limits results to the active season,
        # so no Python-side season instance ID filter is needed.
        # item_category discriminates: 'raid'/'dungeon', 'crafted',
        # 'tier' (5-slot tier token pieces), 'catalyst' (back/wrist/waist/feet set pieces).
        # quality_tracks is pre-computed by the enrichment sproc.
        viz_rows = await conn.fetch(
            """
            SELECT blizzard_item_id, name, icon_url, item_category,
                   tier_set_suffix, instance_type, encounter_name, instance_name,
                   blizzard_instance_id, quality_tracks, primary_stat, armor_type,
                   CASE WHEN item_category = 'crafted' THEN (
                       SELECT p.name
                         FROM enrichment.item_recipes ir
                         JOIN guild_identity.recipes r ON r.id = ir.recipe_id
                         JOIN guild_identity.professions p ON p.id = r.profession_id
                        WHERE ir.blizzard_item_id = viz.slot_items.blizzard_item_id
                        ORDER BY ir.confidence DESC NULLS LAST
                        LIMIT 1
                   ) END AS profession_name
              FROM viz.slot_items
             WHERE slot_type = $1
               AND ($2::text IS NULL OR armor_type = $2 OR armor_type IS NULL)
               AND item_category IN ('raid', 'dungeon', 'crafted', 'tier', 'catalyst')
               AND NOT (blizzard_item_id = ANY($3::int[]))
               AND (item_category NOT IN ('tier', 'catalyst')
                    OR $4::int IS NULL
                    OR playable_class_ids IS NULL
                    OR $4 = ANY(playable_class_ids))
               AND (
                   slot_type NOT IN ('one_hand', 'two_hand', 'ranged', 'off_hand')
                   OR $4::int IS NULL
                   OR weapon_subtype IS NULL
                   OR EXISTS (
                       SELECT 1 FROM ref.class_weapon_proficiencies cwp
                        WHERE cwp.blizzard_class_id = $4
                          AND cwp.weapon_subtype = viz.slot_items.weapon_subtype
                   )
               )
            """,
            slot_type,
            armor_filter,
            excluded_blizzard_ids,
            char_class_id,
        )

        # ── Trinket tier badge data (Phase 1F) ────────────────────────────────
        # Fetch ratings from enrichment.trinket_ratings (replaces guild_identity.trinket_tier_ratings)
        trinket_ratings_by_bid: dict[int, list[dict]] = {}
        if slot in ("trinket_1", "trinket_2") and avail_spec_id:
            t_rows = await conn.fetch(
                """
                SELECT tr.tier, tr.source_id, bls.origin AS source_origin,
                       tr.blizzard_item_id
                  FROM enrichment.trinket_ratings tr
                  JOIN ref.bis_list_sources bls ON bls.id = tr.source_id
                 WHERE tr.spec_id = $1
                   AND (tr.hero_talent_id = $2 OR tr.hero_talent_id IS NULL)
                   AND bls.is_active = TRUE
                 ORDER BY tr.sort_order
                """,
                avail_spec_id, avail_ht_id,
            )
            _seen_tr: set = set()
            for r in t_rows:
                bid = r["blizzard_item_id"]
                key = (bid, r["source_origin"], r["tier"])
                if key not in _seen_tr:
                    _seen_tr.add(key)
                    trinket_ratings_by_bid.setdefault(bid, []).append({
                        "source_id": r["source_id"],
                        "source_origin": r["source_origin"],
                        "tier": r["tier"],
                    })

        # Popularity data: aggregate across paired slots (trinket_1+trinket_2, ring_1+ring_2)
        # so both sections always show the same combined number.
        _pop_paired = _SLOT_META[slot]["paired_slot"]
        _pop_slots_set: set[str] = {slot} | ({_pop_paired} if _pop_paired else set())
        # Include legacy 'main_hand' for existing DB rows pre-Phase 2 popularity re-sync
        if _pop_slots_set & _WEAPON_MH_SLOTS:
            _pop_slots_set.add("main_hand")
        _pop_slots: list[str] = list(_pop_slots_set)
        pop_by_bid: dict[int, dict] = {}
        if avail_spec_id:
            pop_rows = await conn.fetch(
                """
                SELECT blizzard_item_id, content_type,
                       ROUND(SUM(count)::NUMERIC / NULLIF(SUM(total), 0) * 100, 2) AS popularity_pct
                  FROM enrichment.item_popularity ip
                  JOIN ref.bis_list_sources src ON src.id = ip.source_id
                 WHERE ip.spec_id = $1 AND ip.slot = ANY($2::text[])
                 GROUP BY blizzard_item_id, content_type
                UNION ALL
                SELECT blizzard_item_id, 'overall',
                       ROUND(SUM(count)::NUMERIC / NULLIF(SUM(total), 0) * 100, 2)
                  FROM enrichment.item_popularity
                 WHERE spec_id = $1 AND slot = ANY($2::text[])
                 GROUP BY blizzard_item_id
                """,
                avail_spec_id, _pop_slots,
            )
            for pr in pop_rows:
                if pr["popularity_pct"] is not None:
                    pop_by_bid.setdefault(pr["blizzard_item_id"], {})[pr["content_type"]] = float(pr["popularity_pct"])

        # Tier piece boss sources (only relevant for 5-piece tier slots)
        tier_sources_by_bid: dict[int, list[dict]] = {}
        if _SLOT_META[slot]["is_tier_catalyst_slot"]:
            ts_rows = await conn.fetch(
                """
                SELECT DISTINCT tier_piece_blizzard_id, instance_type, boss_name, instance_name
                  FROM viz.tier_piece_sources
                 WHERE slot_type = $1
                """,
                slot_type,
            )
            for ts in ts_rows:
                tier_sources_by_bid.setdefault(ts["tier_piece_blizzard_id"], []).append({
                    "instance_type":   ts["instance_type"],
                    "source_name":     ts["boss_name"],
                    "source_instance": ts["instance_name"],
                })

    # ── Group viz rows by item_category ───────────────────────────────────────
    raid_map:      dict[int, dict] = {}
    dungeon_map:   dict[int, dict] = {}
    crafted_seen:  set[int] = set()
    crafted_items: list[dict] = []
    tier_seen:     set[int] = set()
    tier_rows:     list[dict] = []

    for r in viz_rows:
        bid   = r["blizzard_item_id"]
        cat   = r["item_category"]
        itype = r["instance_type"]

        if cat in ("raid", "dungeon"):
            target = raid_map if cat == "raid" else dungeon_map
            if bid not in target:
                target[bid] = {
                    "blizzard_item_id": bid,
                    "name": r["name"],
                    "icon_url": r["icon_url"],
                    "primary_stat": r["primary_stat"],
                    "sources": [],
                    "popularity": pop_by_bid.get(bid, {}),
                }
            tracks = list(r["quality_tracks"] or [])
            src = {
                "source_name":     r["encounter_name"],
                "source_instance": r["instance_name"],
                "instance_type":   itype,
                "quality_tracks":  tracks,
            }
            if src not in target[bid]["sources"]:
                target[bid]["sources"].append(src)

        elif cat == "crafted":
            if bid not in crafted_seen:
                crafted_seen.add(bid)
                crafted_items.append({
                    "blizzard_item_id": bid,
                    "name": r["name"],
                    "icon_url": r["icon_url"],
                    "primary_stat": r["primary_stat"],
                    "profession_name": r["profession_name"],
                    "popularity": pop_by_bid.get(bid, {}),
                })

        elif cat in ("tier", "catalyst"):
            if bid not in tier_seen:
                tier_seen.add(bid)
                if cat == "catalyst":
                    sources: list[dict] = [{"instance_type": "catalyst"}]
                else:
                    sources = tier_sources_by_bid.get(bid, [])
                tier_rows.append({
                    "blizzard_item_id": bid,
                    "name": r["name"],
                    "icon_url": r["icon_url"],
                    "popularity": pop_by_bid.get(bid, {}),
                    "sources": sources,
                })

    raid_items    = list(raid_map.values())
    dungeon_items = list(dungeon_map.values())

    # Apply primary-stat filter — items with NULL primary_stat always pass through
    primary_stat_filter: Optional[str] = char_row["spec_primary_stat"]
    if primary_stat_filter:
        raid_items    = _filter_by_primary_stat(raid_items, primary_stat_filter)
        dungeon_items = _filter_by_primary_stat(dungeon_items, primary_stat_filter)
        crafted_items = _filter_by_primary_stat(crafted_items, primary_stat_filter)

    # Strip primary_stat from items before returning (internal filter field)
    for item in raid_items + dungeon_items + crafted_items:
        item.pop("primary_stat", None)

    # ── Phase 2C: compute target_ilvl per item ─────────────────────────────────
    noncrafted_ilvl = _noncrafted_target_ilvl(is_bis, equipped_ilvl, equipped_track, quality_ilvl_map)
    crafted_ilvl    = _crafted_target_ilvl(is_bis, equipped_track, crafted_ilvl_map)

    for item in raid_items + dungeon_items:
        item["target_ilvl"] = noncrafted_ilvl

    for item in crafted_items:
        item["target_ilvl"] = crafted_ilvl

    # ── Tier / Catalyst items ──────────────────────────────────────────────────
    # None means "this slot has no tier piece" — frontend hides the section.
    tier_items: Optional[list[dict]] = None
    if _SLOT_META[slot]["is_tier_catalyst_slot"]:
        tier_target: Optional[int] = noncrafted_ilvl
        tier_items = [
            {
                "blizzard_item_id": r["blizzard_item_id"],
                "name": r["name"],
                "icon_url": r["icon_url"],
                "target_ilvl": tier_target,
                "sources": r.get("sources", []),
            }
            for r in tier_rows
        ]

    # Phase 1F: add is_equipped, is_bis, and (for trinkets) source_ratings per item
    for item in raid_items + dungeon_items + crafted_items + (tier_items or []):
        bid = item.get("blizzard_item_id")
        item["is_equipped"] = bool(bid and bid == equipped_bid)
        item["is_bis"]      = bool(bid and bid in all_desired_bids)
        if trinket_ratings_by_bid:
            item["source_ratings"] = trinket_ratings_by_bid.get(bid, [])

    return {
        "tier":    tier_items,
        "raid":    raid_items,
        "dungeon": dungeon_items,
        "crafted": crafted_items,
    }


# ── Item exclusion (Phase 1E.5) ───────────────────────────────────────────────

async def add_exclusion(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    slot: str,
    blizzard_item_id: int,
) -> bool:
    """Append an item to the excluded_item_ids list for a gear plan slot.

    Creates the slot row if it doesn't exist.
    No-ops if the item is already excluded.
    Returns True on success, False if plan not found.
    """
    if not is_valid_slot(slot):
        return False

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return False
        plan_id = plan_row["id"]

        # excluded_item_ids now stores blizzard_item_id values directly (migrated in 0144)
        await conn.execute(
            """
            INSERT INTO guild_identity.gear_plan_slots
                (plan_id, slot, blizzard_item_id, item_name, is_locked, excluded_item_ids)
            VALUES ($1, $2, NULL, NULL, FALSE, ARRAY[$3::int])
            ON CONFLICT (plan_id, slot) DO UPDATE
                SET excluded_item_ids =
                    CASE WHEN $3 = ANY(gear_plan_slots.excluded_item_ids)
                         THEN gear_plan_slots.excluded_item_ids
                         ELSE array_append(gear_plan_slots.excluded_item_ids, $3)
                    END
            """,
            plan_id, slot, blizzard_item_id,
        )
        return True


async def remove_exclusion(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    slot: str,
    blizzard_item_id: int,
) -> bool:
    """Remove an item from the excluded_item_ids list for a gear plan slot.

    No-ops gracefully if the slot row or item doesn't exist.
    Returns True on success, False if plan not found.
    """
    if not is_valid_slot(slot):
        return False

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return False
        plan_id = plan_row["id"]

        # excluded_item_ids stores blizzard_item_id values directly (migrated in 0144)
        await conn.execute(
            """
            UPDATE guild_identity.gear_plan_slots
               SET excluded_item_ids = array_remove(excluded_item_ids, $3)
             WHERE plan_id = $1 AND slot = $2
            """,
            plan_id, slot, blizzard_item_id,
        )
        return True


# ── Trinket ratings (Phase 1F) ────────────────────────────────────────────────

_TRINKET_SLOTS: tuple[str, ...] = ("trinket_1", "trinket_2")
_TIER_ORDER: list[str] = ["S", "A", "B", "C", "D", "F"]


async def get_trinket_ratings(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    slot: str,
) -> Optional[dict]:
    """Return trinket tier ratings for a character's plan spec, grouped by tier.

    Returns None if `slot` is not a trinket slot.  Returns an empty `tiers` list
    if no ratings exist for this spec/slot (spec not set, no data scraped yet).

    Response shape:
        spec_id           — the plan's active spec ID (or None)
        slot              — the requested slot key
        tiers             — list of {tier, items:[...]} in S→F order
        equipped_is_unranked — True when the equipped trinket in this slot has no rating
    """
    if slot not in _TRINKET_SLOTS:
        return None

    async with pool.acquire() as conn:
        await _ensure_slot_meta(conn)
        # Plan spec
        plan_row = await conn.fetchrow(
            """
            SELECT spec_id, hero_talent_id
              FROM guild_identity.gear_plans
             WHERE player_id = $1 AND character_id = $2
            """,
            player_id, character_id,
        )
        if not plan_row or not plan_row["spec_id"]:
            return {"spec_id": None, "slot": slot, "tiers": [], "equipped_is_unranked": False}

        spec_id: int = plan_row["spec_id"]
        hero_talent_id: Optional[int] = plan_row["hero_talent_id"]

        # Equipped trinket items (both slots — EQUIPPED badge fires for either)
        equip_rows = await conn.fetch(
            """
            SELECT slot, blizzard_item_id, item_level, quality_track
              FROM guild_identity.character_equipment
             WHERE character_id = $1 AND slot = ANY($2::text[])
            """,
            character_id, list(_TRINKET_SLOTS),
        )
        equipped_bids: set[int] = {r["blizzard_item_id"] for r in equip_rows if r["blizzard_item_id"]}
        equipped_bid_for_slot: Optional[int] = next(
            (r["blizzard_item_id"] for r in equip_rows if r["slot"] == slot and r["blizzard_item_id"]),
            None,
        )
        equipped_ilvl_for_slot: Optional[int] = next(
            (r["item_level"] for r in equip_rows if r["slot"] == slot and r["item_level"]),
            None,
        )
        equipped_track_for_slot: Optional[str] = next(
            (r["quality_track"] for r in equip_rows if r["slot"] == slot and r["quality_track"]),
            None,
        )

        # Desired items for this slot and its pair (trinkets share BIS pools)
        _tr_paired = _SLOT_META[slot]["paired_slot"]
        _tr_slots = [slot] + ([_tr_paired] if _tr_paired else [])
        gps_rows = await conn.fetch(
            """
            SELECT gps.slot, gps.blizzard_item_id
              FROM guild_identity.gear_plan_slots gps
              JOIN guild_identity.gear_plans gp ON gp.id = gps.plan_id
             WHERE gp.player_id = $1 AND gp.character_id = $2 AND gps.slot = ANY($3::text[])
            """,
            player_id, character_id, _tr_slots,
        )
        desired_bid: Optional[int] = next(
            (r["blizzard_item_id"] for r in gps_rows if r["slot"] == slot and r["blizzard_item_id"]),
            None,
        )
        desired_bids: set[int] = {r["blizzard_item_id"] for r in gps_rows if r["blizzard_item_id"]}

        # Current season instance IDs + ilvl maps for availability check and target_ilvl
        season_row = await conn.fetchrow(
            "SELECT current_instance_ids, current_raid_ids, quality_ilvl_map, crafted_ilvl_map FROM patt.raid_seasons WHERE is_active = TRUE LIMIT 1"
        )
        all_season_ids: list[int] = []
        if season_row:
            all_season_ids = (
                list(season_row["current_raid_ids"]    or []) +
                list(season_row["current_instance_ids"] or [])
            )

        # Parse ilvl maps for target_ilvl computation
        _qmap = season_row["quality_ilvl_map"] if season_row else None
        _cmap = season_row["crafted_ilvl_map"]  if season_row else None
        plan_quality_ilvl_map: dict = json.loads(_qmap) if isinstance(_qmap, str) else (_qmap or {})
        plan_crafted_ilvl_map: dict = json.loads(_cmap) if isinstance(_cmap, str) else (_cmap or {})

        # Tier ratings for this spec
        rows = await conn.fetch(
            """
            SELECT tr.blizzard_item_id, tr.tier, tr.sort_order, tr.source_id,
                   bls.origin AS source_origin, bls.content_type AS source_ct,
                   ei.name, ei.icon_url
              FROM enrichment.trinket_ratings tr
              JOIN ref.bis_list_sources bls ON bls.id = tr.source_id
              JOIN enrichment.items ei ON ei.blizzard_item_id = tr.blizzard_item_id
             WHERE tr.spec_id = $1
               AND (tr.hero_talent_id = $2 OR tr.hero_talent_id IS NULL)
               AND bls.is_active = TRUE
             ORDER BY tr.sort_order, tr.id
            """,
            spec_id, hero_talent_id,
        )

        # Available items: have a source in the current season's instances
        available_bids: set[int] = set()
        if all_season_ids:
            avail_rows = await conn.fetch(
                """
                SELECT DISTINCT tr.blizzard_item_id
                  FROM enrichment.trinket_ratings tr
                  JOIN enrichment.item_sources es ON es.blizzard_item_id = tr.blizzard_item_id
                 WHERE tr.spec_id = $1
                   AND (tr.hero_talent_id = $2 OR tr.hero_talent_id IS NULL)
                   AND es.blizzard_instance_id = ANY($3)
                   AND NOT es.is_junk
                """,
                spec_id, hero_talent_id, all_season_ids,
            )
            available_bids = {r["blizzard_item_id"] for r in avail_rows}

        # Content types and source locations per item from item_sources
        content_rows = await conn.fetch(
            """
            SELECT tr.blizzard_item_id, es.instance_type, es.instance_name, es.encounter_name
              FROM enrichment.trinket_ratings tr
              JOIN enrichment.item_sources es
                   ON es.blizzard_item_id = tr.blizzard_item_id AND NOT es.is_junk
             WHERE tr.spec_id = $1
               AND (tr.hero_talent_id = $2 OR tr.hero_talent_id IS NULL)
            """,
            spec_id, hero_talent_id,
        )
        content_by_bid: dict[int, set[str]] = {}
        sources_by_bid: dict[int, list[dict]] = {}
        for r in content_rows:
            bid = r["blizzard_item_id"]
            content_by_bid.setdefault(bid, set()).add(r["instance_type"])
            src_entry = {
                "instance_type":  r["instance_type"],
                "instance_name":  r["instance_name"]  or "",
                "encounter_name": r["encounter_name"] or "",
            }
            existing = sources_by_bid.setdefault(bid, [])
            if src_entry not in existing:
                existing.append(src_entry)

        # Mark crafted trinkets
        crafted_rows_tr = await conn.fetch(
            """
            SELECT DISTINCT tr.blizzard_item_id
              FROM enrichment.trinket_ratings tr
              JOIN enrichment.item_recipes ir ON ir.blizzard_item_id = tr.blizzard_item_id
             WHERE tr.spec_id = $1
               AND (tr.hero_talent_id = $2 OR tr.hero_talent_id IS NULL)
            """,
            spec_id, hero_talent_id,
        )
        for r in crafted_rows_tr:
            content_by_bid.setdefault(r["blizzard_item_id"], set()).add("crafted")

    # ── Build flat item map with per-origin, per-content-type ratings ─────────
    # ratings shape: {origin: {content_type: {tier, position}}}
    # position = sort_order from the source list (lower = better rank)
    _tier_order_map: dict[str, int] = {t: i for i, t in enumerate(_TIER_ORDER)}

    item_meta: dict[int, dict] = {}   # bid → name/icon
    item_ratings: dict[int, dict] = {}  # bid → {origin: {ct: {tier, position}}}

    for r in rows:
        bid = r["blizzard_item_id"]
        if bid not in item_meta:
            item_meta[bid] = {"name": r["name"] or "", "icon_url": r["icon_url"] or ""}
        origin = r["source_origin"]
        ct = r["source_ct"] or "overall"
        tier = r["tier"]
        pos = r["sort_order"]
        by_origin = item_ratings.setdefault(bid, {})
        by_ct = by_origin.setdefault(origin, {})
        existing = by_ct.get(ct)
        if existing is None or (
            _tier_order_map.get(tier, 99) < _tier_order_map.get(existing["tier"], 99)
            or (tier == existing["tier"] and pos < existing["position"])
        ):
            by_ct[ct] = {"tier": tier, "position": pos}

    rated_bids: set[int] = set(item_meta.keys())
    equipped_is_unranked = bool(equipped_bid_for_slot and equipped_bid_for_slot not in rated_bids)
    crafted_bids: set[int] = {r["blizzard_item_id"] for r in crafted_rows_tr}

    # ── Build flat items list ──────────────────────────────────────────────────
    items_list: list[dict] = []
    for bid, meta in item_meta.items():
        is_bis_item = bool(desired_bids and bid in desired_bids)
        is_crafted_item_v = bid in crafted_bids
        if is_crafted_item_v:
            t_ilvl = _crafted_target_ilvl(
                is_bis_item, equipped_track_for_slot, plan_crafted_ilvl_map
            )
        else:
            t_ilvl = _noncrafted_target_ilvl(
                is_bis_item, equipped_ilvl_for_slot, equipped_track_for_slot, plan_quality_ilvl_map
            )
        items_list.append({
            "blizzard_item_id": bid,
            "name": meta["name"],
            "icon_url": meta["icon_url"],
            "ratings": item_ratings.get(bid, {}),
            "content_types": list(content_by_bid.get(bid, set())),
            "sources": sources_by_bid.get(bid, []),
            "is_equipped": bid in equipped_bids,
            "is_bis": is_bis_item,
            "is_available_this_season": bid in available_bids,
            "target_ilvl": t_ilvl,
        })

    return {
        "spec_id": spec_id,
        "slot": slot,
        "items": items_list,
        "equipped_is_unranked": equipped_is_unranked,
    }
