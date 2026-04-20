"""Unit tests for Method.gg BIS extraction.

Covers _classify_method_heading, _extract_method_sections, and
_resolve_method_section_local with fixture HTML snapshots.
All item IDs in fixtures are plausible but synthetic — not live data.
"""

import pytest

from sv_common.guild_sync.bis_sync import (
    _build_url,
    _classify_method_heading,
    _extract_method_sections,
    _resolve_method_section_local,
)

# Mirrors the seed data in migration 0153 / config.method_slot_labels.
# Tests pass this directly so they don't need a DB connection.
_TEST_SLOT_MAP: dict[str, str | None] = {
    "head":       "head",
    "neck":       "neck",
    "shoulders":  "shoulder",
    "shoulder":   "shoulder",
    "back":       "back",
    "cloak":      "back",
    "chest":      "chest",
    "wrists":     "wrist",
    "wrist":      "wrist",
    "hands":      "hands",
    "gloves":     "hands",
    "waist":      "waist",
    "belt":       "waist",
    "legs":       "legs",
    "feet":       "feet",
    "boots":      "feet",
    "ring 1":     "ring_1",
    "ring 2":     "ring_2",
    "ring":       None,
    "trinket 1":  "trinket_1",
    "trinket 2":  "trinket_2",
    "trinket":    None,
    "main hand":      "main_hand",
    "main-hand":      "main_hand",
    "main hand (2h)": "main_hand",
    "main hand (dw)": "main_hand",
    "one-hand":       "main_hand",
    "one hand":       "main_hand",
    "weapon":         "main_hand",
    "off hand":       "off_hand",
    "off-hand":       "off_hand",
}


# ---------------------------------------------------------------------------
# Fixture HTML helpers
# ---------------------------------------------------------------------------

def _make_method_page(
    sections: list[tuple[str, list[tuple[str, int, str | None]]]],
    bonus_ids: str = "",
) -> str:
    """Build a minimal Method.gg /gearing page.

    sections: list of (h3_heading, rows) where each row is (slot, item_id, source|None).
    """
    def _row(slot: str, item_id: int, source: str | None, bonus: str) -> str:
        bonus_part = f"?bonus={bonus}" if bonus else ""
        source_td = f"<td>{source}</td>" if source else "<td></td>"
        return (
            f"<tr>"
            f"<td>{slot}</td>"
            f'<td><a href="https://www.wowhead.com/item={item_id}{bonus_part}">Item {item_id}</a></td>'
            f"{source_td}"
            f"</tr>"
        )

    def _table(rows: list[tuple[str, int, str | None]]) -> str:
        header = "<thead><tr><th>Slot</th><th>Item</th><th>Source</th></tr></thead>"
        body = "".join(_row(s, i, src, bonus_ids) for s, i, src in rows)
        return f"<table>{header}<tbody>{body}</tbody></table>"

    parts = []
    for heading, rows in sections:
        parts.append(f"<h3>{heading}</h3>")
        parts.append(_table(rows))

    return "<html><body>" + "".join(parts) + "</body></html>"


_STANDARD_OVERALL_ROWS = [
    ("Head", 200001, "The Voidspire"),
    ("Neck", 200002, "Crafted"),
    ("Shoulders", 200003, "Boss A"),
    ("Back", 200004, "Boss B"),
    ("Chest", 200005, "Boss C"),
    ("Wrists", 200006, "Boss D"),
    ("Hands", 200007, "Boss E"),
    ("Waist", 200008, "Boss F"),
    ("Legs", 200009, "Boss G"),
    ("Feet", 200010, "Boss H"),
    ("Ring 1", 200011, "Boss I"),
    ("Ring 2", 200012, "Boss J"),
    ("Trinket 1", 200013, "Boss K"),
    ("Trinket 2", 200014, "Boss L"),
    ("Main Hand", 200015, "Boss M"),
    ("Off Hand", 200016, "Boss N"),
]

_STANDARD_RAID_ROWS = [
    ("Head", 201001, "Boss A"),
    ("Neck", 201002, "Boss B"),
    ("Shoulders", 201003, "Boss C"),
    ("Back", 201004, "Boss D"),
    ("Chest", 201005, "Boss E"),
    ("Wrists", 201006, "Boss F"),
    ("Hands", 201007, "Boss G"),
    ("Waist", 201008, "Boss H"),
    ("Legs", 201009, "Boss I"),
    ("Feet", 201010, "Boss J"),
    ("Ring 1", 201011, "Boss K"),
    ("Ring 2", 201012, "Boss L"),
    ("Trinket 1", 201013, "Boss M"),
    ("Trinket 2", 201014, "Boss N"),
    ("Main Hand", 201015, "Boss O"),
]

_STANDARD_MPLUS_ROWS = [
    ("Head", 202001, "Dungeon A"),
    ("Neck", 202002, "Dungeon B"),
    ("Shoulders", 202003, "Dungeon C"),
    ("Back", 202004, "Dungeon D"),
    ("Chest", 202005, "Dungeon E"),
    ("Wrists", 202006, "Dungeon F"),
    ("Hands", 202007, "Dungeon G"),
    ("Waist", 202008, "Dungeon H"),
    ("Legs", 202009, "Dungeon I"),
    ("Feet", 202010, "Dungeon J"),
    ("Ring 1", 202011, "Dungeon K"),
    ("Ring 2", 202012, "Dungeon L"),
    ("Trinket 1", 202013, "Dungeon M"),
    ("Trinket 2", 202014, "Dungeon N"),
    ("Main Hand", 202015, "Dungeon O"),
]

_STANDARD_PAGE = _make_method_page([
    ("Overall Best Gear", _STANDARD_OVERALL_ROWS),
    ("Raiding Best Gear", _STANDARD_RAID_ROWS),
    ("Mythic+ Best Gear", _STANDARD_MPLUS_ROWS),
])

# Blood DK style: hero-talent headings that all say "Overall Best Gear for <HT>"
_BLOOD_DK_PAGE = _make_method_page([
    ("Overall Best Gear for San'layn", _STANDARD_RAID_ROWS),
    ("Overall Best Gear for Deathbringer", _STANDARD_MPLUS_ROWS),
])


# ---------------------------------------------------------------------------
# _build_url — Method
# ---------------------------------------------------------------------------


class TestMethodBuildUrl:
    def test_balance_druid(self):
        url = _build_url("method", "Druid", "Balance", "", "overall", "-")
        assert url == "https://www.method.gg/guides/balance-druid/gearing"

    def test_frost_death_knight(self):
        url = _build_url("method", "Death Knight", "Frost", "", "raid", "-")
        assert url == "https://www.method.gg/guides/frost-death-knight/gearing"

    def test_arms_warrior(self):
        url = _build_url("method", "Warrior", "Arms", "", "mythic_plus", "-")
        assert url == "https://www.method.gg/guides/arms-warrior/gearing"

    def test_url_same_for_all_content_types(self):
        overall = _build_url("method", "Mage", "Frost", "", "overall", "-")
        raid = _build_url("method", "Mage", "Frost", "", "raid", "-")
        mplus = _build_url("method", "Mage", "Frost", "", "mythic_plus", "-")
        assert overall == raid == mplus


# ---------------------------------------------------------------------------
# _classify_method_heading
# ---------------------------------------------------------------------------


class TestClassifyMethodHeading:
    def test_overall(self):
        assert _classify_method_heading("Overall Best Gear") == "overall"

    def test_overall_case_insensitive(self):
        assert _classify_method_heading("OVERALL BEST GEAR") == "overall"

    def test_raid(self):
        assert _classify_method_heading("Raiding Best Gear") == "raid"

    def test_raid_keyword(self):
        assert _classify_method_heading("Best Raid Gear") == "raid"

    def test_mythic_plus(self):
        assert _classify_method_heading("Mythic+ Best Gear") == "mythic_plus"

    def test_mythic_keyword(self):
        assert _classify_method_heading("Best Mythic Gear") == "mythic_plus"

    def test_dungeon_keyword(self):
        assert _classify_method_heading("Best Dungeon Gear for Affliction Warlock") == "mythic_plus"

    def test_hero_talent_heading_with_overall_classifies_as_overall(self):
        # "Overall Best Gear for San'layn" still contains "overall" → classified as overall.
        # The outlier is detected at the page level (duplicate classification), not here.
        assert _classify_method_heading("Overall Best Gear for San'layn") == "overall"

    def test_hero_talent_heading_overall_word_alone_without_ht_context(self):
        # "Overall" in isolation → classifies as overall
        assert _classify_method_heading("Overall Best Gear") == "overall"

    def test_hero_talent_name_only_returns_none(self):
        assert _classify_method_heading("San'layn Build") is None

    def test_best_in_slot_classifies_as_overall(self):
        assert _classify_method_heading("shadow priest Best in Slot Gear") == "overall"

    def test_bis_keyword_classifies_as_overall(self):
        assert _classify_method_heading("BIS List for Fire Mage") == "overall"


# ---------------------------------------------------------------------------
# _extract_method_sections — standard page
# ---------------------------------------------------------------------------


class TestExtractMethodSectionsStandard:
    def setup_method(self):
        self.sections = _extract_method_sections(_STANDARD_PAGE, _TEST_SLOT_MAP)

    def test_finds_three_sections(self):
        assert len(self.sections) == 3

    def test_overall_classified(self):
        s = self.sections[0]
        assert s.inferred_content_type == "overall"
        assert not s.is_outlier

    def test_raid_classified(self):
        s = self.sections[1]
        assert s.inferred_content_type == "raid"
        assert not s.is_outlier

    def test_mplus_classified(self):
        s = self.sections[2]
        assert s.inferred_content_type == "mythic_plus"
        assert not s.is_outlier

    def test_heading_preserved(self):
        assert self.sections[0].heading == "Overall Best Gear"
        assert self.sections[1].heading == "Raiding Best Gear"
        assert self.sections[2].heading == "Mythic+ Best Gear"

    def test_table_index_sequential(self):
        assert [s.table_index for s in self.sections] == [0, 1, 2]

    def test_row_counts(self):
        assert self.sections[0].row_count == 16
        assert self.sections[1].row_count == 15
        assert self.sections[2].row_count == 15


# ---------------------------------------------------------------------------
# _extract_method_sections — Blood DK outlier page
# ---------------------------------------------------------------------------


class TestExtractMethodSectionsOutlier:
    def setup_method(self):
        self.sections = _extract_method_sections(_BLOOD_DK_PAGE, _TEST_SLOT_MAP)

    def test_finds_two_sections(self):
        assert len(self.sections) == 2

    def test_both_are_outliers(self):
        assert all(s.is_outlier for s in self.sections)

    def test_outlier_reason_mentions_duplicate(self):
        for s in self.sections:
            assert "duplicate" in (s.outlier_reason or "").lower()

    def test_both_inferred_as_overall(self):
        for s in self.sections:
            assert s.inferred_content_type == "overall"


# ---------------------------------------------------------------------------
# _extract_method_sections — edge cases
# ---------------------------------------------------------------------------


class TestExtractMethodSectionsEdgeCases:
    def test_empty_html_returns_empty(self):
        assert _extract_method_sections("", _TEST_SLOT_MAP) == []

    def test_tables_without_heading_skipped(self):
        html = "<html><body><table><tr><td>Head</td><td><a href='/item=500001'>X</a></td></tr></table></body></html>"
        sections = _extract_method_sections(html, _TEST_SLOT_MAP)
        assert sections == []

    def test_h2_heading_detected(self):
        html = (
            "<html><body>"
            "<h2>shadow priest Best in Slot Gear</h2>"
            "<table><tr><th>Slot</th><th>Item</th></tr>"
            "<tr><td>Head</td><td><a href='https://www.wowhead.com/item=800001'>X</a></td></tr>"
            "</table>"
            "</body></html>"
        )
        sections = _extract_method_sections(html, _TEST_SLOT_MAP)
        assert len(sections) == 1
        assert sections[0].inferred_content_type == "overall"
        assert sections[0].slots[0].blizzard_item_id == 800001

    def test_single_section_not_outlier(self):
        page = _make_method_page([("Overall Best Gear", [("Head", 100001, "Boss")])])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 1
        assert not sections[0].is_outlier

    def test_unrecognised_heading_is_outlier(self):
        page = _make_method_page([("San'layn Build", [("Head", 100002, "Boss")])])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        assert sections[0].is_outlier
        assert sections[0].inferred_content_type is None

    def test_single_section_with_overall_and_raid_classifies_as_overall(self):
        """'Overall / Raid Best Gear' as the only section → overall, not raid, not outlier."""
        page = _make_method_page([
            ("Overall / Raid Best Gear for Demonology Warlock", [("Head", 100003, "Boss")])
        ])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 1
        assert sections[0].inferred_content_type == "overall"
        assert not sections[0].is_outlier

    def test_single_section_without_overall_keyword_uses_normal_classification(self):
        """Single section with 'Raid' but no 'overall' keyword → normal classification."""
        page = _make_method_page([
            ("Best Raid Gear for Spec", [("Head", 100004, "Boss")])
        ])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        assert sections[0].inferred_content_type == "raid"
        assert not sections[0].is_outlier

    def test_row_without_link_skipped(self):
        page = _make_method_page([("Overall Best Gear", [("Head", 0, None)])])
        # Override to make a row without a real link
        html = (
            "<html><body>"
            "<h3>Overall Best Gear</h3>"
            "<table><tr><td>Head</td><td>No link here</td></tr></table>"
            "</body></html>"
        )
        sections = _extract_method_sections(html, _TEST_SLOT_MAP)
        assert sections[0].row_count == 0


# ---------------------------------------------------------------------------
# _resolve_method_section_local — table selection by content_type
# ---------------------------------------------------------------------------


class TestResolveMethodSectionLocal:
    def setup_method(self):
        self.sections = _extract_method_sections(_STANDARD_PAGE, _TEST_SLOT_MAP)

    def test_overall_resolves(self):
        slots = _resolve_method_section_local(self.sections, "overall")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 200001 in item_ids
        assert 201001 not in item_ids

    def test_raid_resolves(self):
        slots = _resolve_method_section_local(self.sections, "raid")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 201001 in item_ids
        assert 200001 not in item_ids

    def test_mythic_plus_resolves(self):
        slots = _resolve_method_section_local(self.sections, "mythic_plus")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 202001 in item_ids
        assert 200001 not in item_ids

    def test_outlier_page_returns_empty_without_override(self):
        sections = _extract_method_sections(_BLOOD_DK_PAGE, _TEST_SLOT_MAP)
        # All sections are outliers; local resolve can't help
        assert _resolve_method_section_local(sections, "raid") == []
        assert _resolve_method_section_local(sections, "mythic_plus") == []

    def test_unknown_content_type_returns_empty(self):
        assert _resolve_method_section_local(self.sections, "unknown") == []


# ---------------------------------------------------------------------------
# Slot normalisation (via _resolve_method_section_local on standard page)
# ---------------------------------------------------------------------------


class TestSlotNormalisation:
    def _slots_dict(self, content_type: str = "overall") -> dict[str, int]:
        sections = _extract_method_sections(_STANDARD_PAGE, _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, content_type)
        return {s.slot: s.blizzard_item_id for s in slots}

    def test_head_slot(self):
        assert self._slots_dict()["head"] == 200001

    def test_neck_slot(self):
        assert self._slots_dict()["neck"] == 200002

    def test_shoulders_normalised(self):
        assert self._slots_dict()["shoulder"] == 200003

    def test_wrists_normalised(self):
        assert self._slots_dict()["wrist"] == 200006

    def test_hands_slot(self):
        assert self._slots_dict()["hands"] == 200007

    def test_ring_1_and_2_labelled(self):
        d = self._slots_dict()
        assert d["ring_1"] == 200011
        assert d["ring_2"] == 200012

    def test_trinket_1_and_2_labelled(self):
        d = self._slots_dict()
        assert d["trinket_1"] == 200013
        assert d["trinket_2"] == 200014

    def test_main_hand_slot(self):
        assert self._slots_dict()["main_hand"] == 200015

    def test_off_hand_slot(self):
        assert self._slots_dict()["off_hand"] == 200016

    def test_raid_no_off_hand(self):
        d = self._slots_dict("raid")
        assert "off_hand" not in d
        assert len(d) == 15


# ---------------------------------------------------------------------------
# Positional ring / trinket handling
# ---------------------------------------------------------------------------


class TestPositionalSlots:
    def _make_page(self) -> str:
        rows = [
            ("Head", 300001, None),
            ("Ring", 300011, None),
            ("Ring", 300012, None),
            ("Trinket", 300013, None),
            ("Trinket", 300014, None),
        ]
        return _make_method_page([("Overall Best Gear", rows)])

    def setup_method(self):
        sections = _extract_method_sections(self._make_page(), _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        self.d = {s.slot: s.blizzard_item_id for s in slots}

    def test_ring_1(self):
        assert self.d["ring_1"] == 300011

    def test_ring_2(self):
        assert self.d["ring_2"] == 300012

    def test_trinket_1(self):
        assert self.d["trinket_1"] == 300013

    def test_trinket_2(self):
        assert self.d["trinket_2"] == 300014


class TestAlternativeRingsAndTrinkets:
    """4 Ring rows and 4 Trinket rows should all be captured (no cap at 2)."""

    def _make_page(self) -> str:
        rows = [
            ("Ring", 400011, None),
            ("Ring", 400012, None),
            ("Ring", 400013, None),  # alt 1
            ("Ring", 400014, None),  # alt 2
            ("Trinket", 400021, None),
            ("Trinket", 400022, None),
            ("Trinket", 400023, None),  # alt 1
            ("Trinket", 400024, None),  # alt 2
        ]
        return _make_method_page([("Overall Best Gear", rows)])

    def setup_method(self):
        sections = _extract_method_sections(self._make_page(), _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        self.slots = slots
        # group by slot
        from collections import defaultdict
        self.by_slot: dict[str, list[int]] = defaultdict(list)
        for s in slots:
            self.by_slot[s.slot].append(s.blizzard_item_id)

    def test_ring_1_has_two_items(self):
        assert set(self.by_slot["ring_1"]) == {400011, 400013}

    def test_ring_2_has_two_items(self):
        assert set(self.by_slot["ring_2"]) == {400012, 400014}

    def test_trinket_1_has_two_items(self):
        assert set(self.by_slot["trinket_1"]) == {400021, 400023}

    def test_trinket_2_has_two_items(self):
        assert set(self.by_slot["trinket_2"]) == {400022, 400024}

    def test_total_slot_count(self):
        assert len(self.slots) == 8


class TestMultiLinkRingCell:
    """Method pool rows ("Rings (any 2 of these)") have multiple <a> tags in one cell."""

    def _make_page(self) -> str:
        # Build the multi-link ring row manually — _make_method_page only supports one link per row
        pool_row = (
            '<tr><td>Rings (any 2 of these)</td><td>'
            '<a href="https://www.wowhead.com/item=500001">Pool Ring A</a>'
            '<a href="https://www.wowhead.com/item=500002">Pool Ring B</a>'
            '<a href="https://www.wowhead.com/item=500003">Pool Ring C</a>'
            '<a href="https://www.wowhead.com/item=500004">Pool Ring D</a>'
            '</td><td></td></tr>'
        )
        trinket_pool_row = (
            '<tr><td>Trinket</td><td>'
            '<a href="https://www.wowhead.com/item=500011">Pool Trinket A</a>'
            '<a href="https://www.wowhead.com/item=500012">Pool Trinket B</a>'
            '</td><td></td></tr>'
        )
        table = f'<table><thead><tr><th>Slot</th><th>Item</th><th>Source</th></tr></thead><tbody>{pool_row}{trinket_pool_row}</tbody></table>'
        return f'<html><body><h3>Overall Best Gear</h3>{table}</body></html>'

    def setup_method(self):
        sections = _extract_method_sections(self._make_page(), _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        from collections import defaultdict
        self.by_slot: dict[str, list[int]] = defaultdict(list)
        for s in slots:
            self.by_slot[s.slot].append(s.blizzard_item_id)

    def test_all_four_ring_items_captured(self):
        all_rings = set(self.by_slot["ring_1"] + self.by_slot["ring_2"])
        assert all_rings == {500001, 500002, 500003, 500004}

    def test_rings_distributed_across_both_slots(self):
        assert len(self.by_slot["ring_1"]) == 2
        assert len(self.by_slot["ring_2"]) == 2

    def test_both_trinket_pool_items_captured(self):
        all_trinkets = set(self.by_slot["trinket_1"] + self.by_slot["trinket_2"])
        assert all_trinkets == {500011, 500012}


# ---------------------------------------------------------------------------
# Bonus ID extraction
# ---------------------------------------------------------------------------


class TestBonusIds:
    def test_bonus_ids_extracted(self):
        rows = [("Head", 400001, "Boss")]
        page = _make_method_page([("Overall Best Gear", rows)], bonus_ids="1472:6652:8767")
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        assert slots[0].bonus_ids == [1472, 6652, 8767]

    def test_no_bonus_ids_returns_empty_list(self):
        rows = [("Head", 400002, "Boss")]
        page = _make_method_page([("Overall Best Gear", rows)])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        assert slots[0].bonus_ids == []


# ---------------------------------------------------------------------------
# Edge cases — unknown slots, hyphenated Main-Hand
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_slot_skipped(self):
        rows = [
            ("Head", 600001, "Boss"),
            ("UNKNOWN_SLOT_XYZ", 600099, "Boss"),
        ]
        page = _make_method_page([("Overall Best Gear", rows)])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 600001 in item_ids
        assert 600099 not in item_ids

    def test_hyphenated_main_hand(self):
        html = (
            "<html><body>"
            "<h3>Overall Best Gear</h3>"
            "<table>"
            "<tr><th>Slot</th><th>Item</th></tr>"
            "<tr><td>Main-Hand</td><td><a href='https://www.wowhead.com/item=700001'>X</a></td></tr>"
            "</table>"
            "</body></html>"
        )
        sections = _extract_method_sections(html, _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        assert slots[0].slot == "main_hand"
        assert slots[0].blizzard_item_id == 700001

    def test_mythic_plus_missing_returns_empty(self):
        page = _make_method_page([("Overall Best Gear", [("Head", 500001, "Boss")])])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "mythic_plus")
        assert slots == []


# ---------------------------------------------------------------------------
# Weapon build variants — two main_hand rows (e.g. Frost DK 2H vs DW)
# ---------------------------------------------------------------------------


class TestWeaponBuildVariants:
    """Method parser returns slot='main_hand' as an intermediate value for all
    weapon rows.  Downstream (rebuild_bis_from_landing) resolves each to
    main_hand_2h or main_hand_1h via enrichment.items.slot_type.

    These tests verify the parser:
    1. Returns both weapon rows (not just the first).
    2. Preserves document order (guide_order=1 first, guide_order=2 second).
    3. A single-weapon page still produces one main_hand slot.
    """

    def _parse_weapon_rows(self, rows: list[tuple[str, int, str | None]]) -> list:
        page = _make_method_page([("Overall Best Gear", rows)])
        sections = _extract_method_sections(page, _TEST_SLOT_MAP)
        slots = _resolve_method_section_local(sections, "overall")
        return [s for s in slots if s.slot == "main_hand"]

    def test_single_2h_weapon_row_returns_one_main_hand(self):
        rows = [("Main Hand (2h)", 800001, "Boss")]
        weapons = self._parse_weapon_rows(rows)
        assert len(weapons) == 1
        assert weapons[0].blizzard_item_id == 800001

    def test_single_dw_weapon_row_returns_one_main_hand(self):
        rows = [("Main Hand (dw)", 800002, "Boss")]
        weapons = self._parse_weapon_rows(rows)
        assert len(weapons) == 1
        assert weapons[0].blizzard_item_id == 800002

    def test_two_weapon_rows_both_returned(self):
        rows = [
            ("Main Hand (2h)", 800003, "Boss A"),
            ("Main Hand (dw)", 800004, "Boss B"),
        ]
        weapons = self._parse_weapon_rows(rows)
        assert len(weapons) == 2

    def test_two_weapon_rows_document_order_preserved(self):
        rows = [
            ("Main Hand (2h)", 800003, "Boss A"),
            ("Main Hand (dw)", 800004, "Boss B"),
        ]
        weapons = self._parse_weapon_rows(rows)
        # First in document → guide_order=1, second → guide_order=2
        assert weapons[0].blizzard_item_id == 800003
        assert weapons[1].blizzard_item_id == 800004

    def test_third_weapon_row_ignored(self):
        rows = [
            ("Main Hand (2h)", 800005, "Boss A"),
            ("Main Hand (dw)", 800006, "Boss B"),
            ("Main Hand",      800007, "Boss C"),
        ]
        weapons = self._parse_weapon_rows(rows)
        assert len(weapons) == 2
        assert 800007 not in {w.blizzard_item_id for w in weapons}

    def test_plain_main_hand_label_returns_one(self):
        rows = [("Main Hand", 800010, "Boss")]
        weapons = self._parse_weapon_rows(rows)
        assert len(weapons) == 1
        assert weapons[0].blizzard_item_id == 800010
