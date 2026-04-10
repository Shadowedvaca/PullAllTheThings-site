"""Item source sync — populate item_sources from Blizzard Journal API.

Fetches loot tables for the current expansion's raid + M+ dungeons and
upserts into guild_identity.item_sources.

instance_type values:
  'raid'       — regular raid boss
  'world_boss' — outdoor world boss (raid instance named after the expansion)
  'dungeon'    — M+ dungeon encounter

Track assignments and display names live in source_config.py.
Re-run "Sync Loot Tables" only when Blizzard data changes (new season/content).
Display/track rule changes only require a code deploy — no re-sync.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from .blizzard_client import BlizzardClient
from .source_config import get_tracks

logger = logging.getLogger(__name__)

# Delay between encounter fetches to avoid hammering the API.
_ENCOUNTER_DELAY = 0.2

# Tier set item slots — the only slots where Catalyst-obtained items can exist.
_TIER_SLOTS = {"head", "shoulder", "chest", "hands", "legs"}


async def sync_item_sources(
    pool: asyncpg.Pool,
    client: BlizzardClient,
    expansion_id: Optional[int] = None,
) -> dict:
    """Sync item→source mappings for the latest (or given) expansion.

    Stores raw API data — instance names, encounter names, instance type.
    Track assignment and display names are derived at read time from source_config.

    Returns a summary dict with expansion_name, instances_synced,
    encounters_synced, items_upserted, catalyst_tier_items, errors.
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

    # Collect instances.  Blizzard groups world boss encounters under a
    # synthetic raid instance named after the expansion — classify those as
    # 'world_boss'.  All other raid instances are 'raid'; M+ are 'dungeon'.
    # instance_name is stored RAW from the API; display name is derived by
    # source_config at read time.
    instances: list[dict] = []
    for inst in exp_data.get("dungeons", []):
        instances.append({"id": inst["id"], "name": inst.get("name", ""), "type": "dungeon"})
    for inst in exp_data.get("raids", []):
        inst_name = inst.get("name", "")
        inst_type = "world_boss" if inst_name == expansion_name else "raid"
        instances.append({"id": inst["id"], "name": inst_name, "type": inst_type})

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

    # ── 3. Tier set / Catalyst enrichment ─────────────────────────────────
    catalyst_added, catalyst_errors = await enrich_catalyst_tier_items(pool)
    errors.extend(catalyst_errors)

    return {
        "expansion_name": expansion_name,
        "instances_synced": instances_synced,
        "encounters_synced": total_encounters,
        "items_upserted": total_items,
        "catalyst_tier_items": catalyst_added,
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

    enc_section = inst_data.get("encounters", {})
    if isinstance(enc_section, dict):
        encounter_list = enc_section.get("encounters", [])
    else:
        encounter_list = enc_section if isinstance(enc_section, list) else []

    if not encounter_list:
        logger.debug("No encounters in instance %s (%d)", instance_name, instance_id)
        return 0, 0, []

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
                instance_type,
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
    instance_type: str,
) -> tuple[int, list[str]]:
    """Fetch items for one encounter and upsert into item_sources.

    Stores raw names and instance_type.  No quality_tracks column.
    Returns (items_upserted, errors).
    """
    errors: list[str] = []

    enc_data = await client.get_journal_encounter(encounter_id)
    if not enc_data:
        return 0, [f"Could not fetch encounter {encounter_id} from Blizzard API"]

    items = enc_data.get("items", [])
    if not items:
        return 0, []

    upserted = 0

    async with pool.acquire() as conn:
        for item_entry in items:
            item_obj = item_entry.get("item") or {}
            blizzard_item_id = item_obj.get("id")
            if not blizzard_item_id:
                continue

            item_name = item_obj.get("name", "")

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

            row = await conn.fetchrow(
                "SELECT id FROM guild_identity.wow_items WHERE blizzard_item_id = $1",
                blizzard_item_id,
            )
            if row is None:
                continue
            wow_item_id = row["id"]

            try:
                await conn.execute(
                    """
                    INSERT INTO guild_identity.item_sources
                           (item_id, instance_type, encounter_name, instance_name,
                            blizzard_encounter_id, blizzard_instance_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (item_id, instance_type, encounter_name)
                    DO UPDATE SET
                        instance_name         = EXCLUDED.instance_name,
                        blizzard_encounter_id = EXCLUDED.blizzard_encounter_id,
                        blizzard_instance_id  = EXCLUDED.blizzard_instance_id
                    """,
                    wow_item_id,
                    instance_type,
                    encounter_name,
                    instance_name,
                    encounter_id,
                    instance_id,
                )
                upserted += 1
            except Exception as exc:
                errors.append(
                    f"DB error upserting item {blizzard_item_id} "
                    f"({encounter_name}): {exc}"
                )

    return upserted, errors


async def enrich_catalyst_tier_items(
    pool: asyncpg.Pool,
) -> tuple[int, list[str]]:
    """Add per-boss source rows for tier set BIS items obtained via Revival Catalyst.

    Tier set pieces don't appear in the Blizzard Journal encounter item lists.
    Detection: Wowhead tooltip HTML contains an /item-set=N/ link.
    For each such item, mirror source rows of all bosses that drop gear in the
    same slot — those are the bosses a player farms to get a Catalyst piece.

    Returns (rows_added, errors).
    """
    errors: list[str] = []

    async with pool.acquire() as conn:
        # Remove stale generic "Revival Catalyst" rows (replaced by per-boss rows).
        await conn.execute(
            """
            DELETE FROM guild_identity.item_sources
             WHERE encounter_name = 'Revival Catalyst'
               AND item_id IN (
                   SELECT wi.id FROM guild_identity.wow_items wi
                    WHERE wi.wowhead_tooltip_html LIKE '%/item-set=%'
               )
            """
        )

        # Find all tier set items in BIS data (identified by item-set tooltip link).
        tier_items = await conn.fetch(
            """
            SELECT DISTINCT wi.id AS wow_item_id, wi.blizzard_item_id, wi.name,
                   COALESCE(NULLIF(wi.slot_type, 'other'), ble.slot) AS eff_slot
              FROM guild_identity.bis_list_entries ble
              JOIN guild_identity.wow_items wi ON wi.id = ble.item_id
             WHERE ble.slot = ANY($1::text[])
               AND wi.wowhead_tooltip_html LIKE '%/item-set=%'
            """,
            list(_TIER_SLOTS),
        )

        if not tier_items:
            return 0, []

        # Build slot → [(encounter_name, instance_name, instance_type)] from
        # existing boss sources (raid + world_boss).
        boss_rows = await conn.fetch(
            """
            SELECT DISTINCT is2.encounter_name, is2.instance_name,
                            is2.instance_type, wi.slot_type
              FROM guild_identity.item_sources is2
              JOIN guild_identity.wow_items wi ON wi.id = is2.item_id
             WHERE is2.instance_type IN ('raid', 'world_boss')
               AND wi.slot_type = ANY($1::text[])
            """,
            list(_TIER_SLOTS),
        )

        slot_to_bosses: dict[str, list[tuple[str, str, str]]] = {}
        for r in boss_rows:
            st = r["slot_type"]
            if st:
                slot_to_bosses.setdefault(st, []).append(
                    (r["encounter_name"], r["instance_name"], r["instance_type"])
                )

        logger.info(
            "Adding Catalyst boss-level source rows for %d tier set items", len(tier_items)
        )

        added = 0
        for tier in tier_items:
            bosses = slot_to_bosses.get(tier["eff_slot"], [])
            if not bosses:
                bosses = [("Revival Catalyst", "Revival Catalyst", "raid")]

            for enc_name, inst_name, inst_type in bosses:
                try:
                    await conn.execute(
                        """
                        INSERT INTO guild_identity.item_sources
                               (item_id, instance_type, encounter_name, instance_name)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (item_id, instance_type, encounter_name)
                        DO UPDATE SET
                            instance_name = EXCLUDED.instance_name
                        """,
                        tier["wow_item_id"],
                        inst_type,
                        enc_name,
                        inst_name,
                    )
                    added += 1
                except Exception as exc:
                    errors.append(
                        f"DB error for {tier['blizzard_item_id']} ({tier['name']}) "
                        f"boss {enc_name!r}: {exc}"
                    )

    return added, errors


async def sync_legacy_expansion_dungeons(
    pool: asyncpg.Pool,
    client: BlizzardClient,
) -> dict:
    """Sync dungeon instances from all expansions except the current one.

    The main sync_item_sources() only covers the latest expansion.  Mythic+
    seasons often include dungeons from prior expansions, so this fills that
    gap.  Raids and world bosses are intentionally skipped — they don't drop
    current-season gear.

    Returns a summary dict with expansions_checked, instances_synced,
    encounters_synced, items_upserted, errors.
    """
    errors: list[str] = []

    tiers = await client.get_journal_expansion_index()
    if not tiers:
        return {
            "expansions_checked": 0,
            "instances_synced": 0,
            "encounters_synced": 0,
            "items_upserted": 0,
            "errors": ["Could not fetch expansion index from Blizzard API"],
        }

    # Sort ascending; the last entry is the current expansion — skip it.
    sorted_tiers = sorted(tiers, key=lambda t: t.get("id", 0))
    if not sorted_tiers:
        return {
            "expansions_checked": 0,
            "instances_synced": 0,
            "encounters_synced": 0,
            "items_upserted": 0,
            "errors": ["No expansions returned by API"],
        }

    current_id = sorted_tiers[-1]["id"]
    legacy_tiers = sorted_tiers[:-1]

    logger.info(
        "Syncing legacy dungeons from %d prior expansion(s) (current: %d)",
        len(legacy_tiers), current_id,
    )

    total_encounters = 0
    total_items = 0
    instances_synced = 0
    expansions_checked = 0

    for tier in legacy_tiers:
        exp_id = tier["id"]
        exp_name = tier.get("name", f"Expansion {exp_id}")

        exp_data = await client.get_journal_expansion(exp_id)
        if not exp_data:
            errors.append(f"Could not fetch expansion {exp_id} ({exp_name})")
            continue

        expansions_checked += 1
        dungeons = exp_data.get("dungeons", [])
        if not dungeons:
            logger.debug("No dungeons in expansion %s (%d)", exp_name, exp_id)
            continue

        for inst in dungeons:
            inst_id = inst.get("id")
            inst_name = inst.get("name", "")
            if not inst_id:
                continue
            try:
                enc_count, item_count, inst_errors = await _sync_instance(
                    pool, client, inst_id, inst_name, "dungeon"
                )
                total_encounters += enc_count
                total_items += item_count
                errors.extend(inst_errors)
                instances_synced += 1
                logger.info(
                    "Legacy dungeon %s (%d): %d encounters, %d items",
                    inst_name, inst_id, enc_count, item_count,
                )
            except Exception as exc:
                msg = f"Failed to sync legacy dungeon {inst_name!r} (id={inst_id}): {exc}"
                logger.error(msg)
                errors.append(msg)

    return {
        "expansions_checked": expansions_checked,
        "instances_synced": instances_synced,
        "encounters_synced": total_encounters,
        "items_upserted": total_items,
        "errors": errors,
    }


async def flag_junk_sources(
    pool: asyncpg.Pool,
    flag_tier_pieces: bool = False,
) -> dict:
    """Mark suspected-junk rows in item_sources with is_suspected_junk = TRUE.

    Safe to re-run — clears all flags first, then re-applies.

    Category 1 (always): Null-ID world boss rows — instance_type = 'world_boss'
    with no valid Blizzard encounter/instance IDs.  These are alpha/beta
    artifacts with no usable location data.

    Category 2 (flag_tier_pieces=True only): Tier piece direct-source rows —
    the linked wow_items has a set bonus (tooltip contains /item-set=) in a
    tier slot (head/shoulder/chest/hands/legs).  Tier pieces are obtained via
    tier tokens, not direct drops.  This flag is only safe to set after
    v_tier_piece_sources (Phase 1D.5) is in place as the replacement display
    path; calling with flag_tier_pieces=True before that view exists will
    cause tier piece slots to show "No drop source data".

    Returns {flagged_world_boss, flagged_tier_piece, total_flagged}.
    """
    async with pool.acquire() as conn:
        # ── 1. Clear all existing flags so re-runs are idempotent ─────────
        await conn.execute(
            "UPDATE guild_identity.item_sources SET is_suspected_junk = FALSE"
        )

        # ── 2. Flag null-ID world boss rows ───────────────────────────────
        # Only rows with no encounter name AND no IDs — completely empty stubs
        # with no useful display data.  Rows that have an encounter_name but
        # null IDs are incomplete syncs (re-running Sync Loot Tables fixes
        # them) and should NOT be suppressed — they still have display value.
        wb_result = await conn.execute(
            """
            UPDATE guild_identity.item_sources
               SET is_suspected_junk = TRUE
             WHERE instance_type = 'world_boss'
               AND blizzard_encounter_id IS NULL
               AND blizzard_instance_id IS NULL
               AND (encounter_name IS NULL OR encounter_name = '')
            """
        )
        flagged_wb = int(wb_result.split()[-1])

        # ── 3. Flag tier piece direct-source rows (Phase 1D.5 only) ───────
        # Only applied when the caller (process_tier_tokens) has already
        # created v_tier_piece_sources as the replacement display path.
        flagged_tp = 0
        if flag_tier_pieces:
            tp_result = await conn.execute(
                """
                UPDATE guild_identity.item_sources s
                   SET is_suspected_junk = TRUE
                  FROM guild_identity.wow_items wi
                 WHERE wi.id = s.item_id
                   AND wi.wowhead_tooltip_html LIKE '%/item-set=%'
                   AND wi.slot_type IN ('head', 'shoulder', 'chest', 'hands', 'legs')
                """
            )
            flagged_tp = int(tp_result.split()[-1])

    total = flagged_wb + flagged_tp
    logger.info(
        "flag_junk_sources: %d world_boss + %d tier_piece = %d total flagged",
        flagged_wb, flagged_tp, total,
    )
    return {
        "flagged_world_boss": flagged_wb,
        "flagged_tier_piece": flagged_tp,
        "total_flagged": total,
    }


# WoW class ID → armor type mapping
# Cloth: Priest(5), Mage(8), Warlock(9)
# Leather: Rogue(4), Druid(11), Monk(10), Demon Hunter(12)
# Mail: Hunter(3), Shaman(7), Evoker(13)
# Plate: Warrior(1), Paladin(2), Death Knight(6)
_CLASS_ARMOR_TYPE: dict[int, str] = {
    1: "plate",   # Warrior
    2: "plate",   # Paladin
    3: "mail",    # Hunter
    4: "leather", # Rogue
    5: "cloth",   # Priest
    6: "plate",   # Death Knight
    7: "mail",    # Shaman
    8: "cloth",   # Mage
    9: "cloth",   # Warlock
    10: "leather", # Monk
    11: "leather", # Druid
    12: "leather", # Demon Hunter
    13: "mail",   # Evoker
}

# Regex patterns for tier token and armor type tooltip parsing
_SLOT_RE = re.compile(r"Synthesize a soulbound set (\w+) item", re.IGNORECASE)
_CLASS_HREF_RE = re.compile(r'href="/class=(\d+)/', re.IGNORECASE)
# Matches the armor type word that appears immediately before "Armor</span>" in Wowhead tooltips.
# e.g. "...Plate<br />\n<span class=\"...">Armor</span>..." → 'plate'
_ARMOR_WORD_RE = re.compile(r'>(Cloth|Leather|Mail|Plate)<', re.IGNORECASE)

# Normalise slot words from tooltip text to the canonical slot names used in
# the gear plan.  The Use text says "hand" not "hands", etc.
_SLOT_NORMALISE: dict[str, str] = {
    "hand": "hands",
    "hands": "hands",
    "head": "head",
    "helm": "head",
    "shoulder": "shoulder",
    "shoulders": "shoulder",
    "chest": "chest",
    "legs": "legs",
    "leg": "legs",
}


def _parse_token_slot(tooltip_html: str) -> str:
    """Extract target_slot from a tier token's Wowhead tooltip HTML.

    Returns a canonical slot name ('head', 'shoulder', 'chest', 'hands',
    'legs') or 'any' when no slot word is found in the Use effect text.
    """
    m = _SLOT_RE.search(tooltip_html)
    if m:
        word = m.group(1).lower()
        return _SLOT_NORMALISE.get(word, word)
    return "any"


def _parse_token_class_ids(tooltip_html: str) -> list[int]:
    """Extract eligible class IDs from the Wowhead Classes div in tooltip HTML.

    Returns a list of integer class IDs, or [] if no Classes div is present
    (meaning the token is usable by all classes — e.g. Chiming Void Curio).
    """
    if 'wowhead-tooltip-item-classes' not in tooltip_html:
        return []
    return [int(cid) for cid in _CLASS_HREF_RE.findall(tooltip_html)]


def _armor_type_from_class_ids(class_ids: list[int]) -> str:
    """Derive armor type from a list of eligible class IDs.

    Returns 'any' if the list is empty or covers multiple armor types.
    When all eligible classes share one armor type, returns that type.
    """
    if not class_ids:
        return "any"
    types = {_CLASS_ARMOR_TYPE.get(cid) for cid in class_ids if cid in _CLASS_ARMOR_TYPE}
    types.discard(None)
    if len(types) == 1:
        return types.pop()
    return "any"


def _armor_type_from_tooltip(tooltip_html: str) -> Optional[str]:
    """Parse armor type (cloth/leather/mail/plate) from Wowhead tooltip HTML.

    Wowhead renders armor type as e.g. '>Leather<' in the tooltip.  Returns
    None if no recognisable armor type word is found (e.g. weapons, trinkets).
    """
    if not tooltip_html:
        return None
    m = _ARMOR_WORD_RE.search(tooltip_html)
    return m.group(1).lower() if m else None


def _is_tier_token(tooltip_html: str) -> bool:
    """Return True if this item's tooltip marks it as a tier set token."""
    if not tooltip_html:
        return False
    return (
        "Synthesize a soulbound set" in tooltip_html
        or "trade this for powerful class set armor" in tooltip_html.lower()
    )


async def process_tier_tokens(pool: asyncpg.Pool) -> dict:
    """Detect tier token items, populate tier_token_attrs, then flag junk sources.

    Steps:
    1. Find wow_items with slot_type='other' whose tooltip HTML indicates a
       tier token (contains 'Synthesize a soulbound set' or equivalent).
    2. For each token, parse target_slot, eligible_class_ids, armor_type
       from the Wowhead tooltip HTML.
    3. Upsert into tier_token_attrs — skips rows where is_manual_override=TRUE.
    4. Call flag_junk_sources(flag_tier_pieces=True) to suppress stale
       direct-drop rows for tier pieces now that v_tier_piece_sources exists.
    5. Return a summary dict.
    """
    now = datetime.now(tz=timezone.utc)

    async with pool.acquire() as conn:
        # ── 1. Find candidate tier token items ────────────────────────────
        candidates = await conn.fetch(
            """
            SELECT id, blizzard_item_id, name, wowhead_tooltip_html
              FROM guild_identity.wow_items
             WHERE slot_type = 'other'
               AND wowhead_tooltip_html IS NOT NULL
               AND wowhead_tooltip_html != ''
            """
        )

    tokens_processed = 0
    tokens_skipped_override = 0
    token_ids_found: list[int] = []

    async with pool.acquire() as conn:
        for row in candidates:
            html = row["wowhead_tooltip_html"] or ""
            if not _is_tier_token(html):
                continue

            item_id = row["id"]
            token_ids_found.append(item_id)

            # Check for manual override — never clobber admin edits
            existing = await conn.fetchrow(
                "SELECT is_manual_override FROM guild_identity.tier_token_attrs WHERE token_item_id = $1",
                item_id,
            )
            if existing and existing["is_manual_override"]:
                tokens_skipped_override += 1
                logger.info(
                    "process_tier_tokens: skipping item %d (%s) — manual override set",
                    row["blizzard_item_id"], row["name"],
                )
                # Still update last_processed timestamp so the admin can see it was checked
                await conn.execute(
                    "UPDATE guild_identity.tier_token_attrs SET last_processed = $1 WHERE token_item_id = $2",
                    now, item_id,
                )
                continue

            target_slot = _parse_token_slot(html)
            class_ids = _parse_token_class_ids(html)
            armor_type = _armor_type_from_class_ids(class_ids)

            await conn.execute(
                """
                INSERT INTO guild_identity.tier_token_attrs
                       (token_item_id, target_slot, armor_type, eligible_class_ids,
                        is_auto_detected, is_manual_override, last_processed)
                VALUES ($1, $2, $3, $4, TRUE, FALSE, $5)
                ON CONFLICT (token_item_id) DO UPDATE SET
                    target_slot        = EXCLUDED.target_slot,
                    armor_type         = EXCLUDED.armor_type,
                    eligible_class_ids = EXCLUDED.eligible_class_ids,
                    is_auto_detected   = TRUE,
                    last_processed     = EXCLUDED.last_processed
                """,
                item_id, target_slot, armor_type, class_ids, now,
            )
            tokens_processed += 1
            logger.info(
                "process_tier_tokens: upserted item %d (%s) slot=%s armor=%s classes=%s",
                row["blizzard_item_id"], row["name"], target_slot, armor_type, class_ids,
            )

    logger.info(
        "process_tier_tokens: %d tokens processed, %d skipped (override), %d total found",
        tokens_processed, tokens_skipped_override, len(token_ids_found),
    )

    # ── Step 2: Backfill armor_type on tier piece wow_items ───────────────
    # The Wowhead tooltip HTML contains the armor type as ">Cloth<", ">Leather<",
    # etc.  Parse and write back to wow_items.armor_type so that the view join
    # (tp.armor_type = tta.armor_type) resolves correctly.
    # Only updates rows where armor_type is currently NULL — already-correct rows
    # are left untouched.
    tier_pieces_updated = 0
    async with pool.acquire() as conn:
        tier_piece_rows = await conn.fetch(
            """
            SELECT id, blizzard_item_id, name, wowhead_tooltip_html
              FROM guild_identity.wow_items
             WHERE slot_type = ANY($1::text[])
               AND wowhead_tooltip_html LIKE '%/item-set=%'
               AND (armor_type IS NULL OR armor_type = '')
            """,
            list(_TIER_SLOTS),
        )
        for tp_row in tier_piece_rows:
            at = _armor_type_from_tooltip(tp_row["wowhead_tooltip_html"] or "")
            if at:
                await conn.execute(
                    "UPDATE guild_identity.wow_items SET armor_type = $1 WHERE id = $2",
                    at, tp_row["id"],
                )
                tier_pieces_updated += 1
                logger.debug(
                    "process_tier_tokens: set armor_type=%s on item %d (%s)",
                    at, tp_row["blizzard_item_id"], tp_row["name"],
                )

    logger.info(
        "process_tier_tokens: backfilled armor_type on %d tier piece items",
        tier_pieces_updated,
    )

    # ── Step 3: Flag junk sources now that view is in place ───────────────
    junk_result = await flag_junk_sources(pool, flag_tier_pieces=True)

    return {
        "tokens_found": len(token_ids_found),
        "tokens_processed": tokens_processed,
        "tokens_skipped_override": tokens_skipped_override,
        "tier_pieces_armor_type_updated": tier_pieces_updated,
        "junk_flagged": junk_result["total_flagged"],
        "junk_world_boss": junk_result["flagged_world_boss"],
        "junk_tier_piece": junk_result["flagged_tier_piece"],
    }


async def get_item_sources(
    pool: asyncpg.Pool,
    instance_name: Optional[str] = None,
    instance_id: Optional[int] = None,
    instance_type: Optional[str] = None,
    show_junk: bool = False,
    limit: int = 500,
) -> list[dict]:
    """Query item sources, optionally filtered.

    Returns rows with item metadata joined from wow_items.
    Junk rows (is_suspected_junk = TRUE) are excluded by default;
    pass show_junk=True to include them.
    """
    conditions = ["1=1"]
    args: list = []

    if not show_junk:
        conditions.append("NOT s.is_suspected_junk")

    if instance_id is not None:
        args.append(instance_id)
        conditions.append(f"s.blizzard_instance_id = ${len(args)}")
    if instance_name:
        args.append(instance_name)
        conditions.append(f"s.instance_name = ${len(args)}")
    if instance_type:
        args.append(instance_type)
        conditions.append(f"s.instance_type = ${len(args)}")

    args.append(limit)
    where = " AND ".join(conditions)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT s.id, s.instance_type, s.encounter_name, s.instance_name,
                   s.blizzard_encounter_id, s.blizzard_instance_id,
                   s.is_suspected_junk,
                   wi.blizzard_item_id, wi.name AS item_name,
                   wi.slot_type, wi.icon_url
              FROM guild_identity.item_sources s
              JOIN guild_identity.wow_items wi ON wi.id = s.item_id
             WHERE {where}
             ORDER BY s.instance_name, s.encounter_name, wi.slot_type, wi.name
             LIMIT ${len(args)}
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def get_instance_names(
    pool: asyncpg.Pool,
    show_junk: bool = False,
) -> list[str]:
    """Return distinct instance_name values for filter dropdowns.

    Excludes junk rows by default so the dropdown only shows real instances.
    """
    junk_filter = "" if show_junk else "AND NOT is_suspected_junk"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT DISTINCT instance_name
              FROM guild_identity.item_sources
             WHERE instance_name IS NOT NULL
               {junk_filter}
             ORDER BY instance_name
            """
        )
    return [r["instance_name"] for r in rows]
