"""Unit tests for _wh_slots_from_section — Wowhead slot assignment.

Focuses on weapon slot handling: one-hand items with invtype=13 normally land
in weapon_slots (main_hand), but items explicitly labeled "[td]Offhand[/td]"
in the BBcode table must be assigned to off_hand instead.
"""

import pytest

from sv_common.guild_sync.bis_sync import _wh_slots_from_section

# Minimal slot_map mirroring config.wowhead_invtypes seed data
# 13 = INVTYPE_WEAPON (one-hand)  → main_hand
# 17 = INVTYPE_HOLDABLE (off-hand frill) → off_hand
_TEST_SLOT_MAP: dict[int, str] = {
    1:  "head",
    2:  "neck",
    3:  "shoulder",
    5:  "chest",
    6:  "waist",
    7:  "legs",
    8:  "feet",
    9:  "wrist",
    10: "hands",
    11: "ring",
    12: "trinket",
    13: "main_hand",
    17: "off_hand",
    20: "chest",
    21: "main_hand",  # two-hand
    22: "main_hand",  # two-hand ranged
    23: "main_hand",
    26: "off_hand",
}


def _make_meta(item_id: int, slotbak: int) -> dict:
    return {item_id: {"jsonequip": {"slotbak": slotbak}, "name_enus": f"Item {item_id}"}}


def _make_section_html(*item_entries: tuple[int, str | None]) -> str:
    """Build minimal Wowhead BBcode HTML.

    Each entry is (item_id, slot_label_or_None).
    When slot_label is not None, wrap in [tr][td]<label>[/td][td][item=N][/td][/tr].
    When None, just emit a bare [item=N].
    """
    parts = []
    for item_id, label in item_entries:
        if label:
            parts.append(f"[tr][td]{label}[/td][td][item={item_id}][/td][/tr]")
        else:
            parts.append(f"[item={item_id}]")
    return "\n".join(parts)


def _slot_map_by_id(slots) -> dict[str, int]:
    return {s.slot: s.blizzard_item_id for s in slots}


# ── Basic weapon slot tests ─────────────────────────────────────────────────

def test_single_main_hand_no_label():
    """One-handed weapon with no off-hand label → main_hand."""
    meta = _make_meta(100001, 13)
    html = "[item=100001]"
    slots = _wh_slots_from_section(html, meta, "overall", _TEST_SLOT_MAP)
    result = _slot_map_by_id(slots)
    assert "main_hand" in result
    assert result["main_hand"] == 100001
    assert "off_hand" not in result


def test_explicit_offhand_label_lowercase():
    """Item labeled [td]Offhand[/td] → off_hand slot despite invtype=13."""
    meta = {**_make_meta(100001, 13), **_make_meta(100002, 13)}
    html = _make_section_html((100001, None), (100002, "Offhand"))
    slots = _wh_slots_from_section(html, meta, "overall", _TEST_SLOT_MAP)
    result = _slot_map_by_id(slots)
    assert result.get("main_hand") == 100001
    assert result.get("off_hand") == 100002


def test_explicit_offhand_label_case_insensitive():
    """[td]offhand[/td] and [td]Off Hand[/td] both work."""
    meta = {**_make_meta(100001, 13), **_make_meta(100002, 13), **_make_meta(100003, 13)}

    html_lower = _make_section_html((100001, None), (100002, "offhand"))
    slots = _wh_slots_from_section(html_lower, meta, "overall", _TEST_SLOT_MAP)
    assert _slot_map_by_id(slots).get("off_hand") == 100002

    html_space = _make_section_html((100001, None), (100003, "Off Hand"))
    slots2 = _wh_slots_from_section(html_space, meta, "overall", _TEST_SLOT_MAP)
    assert _slot_map_by_id(slots2).get("off_hand") == 100003


def test_dh_glaive_offhand_scenario():
    """Regression: Devourer DH - Spellbreaker's Warglaive (237840) is in
    an Offhand row with escaped slashes (Wowhead style); Lightless Lament
    (260408) is in the Weapon row.  Both have invtype=13.  Off-hand must land
    in off_hand, not second main_hand.  Also tests multi-option cell.
    """
    meta = {**_make_meta(260408, 13), **_make_meta(249298, 17), **_make_meta(237840, 13)}
    # Mirrors real Wowhead Vengeance DH BBcode (escaped slashes, "or" in cell)
    html = (
        r"[tr][td]Weapon[\/td][td][item=260408 bonus=12806:13335][\/td][td]Midnight Falls[\/td][\/tr]"
        "\r\n\t\t"
        r"[tr][td]Offhand[\/td][td][item=249298 bonus=12806:13335] or [item=237840 bonus=13622:13667][\/td][td]Boss or Crafted[\/td][\/tr]"
    )
    slots = _wh_slots_from_section(html, meta, "overall", _TEST_SLOT_MAP)
    result = _slot_map_by_id(slots)
    assert result.get("main_hand") == 260408, "Lightless Lament should be main_hand"
    assert result.get("off_hand") in (249298, 237840), "Off-hand option should be off_hand"
    main_hand_slots = [s for s in slots if s.slot == "main_hand"]
    assert len(main_hand_slots) == 1


def test_escaped_slash_offhand_label():
    """[td]Offhand[\\/td] (Wowhead-style escaped slash) is recognised."""
    meta = {**_make_meta(100001, 13), **_make_meta(100002, 13)}
    html = r"[tr][td]Offhand[\/td][td][item=100002][\/td][\/tr] [item=100001]"
    slots = _wh_slots_from_section(html, meta, "overall", _TEST_SLOT_MAP)
    result = _slot_map_by_id(slots)
    assert result.get("off_hand") == 100002
    assert result.get("main_hand") == 100001


def test_multi_option_offhand_cell():
    """Cell with 'item A or item B' in Offhand row — both IDs are treated as off_hand."""
    meta = {**_make_meta(100001, 13), **_make_meta(100002, 13), **_make_meta(100003, 13)}
    html = (
        "[tr][td]Weapon[/td][td][item=100001][/td][/tr]\n"
        "[tr][td]Offhand[/td][td][item=100002] or [item=100003][/td][/tr]"
    )
    slots = _wh_slots_from_section(html, meta, "overall", _TEST_SLOT_MAP)
    result = _slot_map_by_id(slots)
    assert result.get("main_hand") == 100001
    # First off_hand option wins (document order)
    assert result.get("off_hand") == 100002


def test_off_hand_not_duplicated_in_weapon_slots():
    """Explicit off-hand item must not also appear in weapon_slots."""
    meta = {**_make_meta(100001, 13), **_make_meta(100002, 13)}
    html = _make_section_html((100001, None), (100002, "Offhand"))
    slots = _wh_slots_from_section(html, meta, "overall", _TEST_SLOT_MAP)
    # Should be exactly 2 slots total: main_hand + off_hand
    assert len(slots) == 2
    slot_names = {s.slot for s in slots}
    assert slot_names == {"main_hand", "off_hand"}


def test_two_weapon_items_no_offhand_label():
    """Two one-handed weapons, neither labeled off-hand → both in weapon_slots
    as main_hand (existing behavior for specs that list two MH options)."""
    meta = {**_make_meta(100001, 13), **_make_meta(100002, 13)}
    html = _make_section_html((100001, None), (100002, None))
    slots = _wh_slots_from_section(html, meta, "overall", _TEST_SLOT_MAP)
    main_hand_slots = [s for s in slots if s.slot == "main_hand"]
    assert len(main_hand_slots) == 2
    assert "off_hand" not in {s.slot for s in slots}
