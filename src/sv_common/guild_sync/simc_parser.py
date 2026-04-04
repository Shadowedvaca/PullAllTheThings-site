"""SimC profile parser + export utility.

SimulationCraft (.simc) profiles are the universal WoW gear interchange
format — the in-game Simulationcraft addon exports your current gear, and
every major BIS site (Archon/u.gg, Wowhead, Icy Veins) has an
"Export SimC" or "Copy SimC" button.

Data model
----------
SimcSlot    — one gear slot (item_id + bonus_ids + enchant + gems + track)
SimcProfile — full profile (character metadata + list of SimcSlot)

All BIS extractors in bis_sync.py return list[SimcSlot].
All character_equipment rows normalise to SimcSlot before upsert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .quality_track import track_from_bonus_ids, SLOT_ORDER


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SimcSlot:
    """One gear slot from a SimC profile."""
    slot: str                              # normalised slot key (head, neck, …)
    blizzard_item_id: int
    bonus_ids: list[int] = field(default_factory=list)
    enchant_id: Optional[int] = None
    gem_ids: list[int] = field(default_factory=list)
    quality_track: Optional[str] = None   # V/C/H/M — derived from bonus_ids if absent


@dataclass
class SimcProfile:
    """Parsed SimC profile — character metadata + gear slots."""
    char_name: str = ""
    spec: str = ""
    wow_class: str = ""
    level: int = 80
    race: str = ""
    region: str = "us"
    server: str = ""
    slots: list[SimcSlot] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Slot name normalisation (SimC slot names → our internal keys)
# ---------------------------------------------------------------------------

_SIMC_SLOT_MAP: dict[str, str] = {
    "head":        "head",
    "neck":        "neck",
    "shoulder":    "shoulder",
    "shoulders":   "shoulder",
    "back":        "back",
    "chest":       "chest",
    "wrists":      "wrist",
    "wrist":       "wrist",
    "hands":       "hands",
    "waist":       "waist",
    "legs":        "legs",
    "feet":        "feet",
    "finger1":     "ring_1",
    "finger2":     "ring_2",
    "trinket1":    "trinket_1",
    "trinket2":    "trinket_2",
    "main_hand":   "main_hand",
    "off_hand":    "off_hand",
}

# Known WoW class identifiers used as the line key in SimC profiles
_CLASS_KEYS = {
    "druid", "warrior", "paladin", "hunter", "rogue", "priest",
    "shaman", "mage", "warlock", "monk", "death_knight",
    "demon_hunter", "evoker",
}

# Regex: slot=item_name,k=v,k=v,...
_GEAR_LINE_RE = re.compile(
    r"^(?P<slot>\w+)=(?P<item_name>[^,]*)"   # slot=item_name
    r"(?:,(?P<kvs>.+))?$"                     # optional ,key=val pairs
)


def _parse_int_list(value: str) -> list[int]:
    """Parse a colon-separated integer list like '4800:1517:8767'."""
    try:
        return [int(v) for v in value.split(":") if v]
    except ValueError:
        return []


def _parse_kv_pairs(kvs: str) -> dict[str, str]:
    """Split 'key=val,key=val' into a dict."""
    result: dict[str, str] = {}
    for part in kvs.split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = v.strip()
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_profile(text: str) -> SimcProfile:
    """Parse a full SimC profile text into a SimcProfile.

    Extracts character metadata (spec, class, realm, region) and all gear slots.
    Silently skips lines that don't look like SimC syntax.
    """
    profile = SimcProfile()

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip().strip('"')

        if key in _CLASS_KEYS:
            profile.wow_class = key
            profile.char_name = value
        elif key == "spec":
            profile.spec = value
        elif key == "level":
            try:
                profile.level = int(value)
            except ValueError:
                pass
        elif key == "race":
            profile.race = value
        elif key == "region":
            profile.region = value
        elif key in ("server", "realm"):
            profile.server = value
        else:
            slot = parse_gear_slot_line(line)
            if slot is not None:
                profile.slots.append(slot)

    return profile


def parse_gear_slots(text: str) -> list[SimcSlot]:
    """Extract only the gear slot lines from a SimC profile text.

    Ignores character metadata lines.  Returns one SimcSlot per recognised
    gear slot (up to 16 canonical slots).
    """
    slots: list[SimcSlot] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slot = parse_gear_slot_line(line)
        if slot is not None:
            slots.append(slot)
    return slots


def parse_gear_slot_line(line: str) -> Optional[SimcSlot]:
    """Parse a single SimC gear line.  Returns None if not a recognised slot.

    Example line::

        head=dreambinder_loom_of_the_great_cycle,id=208616,bonus_id=4800:1517:8767,enchant_id=7936
    """
    m = _GEAR_LINE_RE.match(line.strip())
    if not m:
        return None

    raw_slot = m.group("slot").lower()
    normalised = _SIMC_SLOT_MAP.get(raw_slot)
    if normalised is None:
        return None

    kvs_str = m.group("kvs") or ""
    kvs = _parse_kv_pairs(kvs_str)

    item_id_str = kvs.get("id", "")
    try:
        item_id = int(item_id_str)
    except ValueError:
        return None  # no valid item ID — skip

    bonus_ids = _parse_int_list(kvs.get("bonus_id", ""))
    enchant_str = kvs.get("enchant_id", "")
    enchant_id = int(enchant_str) if enchant_str.isdigit() else None
    gem_ids = _parse_int_list(kvs.get("gem_id", ""))

    quality_track = track_from_bonus_ids(bonus_ids) if bonus_ids else None

    return SimcSlot(
        slot=normalised,
        blizzard_item_id=item_id,
        bonus_ids=bonus_ids,
        enchant_id=enchant_id,
        gem_ids=gem_ids,
        quality_track=quality_track,
    )


def bonus_ids_to_quality_track(
    bonus_ids: list[int],
    custom_map: Optional[dict[str, list[int]]] = None,
) -> Optional[str]:
    """Map a bonus_id list to V/C/H/M via quality_track.py's mapping."""
    return track_from_bonus_ids(bonus_ids, custom_map)


def export_gear_plan(
    plan_slots: list[dict],
    char_name: str,
    spec: str,
    wow_class: str,
    realm: str = "senjin",
    region: str = "us",
) -> str:
    """Generate a SimC profile text from gear_plan_slots rows.

    Args:
        plan_slots: list of dicts with keys: slot, blizzard_item_id,
                    item_name, bonus_ids (optional), enchant_id (optional),
                    gem_ids (optional)
        char_name:  WoW character name
        spec:       spec slug (e.g. "balance")
        wow_class:  class slug (e.g. "druid")
        realm:      realm slug (default "senjin")
        region:     region code (default "us")
    """
    lines = [
        f'{wow_class}="{char_name}"',
        f"spec={spec}",
        "level=80",
        f"region={region}",
        f"server={realm}",
        "",
    ]

    slot_index = {s: i for i, s in enumerate(SLOT_ORDER)}
    sorted_slots = sorted(
        plan_slots,
        key=lambda s: slot_index.get(s.get("slot", ""), 99),
    )

    for s in sorted_slots:
        slot = s.get("slot", "")
        item_id = s.get("blizzard_item_id")
        item_name_raw = s.get("item_name", "unknown")
        if not item_id:
            continue

        item_name = item_name_raw.lower().replace(" ", "_")
        parts = [f"{slot}={item_name}", f"id={item_id}"]

        bonus_ids = s.get("bonus_ids") or []
        if bonus_ids:
            parts.append(f"bonus_id={':'.join(str(b) for b in bonus_ids)}")
        enchant_id = s.get("enchant_id")
        if enchant_id:
            parts.append(f"enchant_id={enchant_id}")
        gem_ids = s.get("gem_ids") or []
        if gem_ids:
            parts.append(f"gem_id={':'.join(str(g) for g in gem_ids)}")

        lines.append(",".join(parts))

    return "\n".join(lines)
