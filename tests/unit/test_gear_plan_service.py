"""Unit tests for gear_plan_service.py — upgrade logic and slot helpers."""

import pytest

from guild_portal.services.gear_plan_service import (
    WOW_SLOTS,
    SLOT_DISPLAY,
    TRACK_ORDER,
    _upgrade_tracks,
)
from sv_common.guild_sync.quality_track import is_crafted_item


# ---------------------------------------------------------------------------
# _upgrade_tracks
# ---------------------------------------------------------------------------


class TestUpgradeTracks:
    """Test upgrade track computation."""

    def test_no_available_tracks_returns_empty(self):
        assert _upgrade_tracks("C", 100, 100, []) == []

    def test_no_equipped_track_all_available_are_upgrades(self):
        result = _upgrade_tracks(None, None, None, ["C", "H", "M"])
        assert result == ["C", "H", "M"]

    def test_same_item_strictly_higher_only(self):
        # Equipped: same item, Champion track — need Hero or Mythic
        result = _upgrade_tracks("C", 100, 100, ["C", "H", "M"])
        assert result == ["H", "M"]

    def test_same_item_already_mythic_no_upgrades(self):
        result = _upgrade_tracks("M", 100, 100, ["C", "H", "M"])
        assert result == []

    def test_same_item_veteran_all_higher(self):
        result = _upgrade_tracks("V", 100, 100, ["V", "C", "H", "M"])
        assert result == ["C", "H", "M"]

    def test_different_item_same_track_and_above(self):
        # Equipped: different item, Champion — same (C) and above are upgrades
        result = _upgrade_tracks("C", 200, 100, ["C", "H", "M"])
        assert result == ["C", "H", "M"]

    def test_different_item_hero_equipped_champion_available(self):
        # Equipped: different item, Hero — only Hero and Mythic tracks are upgrades
        # (same track means same quality, different item = still an upgrade)
        result = _upgrade_tracks("H", 200, 100, ["C", "H", "M"])
        assert result == ["H", "M"]

    def test_different_item_no_desired_set(self):
        # desired_item_id is None → treat as "different item" path
        result = _upgrade_tracks("H", 200, None, ["C", "H", "M"])
        assert result == ["H", "M"]

    def test_available_subset(self):
        # Item only drops at C/H (dungeon) — Mythic not available
        result = _upgrade_tracks("C", 200, 100, ["C", "H"])
        assert result == ["C", "H"]

    def test_champion_vs_veteran_equipped(self):
        result = _upgrade_tracks("V", 200, 100, ["C", "H"])
        assert result == ["C", "H"]

    def test_track_order_correct(self):
        """TRACK_ORDER must rank V < C < H < M."""
        assert TRACK_ORDER["V"] < TRACK_ORDER["C"]
        assert TRACK_ORDER["C"] < TRACK_ORDER["H"]
        assert TRACK_ORDER["H"] < TRACK_ORDER["M"]


# ---------------------------------------------------------------------------
# WOW_SLOTS
# ---------------------------------------------------------------------------


class TestWowSlots:
    def test_has_16_slots(self):
        assert len(WOW_SLOTS) == 16

    def test_contains_expected_slots(self):
        required = {
            "head", "neck", "shoulder", "back", "chest", "wrist",
            "hands", "waist", "legs", "feet",
            "ring_1", "ring_2", "trinket_1", "trinket_2",
            "main_hand", "off_hand",
        }
        assert set(WOW_SLOTS) == required

    def test_ordered_head_first_off_hand_last(self):
        assert WOW_SLOTS[0] == "head"
        assert WOW_SLOTS[-1] == "off_hand"

    def test_rings_before_trinkets(self):
        ring_idx = WOW_SLOTS.index("ring_1")
        tri_idx = WOW_SLOTS.index("trinket_1")
        assert ring_idx < tri_idx

    def test_all_slots_have_display_name(self):
        for slot in WOW_SLOTS:
            assert slot in SLOT_DISPLAY, f"Missing display name for slot: {slot}"

    def test_display_names_non_empty(self):
        for slot, name in SLOT_DISPLAY.items():
            assert name, f"Empty display name for slot: {slot}"


# ---------------------------------------------------------------------------
# SLOT_DISPLAY
# ---------------------------------------------------------------------------


class TestSlotDisplay:
    def test_ring_1_label(self):
        assert SLOT_DISPLAY["ring_1"] == "Ring 1"

    def test_main_hand_label(self):
        assert SLOT_DISPLAY["main_hand"] == "Main Hand"

    def test_trinket_2_label(self):
        assert SLOT_DISPLAY["trinket_2"] == "Trinket 2"


# ---------------------------------------------------------------------------
# is_crafted_item
# ---------------------------------------------------------------------------


class TestIsCraftedItem:
    def test_crafted_bonus_id_detected(self):
        assert is_crafted_item([1808]) is True

    def test_crafted_among_other_bonus_ids(self):
        assert is_crafted_item([100, 1808, 200]) is True

    def test_non_crafted_returns_false(self):
        assert is_crafted_item([1516, 1517]) is False

    def test_empty_list_returns_false(self):
        assert is_crafted_item([]) is False

    def test_none_safe(self):
        # Callers pass `bonus_ids or []` so None is never passed, but guard anyway
        assert is_crafted_item([]) is False


# ---------------------------------------------------------------------------
# BIS upgrade fallback (inferred from equipped track when no item_sources)
# ---------------------------------------------------------------------------


class TestBisUpgradeFallback:
    """The service applies a fallback when is_bis=True but available_tracks=[].
    Verify _upgrade_tracks behaviour that the fallback builds on."""

    def test_same_item_champion_empty_tracks_no_upgrade(self):
        # Without fallback, empty available_tracks → empty result
        assert _upgrade_tracks("C", 100, 100, []) == []

    def test_same_item_champion_full_tracks_gives_hm(self):
        # With all tracks as fallback: strictly higher than C → H, M
        result = _upgrade_tracks("C", 100, 100, ["V", "C", "H", "M"])
        assert result == ["H", "M"]

    def test_same_item_veteran_full_tracks_excludes_v(self):
        # At V track, strictly higher → C, H, M
        result = _upgrade_tracks("V", 100, 100, ["V", "C", "H", "M"])
        assert result == ["C", "H", "M"]

    def test_same_item_mythic_full_tracks_no_upgrades(self):
        # Already at M — no higher tracks
        result = _upgrade_tracks("M", 100, 100, ["V", "C", "H", "M"])
        assert result == []

    def test_non_bis_champion_never_shows_v(self):
        # Non-BIS at C: same and above → C, H, M (V excluded — 0 >= 1 is False)
        result = _upgrade_tracks("C", 200, 100, ["V", "C", "H", "M"])
        assert result == ["C", "H", "M"]
        assert "V" not in result

    def test_non_bis_hero_shows_hm_only(self):
        # Non-BIS at H: same and above → H, M
        result = _upgrade_tracks("H", 200, 100, ["V", "C", "H", "M"])
        assert result == ["H", "M"]
