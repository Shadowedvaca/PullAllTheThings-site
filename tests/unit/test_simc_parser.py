"""Unit tests for simc_parser.py — SimC profile parsing + export."""

import pytest

from sv_common.guild_sync.simc_parser import (
    SimcProfile,
    SimcSlot,
    bonus_ids_to_quality_track,
    export_gear_plan,
    parse_gear_slot_line,
    parse_gear_slots,
    parse_profile,
)

# ---------------------------------------------------------------------------
# Sample SimC text
# ---------------------------------------------------------------------------

_SAMPLE_PROFILE = """\
druid="Trogmoon"
spec=balance
level=80
race=night_elf
region=us
server=senjin

head=dreambinder_loom_of_the_great_cycle,id=208616,bonus_id=4800:1517:8767,enchant_id=7936
neck=fateweaved_needle,id=212449,bonus_id=4800:1520
shoulder=mantle_of_volcanic_grief,id=212065,bonus_id=4800:1520
back=shadows_of_the_falling_star,id=225492,bonus_id=4800:1517
chest=vestments_of_cosmic_dissonance,id=212068,bonus_id=4800:1520
wrists=braces_of_the_lunar_conclave,id=225492,bonus_id=4800:1517
hands=gloves_of_the_lunar_conclave,id=225494,bonus_id=4800:1517
waist=moonlit_prism_sash,id=225496,bonus_id=4800:1520
legs=leggings_of_the_lunar_conclave,id=225495,bonus_id=4800:1520
feet=sandals_of_wild_dreams,id=225499,bonus_id=4800:1517
finger1=scalebane_signet,id=212454,bonus_id=4800:1520
finger2=seal_of_the_poisoned_pact,id=212456,bonus_id=4800:1517
trinket1=spymasters_web,id=220202,bonus_id=4800:1520
trinket2=ara_karas_dragonfire_oil,id=220605,bonus_id=4800:1517
main_hand=fang_adept_staff,id=220176,bonus_id=4800:1520,enchant_id=7964
"""

_PARTIAL_TEXT = """\
# Just some gear, no char metadata
head=some_helm,id=100001,bonus_id=1517
trinket1=some_trinket,id=100002
off_hand=some_offhand,id=100003,bonus_id=4800:1520
"""


# ---------------------------------------------------------------------------
# parse_gear_slot_line
# ---------------------------------------------------------------------------


class TestParseGearSlotLine:
    def test_basic_head(self):
        slot = parse_gear_slot_line(
            "head=dreambinder_loom,id=208616,bonus_id=4800:1517"
        )
        assert slot is not None
        assert slot.slot == "head"
        assert slot.blizzard_item_id == 208616
        assert 1517 in slot.bonus_ids
        assert slot.quality_track == "C"   # bonus_id 1517 = Champion

    def test_with_enchant(self):
        slot = parse_gear_slot_line(
            "main_hand=fang_staff,id=220176,bonus_id=4800:1520,enchant_id=7964"
        )
        assert slot is not None
        assert slot.slot == "main_hand"
        assert slot.enchant_id == 7964

    def test_finger_normalisation(self):
        s1 = parse_gear_slot_line("finger1=ring_a,id=111,bonus_id=1521")
        s2 = parse_gear_slot_line("finger2=ring_b,id=222,bonus_id=1521")
        assert s1 is not None and s1.slot == "ring_1"
        assert s2 is not None and s2.slot == "ring_2"

    def test_trinket_normalisation(self):
        t1 = parse_gear_slot_line("trinket1=trinket_a,id=333")
        t2 = parse_gear_slot_line("trinket2=trinket_b,id=444")
        assert t1 is not None and t1.slot == "trinket_1"
        assert t2 is not None and t2.slot == "trinket_2"

    def test_wrists_alias(self):
        s = parse_gear_slot_line("wrists=bracers,id=555")
        assert s is not None and s.slot == "wrist"

    def test_shoulders_alias(self):
        s = parse_gear_slot_line("shoulders=shoulderpads,id=666")
        assert s is not None and s.slot == "shoulder"

    def test_unknown_slot_returns_none(self):
        assert parse_gear_slot_line("tabard=fancy,id=777") is None
        assert parse_gear_slot_line("shirt=white,id=888") is None

    def test_missing_id_returns_none(self):
        assert parse_gear_slot_line("head=some_helm") is None
        assert parse_gear_slot_line("head=some_helm,bonus_id=1517") is None

    def test_hero_track_from_bonus_ids(self):
        s = parse_gear_slot_line("neck=chain,id=999,bonus_id=1521")
        assert s is not None and s.quality_track == "H"

    def test_no_bonus_ids(self):
        s = parse_gear_slot_line("feet=boots,id=12345")
        assert s is not None
        assert s.bonus_ids == []
        assert s.quality_track is None

    def test_gem_ids(self):
        s = parse_gear_slot_line("head=helm,id=200,bonus_id=1520,gem_id=192985:192919")
        assert s is not None
        assert 192985 in s.gem_ids
        assert 192919 in s.gem_ids


# ---------------------------------------------------------------------------
# parse_gear_slots
# ---------------------------------------------------------------------------


class TestParseGearSlots:
    def test_parses_all_slots_in_sample(self):
        slots = parse_gear_slots(_SAMPLE_PROFILE)
        slot_names = {s.slot for s in slots}
        assert "head" in slot_names
        assert "neck" in slot_names
        assert "main_hand" in slot_names
        assert "ring_1" in slot_names
        assert "ring_2" in slot_names

    def test_ignores_metadata_lines(self):
        # spec=, level=, etc. should not be treated as gear slots
        slots = parse_gear_slots(_SAMPLE_PROFILE)
        for s in slots:
            assert s.slot in {
                "head", "neck", "shoulder", "back", "chest", "wrist",
                "hands", "waist", "legs", "feet",
                "ring_1", "ring_2", "trinket_1", "trinket_2",
                "main_hand", "off_hand",
            }

    def test_partial_text(self):
        slots = parse_gear_slots(_PARTIAL_TEXT)
        assert len(slots) == 3
        names = {s.slot for s in slots}
        assert names == {"head", "trinket_1", "off_hand"}

    def test_empty_text_returns_empty(self):
        assert parse_gear_slots("") == []

    def test_comments_ignored(self):
        text = "# This is a comment\nhead=helm,id=100,bonus_id=1517\n"
        slots = parse_gear_slots(text)
        assert len(slots) == 1

    def test_champion_track_detected(self):
        slots = parse_gear_slots("head=helm,id=100,bonus_id=4800:1517")
        assert slots[0].quality_track == "C"

    def test_hero_track_detected(self):
        slots = parse_gear_slots("head=helm,id=100,bonus_id=4800:1521")
        assert slots[0].quality_track == "H"

    def test_mythic_track_detected(self):
        slots = parse_gear_slots("head=helm,id=100,bonus_id=4800:1524")
        assert slots[0].quality_track == "M"

    def test_midnight_crafted_hero_track(self):
        # 13621 = Midnight crafted H — wrist/back crafted pieces
        slots = parse_gear_slots("wrists=aetherlume_bands,id=225300,bonus_id=12214:13621:5000")
        assert slots[0].quality_track == "H"

    def test_midnight_crafted_mythic_track(self):
        # 13622 = Midnight crafted M — ring crafted pieces
        slots = parse_gear_slots("finger2=loa_worshipers_band,id=225310,bonus_id=12214:13622:5000")
        assert slots[0].quality_track == "M"


# ---------------------------------------------------------------------------
# parse_profile
# ---------------------------------------------------------------------------


class TestParseProfile:
    def test_parses_char_name(self):
        p = parse_profile(_SAMPLE_PROFILE)
        assert p.char_name == "Trogmoon"

    def test_parses_spec(self):
        p = parse_profile(_SAMPLE_PROFILE)
        assert p.spec == "balance"

    def test_parses_class(self):
        p = parse_profile(_SAMPLE_PROFILE)
        assert p.wow_class == "druid"

    def test_parses_region(self):
        p = parse_profile(_SAMPLE_PROFILE)
        assert p.region == "us"

    def test_parses_server(self):
        p = parse_profile(_SAMPLE_PROFILE)
        assert p.server == "senjin"

    def test_parses_level(self):
        p = parse_profile(_SAMPLE_PROFILE)
        assert p.level == 80

    def test_slots_populated(self):
        p = parse_profile(_SAMPLE_PROFILE)
        assert len(p.slots) > 0

    def test_empty_profile(self):
        p = parse_profile("")
        assert p.char_name == ""
        assert p.slots == []

    def test_other_class_keys(self):
        text = 'warrior="Bouldersmash"\nspec=arms\nlevel=80\nregion=us\nserver=senjin\n'
        p = parse_profile(text)
        assert p.wow_class == "warrior"
        assert p.char_name == "Bouldersmash"


# ---------------------------------------------------------------------------
# bonus_ids_to_quality_track
# ---------------------------------------------------------------------------


class TestBonusIdsToQualityTrack:
    def test_champion(self):
        assert bonus_ids_to_quality_track([1517]) == "C"

    def test_hero(self):
        assert bonus_ids_to_quality_track([1521]) == "H"

    def test_mythic(self):
        assert bonus_ids_to_quality_track([1524]) == "M"

    def test_veteran(self):
        assert bonus_ids_to_quality_track([1498]) == "V"

    def test_no_match(self):
        assert bonus_ids_to_quality_track([9999]) is None

    def test_empty(self):
        assert bonus_ids_to_quality_track([]) is None

    def test_mixed_ids(self):
        # Non-track IDs mixed in — still finds the track
        assert bonus_ids_to_quality_track([4800, 1517, 8767]) == "C"

    def test_custom_map(self):
        custom = {"C": [9001], "H": [9002], "M": [9003]}
        assert bonus_ids_to_quality_track([9002], custom) == "H"
        assert bonus_ids_to_quality_track([1517], custom) is None


# ---------------------------------------------------------------------------
# export_gear_plan
# ---------------------------------------------------------------------------


class TestExportGearPlan:
    def _make_slots(self):
        return [
            {"slot": "head",    "blizzard_item_id": 208616, "item_name": "Helm of Doom",
             "bonus_ids": [1520], "enchant_id": None, "gem_ids": []},
            {"slot": "neck",    "blizzard_item_id": 212449, "item_name": "Necklace of Fate",
             "bonus_ids": [1517], "enchant_id": None, "gem_ids": []},
            {"slot": "main_hand","blizzard_item_id": 220176, "item_name": "Staff of Stars",
             "bonus_ids": [1520], "enchant_id": 7964, "gem_ids": []},
        ]

    def test_outputs_class_header(self):
        out = export_gear_plan(self._make_slots(), "Trogmoon", "balance", "druid")
        assert out.startswith('druid="Trogmoon"')

    def test_outputs_spec(self):
        out = export_gear_plan(self._make_slots(), "Trogmoon", "balance", "druid")
        assert "spec=balance" in out

    def test_outputs_item_lines(self):
        out = export_gear_plan(self._make_slots(), "Trogmoon", "balance", "druid")
        assert "head=" in out
        assert "id=208616" in out

    def test_bonus_ids_exported(self):
        out = export_gear_plan(self._make_slots(), "Trogmoon", "balance", "druid")
        assert "bonus_id=1520" in out

    def test_enchant_exported(self):
        out = export_gear_plan(self._make_slots(), "Trogmoon", "balance", "druid")
        assert "enchant_id=7964" in out

    def test_slot_ordering(self):
        out = export_gear_plan(self._make_slots(), "Trogmoon", "balance", "druid")
        lines = [l for l in out.splitlines() if l.startswith("head=") or l.startswith("neck=") or l.startswith("main_hand=")]
        assert lines[0].startswith("head=")
        assert lines[-1].startswith("main_hand=")

    def test_empty_slots(self):
        out = export_gear_plan([], "Trogmoon", "balance", "druid")
        assert 'druid="Trogmoon"' in out
        assert "head=" not in out

    def test_skips_slots_without_item_id(self):
        slots = [
            {"slot": "head", "blizzard_item_id": None, "item_name": "Missing"},
            {"slot": "neck", "blizzard_item_id": 12345, "item_name": "Chain"},
        ]
        out = export_gear_plan(slots, "Test", "balance", "druid")
        assert "head=" not in out
        assert "id=12345" in out

    def test_round_trip(self):
        """export → parse_gear_slots should recover the same item IDs."""
        slots_in = self._make_slots()
        text = export_gear_plan(slots_in, "Trogmoon", "balance", "druid")
        slots_out = parse_gear_slots(text)
        item_ids_in  = {s["blizzard_item_id"] for s in slots_in if s["blizzard_item_id"]}
        item_ids_out = {s.blizzard_item_id for s in slots_out}
        assert item_ids_in == item_ids_out
