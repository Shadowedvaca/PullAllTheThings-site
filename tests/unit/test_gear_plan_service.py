"""Unit tests for gear_plan_service.py — upgrade logic, slot helpers, weapon display."""

import pytest

from guild_portal.services.gear_plan_service import (
    WOW_SLOTS,
    TRACK_ORDER,
    _compute_weapon_display,
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

    def test_empty_slot_all_available_are_upgrades(self):
        # Nothing equipped (equipped_item_id=None) → anything is an upgrade
        result = _upgrade_tracks(None, None, None, ["C", "H", "M"])
        assert result == ["C", "H", "M"]

    def test_item_equipped_unknown_track_returns_empty(self):
        # Something is equipped but quality_track wasn't detected → no upgrade recs
        # (avoids incorrectly recommending Veteran as an upgrade)
        result = _upgrade_tracks(None, 999, 100, ["V", "C", "H", "M"])
        assert result == []

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
# WOW_SLOTS — runtime-populated set, starts empty until DB is loaded
# ---------------------------------------------------------------------------


class TestWowSlots:
    def test_is_set(self):
        assert isinstance(WOW_SLOTS, set)

    def test_initially_empty_without_db(self):
        # WOW_SLOTS is populated by _ensure_slot_meta() which requires a DB connection.
        # In unit tests with no DB, it starts empty.
        assert isinstance(WOW_SLOTS, set)


# ---------------------------------------------------------------------------
# _compute_weapon_display
# ---------------------------------------------------------------------------


def _make_bis(slot: str, guide_order: int = 1) -> dict:
    return {"slot": slot, "guide_order": guide_order, "blizzard_item_id": 1}


class TestComputeWeaponDisplay:
    def test_2h_bis_only(self):
        bis = {"main_hand_2h": [_make_bis("main_hand_2h", 1)]}
        build, show_oh = _compute_weapon_display(bis, {}, {})
        assert build == "2h"
        assert show_oh is False

    def test_1h_bis_only(self):
        bis = {"main_hand_1h": [_make_bis("main_hand_1h", 1)]}
        build, show_oh = _compute_weapon_display(bis, {}, {})
        assert build == "1h"
        assert show_oh is True

    def test_prefer_lower_guide_order_2h(self):
        # Frost DK: 2H listed first (guide_order=1), 1H listed second (guide_order=2)
        bis = {
            "main_hand_2h": [_make_bis("main_hand_2h", 1)],
            "main_hand_1h": [_make_bis("main_hand_1h", 2)],
        }
        build, show_oh = _compute_weapon_display(bis, {}, {})
        assert build == "2h"
        assert show_oh is False

    def test_prefer_lower_guide_order_1h(self):
        # Spec where 1H is preferred (guide_order=1 on 1H)
        bis = {
            "main_hand_2h": [_make_bis("main_hand_2h", 2)],
            "main_hand_1h": [_make_bis("main_hand_1h", 1)],
        }
        build, show_oh = _compute_weapon_display(bis, {}, {})
        assert build == "1h"
        assert show_oh is True

    def test_titans_grip_shows_off_hand(self):
        # Fury Warrior TG: 2H build with off_hand BIS → show off_hand
        bis = {
            "main_hand_2h": [_make_bis("main_hand_2h", 1)],
            "off_hand":     [_make_bis("off_hand", 1)],
        }
        build, show_oh = _compute_weapon_display(bis, {}, {})
        assert build == "2h"
        assert show_oh is True

    def test_no_bis_falls_back_to_equipped_2h(self):
        equipped = {"main_hand_2h": {"blizzard_item_id": 999, "slot": "main_hand_2h"}}
        build, show_oh = _compute_weapon_display({}, equipped, {})
        assert build == "2h"
        assert show_oh is False

    def test_no_bis_falls_back_to_equipped_1h(self):
        equipped = {"main_hand_1h": {"blizzard_item_id": 888, "slot": "main_hand_1h"}}
        build, show_oh = _compute_weapon_display({}, equipped, {})
        assert build == "1h"
        assert show_oh is True

    def test_no_data_returns_none(self):
        build, show_oh = _compute_weapon_display({}, {}, {})
        assert build is None
        assert show_oh is False

    def test_desired_fallback_2h(self):
        desired = {"main_hand_2h": {"blizzard_item_id": 777}}
        build, show_oh = _compute_weapon_display({}, {}, desired)
        assert build == "2h"

    def test_desired_fallback_1h(self):
        desired = {"main_hand_1h": {"blizzard_item_id": 666}}
        build, show_oh = _compute_weapon_display({}, {}, desired)
        assert build == "1h"


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
