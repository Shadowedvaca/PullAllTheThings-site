"""Unit tests for quality_track.py — track detection and slot normalisation."""

import pytest

from sv_common.guild_sync.quality_track import (
    detect_quality_track,
    normalize_slot,
    track_from_bonus_ids,
    track_from_display_string,
    SLOT_ORDER,
)


class TestTrackFromDisplayString:
    def test_champion(self):
        assert track_from_display_string("Champion 4/8") == "C"

    def test_hero(self):
        assert track_from_display_string("Hero 2/8") == "H"

    def test_mythic(self):
        assert track_from_display_string("Mythic 1/8") == "M"

    def test_veteran(self):
        assert track_from_display_string("Veteran 7/8") == "V"

    def test_case_insensitive(self):
        assert track_from_display_string("CHAMPION 4/8") == "C"
        assert track_from_display_string("hero 1/8") == "H"

    def test_none_input(self):
        assert track_from_display_string(None) is None

    def test_empty_string(self):
        assert track_from_display_string("") is None

    def test_non_track_string(self):
        # Crafted, season, etc. — not an upgrade track item
        assert track_from_display_string("Crafted by Players") is None
        assert track_from_display_string("Binds when picked up") is None

    def test_whitespace_stripped(self):
        assert track_from_display_string("  Hero 3/8  ") == "H"


class TestTrackFromBonusIds:
    def test_champion_track(self):
        assert track_from_bonus_ids([1517]) == "C"

    def test_hero_track(self):
        assert track_from_bonus_ids([1521]) == "H"

    def test_mythic_track(self):
        assert track_from_bonus_ids([1524]) == "M"

    def test_veteran_track(self):
        assert track_from_bonus_ids([1498]) == "V"

    def test_mixed_bonus_ids(self):
        # Non-track bonus IDs alongside a track ID
        result = track_from_bonus_ids([4800, 1520, 6808])
        assert result == "H"

    def test_no_match(self):
        assert track_from_bonus_ids([9999, 12345]) is None

    def test_empty_list(self):
        assert track_from_bonus_ids([]) is None

    def test_custom_map(self):
        custom = {"C": [9001], "H": [9002]}
        assert track_from_bonus_ids([9001], custom_map=custom) == "C"
        assert track_from_bonus_ids([1517], custom_map=custom) is None

    def test_midnight_crafted_hero_track(self):
        # 13621 = Midnight crafted H quality (discovered from Aetherlume Bands + back)
        assert track_from_bonus_ids([13621]) == "H"

    def test_midnight_crafted_mythic_track(self):
        # 13622 = Midnight crafted M quality (discovered from Loa Worshiper's Band)
        assert track_from_bonus_ids([13622]) == "M"

    def test_midnight_crafted_mixed_bonus_ids(self):
        # Crafted items have additional bonus IDs alongside the quality marker
        assert track_from_bonus_ids([12214, 13621, 5000]) == "H"


class TestDetectQualityTrack:
    def test_prefers_display_string(self):
        # Display string takes priority over bonus IDs
        result = detect_quality_track("Hero 1/8", bonus_ids=[1517])  # 1517 = Champion
        assert result == "H"

    def test_falls_back_to_bonus_ids(self):
        result = detect_quality_track("Crafted by Players", bonus_ids=[1520])
        assert result == "H"

    def test_both_none(self):
        assert detect_quality_track(None) is None
        assert detect_quality_track("", bonus_ids=[]) is None


class TestNormalizeSlot:
    def test_standard_slots(self):
        assert normalize_slot("HEAD") == "head"
        assert normalize_slot("NECK") == "neck"
        assert normalize_slot("SHOULDER") == "shoulder"
        assert normalize_slot("BACK") == "back"
        assert normalize_slot("CHEST") == "chest"
        assert normalize_slot("WRIST") == "wrist"
        assert normalize_slot("HANDS") == "hands"
        assert normalize_slot("WAIST") == "waist"
        assert normalize_slot("LEGS") == "legs"
        assert normalize_slot("FEET") == "feet"

    def test_ring_and_trinket_slots(self):
        assert normalize_slot("FINGER_1") == "ring_1"
        assert normalize_slot("FINGER_2") == "ring_2"
        assert normalize_slot("TRINKET_1") == "trinket_1"
        assert normalize_slot("TRINKET_2") == "trinket_2"

    def test_weapon_slots(self):
        assert normalize_slot("MAIN_HAND") == "main_hand"
        assert normalize_slot("OFF_HAND") == "off_hand"
        assert normalize_slot("TWOHWEAPON") == "main_hand"
        assert normalize_slot("RANGED") == "main_hand"

    def test_ignored_slots(self):
        assert normalize_slot("TABARD") is None
        assert normalize_slot("SHIRT") is None

    def test_case_insensitive(self):
        assert normalize_slot("head") == "head"
        assert normalize_slot("Finger_1") == "ring_1"

    def test_wristband_alias(self):
        assert normalize_slot("WRISTBAND") == "wrist"


class TestSlotOrder:
    def test_has_16_slots(self):
        assert len(SLOT_ORDER) == 16

    def test_ring_and_trinket_pairs_present(self):
        assert "ring_1" in SLOT_ORDER
        assert "ring_2" in SLOT_ORDER
        assert "trinket_1" in SLOT_ORDER
        assert "trinket_2" in SLOT_ORDER
