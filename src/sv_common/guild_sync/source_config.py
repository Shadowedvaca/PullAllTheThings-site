"""Item source configuration — display names and quality track assignments.

Edit this file to update display rules or track thresholds for a new season.
No re-sync from Blizzard is required; changes take effect on next deploy.

instance_type values stored in item_sources:
  'raid'       — regular raid boss drop
  'world_boss' — outdoor world boss (no Raid Finder tier)
  'dungeon'    — M+ dungeon drop
"""

# ---------------------------------------------------------------------------
# Track assignments per instance type
# ---------------------------------------------------------------------------

# V = Raid Finder, C = Normal/Champion, H = Heroic, M = Mythic
TRACKS_BY_TYPE: dict[str, list[str]] = {
    "raid":       ["V", "C", "H", "M"],
    "world_boss": ["C", "H", "M"],   # No RF tier for outdoor world bosses
    "dungeon":    ["C", "H", "M"],
}

# ---------------------------------------------------------------------------
# Display name overrides per instance type
# ---------------------------------------------------------------------------

# None = use the raw instance_name stored in the DB
DISPLAY_NAME_BY_TYPE: dict[str, str | None] = {
    "raid":       None,
    "world_boss": "World Boss",
    "dungeon":    None,
}

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_tracks(instance_type: str) -> list[str]:
    """Return quality tracks for a given instance type."""
    return TRACKS_BY_TYPE.get(instance_type, ["C", "H", "M"])


def get_display_name(instance_name: str, instance_type: str) -> str:
    """Return the display name for an instance.

    Uses the type-level override if defined; otherwise the raw DB name.
    """
    override = DISPLAY_NAME_BY_TYPE.get(instance_type)
    return override if override is not None else instance_name


def get_track_label(instance_type: str) -> str:
    """Return the minimum-difficulty display label for an instance type.

    Raid:       V→RF+  C→N+  H→H+  M→M    (shows lowest available)
    World boss: C→N+   (no RF tier)
    Dungeon:    C→0+   H→4+  M→10+
    """
    tracks = get_tracks(instance_type)
    if not tracks:
        return ""
    min_track = next((t for t in ("V", "C", "H", "M") if t in tracks), None)
    if not min_track:
        return ""
    if instance_type == "dungeon":
        return {"C": "0+", "H": "4+", "M": "10+"}.get(min_track, "")
    return {"V": "RF+", "C": "N+", "H": "H+", "M": "M"}.get(min_track, "")
