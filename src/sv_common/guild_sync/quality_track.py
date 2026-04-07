"""Quality track detection for WoW gear items.

Parses the V/C/H/M quality track from Blizzard API equipment data and from
SimC bonus IDs.  The track letter maps to WoW's upgrade track system:

    V = Veteran  (green)  — Raid Finder
    C = Champion (blue)   — Normal Raid / M+ 0-5
    H = Hero     (purple) — Heroic Raid / M+ 6+
    M = Mythic   (orange) — Mythic Raid only
"""

import re
from typing import Optional

# Blizzard display_string → track letter
_DISPLAY_PATTERN = re.compile(
    r"^(Veteran|Champion|Hero|Mythic)\s+\d+/\d+$", re.IGNORECASE
)
_DISPLAY_MAP = {
    "veteran": "V",
    "champion": "C",
    "hero": "H",
    "mythic": "M",
}

# SimC bonus ID → quality track for The War Within Season 2.
# These IDs are season-specific.  The admin can update them via site_config
# key "simc_track_bonus_ids" (JSON: {"C": [ids], "H": [ids], "M": [ids]}).
_DEFAULT_SIMC_BONUS_IDS: dict[str, list[int]] = {
    "V": [1498, 1499],
    "C": [1516, 1517, 1518],
    "H": [1520, 1521, 1522],
    "M": [1524, 1525, 1526],
}


def track_from_display_string(display_string: Optional[str]) -> Optional[str]:
    """Parse V/C/H/M from Blizzard name_description.display_string.

    e.g. "Champion 4/8" → "C", "Hero 2/8" → "H", "Veteran 1/8" → "V"
    Returns None if not an upgrade-track item.
    """
    if not display_string:
        return None
    m = _DISPLAY_PATTERN.match(display_string.strip())
    if not m:
        return None
    return _DISPLAY_MAP.get(m.group(1).lower())


def track_from_bonus_ids(
    bonus_ids: list[int],
    custom_map: Optional[dict[str, list[int]]] = None,
) -> Optional[str]:
    """Derive V/C/H/M from a list of SimC bonus IDs.

    Uses the built-in TWW S2 mapping by default.  Pass a custom_map from
    site_config.simc_track_bonus_ids to override for a new season.
    """
    mapping = custom_map if custom_map is not None else _DEFAULT_SIMC_BONUS_IDS
    bonus_set = set(bonus_ids)
    for track, ids in mapping.items():
        if bonus_set & set(ids):
            return track
    return None


def detect_quality_track(
    display_string: Optional[str],
    bonus_ids: Optional[list[int]] = None,
    custom_bonus_map: Optional[dict[str, list[int]]] = None,
) -> Optional[str]:
    """Best-effort quality track detection — tries display_string first, then bonus IDs.

    Args:
        display_string: Blizzard name_description.display_string value.
        bonus_ids: List of bonus IDs from the item (SimC or Blizzard).
        custom_bonus_map: Season-specific override from site_config.
    """
    track = track_from_display_string(display_string)
    if track:
        return track
    if bonus_ids:
        return track_from_bonus_ids(bonus_ids, custom_bonus_map)
    return None


# Canonical 16-slot ordering (WoW character sheet order)
SLOT_ORDER = [
    "head", "neck", "shoulder", "back", "chest", "wrist",
    "hands", "waist", "legs", "feet",
    "ring_1", "ring_2", "trinket_1", "trinket_2",
    "main_hand", "off_hand",
]

# Blizzard API slot_type string → normalised slot key
BLIZZARD_SLOT_MAP: dict[str, str] = {
    "HEAD":       "head",
    "NECK":       "neck",
    "SHOULDER":   "shoulder",
    "BACK":       "back",
    "CHEST":      "chest",
    "WRIST":      "wrist",
    "WRISTBAND":  "wrist",
    "HANDS":      "hands",
    "WAIST":      "waist",
    "LEGS":       "legs",
    "FEET":       "feet",
    "FINGER_1":   "ring_1",
    "FINGER_2":   "ring_2",
    "TRINKET_1":  "trinket_1",
    "TRINKET_2":  "trinket_2",
    "MAIN_HAND":  "main_hand",
    "OFF_HAND":   "off_hand",
    "TWOHWEAPON": "main_hand",
    "RANGED":     "main_hand",
}


def normalize_slot(blizzard_slot: str) -> Optional[str]:
    """Convert a Blizzard slot_type string to our normalised slot key.

    Returns None for slots we don't track (TABARD, SHIRT, etc.).
    """
    return BLIZZARD_SLOT_MAP.get(blizzard_slot.upper())


# Crafted item bonus IDs (TWW; update for new expansions as needed)
_CRAFTED_BONUS_IDS: frozenset[int] = frozenset({1808})


def is_crafted_item(bonus_ids: list[int]) -> bool:
    """Return True if the item is crafted gear (not on V/C/H/M upgrade track)."""
    if not bonus_ids:
        return False
    return bool(frozenset(bonus_ids) & _CRAFTED_BONUS_IDS)
