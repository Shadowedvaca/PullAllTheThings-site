"""Gear Plan service — plan CRUD, BIS population, upgrade computation.

Works with asyncpg pool (raw SQL) for consistency with item_service.py and
the broader guild_sync pattern.  Character ownership must be verified by
the caller before invoking any mutating function.
"""

from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from sv_common.guild_sync.quality_track import is_crafted_item
from sv_common.guild_sync.simc_parser import (
    SimcSlot,
    export_gear_plan,
    parse_gear_slots,
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


def _normalize_paired_slot(
    slot_a: str,
    slot_b: str,
    equipped_by_slot: dict,
    desired_by_slot: dict,
    bis_by_slot: dict,
    bis_source_id: Optional[int],
) -> None:
    """Normalize a paired slot (rings, trinkets) by swapping equipped items for display.

    Rules:
    1. If swapping the equipped items increases the number of equipped==desired
       matches, swap equipped only (BIS data already consistent with desired).
    2. If neither assignment produces a match, sort equipped items alphabetically
       by item name so the display is always consistent, AND swap bis_by_slot to
       match — so the BIS grid for ring_1 also shows the alphabetically-earlier
       BIS item first rather than whatever the scraper happened to assign.

    Modifies equipped_by_slot in-place; modifies bis_by_slot in-place for rule 2.
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
                   bis_source_id, simc_profile, is_active
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
                      bis_source_id, simc_profile, is_active
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

        # Find current locked slots
        locked_slots_rows = await conn.fetch(
            "SELECT slot FROM guild_identity.gear_plan_slots WHERE plan_id=$1 AND is_locked=TRUE",
            plan_id,
        )
        locked_slots = {r["slot"] for r in locked_slots_rows}

        # Get BIS entries (first priority per slot)
        bis_rows = await conn.fetch(
            """
            SELECT DISTINCT ON (ble.slot) ble.slot, ble.item_id, ble.priority,
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

        populated = 0
        for row in bis_rows:
            slot = row["slot"]
            if slot not in WOW_SLOTS:
                continue
            if slot in locked_slots:
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
                plan_id, slot, row["item_id"], row["blizzard_item_id"], row["item_name"],
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


async def import_simc(
    pool: asyncpg.Pool,
    player_id: int,
    character_id: int,
    simc_text: str,
) -> dict:
    """Parse SimC text and populate gear_plan_slots.

    Overwrites all non-locked slots.  Stores the raw simc_text on the plan.
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

        # Store raw simc text
        await conn.execute(
            "UPDATE guild_identity.gear_plans SET simc_profile=$1, updated_at=NOW()"
            " WHERE id=$2",
            simc_text, plan_id,
        )

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
            SELECT gps.slot, gps.blizzard_item_id, gps.item_name,
                   ce.bonus_ids, ce.enchant_id, ce.gem_ids
              FROM guild_identity.gear_plan_slots gps
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
                   s.name AS spec_name,
                   ht.name AS hero_talent_name,
                   bls.name AS bis_source_name
              FROM guild_identity.gear_plans gp
              LEFT JOIN guild_identity.specializations s ON s.id = gp.spec_id
              LEFT JOIN guild_identity.hero_talents ht ON ht.id = gp.hero_talent_id
              LEFT JOIN guild_identity.bis_list_sources bls ON bls.id = gp.bis_source_id
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

        # Desired items (plan slots)
        desired_rows = await conn.fetch(
            """
            SELECT gps.slot, gps.blizzard_item_id, gps.item_name, gps.is_locked,
                   wi.icon_url
              FROM guild_identity.gear_plan_slots gps
              LEFT JOIN guild_identity.wow_items wi ON wi.id = gps.desired_item_id
             WHERE gps.plan_id = $1
            """,
            plan_id,
        )
        desired_by_slot: dict[str, dict] = {r["slot"]: dict(r) for r in desired_rows}

        # BIS recommendations for this spec + hero_talent
        bis_by_slot: dict[str, list[dict]] = {}
        if spec_id:
            bis_rows = await conn.fetch(
                """
                SELECT ble.slot, ble.item_id, ble.source_id, ble.hero_talent_id,
                       ble.priority,
                       wi.blizzard_item_id, wi.name AS item_name, wi.icon_url,
                       bls.name AS source_name, bls.short_label
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

        # Available quality tracks per blizzard_item_id
        tracks_by_item: dict[int, list[str]] = {}
        # Also get source location info
        sources_by_item: dict[int, list[dict]] = {}
        if all_bids:
            src_rows = await conn.fetch(
                """
                SELECT wi.blizzard_item_id, is2.source_type, is2.source_name,
                       is2.source_instance, is2.quality_tracks
                  FROM guild_identity.item_sources is2
                  JOIN guild_identity.wow_items wi ON wi.id = is2.item_id
                 WHERE wi.blizzard_item_id = ANY($1::int[])
                """,
                list(all_bids),
            )
            for r in src_rows:
                bid = r["blizzard_item_id"]
                tracks = list(r["quality_tracks"] or [])
                # Raid boss items always include V (LFR) even if not stored in older data
                if r["source_type"] == "raid_boss" and "V" not in tracks and "C" in tracks:
                    tracks = ["V"] + tracks
                existing_tracks = tracks_by_item.get(bid, [])
                # Merge + deduplicate, preserving order V<C<H<M
                merged = sorted(
                    set(existing_tracks) | set(tracks),
                    key=lambda t: TRACK_ORDER.get(t, 99),
                )
                tracks_by_item[bid] = merged
                sources_by_item.setdefault(bid, []).append({
                    "source_type": r["source_type"],
                    "source_name": r["source_name"],
                    "source_instance": r["source_instance"],
                    "quality_tracks": tracks,
                })

        # Available BIS sources (for UI dropdowns)
        source_list = await conn.fetch(
            """
            SELECT id, name, short_label, content_type, is_default, sort_order
              FROM guild_identity.bis_list_sources
             WHERE is_active = TRUE
             ORDER BY sort_order
            """
        )

        # Hero talents for the plan's spec (for UI dropdown)
        ht_list = []
        if spec_id:
            ht_rows = await conn.fetch(
                "SELECT id, name, slug FROM guild_identity.hero_talents WHERE spec_id=$1 ORDER BY name",
                spec_id,
            )
            ht_list = [dict(r) for r in ht_rows]

    # Normalize ring and trinket pairs: swap equipped items to maximise BIS matches
    # and ensure consistent alphabetical ordering when no match exists.
    _normalize_paired_slot("ring_1", "ring_2", equipped_by_slot, desired_by_slot, bis_by_slot, bis_source_id)
    _normalize_paired_slot("trinket_1", "trinket_2", equipped_by_slot, desired_by_slot, bis_by_slot, bis_source_id)

    # Build per-slot data
    slots_data: dict[str, dict] = {}
    for slot in WOW_SLOTS:
        equipped = equipped_by_slot.get(slot)
        desired = desired_by_slot.get(slot)
        bis_recs = bis_by_slot.get(slot, [])

        # Determine effective desired blizzard_item_id
        desired_bid: Optional[int] = desired["blizzard_item_id"] if desired else None
        if desired_bid is None and bis_recs and bis_source_id:
            for rec in bis_recs:
                if rec["source_id"] == bis_source_id:
                    desired_bid = rec["blizzard_item_id"]
                    break

        available_tracks = tracks_by_item.get(desired_bid, []) if desired_bid else []
        item_sources = sources_by_item.get(desired_bid, []) if desired_bid else []

        equipped_track = equipped["quality_track"] if equipped else None
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

        slots_data[slot] = {
            "slot": slot,
            "display_name": SLOT_DISPLAY.get(slot, slot.replace("_", " ").title()),
            "equipped": equipped,
            "desired": desired,
            "desired_blizzard_item_id": desired_bid,
            "bis_recommendations": bis_recs,
            "item_sources": item_sources,
            "available_tracks": available_tracks,
            "upgrade_tracks": upgrade_tracks,
            "is_bis": is_bis,
            "needs_upgrade": needs_upgrade,
        }

    return {
        "plan": dict(plan_row),
        "slots": slots_data,
        "bis_sources": [dict(r) for r in source_list],
        "hero_talents": ht_list,
        "track_colors": TRACK_COLORS,
    }
