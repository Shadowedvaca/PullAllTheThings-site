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

# Blizzard display_string → track letter (TWW legacy format: "Hero 4/8")
_DISPLAY_PATTERN = re.compile(
    r"^(Veteran|Champion|Hero|Mythic)\s+\d+/\d+$", re.IGNORECASE
)
_DISPLAY_MAP = {
    "veteran": "V",
    "champion": "C",
    "hero": "H",
    "mythic": "M",
}

# Midnight expansion bare-word display_string format (no upgrade counter).
# "Heroic" and "Mythic+" are both Hero-tier quality; "Mythic" alone = Mythic raid.
_DISPLAY_MAP_BARE = {
    "veteran": "V",
    "champion": "C",
    "heroic": "H",
    "mythic+": "H",   # M+ drops — Hero-tier equivalent
    "mythic": "M",
}

# SimC bonus ID → quality track.
# TWW Season 2 IDs kept for backward compat; Midnight IDs appended.
# Admin can override via site_config key "simc_track_bonus_ids".
#
# Crafted-quality IDs (13621, 13622) are included here because they ARE
# quality-discriminating bonus IDs, just sourced from crests rather than
# upgrade tokens.  They're detected empirically via get_item_preview() during
# equipment sync (see _CRAFTED_TRACK_IDS below for discovery notes).
_DEFAULT_SIMC_BONUS_IDS: dict[str, list[int]] = {
    "V": [1498, 1499],
    "C": [1516, 1517, 1518, 12790, 12795],           # TWW S2 + Midnight base/normal
    "H": [1520, 1521, 1522, 12798, 12801, 13621],    # TWW S2 + Midnight heroic/M+ + Midnight crafted H
    "M": [1524, 1525, 1526, 13622],                  # TWW S2 + Midnight crafted M
}


def track_from_display_string(display_string: Optional[str]) -> Optional[str]:
    """Parse V/C/H/M from Blizzard name_description.display_string.

    Handles two formats:
    - TWW legacy: "Champion 4/8" → "C", "Hero 2/8" → "H"
    - Midnight bare: "Heroic" → "H", "Mythic+" → "H", "Champion" → "C"
    Returns None if not an upgrade-track item.
    """
    if not display_string:
        return None
    s = display_string.strip()
    # TWW legacy format: "Hero 4/8", "Champion 3/8", etc.
    m = _DISPLAY_PATTERN.match(s)
    if m:
        return _DISPLAY_MAP.get(m.group(1).lower())
    # Midnight bare-word format: "Heroic", "Mythic+", "Champion", etc.
    return _DISPLAY_MAP_BARE.get(s.lower())


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
    "main_hand_2h", "main_hand_1h", "off_hand",
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
    "MAIN_HAND":  "main_hand_1h",
    "OFF_HAND":   "off_hand",
    "TWOHWEAPON": "main_hand_2h",
    "RANGED":     "main_hand_2h",
}


def normalize_slot(blizzard_slot: str) -> Optional[str]:
    """Convert a Blizzard slot_type string to our normalised slot key.

    Returns None for slots we don't track (TABARD, SHIRT, etc.).
    """
    return BLIZZARD_SLOT_MAP.get(blizzard_slot.upper())


# Crafted item bonus IDs.
# TWW: 1808 = "Crafted by"; Midnight: 12214 appears on all Radiance Crafted items.
_CRAFTED_BONUS_IDS: frozenset[int] = frozenset({1808, 12214})

# Crafted crest-quality track bonus IDs.
# These are SEPARATE from _DEFAULT_SIMC_BONUS_IDS (which maps regular upgrade-track
# gear).  Crafted items are not on the V/C/H/M upgrade track — their quality comes
# from the type of crest used during crafting, encoded as a distinct bonus ID.
#
# IDs are discovered by calling get_item_preview(item_id, bonus_ids) during equipment
# sync: Blizzard returns "Heroic"/"Mythic" in preview_item.name_description.display_string
# when these IDs are present, letting us identify the discriminating ID empirically.
# Add new entries here as they are confirmed from real character data.
#
# Midnight expansion (confirmed from Trogmoon's gear, April 2026):
#   13621 → H  discovered from wrist (Aetherlume Bands, ilvl 272) and back (ilvl 272)
#   13622 → M  discovered from ring_2 (Loa Worshiper's Band, ilvl 285)
_CRAFTED_TRACK_IDS: dict[str, list[int]] = {
    "H": [13621],
    "M": [13622],
}


def is_crafted_item(bonus_ids: list[int]) -> bool:
    """Return True if the item is crafted gear (not on V/C/H/M upgrade track)."""
    if not bonus_ids:
        return False
    return bool(frozenset(bonus_ids) & _CRAFTED_BONUS_IDS)


def detect_crafted_track(
    bonus_ids: Optional[list[int]],
    custom_bonus_map: Optional[dict[str, list[int]]] = None,
) -> Optional[str]:
    """Detect H or M quality track for a crafted item.

    Priority:
    1. Admin-provided custom_bonus_map (site_config override for unusual items).
    2. Built-in _CRAFTED_TRACK_IDS — empirically discovered bonus IDs per expansion.
       New IDs are identified via get_item_preview() during equipment sync and added
       to _CRAFTED_TRACK_IDS once confirmed from real character data.
    3. Default fallback — crafted items default to Hero (H) track.

    Returns None if bonus_ids do not indicate a crafted item at all.
    """
    if not is_crafted_item(bonus_ids or []):
        return None

    if bonus_ids:
        # 1. Admin-provided override.
        if custom_bonus_map:
            track = track_from_bonus_ids(bonus_ids, custom_bonus_map)
            if track in ("H", "M"):
                return track
        # 2. Built-in discovered crafted-crest IDs.
        track = track_from_bonus_ids(bonus_ids, _CRAFTED_TRACK_IDS)
        if track in ("H", "M"):
            return track

    # 3. Default: crafted gear is Hero-track equivalent.
    return "H"
