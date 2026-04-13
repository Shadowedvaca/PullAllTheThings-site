"""Gear Plan service — plan CRUD, BIS population, upgrade computation.

Works with asyncpg pool (raw SQL) for consistency with item_service.py and
the broader guild_sync pattern.  Character ownership must be verified by
the caller before invoking any mutating function.
"""

from __future__ import annotations

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
WOW_SLOTS = [
    "head", "neck", "shoulder", "back", "chest", "wrist",
    "hands", "waist", "legs", "feet",
    "ring_1", "ring_2", "trinket_1", "trinket_2",
    "main_hand", "off_hand",
]

SLOT_DISPLAY = {
    "head": "Head",
    "neck": "Neck",
    "shoulder": "Shoulder",
    "back": "Back",
    "chest": "Chest",
    "wrist": "Wrist",
    "hands": "Hands",
    "waist": "Waist",
    "legs": "Legs",
    "feet": "Feet",
    "ring_1": "Ring 1",
    "ring_2": "Ring 2",
    "trinket_1": "Trinket 1",
    "trinket_2": "Trinket 2",
    "main_hand": "Main Hand",
    "off_hand": "Off Hand",
}

# Quality track ranking (lowest to highest)
TRACK_ORDER: dict[str, int] = {"V": 0, "C": 1, "H": 2, "M": 3}


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

# (class_name, spec_name) → primary stat for weapon item filtering
SPEC_PRIMARY_STAT: dict[tuple[str, str], str] = {
    ("Mage", "Arcane"): "int", ("Mage", "Fire"): "int", ("Mage", "Frost"): "int",
    ("Warlock", "Affliction"): "int", ("Warlock", "Demonology"): "int", ("Warlock", "Destruction"): "int",
    ("Priest", "Discipline"): "int", ("Priest", "Holy"): "int", ("Priest", "Shadow"): "int",
    ("Death Knight", "Blood"): "str", ("Death Knight", "Frost"): "str", ("Death Knight", "Unholy"): "str",
    ("Warrior", "Arms"): "str", ("Warrior", "Fury"): "str", ("Warrior", "Protection"): "str",
    ("Paladin", "Holy"): "int", ("Paladin", "Protection"): "str", ("Paladin", "Retribution"): "str",
    ("Druid", "Balance"): "int", ("Druid", "Feral"): "agi", ("Druid", "Guardian"): "agi",
    ("Druid", "Restoration"): "int",
    ("Monk", "Brewmaster"): "agi", ("Monk", "Mistweaver"): "int", ("Monk", "Windwalker"): "agi",
    ("Rogue", "Assassination"): "agi", ("Rogue", "Outlaw"): "agi", ("Rogue", "Subtlety"): "agi",
    ("Demon Hunter", "Havoc"): "agi", ("Demon Hunter", "Vengeance"): "agi",
    ("Hunter", "Beast Mastery"): "agi", ("Hunter", "Marksmanship"): "agi", ("Hunter", "Survival"): "agi",
    ("Shaman", "Elemental"): "int", ("Shaman", "Enhancement"): "agi", ("Shaman", "Restoration"): "int",
    ("Evoker", "Devastation"): "int", ("Evoker", "Preservation"): "int", ("Evoker", "Augmentation"): "int",
}

# Slots that filter by armor_type; weapons use primary-stat check; accessories have no restriction
_ARMOR_FILTER_SLOTS: frozenset[str] = frozenset(
    {"head", "shoulder", "chest", "wrist", "hands", "waist", "legs", "feet"}
)
_WEAPON_SLOTS: frozenset[str] = frozenset({"main_hand", "off_hand"})

# Slots that can have a tier/catalyst class set piece.
_TIER_CATALYST_SLOTS: frozenset[str] = frozenset(
    {"head", "shoulder", "chest", "hands", "legs", "back", "wrist", "waist", "feet"}
)
# The 5 main tier slots have Blizzard Journal source rows and a Wowhead
# /item-set= link in their tooltip HTML — used as the reliable tier anchor.
_MAIN_TIER_SLOTS: frozenset[str] = frozenset({"head", "shoulder", "chest", "hands", "legs"})
# The 4 catalyst slots (back/wrist/waist/feet) have no Journal encounter and
# therefore no item_sources rows.  They are found via name-suffix matching
# against the current season's main tier anchor.

# Map from plan slot → wow_items.slot_type for paired slots
_SLOT_TYPE_QUERY_MAP: dict[str, str] = {
    "ring_2":    "ring_1",
    "trinket_2": "trinket_1",
}

# Wowhead tooltip HTML armor-type marker: <!--scstart4:{subclass_id}-->
# Used when wow_items.armor_type is NULL (items seeded via Journal API don't
# have armor_type populated; the type is embedded in the tooltip HTML instead).
_ARMOR_TYPE_MARKER: dict[str, str] = {
    "cloth":   "%<!--scstart4:1-->%",
    "leather": "%<!--scstart4:2-->%",
    "mail":    "%<!--scstart4:3-->%",
    "plate":   "%<!--scstart4:4-->%",
}


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
                SELECT id FROM guild_identity.bis_list_sources
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

        # Resolve desired_item_id from wow_items if available
        item_row = await conn.fetchrow(
            "SELECT id, name FROM guild_identity.wow_items WHERE blizzard_item_id=$1",
            blizzard_item_id,
        )
        desired_item_id = item_row["id"] if item_row else None
        resolved_name = item_name or (item_row["name"] if item_row else None)

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
                (plan_id, slot, desired_item_id, blizzard_item_id, item_name, is_locked)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (plan_id, slot) DO UPDATE
                SET desired_item_id = EXCLUDED.desired_item_id,
                    blizzard_item_id = EXCLUDED.blizzard_item_id,
                    item_name        = EXCLUDED.item_name,
                    is_locked        = EXCLUDED.is_locked
            """,
            plan_id, slot, desired_item_id, blizzard_item_id, resolved_name, locked_val,
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
            SELECT ble.slot, ble.item_id, ble.priority,
                   wi.blizzard_item_id, wi.name AS item_name
              FROM guild_identity.bis_list_entries ble
              JOIN guild_identity.wow_items wi ON wi.id = ble.item_id
             WHERE ble.source_id = $1
               AND ble.spec_id = $2
               AND (ble.hero_talent_id = $3 OR ble.hero_talent_id IS NULL)
             ORDER BY ble.slot, ble.priority
            """,
            use_source, spec_id, use_ht,
        )

        # Group candidates by slot (already ordered by priority)
        by_slot: dict[str, list] = {}
        for row in bis_rows:
            by_slot.setdefault(row["slot"], []).append(row)

        populated = 0
        for slot, candidates in by_slot.items():
            if slot not in WOW_SLOTS or slot in locked_slots:
                continue
            # Skip excluded items, pick first non-excluded candidate
            excluded = excluded_by_slot.get(slot, set())
            chosen = next(
                (r for r in candidates if r["item_id"] not in excluded), None
            )
            if not chosen:
                continue

            await conn.execute(
                """
                INSERT INTO guild_identity.gear_plan_slots
                    (plan_id, slot, desired_item_id, blizzard_item_id, item_name, is_locked)
                VALUES ($1, $2, $3, $4, $5, FALSE)
                ON CONFLICT (plan_id, slot) DO UPDATE
                    SET desired_item_id = EXCLUDED.desired_item_id,
                        blizzard_item_id = EXCLUDED.blizzard_item_id,
                        item_name        = EXCLUDED.item_name
                    WHERE gear_plan_slots.is_locked = FALSE
                """,
                plan_id, slot, chosen["item_id"], chosen["blizzard_item_id"], chosen["item_name"],
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
            if slot not in WOW_SLOTS:
                unrecognised += 1
                continue
            if slot in locked_slots:
                skipped_locked += 1
                continue

            bid = simc_slot.blizzard_item_id

            # Resolve wow_items row
            item_row = await conn.fetchrow(
                "SELECT id, name FROM guild_identity.wow_items WHERE blizzard_item_id=$1",
                bid,
            )
            desired_item_id = item_row["id"] if item_row else None
            item_name = item_row["name"] if item_row else f"Item {bid}"

            await conn.execute(
                """
                INSERT INTO guild_identity.gear_plan_slots
                    (plan_id, slot, desired_item_id, blizzard_item_id, item_name, is_locked)
                VALUES ($1, $2, $3, $4, $5, FALSE)
                ON CONFLICT (plan_id, slot) DO UPDATE
                    SET desired_item_id = EXCLUDED.desired_item_id,
                        blizzard_item_id = EXCLUDED.blizzard_item_id,
                        item_name        = EXCLUDED.item_name
                    WHERE gear_plan_slots.is_locked = FALSE
                """,
                plan_id, slot, desired_item_id, bid, item_name,
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
            SELECT ce.slot, ce.blizzard_item_id, ce.item_name,
                   wi.id AS wow_item_id
              FROM guild_identity.character_equipment ce
              LEFT JOIN guild_identity.wow_items wi
                     ON wi.blizzard_item_id = ce.blizzard_item_id
             WHERE ce.character_id = $1
            """,
            character_id,
        )

        populated = 0
        skipped_locked = 0

        for row in equip_rows:
            slot = row["slot"]
            if slot not in WOW_SLOTS:
                continue
            if slot in locked_slots:
                skipped_locked += 1
                continue

            bid = row["blizzard_item_id"]
            if not bid:
                continue

            item_name = row["item_name"] or f"Item {bid}"
            desired_item_id = row["wow_item_id"]  # may be None if not yet in wow_items

            await conn.execute(
                """
                INSERT INTO guild_identity.gear_plan_slots
                    (plan_id, slot, desired_item_id, blizzard_item_id, item_name, is_locked)
                VALUES ($1, $2, $3, $4, $5, FALSE)
                ON CONFLICT (plan_id, slot) DO UPDATE
                    SET desired_item_id = EXCLUDED.desired_item_id,
                        blizzard_item_id = EXCLUDED.blizzard_item_id,
                        item_name        = EXCLUDED.item_name
                    WHERE gear_plan_slots.is_locked = FALSE
                """,
                plan_id, slot, desired_item_id, bid, item_name,
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
              LEFT JOIN guild_identity.specializations s ON s.id = gp.spec_id
              LEFT JOIN guild_identity.classes c ON c.id = wc.class_id
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
                   COALESCE(wi.name, gps.item_name) AS item_name,
                   ce.bonus_ids, ce.enchant_id, ce.gem_ids
              FROM guild_identity.gear_plan_slots gps
              LEFT JOIN guild_identity.wow_items wi ON wi.id = gps.desired_item_id
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
              LEFT JOIN guild_identity.specializations s ON s.id = gp.spec_id
              LEFT JOIN guild_identity.classes c ON c.id = wc.class_id
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
                   COALESCE(wi.name, ce.item_name) AS item_name,
                   ce.bonus_ids, ce.enchant_id, ce.gem_ids
              FROM guild_identity.character_equipment ce
              LEFT JOIN guild_identity.wow_items wi ON wi.blizzard_item_id = ce.blizzard_item_id
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
              LEFT JOIN guild_identity.specializations s ON s.id = gp.spec_id
              LEFT JOIN guild_identity.hero_talents ht ON ht.id = gp.hero_talent_id
              LEFT JOIN guild_identity.bis_list_sources bls ON bls.id = gp.bis_source_id
              LEFT JOIN guild_identity.wow_characters wc ON wc.id = gp.character_id
              LEFT JOIN guild_identity.classes c ON c.id = wc.class_id
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
                   wi.icon_url
              FROM guild_identity.character_equipment ce
              LEFT JOIN guild_identity.wow_items wi
                     ON wi.blizzard_item_id = ce.blizzard_item_id
             WHERE ce.character_id = $1
            """,
            character_id,
        )
        equipped_by_slot: dict[str, dict] = {}
        for r in equip_rows:
            d = dict(r)
            d["is_crafted"] = is_crafted_item(d.get("bonus_ids") or [])
            equipped_by_slot[r["slot"]] = d

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
                          FROM guild_identity.wow_items
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
                   COALESCE(wi.name, gps.item_name) AS item_name,
                   gps.is_locked,
                   gps.excluded_item_ids,
                   wi.icon_url, wi.wowhead_tooltip_html
              FROM guild_identity.gear_plan_slots gps
              LEFT JOIN guild_identity.wow_items wi ON wi.id = gps.desired_item_id
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
            bid = r["blizzard_item_id"]
            tooltip = r["wowhead_tooltip_html"] or ""
            if bid and "Random Stat" in tooltip:
                craftable_desired_bids.add(bid)
            if bid and "/item-set=" in tooltip:
                tier_piece_desired_bids.add(bid)
            row_dict = {k: v for k, v in dict(r).items() if k != "wowhead_tooltip_html"}
            desired_by_slot[r["slot"]] = row_dict
            excluded_ids_by_slot[r["slot"]] = list(r["excluded_item_ids"] or [])

        # Batch-fetch info for all excluded wow_items so the drawer can show names/icons
        all_excluded_ids: list[int] = [
            id_ for ids in excluded_ids_by_slot.values() for id_ in ids
        ]
        excluded_item_info: dict[int, dict] = {}
        if all_excluded_ids:
            ex_rows = await conn.fetch(
                """
                SELECT id, blizzard_item_id, name, icon_url
                  FROM guild_identity.wow_items
                 WHERE id = ANY($1::int[])
                """,
                all_excluded_ids,
            )
            excluded_item_info = {r["id"]: dict(r) for r in ex_rows}

        # BIS recommendations for this spec + hero_talent
        bis_by_slot: dict[str, list[dict]] = {}
        if spec_id:
            bis_rows = await conn.fetch(
                """
                SELECT ble.slot, ble.item_id, ble.source_id, ble.hero_talent_id,
                       ble.priority,
                       wi.blizzard_item_id, wi.name AS item_name, wi.icon_url,
                       bls.name AS source_name, bls.short_label, bls.origin, bls.content_type
                  FROM guild_identity.bis_list_entries ble
                  JOIN guild_identity.wow_items wi ON wi.id = ble.item_id
                  JOIN guild_identity.bis_list_sources bls ON bls.id = ble.source_id
                 WHERE ble.spec_id = $1
                   AND (ble.hero_talent_id = $2 OR ble.hero_talent_id IS NULL)
                   AND bls.is_active = TRUE
                 ORDER BY bls.sort_order, ble.slot, ble.priority
                """,
                spec_id, hero_talent_id,
            )
            for r in bis_rows:
                bis_by_slot.setdefault(r["slot"], []).append(dict(r))

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
                """
                SELECT DISTINCT wi.blizzard_item_id
                  FROM guild_identity.item_recipe_links irl
                  JOIN guild_identity.wow_items wi ON wi.id = irl.item_id
                 WHERE wi.blizzard_item_id = ANY($1::int[])
                """,
                desired_bids_list,
            )
            craftable_desired_bids |= {r["blizzard_item_id"] for r in link_rows}

            # Tier piece: desired item is in a tier slot AND has no direct
            # non-junk sources (meaning it's only obtainable via tier token).
            tier_candidate_rows = await conn.fetch(
                """
                SELECT wi.blizzard_item_id
                  FROM guild_identity.wow_items wi
                 WHERE wi.blizzard_item_id = ANY($1::int[])
                   AND wi.slot_type IN ('head','shoulder','chest','hands','legs')
                   AND NOT EXISTS (
                       SELECT 1 FROM guild_identity.item_sources s
                        WHERE s.item_id = wi.id AND NOT s.is_suspected_junk
                   )
                """,
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
                SELECT wi.blizzard_item_id, is2.instance_type,
                       is2.encounter_name, is2.instance_name
                  FROM guild_identity.item_sources is2
                  JOIN guild_identity.wow_items wi ON wi.id = is2.item_id
                 WHERE wi.blizzard_item_id = ANY($1::int[])
                   AND NOT is2.is_suspected_junk
                """,
                list(all_bids),
            )
            for r in src_rows:
                bid = r["blizzard_item_id"]
                inst_type = r["instance_type"]
                tracks = _get_tracks(inst_type)
                existing_tracks = tracks_by_item.get(bid, [])
                # Merge + deduplicate, preserving order V<C<H<M
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
                    SELECT v.tier_piece_blizzard_id AS blizzard_item_id,
                           v.instance_type,
                           v.boss_name AS encounter_name,
                           v.instance_name
                      FROM guild_identity.v_tier_piece_sources v
                     WHERE v.tier_piece_blizzard_id = ANY($1::int[])
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
                logger.warning("v_tier_piece_sources lookup failed: %s", exc)

        # Available BIS sources (for UI dropdowns)
        source_list = await conn.fetch(
            """
            SELECT id, name, short_label, content_type, origin, is_default, sort_order
              FROM guild_identity.bis_list_sources
             WHERE is_active = TRUE
             ORDER BY sort_order
            """
        )

        # Which sources have hero-talent-specific BIS entries
        ht_source_ids = await conn.fetchval(
            """
            SELECT array_agg(DISTINCT source_id)
              FROM guild_identity.bis_list_entries
             WHERE hero_talent_id IS NOT NULL
            """
        ) or []

        # Hero talents for the plan's spec (for UI dropdown)
        ht_list = []
        if spec_id:
            ht_rows = await conn.fetch(
                "SELECT id, name, slug FROM guild_identity.hero_talents WHERE spec_id=$1 ORDER BY name",
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
                WITH crafters AS (
                    SELECT DISTINCT
                        wi.blizzard_item_id,
                        pr.name AS profession_name,
                        wc.id AS character_id,
                        wc.character_name,
                        gr.level AS rank_level
                    FROM guild_identity.item_recipe_links irl
                    JOIN guild_identity.wow_items wi ON wi.id = irl.item_id
                    JOIN guild_identity.recipes r ON r.id = irl.recipe_id
                    JOIN guild_identity.professions pr ON pr.id = r.profession_id
                    JOIN guild_identity.character_recipes cr ON cr.recipe_id = r.id
                    JOIN guild_identity.wow_characters wc ON wc.id = cr.character_id
                    JOIN guild_identity.player_characters pc ON pc.character_id = wc.id
                    JOIN guild_identity.players pl ON pl.id = pc.player_id
                    JOIN common.guild_ranks gr ON gr.id = pl.guild_rank_id
                    WHERE wi.blizzard_item_id = ANY($1::int[])
                      AND wc.in_guild = TRUE
                      AND wc.removed_at IS NULL
                )
                SELECT
                    blizzard_item_id,
                    profession_name,
                    character_name,
                    rank_level,
                    COUNT(*) OVER (PARTITION BY blizzard_item_id) AS total_crafters
                FROM crafters
                ORDER BY blizzard_item_id, rank_level DESC, character_name ASC
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

    # Build per-slot data
    slots_data: dict[str, dict] = {}
    for slot in WOW_SLOTS:
        equipped = equipped_by_slot.get(slot)
        desired = desired_by_slot.get(slot)

        # Phase 1E.5: per-slot exclusions filter BIS recommendations
        excluded_ids = excluded_ids_by_slot.get(slot, [])
        excluded_set = set(excluded_ids)
        excluded_items = [
            excluded_item_info[id_]
            for id_ in excluded_ids
            if id_ in excluded_item_info
        ]
        bis_recs = [
            r for r in bis_by_slot.get(slot, [])
            if r["item_id"] not in excluded_set
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
            "display_name": SLOT_DISPLAY.get(slot, slot.replace("_", " ").title()),
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
    }


# ── Available items (Phase 1E.4) ──────────────────────────────────────────────

def _filter_by_primary_stat(items: list[dict], primary_stat: str) -> list[dict]:
    """Filter weapon items by primary stat via tooltip HTML substring checks.

    Includes the item when uncertain (no tooltip or no recognisable stat text).
    Simple substring matching as described in the Phase 1E.4 spec.
    """
    result = []
    for item in items:
        tooltip = item.get("wowhead_tooltip_html") or ""
        if not tooltip:
            # No tooltip data — include (uncertain → include per spec)
            result.append(item)
            continue
        has_str = "Strength" in tooltip
        has_agi = "Agility" in tooltip
        has_int = "Intellect" in tooltip
        if primary_stat == "int" and (has_str or has_agi):
            continue
        if primary_stat == "str" and (has_int or has_agi):
            continue
        if primary_stat == "agi" and (has_int or has_str):
            continue
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
      - crafted: items linked via item_recipe_links (inherently current content)

    Excluded items (Phase 1E.5) are omitted from all groups.

    Eligibility rules:
      - Armor slots: filter by character's class armor type (cloth/leather/mail/plate)
      - Weapon slots: filter by primary stat derived from (class, spec); tooltip HTML check
      - Accessories (neck/back/rings/trinkets): no armor restriction
    """
    empty: dict = {"tier": None, "raid": [], "dungeon": [], "crafted": []}
    if slot not in WOW_SLOTS:
        return empty

    async with pool.acquire() as conn:
        char_row = await conn.fetchrow(
            """
            SELECT c.name AS class_name, s.name AS spec_name
              FROM guild_identity.wow_characters wc
              LEFT JOIN guild_identity.classes c ON c.id = wc.class_id
              LEFT JOIN guild_identity.gear_plans gp
                     ON gp.character_id = wc.id AND gp.player_id = $2
              LEFT JOIN guild_identity.specializations s ON s.id = gp.spec_id
             WHERE wc.id = $1
            """,
            character_id, player_id,
        )
        if not char_row:
            return empty

        class_name = char_row["class_name"] or ""
        spec_name  = char_row["spec_name"] or ""

        # Load current season filters — kept separate so raid and dungeon lists
        # are drawn from their respective instance pools.
        season_row = await conn.fetchrow(
            """SELECT current_instance_ids, current_raid_ids
                 FROM patt.raid_seasons WHERE is_active = TRUE LIMIT 1"""
        )
        raid_ids:    list[int] = []
        dungeon_ids: list[int] = []
        if season_row:
            raid_ids    = list(season_row["current_raid_ids"]    or [])
            dungeon_ids = list(season_row["current_instance_ids"] or [])
        all_season_ids = raid_ids + dungeon_ids

        # Phase 1E.5: fetch per-slot exclusions so we can hide excluded items
        excluded_ids: list[int] = []
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if plan_row:
            slot_row = await conn.fetchrow(
                """
                SELECT excluded_item_ids
                  FROM guild_identity.gear_plan_slots
                 WHERE plan_id = $1 AND slot = $2
                """,
                plan_row["id"], slot,
            )
            if slot_row:
                excluded_ids = list(slot_row["excluded_item_ids"] or [])

        # Normalize paired slots to canonical wow_items.slot_type
        slot_type = _SLOT_TYPE_QUERY_MAP.get(slot, slot)

        # Build shared WHERE clause fragments (indices relative to base params)
        params: list = [slot_type]
        armor_clause = ""
        if slot in _ARMOR_FILTER_SLOTS:
            armor_type = CLASS_ARMOR_TYPE.get(class_name)
            if armor_type:
                tooltip_marker = _ARMOR_TYPE_MARKER.get(armor_type, "")
                params.append(armor_type)        # $2 — direct column match
                params.append(tooltip_marker)    # $3 — tooltip HTML fallback
                armor_clause = (
                    f"AND (wi.armor_type = ${len(params) - 1} "
                    f"OR (wi.armor_type IS NULL AND wi.wowhead_tooltip_html LIKE ${len(params)}))"
                )

        # Exclusion filter: hide items the player has permanently excluded (Phase 1E.5)
        exclude_clause = ""
        if excluded_ids:
            params.append(excluded_ids)
            exclude_clause = f"AND wi.id != ALL(${len(params)}::int[])"

        # Only fetch tooltip HTML when needed for weapon stat filtering
        need_tooltip = slot in _WEAPON_SLOTS
        tooltip_col  = "wi.wowhead_tooltip_html," if need_tooltip else ""

        # ── Query 1: raid + dungeon drops ─────────────────────────────────────
        # Single query for both; split by instance_type in Python.
        drop_rows: list = []
        if all_season_ids:
            drop_params = list(params)
            drop_params.append(all_season_ids)
            season_clause = f"AND is2.blizzard_instance_id = ANY(${len(drop_params)})"

            drop_rows = await conn.fetch(
                f"""
                SELECT wi.blizzard_item_id, wi.name, wi.icon_url,
                       {tooltip_col}
                       is2.encounter_name, is2.instance_name, is2.instance_type
                  FROM guild_identity.wow_items wi
                  JOIN guild_identity.item_sources is2
                       ON is2.item_id = wi.id AND NOT is2.is_suspected_junk
                 WHERE wi.slot_type = $1
                   AND is2.instance_type IN ('raid', 'dungeon')
                   {armor_clause}
                   {season_clause}
                   {exclude_clause}
                 ORDER BY wi.name, is2.instance_name, is2.encounter_name
                """,
                *drop_params,
            )

        # ── Query 2: crafted items via item_recipe_links ──────────────────────
        # Scoped to the active raid season's expansion (via profession_tiers) so
        # prior-expansion crafted gear doesn't appear.  Only confirmed-epic items
        # (class="q4" in Wowhead tooltip) are shown — blue/green crafted gear is
        # never BIS and must never appear regardless of tooltip state.
        craft_select = f"wi.blizzard_item_id, wi.name, wi.icon_url{', wi.wowhead_tooltip_html' if need_tooltip else ''}"
        craft_rows = await conn.fetch(
            f"""
            SELECT DISTINCT {craft_select}
              FROM guild_identity.wow_items wi
              JOIN guild_identity.item_recipe_links irl ON irl.item_id = wi.id
              JOIN guild_identity.recipes rec ON rec.id = irl.recipe_id
              JOIN guild_identity.profession_tiers pt ON pt.id = rec.tier_id
             WHERE wi.slot_type = $1
               AND pt.expansion_name = (
                       SELECT expansion_name FROM patt.raid_seasons
                        WHERE is_active = TRUE LIMIT 1
                   )
               AND wi.wowhead_tooltip_html LIKE '%class="q4"%'
               {armor_clause}
               {exclude_clause}
             ORDER BY wi.name
            """,
            *params,
        )

        # ── Query 3: tier / catalyst class set piece ──────────────────────────
        # Two distinct paths:
        #
        # MAIN TIER SLOTS (head/shoulder/chest/hands/legs):
        #   These have Blizzard Journal source rows AND a Wowhead /item-set= link
        #   in their tooltip HTML.  We require both to avoid matching old expansion
        #   tier pieces (which have item-set links but no current-raid source rows)
        #   or non-tier items from the current raid (which have source rows but no
        #   item-set link).  Class is discriminated by class name in the tooltip.
        #
        # CATALYST SLOTS (back/wrist/waist/feet):
        #   Catalyst items have NO Journal source rows and NO item-set link.
        #   We find them by name-suffix matching against any main tier anchor for
        #   this class+season (e.g., anchor "Branches of the Luminous Bloom" →
        #   suffix " of the Luminous Bloom" → find "Leafdrape of the Luminous Bloom").
        #   If no anchor is found (e.g., tooltips not yet enriched), we return
        #   nothing rather than guessing — prevents junk from appearing.
        #
        # Returns None for non-tier slots so the frontend hides the section.
        tier_rows: list = []
        if slot in _TIER_CATALYST_SLOTS and class_name and raid_ids:
            t_armor = CLASS_ARMOR_TYPE.get(class_name, "")

            excl_params: list = [excluded_ids] if excluded_ids else []

            # Derive the tier set name suffix once, used by both main and catalyst
            # slot queries.  Looks for any main-tier piece for this class that has
            # " of " in its name (e.g. "Branches of the Luminous Bloom" →
            # " of the Luminous Bloom").  Works via PRIMARY (tooltip) or FALLBACK
            # (armor_type + BIS) so it functions before and after Enrich Items runs.
            anchor_row = await conn.fetchrow(
                """
                SELECT wi.name
                  FROM guild_identity.wow_items wi
                 WHERE wi.slot_type = ANY(ARRAY['head','shoulder','chest','hands','legs'])
                   AND wi.name LIKE '% of %'
                   AND (
                       (    wi.wowhead_tooltip_html LIKE $1
                        AND wi.wowhead_tooltip_html LIKE '%/item-set=%'
                        AND EXISTS (
                                SELECT 1 FROM guild_identity.item_sources is2
                                 WHERE is2.item_id = wi.id
                                   AND is2.instance_name IN (
                                       SELECT DISTINCT is3.instance_name
                                         FROM guild_identity.item_sources is3
                                        WHERE is3.blizzard_instance_id = ANY($2)
                                   )
                            )
                       )
                       OR
                       (    wi.armor_type = $3
                        AND EXISTS (
                                SELECT 1 FROM guild_identity.bis_list_entries ble
                                  JOIN guild_identity.specializations sp ON sp.id = ble.spec_id
                                  JOIN guild_identity.classes cl ON cl.id = sp.class_id
                                 WHERE ble.item_id = wi.id AND cl.name = $4
                            )
                       )
                   )
                 LIMIT 1
                """,
                f"%{class_name}%", raid_ids, t_armor or "leather", class_name,
            )
            set_suffix: Optional[str] = None
            if anchor_row:
                anchor_name: str = anchor_row["name"]
                of_idx = anchor_name.find(" of ")
                if of_idx >= 0:
                    set_suffix = anchor_name[of_idx:]  # e.g. " of the Luminous Bloom"

            if slot in _MAIN_TIER_SLOTS:
                # $1=slot_type  $2='%ClassName%'  $3=raid_ids  $4=class_name(exact)
                # [$5=armor_type]  [$6=set_suffix]  [$N=excluded_ids]
                params: list = [slot_type, f"%{class_name}%", raid_ids, class_name]
                if t_armor:
                    params.append(t_armor)
                    fallback_armor_sql = f"AND wi.armor_type = ${len(params)}"
                else:
                    fallback_armor_sql = ""
                # If we know the tier set suffix, restrict the fallback to items
                # whose name matches it.  This prevents non-tier BIS items (e.g. a
                # raid drop with no sources yet) from appearing in the tier section.
                fallback_suffix_sql = ""
                if set_suffix:
                    params.append(f"%{set_suffix}")
                    fallback_suffix_sql = f"AND wi.name LIKE ${len(params)}"
                excl_sql = ""
                if excl_params:
                    params += excl_params
                    excl_sql = f"AND wi.id != ALL(${len(params)}::int[])"

                tier_rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT wi.blizzard_item_id, wi.name, wi.icon_url
                      FROM guild_identity.wow_items wi
                     WHERE wi.slot_type = $1
                       AND (
                           -- PRIMARY: Wowhead tooltip has class name + /item-set= link
                           --          + encounter source in current raid.
                           (    wi.wowhead_tooltip_html LIKE $2
                            AND wi.wowhead_tooltip_html LIKE '%/item-set=%'
                            AND EXISTS (
                                    SELECT 1 FROM guild_identity.item_sources is2
                                     WHERE is2.item_id = wi.id
                                       AND is2.instance_name IN (
                                           SELECT DISTINCT is3.instance_name
                                             FROM guild_identity.item_sources is3
                                            WHERE is3.blizzard_instance_id = ANY($3)
                                       )
                                )
                           )
                           OR
                           -- FALLBACK: items not yet in Wowhead item-set (e.g. new
                           --   expansion tier before Wowhead indexes the set).  Armor
                           --   type + BIS entry + set suffix prevents non-tier items
                           --   from appearing.  NOT gated on sources — items may have
                           --   sources already (sources added on a prior sync).
                           (    {fallback_armor_sql.lstrip("AND ") if fallback_armor_sql else "TRUE"}
                            AND EXISTS (
                                    SELECT 1 FROM guild_identity.bis_list_entries ble
                                      JOIN guild_identity.specializations sp ON sp.id = ble.spec_id
                                      JOIN guild_identity.classes cl ON cl.id = sp.class_id
                                     WHERE ble.item_id = wi.id AND cl.name = $4
                                )
                            {fallback_suffix_sql}
                           )
                       )
                       {excl_sql}
                     ORDER BY wi.name
                    """,
                    *params,
                )

            else:
                # Catalyst slot — suffix is class-discriminated (each armor class has a
                # unique tier set name), so suffix + slot is enough.  No class filter
                # needed — cloaks have armor_type='cloth' regardless of wearer class,
                # and catalyst items may not have BIS entries or enriched tooltips.
                if set_suffix:
                    params = [slot_type, f"%{set_suffix}"] + excl_params
                    excl_sql = f"AND wi.id != ALL(${len(params)}::int[])" if excl_params else ""
                    tier_rows = await conn.fetch(
                        f"""
                        SELECT DISTINCT wi.blizzard_item_id, wi.name, wi.icon_url
                          FROM guild_identity.wow_items wi
                         WHERE wi.slot_type = $1
                           AND wi.name LIKE $2
                           {excl_sql}
                         ORDER BY wi.name
                        """,
                        *params,
                    )

    # ── Split drop rows by instance_type ──────────────────────────────────────
    raid_map:    dict[int, dict] = {}
    dungeon_map: dict[int, dict] = {}

    for r in drop_rows:
        bid   = r["blizzard_item_id"]
        itype = r["instance_type"]
        target = raid_map if itype == "raid" else dungeon_map

        if bid not in target:
            entry: dict = {
                "blizzard_item_id": bid,
                "name": r["name"],
                "icon_url": r["icon_url"],
                "sources": [],
            }
            if need_tooltip:
                entry["wowhead_tooltip_html"] = r["wowhead_tooltip_html"] or ""
            target[bid] = entry

        tracks = _get_tracks(itype)
        src = {
            "source_name":     r["encounter_name"],
            "source_instance": r["instance_name"],
            "instance_type":   itype,
            "quality_tracks":  tracks,
        }
        if src not in target[bid]["sources"]:
            target[bid]["sources"].append(src)

    raid_items    = list(raid_map.values())
    dungeon_items = list(dungeon_map.values())

    # ── Crafted items ──────────────────────────────────────────────────────────
    crafted_items: list[dict] = []
    for r in craft_rows:
        entry = {
            "blizzard_item_id": r["blizzard_item_id"],
            "name": r["name"],
            "icon_url": r["icon_url"],
        }
        if need_tooltip:
            entry["wowhead_tooltip_html"] = r["wowhead_tooltip_html"] or ""
        crafted_items.append(entry)

    # Apply primary-stat filter for weapon slots (in Python to keep SQL simple)
    if slot in _WEAPON_SLOTS:
        primary_stat = SPEC_PRIMARY_STAT.get((class_name, spec_name))
        if primary_stat:
            raid_items    = _filter_by_primary_stat(raid_items, primary_stat)
            dungeon_items = _filter_by_primary_stat(dungeon_items, primary_stat)
            crafted_items = _filter_by_primary_stat(crafted_items, primary_stat)

    # Strip tooltip HTML before returning (only needed for in-process filtering)
    for item in raid_items + dungeon_items + crafted_items:
        item.pop("wowhead_tooltip_html", None)

    # ── Tier / Catalyst items ──────────────────────────────────────────────────
    # None means "this slot has no tier piece" — frontend hides the section.
    tier_items: Optional[list[dict]] = None
    if slot in _TIER_CATALYST_SLOTS:
        tier_items = [
            {
                "blizzard_item_id": r["blizzard_item_id"],
                "name": r["name"],
                "icon_url": r["icon_url"],
            }
            for r in tier_rows
        ]

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

    Resolves blizzard_item_id → wow_items.id (internal FK).
    Creates the slot row if it doesn't exist (desired_item_id=NULL).
    No-ops if the item is already excluded.
    Returns True on success, False if plan not found.
    """
    if slot not in WOW_SLOTS:
        return False

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return False
        plan_id = plan_row["id"]

        # Resolve to internal wow_items.id
        item_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
            blizzard_item_id,
        )
        if not item_row:
            return False
        item_id = item_row["id"]

        # Upsert slot row and append item_id if not already present
        await conn.execute(
            """
            INSERT INTO guild_identity.gear_plan_slots
                (plan_id, slot, desired_item_id, blizzard_item_id, item_name, is_locked,
                 excluded_item_ids)
            VALUES ($1, $2, NULL, NULL, NULL, FALSE, ARRAY[$3::int])
            ON CONFLICT (plan_id, slot) DO UPDATE
                SET excluded_item_ids =
                    CASE WHEN $3 = ANY(gear_plan_slots.excluded_item_ids)
                         THEN gear_plan_slots.excluded_item_ids
                         ELSE array_append(gear_plan_slots.excluded_item_ids, $3)
                    END
            """,
            plan_id, slot, item_id,
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
    if slot not in WOW_SLOTS:
        return False

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.gear_plans WHERE player_id=$1 AND character_id=$2",
            player_id, character_id,
        )
        if not plan_row:
            return False
        plan_id = plan_row["id"]

        item_row = await conn.fetchrow(
            "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
            blizzard_item_id,
        )
        if not item_row:
            return True  # Nothing to remove
        item_id = item_row["id"]

        await conn.execute(
            """
            UPDATE guild_identity.gear_plan_slots
               SET excluded_item_ids = array_remove(excluded_item_ids, $3)
             WHERE plan_id = $1 AND slot = $2
            """,
            plan_id, slot, item_id,
        )
        return True
