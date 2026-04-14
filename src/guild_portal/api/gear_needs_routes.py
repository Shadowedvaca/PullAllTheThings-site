"""Roster gear needs API — aggregated gear plan needs across the roster.

Public endpoints answering:
  "Which bosses/dungeons should we prioritize for loot this week?"

GET /api/v1/gear-needs/raid    — needs by instance → boss → track
GET /api/v1/gear-needs/dungeon — needs by dungeon → track (C/H only)

Query parameters (both endpoints):
  include_initiates: bool (default True)  — include rank_level=1 players
  include_offspec:   bool (default False) — include offspec character plans
"""

import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/gear-needs",
    tags=["gear-needs"],
)

# Quality track ranking — same order as gear_plan_service
TRACK_ORDER: dict[str, int] = {"V": 0, "C": 1, "H": 2, "M": 3}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pool(request: Request):
    pool = getattr(request.app.state, "guild_sync_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool unavailable")
    return pool


def _upgrade_tracks(
    equipped_track: Optional[str],
    equipped_item_id: Optional[int],
    desired_item_id: Optional[int],
    available_tracks: list[str],
) -> list[str]:
    """Return which available tracks would be upgrades.

    Same logic as gear_plan_service._upgrade_tracks.
    - Empty slot → any track is an upgrade
    - Item equipped, track unknown → cannot determine (return [])
    - Same item, lower track → need strictly higher track
    - Different item → same track and above
    """
    if not available_tracks:
        return []
    if equipped_track is None:
        return available_tracks if equipped_item_id is None else []

    eq_idx = TRACK_ORDER.get(equipped_track, -1)
    if desired_item_id and equipped_item_id == desired_item_id:
        return [t for t in available_tracks if TRACK_ORDER.get(t, -1) > eq_idx]
    return [t for t in available_tracks if TRACK_ORDER.get(t, -1) >= eq_idx]


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "unknown"


# ---------------------------------------------------------------------------
# Shared DB fetch
# ---------------------------------------------------------------------------


async def _fetch_needs_rows(conn, include_initiates: bool, include_offspec: bool) -> list:
    """Load all player gear-plan slot needs.

    Returns asyncpg records with: player info + slot + desired item + equipped item.
    Includes both main and (optionally) offspec characters.
    """
    # Main character needs
    rows = list(await conn.fetch("""
        SELECT
            p.id                                    AS player_id,
            COALESCE(p.display_name, wc.character_name) AS player_name,
            gr.level                                AS rank_level,
            wc.character_name                       AS character_name,
            sp.name                                 AS spec_name,
            cl.id                                   AS class_id,
            cl.name                                 AS class_name,
            gps.slot,
            wi_d.blizzard_item_id                   AS desired_bid,
            wi_d.name                               AS desired_item_name,
            wi_d.icon_url                           AS desired_icon_url,
            wi_d.wowhead_tooltip_html               AS desired_tooltip,
            ce.blizzard_item_id                     AS equipped_bid,
            ce.quality_track                        AS equipped_track,
            FALSE                                   AS is_offspec
        FROM guild_identity.players p
        JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
        JOIN guild_identity.wow_characters wc ON wc.id = p.main_character_id
        JOIN guild_identity.gear_plans gp
            ON gp.character_id = p.main_character_id AND gp.is_active = TRUE
        LEFT JOIN guild_identity.specializations sp ON sp.id = gp.spec_id
        LEFT JOIN guild_identity.classes cl ON cl.id = sp.class_id
        JOIN guild_identity.gear_plan_slots gps
            ON gps.plan_id = gp.id AND gps.desired_item_id IS NOT NULL
        JOIN guild_identity.wow_items wi_d ON wi_d.id = gps.desired_item_id
        LEFT JOIN guild_identity.character_equipment ce
            ON ce.character_id = p.main_character_id AND ce.slot = gps.slot
        WHERE p.main_character_id IS NOT NULL
          AND p.is_active = TRUE
          AND p.on_raid_hiatus = FALSE
    """))

    if include_offspec:
        offspec_rows = await conn.fetch("""
            SELECT
                p.id                                    AS player_id,
                COALESCE(p.display_name, wc.character_name) AS player_name,
                gr.level                                AS rank_level,
                wc.character_name                       AS character_name,
                sp.name                                 AS spec_name,
                cl.id                                   AS class_id,
                cl.name                                 AS class_name,
                gps.slot,
                wi_d.blizzard_item_id                   AS desired_bid,
                wi_d.name                               AS desired_item_name,
                wi_d.icon_url                           AS desired_icon_url,
                wi_d.wowhead_tooltip_html               AS desired_tooltip,
                ce.blizzard_item_id                     AS equipped_bid,
                ce.quality_track                        AS equipped_track,
                TRUE                                    AS is_offspec
            FROM guild_identity.players p
            JOIN common.guild_ranks gr ON gr.id = p.guild_rank_id
            JOIN guild_identity.wow_characters wc ON wc.id = p.offspec_character_id
            JOIN guild_identity.gear_plans gp
                ON gp.character_id = p.offspec_character_id AND gp.is_active = TRUE
            LEFT JOIN guild_identity.specializations sp ON sp.id = gp.spec_id
            LEFT JOIN guild_identity.classes cl ON cl.id = sp.class_id
            JOIN guild_identity.gear_plan_slots gps
                ON gps.plan_id = gp.id AND gps.desired_item_id IS NOT NULL
            JOIN guild_identity.wow_items wi_d ON wi_d.id = gps.desired_item_id
            LEFT JOIN guild_identity.character_equipment ce
                ON ce.character_id = p.offspec_character_id AND ce.slot = gps.slot
            WHERE p.offspec_character_id IS NOT NULL
              AND p.is_active = TRUE
              AND p.on_raid_hiatus = FALSE
        """)
        rows.extend(offspec_rows)

    if not include_initiates:
        rows = [r for r in rows if r["rank_level"] != 1]

    return rows


async def _fetch_sources_for_instance_type(
    conn, desired_bids: list[int], instance_type: str
) -> dict[int, list[dict]]:
    """Fetch item_sources rows for the given bids and instance_type.

    For 'raid', also checks v_tier_piece_sources for tier-gated items.
    Returns a dict: blizzard_item_id → list of source dicts.
    """
    from sv_common.guild_sync.source_config import get_tracks

    sources: dict[int, list[dict]] = {}
    if not desired_bids:
        return sources

    src_rows = await conn.fetch(
        """
        SELECT wi.blizzard_item_id,
               is2.instance_type,
               is2.encounter_name,
               is2.instance_name,
               is2.blizzard_encounter_id,
               is2.blizzard_instance_id
          FROM guild_identity.item_sources is2
          JOIN guild_identity.wow_items wi ON wi.id = is2.item_id
         WHERE wi.blizzard_item_id = ANY($1::int[])
           AND is2.instance_type = $2
           AND NOT is2.is_suspected_junk
        """,
        desired_bids,
        instance_type,
    )
    for r in src_rows:
        bid = r["blizzard_item_id"]
        sources.setdefault(bid, []).append({
            "instance_type": r["instance_type"],
            "encounter_name": r["encounter_name"],
            "instance_name": r["instance_name"],
            "blizzard_encounter_id": r["blizzard_encounter_id"],
            "blizzard_instance_id": r["blizzard_instance_id"],
            "tracks": get_tracks(r["instance_type"]),
        })

    # Tier pieces have no direct item_sources — resolve via token view (raid only)
    if instance_type == "raid":
        try:
            tier_rows = await conn.fetch(
                """
                SELECT v.tier_piece_blizzard_id AS blizzard_item_id,
                       v.instance_type,
                       v.boss_name              AS encounter_name,
                       v.instance_name,
                       v.blizzard_encounter_id,
                       v.blizzard_instance_id
                  FROM guild_identity.v_tier_piece_sources v
                 WHERE v.tier_piece_blizzard_id = ANY($1::int[])
                """,
                desired_bids,
            )
            for r in tier_rows:
                bid = r["blizzard_item_id"]
                itype = r["instance_type"] or "raid"
                entry = {
                    "instance_type": itype,
                    "encounter_name": r["encounter_name"],
                    "instance_name": r["instance_name"],
                    "blizzard_encounter_id": r["blizzard_encounter_id"],
                    "blizzard_instance_id": r["blizzard_instance_id"],
                    "tracks": get_tracks(itype),
                }
                existing = sources.get(bid, [])
                # Deduplicate by encounter + instance
                key = (r["encounter_name"], r["instance_name"])
                if not any(
                    (e["encounter_name"], e["instance_name"]) == key
                    for e in existing
                ):
                    sources.setdefault(bid, []).append(entry)
        except Exception as exc:
            logger.warning("v_tier_piece_sources lookup failed: %s", exc)

    return sources


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _player_entry(row) -> dict:
    return {
        "player_id": row["player_id"],
        "player_name": row["player_name"],
        "character_name": row["character_name"],
        "class_id": row["class_id"],
        "class_name": row["class_name"],
        "spec_name": row["spec_name"],
        "is_offspec": bool(row["is_offspec"]),
    }


def _item_entry(row) -> dict:
    return {
        "bid": row["desired_bid"],
        "name": row["desired_item_name"],
        "icon_url": row["desired_icon_url"],
        "slot": row["slot"],
    }


def _add_to_track(track_data: dict, track: str, row, item_entry: dict) -> None:
    """Add a player+item need to the track accumulator dict."""
    if track not in track_data:
        track_data[track] = {
            "player_ids": set(),
            "players": {},         # player_id → player_entry
            "need_keys": set(),    # (player_id, bid, slot) for unique item count
            "items_by_player": {}, # player_id → [item_entries]
        }
    td = track_data[track]
    pid = row["player_id"]
    bid = row["desired_bid"]
    td["player_ids"].add(pid)
    td["players"][pid] = _player_entry(row)
    need_key = (pid, bid, row["slot"])
    if need_key not in td["need_keys"]:
        td["need_keys"].add(need_key)
        td["items_by_player"].setdefault(pid, []).append(item_entry)


def _serialize_tracks(track_data: dict, allowed_tracks: list[str]) -> dict:
    """Convert accumulated track data into the serializable response shape."""
    out = {}
    for track in allowed_tracks:
        td = track_data.get(track)
        if not td:
            continue
        entries = []
        for pid, pdata in td["players"].items():
            entries.append({**pdata, "items": td["items_by_player"].get(pid, [])})
        entries.sort(key=lambda e: (e["player_name"], e["player_id"]))
        out[track] = {
            "player_count": len(td["player_ids"]),
            "item_count": len(td["need_keys"]),
            "entries": entries,
        }
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/raid")
async def get_raid_needs(
    request: Request,
    include_initiates: bool = Query(True),
    include_offspec: bool = Query(False),
):
    """Aggregated roster gear needs by raid instance → boss → track.

    Columns: V / C / H / M (auto-hidden client-side when all zeros).
    Instance rows carry a rollup of their bosses' totals.
    """
    pool = _pool(request)

    async with pool.acquire() as conn:
        rows = await _fetch_needs_rows(conn, include_initiates, include_offspec)
        all_bids = list({r["desired_bid"] for r in rows if r["desired_bid"]})
        sources_by_bid = await _fetch_sources_for_instance_type(conn, all_bids, "raid")

    # Accumulate: instance_key → {meta, bosses: boss_key → {meta, track_data}}
    instances: dict[str, dict] = {}

    for row in rows:
        bid = row["desired_bid"]
        if not bid:
            continue
        equipped_bid = row["equipped_bid"]
        is_bis = bool(equipped_bid and equipped_bid == bid)
        if not (bid and not is_bis):
            continue  # player already has this exact item

        srcs = sources_by_bid.get(bid, [])
        if not srcs:
            continue  # no raid source known for this item

        item_e = _item_entry(row)

        for src in srcs:
            upgrade_tracks = _upgrade_tracks(
                row["equipped_track"],
                equipped_bid,
                bid,
                src["tracks"],
            )
            if not upgrade_tracks:
                continue

            inst_id = src["blizzard_instance_id"]
            inst_name = src["instance_name"] or "Unknown Instance"
            inst_key = str(inst_id) if inst_id else _slugify(inst_name)

            enc_id = src["blizzard_encounter_id"]
            enc_name = src["encounter_name"] or "Unknown Boss"
            boss_key = str(enc_id) if enc_id else _slugify(enc_name)

            if inst_key not in instances:
                instances[inst_key] = {
                    "name": inst_name,
                    "blizzard_instance_id": inst_id,
                    "bosses": {},
                }
            inst = instances[inst_key]

            if boss_key not in inst["bosses"]:
                inst["bosses"][boss_key] = {
                    "name": enc_name,
                    "blizzard_encounter_id": enc_id,
                    "tracks": {},
                }
            boss = inst["bosses"][boss_key]

            for track in upgrade_tracks:
                _add_to_track(boss["tracks"], track, row, item_e)

    # Serialize
    out_instances = []
    for _k, inst_data in sorted(
        instances.items(), key=lambda kv: kv[1].get("blizzard_instance_id") or 0
    ):
        out_bosses = []
        rollup_tracks: dict[str, dict] = {}

        for _bk, boss_data in sorted(
            inst_data["bosses"].items(),
            key=lambda kv: kv[1].get("blizzard_encounter_id") or 0,
        ):
            out_tracks = _serialize_tracks(boss_data["tracks"], ["V", "C", "H", "M"])
            if not out_tracks:
                continue
            out_bosses.append({
                "name": boss_data["name"],
                "blizzard_encounter_id": boss_data["blizzard_encounter_id"],
                "tracks": out_tracks,
            })
            # Accumulate rollup
            for track, td_raw in boss_data["tracks"].items():
                if track not in rollup_tracks:
                    rollup_tracks[track] = {"player_ids": set(), "item_count": 0}
                rollup_tracks[track]["player_ids"].update(td_raw["player_ids"])
                rollup_tracks[track]["item_count"] += len(td_raw["need_keys"])

        if not out_bosses:
            continue

        rollup = {
            track: {
                "player_count": len(rtd["player_ids"]),
                "item_count": rtd["item_count"],
            }
            for track, rtd in rollup_tracks.items()
        }
        out_instances.append({
            "name": inst_data["name"],
            "blizzard_instance_id": inst_data["blizzard_instance_id"],
            "rollup": rollup,
            "bosses": out_bosses,
        })

    return {"instances": out_instances}


@router.get("/dungeon")
async def get_dungeon_needs(
    request: Request,
    include_initiates: bool = Query(True),
    include_offspec: bool = Query(False),
):
    """Aggregated roster gear needs by M+ dungeon → track (C / H only).

    M-track (10+ vault) is intentionally excluded from the table columns
    in Phase 1E — it will be added as an optional view in a later phase.
    """
    pool = _pool(request)

    async with pool.acquire() as conn:
        rows = await _fetch_needs_rows(conn, include_initiates, include_offspec)
        all_bids = list({r["desired_bid"] for r in rows if r["desired_bid"]})
        sources_by_bid = await _fetch_sources_for_instance_type(conn, all_bids, "dungeon")

    # Accumulate: dungeon_key → {meta, track_data}
    # Dungeons: instance_name = dungeon name; encounter_name = boss (ignored for flat list)
    dungeons: dict[str, dict] = {}

    for row in rows:
        bid = row["desired_bid"]
        if not bid:
            continue
        equipped_bid = row["equipped_bid"]
        is_bis = bool(equipped_bid and equipped_bid == bid)
        if not (bid and not is_bis):
            continue

        srcs = sources_by_bid.get(bid, [])
        if not srcs:
            continue

        item_e = _item_entry(row)

        for src in srcs:
            # M+ table shows C/H only (M-track = vault, deferred)
            available_tracks = [t for t in src["tracks"] if t in ("C", "H")]
            upgrade_tracks = _upgrade_tracks(
                row["equipped_track"],
                equipped_bid,
                bid,
                available_tracks,
            )
            if not upgrade_tracks:
                continue

            # Group by dungeon (instance_name), not by boss
            inst_id = src["blizzard_instance_id"]
            inst_name = src["instance_name"] or src["encounter_name"] or "Unknown Dungeon"
            dung_key = str(inst_id) if inst_id else _slugify(inst_name)

            if dung_key not in dungeons:
                dungeons[dung_key] = {
                    "name": inst_name,
                    "blizzard_instance_id": inst_id,
                    "tracks": {},
                }

            for track in upgrade_tracks:
                _add_to_track(dungeons[dung_key]["tracks"], track, row, item_e)

    # Serialize
    out_dungeons = []
    for _k, dung_data in sorted(
        dungeons.items(), key=lambda kv: kv[1].get("blizzard_instance_id") or 0
    ):
        out_tracks = _serialize_tracks(dung_data["tracks"], ["C", "H"])
        if not out_tracks:
            continue
        out_dungeons.append({
            "name": dung_data["name"],
            "blizzard_instance_id": dung_data["blizzard_instance_id"],
            "tracks": out_tracks,
        })

    return {"dungeons": out_dungeons}
