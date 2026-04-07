"""Item source sync — populate item_sources from Blizzard Journal API.

Fetches loot tables for the current expansion's raid + M+ dungeons and
upserts into guild_identity.item_sources.

Quality tracks inferred from source type:
  - Raid boss  → {C, H, M}
  - M+ dungeon → {C, H}

Run once per season via Admin → Gear Plan (Item Sources section).

Stub wow_items rows are created for any item not yet in the cache; full
metadata (icon, slot type, tooltip) is populated lazily by item_service.py
when the item is first viewed.
"""

import asyncio
import logging
from typing import Optional

import asyncpg

from .blizzard_client import BlizzardClient

logger = logging.getLogger(__name__)

_RAID_TRACKS: list[str] = ["V", "C", "H", "M"]
_DUNGEON_TRACKS: list[str] = ["C", "H"]

# Delay between encounter fetches to avoid hammering the API.
_ENCOUNTER_DELAY = 0.2


async def sync_item_sources(
    pool: asyncpg.Pool,
    client: BlizzardClient,
    expansion_id: Optional[int] = None,
) -> dict:
    """Sync item→source mappings for the latest (or given) expansion.

    1. Resolve expansion (index → max id, or use provided expansion_id).
    2. For each dungeon and raid instance, fetch encounter list.
    3. For each encounter, fetch items and upsert into item_sources.

    Returns a summary dict:
        expansion_name  — display name
        instances_synced — number of instances processed without fatal error
        encounters_synced — total encounter rows processed
        items_upserted  — total item_sources rows written
        errors          — list of non-fatal error strings
    """
    errors: list[str] = []

    # ── 1. Resolve expansion ───────────────────────────────────────────────
    if expansion_id is None:
        tiers = await client.get_journal_expansion_index()
        if not tiers:
            return {
                "expansion_name": None,
                "instances_synced": 0,
                "encounters_synced": 0,
                "items_upserted": 0,
                "errors": ["Could not fetch expansion index from Blizzard API"],
            }
        # Most recent expansion has the highest id.
        tier = max(tiers, key=lambda t: t.get("id", 0))
        expansion_id = tier["id"]
        expansion_name = tier.get("name", f"Expansion {expansion_id}")
    else:
        expansion_name = f"Expansion {expansion_id}"

    logger.info("Syncing item sources for expansion %d (%s)", expansion_id, expansion_name)

    exp_data = await client.get_journal_expansion(expansion_id)
    if not exp_data:
        return {
            "expansion_name": expansion_name,
            "instances_synced": 0,
            "encounters_synced": 0,
            "items_upserted": 0,
            "errors": [f"Could not fetch expansion {expansion_id} data from Blizzard API"],
        }

    expansion_name = exp_data.get("name", expansion_name)

    # Collect all instances: dungeons first, then raids.
    instances: list[dict] = []
    for inst in exp_data.get("dungeons", []):
        instances.append({"id": inst["id"], "name": inst.get("name", ""), "type": "dungeon"})
    for inst in exp_data.get("raids", []):
        instances.append({"id": inst["id"], "name": inst.get("name", ""), "type": "raid"})

    if not instances:
        return {
            "expansion_name": expansion_name,
            "instances_synced": 0,
            "encounters_synced": 0,
            "items_upserted": 0,
            "errors": ["No instances (dungeons or raids) found in expansion"],
        }

    # ── 2. Sync each instance ──────────────────────────────────────────────
    total_encounters = 0
    total_items = 0
    instances_synced = 0

    for inst in instances:
        inst_id = inst["id"]
        inst_name = inst["name"]
        inst_type = inst["type"]

        try:
            enc_count, item_count, inst_errors = await _sync_instance(
                pool, client, inst_id, inst_name, inst_type
            )
            total_encounters += enc_count
            total_items += item_count
            errors.extend(inst_errors)
            instances_synced += 1
            logger.info(
                "Instance %s (%d/%s): %d encounters, %d items",
                inst_name, inst_id, inst_type, enc_count, item_count,
            )
        except Exception as exc:
            msg = f"Failed to sync instance {inst_name!r} (id={inst_id}): {exc}"
            logger.error(msg)
            errors.append(msg)

    return {
        "expansion_name": expansion_name,
        "instances_synced": instances_synced,
        "encounters_synced": total_encounters,
        "items_upserted": total_items,
        "errors": errors,
    }


async def _sync_instance(
    pool: asyncpg.Pool,
    client: BlizzardClient,
    instance_id: int,
    instance_name: str,
    instance_type: str,
) -> tuple[int, int, list[str]]:
    """Sync all encounters for one instance.

    Returns (encounter_count, items_upserted, errors).
    """
    errors: list[str] = []

    inst_data = await client.get_journal_instance(instance_id)
    if not inst_data:
        return 0, 0, [f"Could not fetch instance {instance_id} from Blizzard API"]

    # encounters may be nested: {"encounters": {"encounters": [...]}}
    enc_section = inst_data.get("encounters", {})
    if isinstance(enc_section, dict):
        encounter_list = enc_section.get("encounters", [])
    else:
        encounter_list = enc_section if isinstance(enc_section, list) else []

    if not encounter_list:
        logger.debug("No encounters in instance %s (%d)", instance_name, instance_id)
        return 0, 0, []

    quality_tracks = _DUNGEON_TRACKS if instance_type == "dungeon" else _RAID_TRACKS
    total_items = 0

    for enc in encounter_list:
        enc_id = enc.get("id")
        enc_name = enc.get("name", "")
        if not enc_id:
            continue

        try:
            item_count, enc_errors = await _sync_encounter(
                pool, client,
                enc_id, enc_name,
                instance_id, instance_name,
                instance_type, quality_tracks,
            )
            total_items += item_count
            errors.extend(enc_errors)
        except Exception as exc:
            msg = f"Failed to sync encounter {enc_name!r} (id={enc_id}): {exc}"
            logger.warning(msg)
            errors.append(msg)

        await asyncio.sleep(_ENCOUNTER_DELAY)

    return len(encounter_list), total_items, errors


async def _sync_encounter(
    pool: asyncpg.Pool,
    client: BlizzardClient,
    encounter_id: int,
    encounter_name: str,
    instance_id: int,
    instance_name: str,
    source_type: str,
    quality_tracks: list[str],
) -> tuple[int, list[str]]:
    """Fetch items for one encounter and upsert into item_sources.

    Returns (items_upserted, errors).
    """
    errors: list[str] = []

    enc_data = await client.get_journal_encounter(encounter_id)
    if not enc_data:
        return 0, [f"Could not fetch encounter {encounter_id} from Blizzard API"]

    items = enc_data.get("items", [])
    if not items:
        return 0, []

    db_source_type = "raid_boss" if source_type == "raid" else "dungeon"
    upserted = 0

    async with pool.acquire() as conn:
        for item_entry in items:
            # Actual Blizzard item ID lives under item.id, not the top-level id
            # (the top-level id is the journal encounter-item join key).
            item_obj = item_entry.get("item") or {}
            blizzard_item_id = item_obj.get("id")
            if not blizzard_item_id:
                continue

            # Name is nested under item_entry["item"]["name"], not at the top level.
            item_name = item_obj.get("name", "")

            # Ensure a wow_items stub row exists.  Update name if it was previously
            # stored blank (first sync had the extraction bug); never overwrite
            # icon_url / slot_type that item_service has already populated.
            await conn.execute(
                """
                INSERT INTO guild_identity.wow_items
                       (blizzard_item_id, name, slot_type)
                VALUES ($1, $2, 'other')
                ON CONFLICT (blizzard_item_id) DO UPDATE SET
                    name = CASE
                        WHEN guild_identity.wow_items.name = '' OR
                             guild_identity.wow_items.name IS NULL
                        THEN EXCLUDED.name
                        ELSE guild_identity.wow_items.name
                    END
                """,
                blizzard_item_id,
                item_name,
            )

            # Look up the internal id (may have just been inserted above).
            row = await conn.fetchrow(
                "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
                blizzard_item_id,
            )
            if row is None:
                continue
            wow_item_id = row["id"]

            # Upsert into item_sources.
            try:
                await conn.execute(
                    """
                    INSERT INTO guild_identity.item_sources
                           (item_id, source_type, source_name, source_instance,
                            blizzard_encounter_id, blizzard_instance_id, quality_tracks)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (item_id, source_type, source_name)
                    DO UPDATE SET
                        source_instance       = EXCLUDED.source_instance,
                        blizzard_encounter_id = EXCLUDED.blizzard_encounter_id,
                        blizzard_instance_id  = EXCLUDED.blizzard_instance_id,
                        quality_tracks        = EXCLUDED.quality_tracks
                    """,
                    wow_item_id,
                    db_source_type,
                    encounter_name,
                    instance_name,
                    encounter_id,
                    instance_id,
                    quality_tracks,
                )
                upserted += 1
            except Exception as exc:
                errors.append(
                    f"DB error upserting item {blizzard_item_id} "
                    f"({encounter_name}): {exc}"
                )

    return upserted, errors


async def get_item_sources(
    pool: asyncpg.Pool,
    instance_name: Optional[str] = None,
    instance_id: Optional[int] = None,
    source_type: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """Query item sources, optionally filtered.

    Returns rows with item metadata joined from wow_items.
    """
    conditions = ["1=1"]
    args: list = []

    if instance_id is not None:
        args.append(instance_id)
        conditions.append(f"s.blizzard_instance_id = ${len(args)}")
    if instance_name:
        args.append(instance_name)
        conditions.append(f"s.source_instance = ${len(args)}")
    if source_type:
        args.append(source_type)
        conditions.append(f"s.source_type = ${len(args)}")

    args.append(limit)
    where = " AND ".join(conditions)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT s.id, s.source_type, s.source_name, s.source_instance,
                   s.blizzard_encounter_id, s.blizzard_instance_id,
                   s.quality_tracks,
                   wi.blizzard_item_id, wi.name AS item_name,
                   wi.slot_type, wi.icon_url
              FROM guild_identity.item_sources s
              JOIN guild_identity.wow_items wi ON wi.id = s.item_id
             WHERE {where}
             ORDER BY s.source_instance, s.source_name, wi.slot_type, wi.name
             LIMIT ${len(args)}
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def get_instance_names(pool: asyncpg.Pool) -> list[str]:
    """Return distinct source_instance values for filter dropdowns."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT source_instance
              FROM guild_identity.item_sources
             WHERE source_instance IS NOT NULL
             ORDER BY source_instance
            """
        )
    return [r["source_instance"] for r in rows]
