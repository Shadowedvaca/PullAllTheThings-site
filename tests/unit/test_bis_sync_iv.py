"""Unit tests for Icy Veins BIS extraction helpers.

Covers _iv_classify_section, _iv_classify_tab_label, _iv_parse_sections,
_iv_extract_regular_rows, _iv_extract_trinket_rows, and _iv_is_outlier.
All item IDs are synthetic.
"""

import pytest

from sv_common.guild_sync.bis_sync import (
    IVSection,
    _iv_classify_section,
    _iv_classify_tab_label,
    _iv_extract_regular_rows,
    _iv_extract_trinket_rows,
    _iv_is_outlier,
    _iv_parse_bis_from_raw,
    _iv_parse_sections,
    _iv_parse_trinkets_from_raw,
)

# Mirrors config.slot_labels seed data (relevant subset for IV)
_TEST_SLOT_MAP: dict[str, str | None] = {
    "head": "head",
    "neck": "neck",
    "shoulders": "shoulder",
    "shoulder": "shoulder",
    "back": "back",
    "cloak": "back",
    "chest": "chest",
    "wrists": "wrist",
    "wrist": "wrist",
    "hands": "hands",
    "gloves": "hands",
    "waist": "waist",
    "belt": "waist",
    "legs": "legs",
    "feet": "feet",
    "boots": "feet",
    "ring": None,        # positional: ring_1 / ring_2
    "trinket": None,     # positional: trinket_1 / trinket_2
    "main hand": "main_hand",
    "off hand": "off_hand",
    "off-hand": "off_hand",
    "weapon": "main_hand",
}


def _make_iv_table(*rows: tuple[str, int]) -> str:
    """Build a minimal IV BIS table HTML with (slot_label, item_id) rows."""
    trs = "".join(
        f'<tr><td>{slot}</td>'
        f'<td><span class="spell_icon_span">'
        f'<span data-wowhead="item={item_id}" class="q4">Item {item_id}</span>'
        f'</span></td><td>Source</td></tr>'
        for slot, item_id in rows
    )
    return f"<table><tr><th>Slot</th><th>Item</th><th>Source</th></tr>{trs}</table>"


def _make_iv_page(sections: list[tuple[str, str, str]]) -> str:
    """Build a minimal IV page HTML.

    sections: list of (h3_id, h3_title, table_html)
    """
    parts = []
    for h3_id, h3_title, table_html in sections:
        parts.append(
            f'<div class="heading_container heading_number_3">'
            f'<h3 id="{h3_id}">{h3_title}</h3></div>'
            f"{table_html}"
        )
    return f"<html><body>{''.join(parts)}</body></html>"


def _make_trinket_details(*tiers: tuple[str, list[int]]) -> str:
    """Build a minimal IV trinket-dropdown <details> element.

    tiers: list of (tier_label, [item_ids])
    """
    trs = ""
    for tier_label, item_ids in tiers:
        items_html = "".join(
            f'<li><span class="spell_icon_span">'
            f'<span data-wowhead="item={iid}" class="q4">Item {iid}</span>'
            f'</span></li>'
            for iid in item_ids
        )
        trs += (
            f'<tr><td><span style="color:#e6cc80"><strong>{tier_label}</strong></span></td>'
            f'<td><ul>{items_html}</ul></td></tr>'
        )
    return f'<details class="trinket-dropdown" open><table>{trs}</table></details>'


def _make_iv_image_block(*areas: tuple[str, str, str]) -> str:
    """Build a minimal IV image_block tab page.

    areas: list of (button_label, h3_id_or_empty, table_html)
    If h3_id is empty, the area has no h3 (simulates Blood DK / Vengeance DH).
    """
    buttons = "".join(
        f'<span id="area_{i+1}_button">{label}</span>'
        for i, (label, _, _) in enumerate(areas)
    )
    contents = "".join(
        (
            f'<div class="image_block_content" id="area_{i+1}">'
            f'<div class="heading_container heading_number_3">'
            f'<h3 id="{h3_id}">{label}</h3></div>'
            f"{table_html}</div>"
            if h3_id else
            f'<div class="image_block_content" id="area_{i+1}">'
            f"{table_html}</div>"
        )
        for i, (label, h3_id, table_html) in enumerate(areas)
    )
    return (
        f"<html><body>"
        f'<div class="image_block">'
        f'<div class="image_block_header">'
        f'<div class="image_block_header_buttons">{buttons}</div>'
        f"</div>"
        f"{contents}"
        f"</div>"
        f"</body></html>"
    )


# ---------------------------------------------------------------------------
# _iv_classify_tab_label
# ---------------------------------------------------------------------------


class TestIvClassifyTabLabel:
    def test_overall_label(self):
        assert _iv_classify_tab_label("Overall BiS List") == "overall"

    def test_bis_label(self):
        assert _iv_classify_tab_label("BiS List for Season 1") == "overall"

    def test_best_in_slot_label(self):
        assert _iv_classify_tab_label("Overall Best-in-Slot") == "overall"

    def test_raid_label(self):
        assert _iv_classify_tab_label("Raid Gear BiS List") == "raid"

    def test_raid_specific_label(self):
        # "BiS Raid (San'layn)" — raid takes precedence over bis
        assert _iv_classify_tab_label("BiS Raid (San'layn)") == "raid"

    def test_mythic_label(self):
        assert _iv_classify_tab_label("Mythic+ Gear BiS List") == "mythic_plus"

    def test_mythic_plus_label(self):
        assert _iv_classify_tab_label("Mythic + Best-in-Slot") == "mythic_plus"

    def test_unrecognised_label_returns_none(self):
        # Without instance names the label stays unclassified
        assert _iv_classify_tab_label("Dreamrift, Voidspire, and March Gear") is None

    def test_empty_label_returns_none(self):
        assert _iv_classify_tab_label("") is None

    def test_case_insensitive(self):
        assert _iv_classify_tab_label("RAID GEAR BIS LIST") == "raid"

    # --- Phase 5: raid instance name detection ---

    def test_instance_name_classifies_as_raid(self):
        names = frozenset({"Dreamrift", "Voidspire", "March on Quel'Danas"})
        assert _iv_classify_tab_label("Dreamrift BiS List", names) == "raid"

    def test_instance_name_takes_priority_over_bis_keyword(self):
        # Label has "BiS" (would normally → overall) but also contains a raid
        # instance name → should be classified as raid, not overall.
        names = frozenset({"Liberation of Undermine"})
        assert _iv_classify_tab_label("Liberation of Undermine BiS List", names) == "raid"

    def test_instance_name_case_insensitive(self):
        names = frozenset({"Dreamrift"})
        assert _iv_classify_tab_label("DREAMRIFT GEAR BIS", names) == "raid"

    def test_instance_name_partial_label(self):
        # Instance name embedded in a longer label
        names = frozenset({"Voidspire"})
        assert _iv_classify_tab_label("Dreamrift, Voidspire, and March Gear", names) == "raid"

    def test_multiple_instance_names_any_match(self):
        names = frozenset({"Dreamrift", "Voidspire"})
        assert _iv_classify_tab_label("Voidspire BiS", names) == "raid"

    def test_empty_instance_names_falls_through(self):
        # Empty set → no instance name check, label with "BiS" → overall
        assert _iv_classify_tab_label("Liberation of Undermine BiS List", frozenset()) == "overall"

    def test_mythic_still_takes_precedence(self):
        # Even if "Mythic" appears AND there's an instance name match, mythic wins
        names = frozenset({"Mythic Overland"})
        assert _iv_classify_tab_label("Mythic Overland BiS", names) == "mythic_plus"

    def test_raid_keyword_still_wins_before_instance_check(self):
        # The "raid" keyword path fires before instance name check
        names = frozenset({"Dreamrift"})
        assert _iv_classify_tab_label("Raid: Dreamrift BiS", names) == "raid"

    def test_non_raid_instance_name_no_false_positive(self):
        # Instance names that don't appear in label → no match → None
        names = frozenset({"Nerub-ar Palace", "Liberation of Undermine"})
        assert _iv_classify_tab_label("Overall Best-in-Slot Gear", names) == "overall"


# ---------------------------------------------------------------------------
# _iv_classify_section
# ---------------------------------------------------------------------------


class TestIvClassifySection:
    def test_overall_prefix(self):
        ct, _ = _iv_classify_section("overall-bis-list-for-balance-druid")
        assert ct == "overall"

    def test_raid_prefix(self):
        ct, _ = _iv_classify_section("raid-bis-list-for-balance-druid")
        assert ct == "raid"

    def test_mythic_gear_prefix(self):
        ct, _ = _iv_classify_section("mythic-gear-bis-list-for-balance-druid")
        assert ct == "mythic_plus"

    def test_unknown_prefix_returns_none(self):
        ct, _ = _iv_classify_section("some-other-section")
        assert ct is None

    def test_empty_id_returns_none(self):
        ct, _ = _iv_classify_section("")
        assert ct is None

    def test_is_trinket_section_always_false(self):
        # _iv_classify_section never sets is_trinket; that's determined by DOM structure
        _, is_trinket = _iv_classify_section("overall-bis-list-for-balance-druid")
        assert is_trinket is False


# ---------------------------------------------------------------------------
# _iv_extract_regular_rows
# ---------------------------------------------------------------------------


class TestIvExtractRegularRows:
    def _parse(self, html: str):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            pytest.skip("BeautifulSoup not available")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        return _iv_extract_regular_rows(table, _TEST_SLOT_MAP)

    def test_extracts_named_slots(self):
        html = _make_iv_table(("Head", 111111), ("Neck", 222222), ("Chest", 333333))
        slots = self._parse(html)
        by_slot = {s.slot: s.blizzard_item_id for s in slots}
        assert by_slot["head"] == 111111
        assert by_slot["neck"] == 222222
        assert by_slot["chest"] == 333333

    def test_ring_positional_assignment(self):
        html = _make_iv_table(("Ring", 100001), ("Ring", 100002))
        slots = self._parse(html)
        by_slot = {s.slot: s.blizzard_item_id for s in slots}
        assert by_slot.get("ring_1") == 100001
        assert by_slot.get("ring_2") == 100002

    def test_trinket_positional_assignment(self):
        html = _make_iv_table(("Trinket", 200001), ("Trinket", 200002))
        slots = self._parse(html)
        by_slot = {s.slot: s.blizzard_item_id for s in slots}
        assert by_slot.get("trinket_1") == 200001
        assert by_slot.get("trinket_2") == 200002

    def test_unknown_slot_skipped(self):
        html = _make_iv_table(("Head", 111111), ("Weird Slot", 999999))
        slots = self._parse(html)
        assert len(slots) == 1
        assert slots[0].slot == "head"

    def test_skips_header_row(self):
        # The table fixture includes a header row — only data rows should appear
        html = _make_iv_table(("Head", 111111))
        slots = self._parse(html)
        assert len(slots) == 1

    def test_full_slot_set(self):
        rows = [
            ("Head", 1), ("Neck", 2), ("Shoulder", 3), ("Back", 4),
            ("Chest", 5), ("Wrist", 6), ("Hands", 7), ("Waist", 8),
            ("Legs", 9), ("Feet", 10), ("Ring", 11), ("Ring", 12),
            ("Trinket", 13), ("Trinket", 14), ("Main Hand", 15), ("Off Hand", 16),
        ]
        html = _make_iv_table(*rows)
        slots = self._parse(html)
        slot_keys = {s.slot for s in slots}
        assert "ring_1" in slot_keys
        assert "ring_2" in slot_keys
        assert "trinket_1" in slot_keys
        assert "trinket_2" in slot_keys
        assert len(slots) == 16


# ---------------------------------------------------------------------------
# _iv_extract_trinket_rows
# ---------------------------------------------------------------------------


class TestIvExtractTrinketRows:
    def _parse(self, html: str):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            pytest.skip("BeautifulSoup not available")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        details = soup.find("details")
        return _iv_extract_trinket_rows(details)

    def test_extracts_tiers(self):
        html = _make_trinket_details(
            ("S Tier", [111111, 222222]),
            ("A Tier", [333333]),
        )
        rows = self._parse(html)
        by_tier = {}
        for r in rows:
            by_tier.setdefault(r["tier"], []).append(r["item_id"])
        assert 111111 in by_tier["S"]
        assert 222222 in by_tier["S"]
        assert 333333 in by_tier["A"]

    def test_sort_order_within_tier(self):
        html = _make_trinket_details(("S Tier", [111, 222, 333]))
        rows = self._parse(html)
        s_rows = [r for r in rows if r["tier"] == "S"]
        assert s_rows[0]["sort_order"] == 0
        assert s_rows[1]["sort_order"] == 1
        assert s_rows[2]["sort_order"] == 2

    def test_sort_order_resets_per_tier(self):
        html = _make_trinket_details(("S Tier", [111]), ("A Tier", [222]))
        rows = self._parse(html)
        by_id = {r["item_id"]: r for r in rows}
        assert by_id[111]["sort_order"] == 0
        assert by_id[222]["sort_order"] == 0  # resets for A tier

    def test_empty_details_returns_empty(self):
        html = '<details class="trinket-dropdown"></details>'
        rows = self._parse(html)
        assert rows == []


# ---------------------------------------------------------------------------
# _iv_is_outlier
# ---------------------------------------------------------------------------


class TestIvIsOutlier:
    def _make_section(self, **kwargs) -> IVSection:
        defaults = {
            "h3_id": "overall-bis-list-for-balance-druid",
            "section_title": "Overall BiS",
            "content_type": "overall",
            "is_trinket_section": False,
            "row_count": 16,
            "slots": [],
            "trinket_rows": [],
            "is_outlier": False,
            "outlier_reason": None,
        }
        defaults.update(kwargs)
        return IVSection(**defaults)

    def test_normal_section_not_outlier(self):
        s = self._make_section()
        is_out, reason = _iv_is_outlier(s)
        assert not is_out
        assert reason is None

    def test_unknown_content_type_is_outlier(self):
        s = self._make_section(content_type=None)
        is_out, reason = _iv_is_outlier(s)
        assert is_out
        assert "unrecognised" in reason

    def test_zero_rows_is_outlier(self):
        s = self._make_section(row_count=0)
        is_out, reason = _iv_is_outlier(s)
        assert is_out
        assert "no rows" in reason

    def test_fewer_than_5_rows_is_outlier(self):
        s = self._make_section(row_count=3)
        is_out, reason = _iv_is_outlier(s)
        assert is_out
        assert "short" in reason

    def test_exactly_5_rows_not_outlier(self):
        s = self._make_section(row_count=5)
        is_out, _ = _iv_is_outlier(s)
        assert not is_out

    def test_trinket_section_no_rows_is_outlier(self):
        s = self._make_section(is_trinket_section=True, row_count=0, trinket_rows=[])
        is_out, reason = _iv_is_outlier(s)
        assert is_out

    def test_trinket_section_with_rows_not_outlier(self):
        rows = [{"tier": "S", "item_id": 111, "sort_order": 0}]
        s = self._make_section(is_trinket_section=True, row_count=1, trinket_rows=rows)
        # row_count=1 is < 5, still flags as short — trinket sections are naturally shorter
        is_out, reason = _iv_is_outlier(s)
        assert is_out  # short trinket section still flagged — low count is suspicious


# ---------------------------------------------------------------------------
# _iv_parse_sections — integration of the above
# ---------------------------------------------------------------------------


class TestIvParseSections:
    def test_parses_overall_section(self):
        table_html = _make_iv_table(
            *[("Head", i) for i in range(1, 17)]  # 16 rows
        )
        page = _make_iv_page([
            ("overall-bis-list-for-balance-druid", "Overall BiS List for Balance Druid", table_html)
        ])
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 1
        assert sections[0].content_type == "overall"
        assert sections[0].row_count == 16
        assert not sections[0].is_outlier

    def test_parses_raid_section(self):
        table_html = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_page([
            ("raid-bis-list-for-balance-druid", "Raid BiS", table_html)
        ])
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert sections[0].content_type == "raid"

    def test_parses_mythic_plus_section(self):
        table_html = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_page([
            ("mythic-gear-bis-list-for-balance-druid", "Mythic+ BiS", table_html)
        ])
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert sections[0].content_type == "mythic_plus"

    def test_parses_multiple_sections(self):
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_page([
            ("overall-bis-list-for-balance-druid", "Overall", table),
            ("raid-bis-list-for-balance-druid", "Raid", table),
            ("mythic-gear-bis-list-for-balance-druid", "M+", table),
        ])
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 3
        types = {s.content_type for s in sections}
        assert types == {"overall", "raid", "mythic_plus"}

    def test_trinket_section_detected(self):
        details_html = _make_trinket_details(
            ("S Tier", [111111, 222222]),
            ("A Tier", [333333]),
        )
        # Wrap details in a page with a heading
        page = (
            '<html><body>'
            '<div class="heading_container heading_number_3">'
            '<h3 id="overall-bis-list-for-balance-druid">Overall BiS</h3></div>'
            + details_html +
            '</body></html>'
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 1
        assert sections[0].is_trinket_section is True
        assert sections[0].row_count == 3  # 2 S-tier + 1 A-tier items

    def test_empty_html_returns_empty(self):
        assert _iv_parse_sections("", _TEST_SLOT_MAP) == []

    def test_no_heading_containers_returns_empty(self):
        assert _iv_parse_sections("<html><body><table></table></body></html>", _TEST_SLOT_MAP) == []

    def test_outlier_section_flagged(self):
        table_html = _make_iv_table(("Head", 111))  # only 1 row → outlier
        page = _make_iv_page([
            ("overall-bis-list-for-balance-druid", "Overall", table_html)
        ])
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert sections[0].is_outlier is True

    def test_unknown_h3_id_is_outlier(self):
        table_html = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_page([
            ("some-random-heading", "Random Section", table_html)
        ])
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert sections[0].is_outlier is True
        assert sections[0].content_type is None

    # --- image_block tab path ---

    def test_image_block_parses_overall(self):
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_image_block(
            ("Overall BiS List", "overall-bis-list-for-specspec", table),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 1
        assert sections[0].content_type == "overall"
        assert not sections[0].is_outlier

    def test_image_block_parses_three_tabs(self):
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_image_block(
            ("Overall BiS List", "overall-bis-list-for-specspec", table),
            ("Raid Gear BiS List", "raid-bis-list-for-specspec", table),
            ("Mythic+ Gear BiS List", "mythic-gear-bis-list-for-specspec", table),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 3
        types = {s.content_type for s in sections}
        assert types == {"overall", "raid", "mythic_plus"}

    def test_image_block_no_h3_uses_area_id(self):
        # Simulates Vengeance DH / Blood DK style: no h3 inside the content div
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_image_block(
            ("Overall BiS List", "", table),
            ("Mythic+", "", table),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert len(sections) == 2
        by_type = {s.content_type: s for s in sections}
        assert by_type["overall"].h3_id == "area_1"
        assert by_type["mythic_plus"].h3_id == "area_2"

    def test_image_block_skips_unclassifiable_tabs(self):
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_image_block(
            ("Overall BiS List", "", table),
            ("Dreamrift, Voidspire, and March Gear", "", table),  # no keyword
            ("Mythic+", "", table),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        types = {s.content_type for s in sections}
        assert types == {"overall", "mythic_plus"}
        assert len(sections) == 2

    # --- Phase 5: raid instance name detection via _iv_parse_sections ---

    def test_image_block_classifies_instance_name_tab_as_raid(self):
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        names = frozenset({"Dreamrift", "Voidspire"})
        page = _make_iv_image_block(
            ("Overall BiS List", "", table),
            ("Dreamrift, Voidspire, and March Gear", "", table),
            ("Mythic+", "", table),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP, names)
        types = {s.content_type for s in sections}
        assert "raid" in types
        assert types == {"overall", "raid", "mythic_plus"}
        assert len(sections) == 3

    def test_image_block_instance_name_bis_label_classifies_as_raid(self):
        # Simulates "Liberation of Undermine BiS List" — contains "BiS" but the
        # instance name check fires first and classifies as raid.
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        names = frozenset({"Liberation of Undermine"})
        page = _make_iv_image_block(
            ("Overall BiS List", "", table),
            ("Liberation of Undermine BiS List", "", table),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP, names)
        types = {s.content_type for s in sections}
        assert types == {"overall", "raid"}

    def test_image_block_no_instance_names_bis_label_not_raid(self):
        # Without instance names, "Liberation of Undermine BiS List" is classified
        # as "overall" (via "bis" keyword) — not "raid".
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_image_block(
            ("Liberation of Undermine BiS List", "", table),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        assert all(s.content_type != "raid" for s in sections)

    def test_image_block_parses_trinket_tab(self):
        details_html = _make_trinket_details(
            ("S Tier", [111111, 222222]),
            ("A Tier", [333333]),
        )
        page = _make_iv_image_block(
            ("Overall BiS List", "overall-specspec", _make_iv_table(*[("Head", i) for i in range(1, 17)])),
            ("Mythic+ Trinket Rankings", "", details_html),
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        trinket_secs = [s for s in sections if s.is_trinket_section]
        assert len(trinket_secs) == 1
        assert trinket_secs[0].content_type == "mythic_plus"
        assert len(trinket_secs[0].trinket_rows) == 3

    def test_image_block_takes_priority_over_heading_container(self):
        # When image_block tabs are present, they should be used even if
        # the page also has bare heading_container divs outside the block.
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_image_block(
            ("Overall BiS List", "overall-specspec", table),
        )
        # Also append a bare heading_container that would parse via fallback
        page = page.replace(
            "</body>",
            '<div class="heading_container"><h3 id="some-random">X</h3></div>'
            + table
            + "</body>",
        )
        sections = _iv_parse_sections(page, _TEST_SLOT_MAP)
        # Should use image_block result only (1 section, not 2)
        assert len(sections) == 1
        assert sections[0].content_type == "overall"


# ---------------------------------------------------------------------------
# _iv_parse_bis_from_raw
# ---------------------------------------------------------------------------


class TestIvParseBisFromRaw:
    def _make_page(self, content_type: str, n_rows: int = 16) -> str:
        table = _make_iv_table(*[("Head", i) for i in range(1, n_rows + 1)])
        label = {"overall": "Overall BiS List", "raid": "Raid Gear BiS List", "mythic_plus": "Mythic+ Gear BiS List"}[content_type]
        return _make_iv_image_block((label, f"{content_type}-specspec", table))

    def test_returns_slots_for_matching_content_type(self):
        page = self._make_page("overall")
        slots = _iv_parse_bis_from_raw(page, "overall", _TEST_SLOT_MAP)
        assert len(slots) == 16
        assert all(s.slot == "head" for s in slots)

    def test_returns_empty_for_missing_content_type(self):
        page = self._make_page("raid")
        slots = _iv_parse_bis_from_raw(page, "overall", _TEST_SLOT_MAP)
        assert slots == []

    def test_skips_outlier_sections(self):
        # Only 2 rows → flagged as outlier → should be skipped
        table = _make_iv_table(("Head", 1), ("Neck", 2))
        page = _make_iv_image_block(("Overall BiS List", "overall-specspec", table))
        slots = _iv_parse_bis_from_raw(page, "overall", _TEST_SLOT_MAP)
        assert slots == []

    def test_skips_trinket_sections(self):
        details = _make_trinket_details(("S Tier", [111, 222]))
        page = _make_iv_image_block(("Overall BiS List", "", details))
        slots = _iv_parse_bis_from_raw(page, "overall", _TEST_SLOT_MAP)
        assert slots == []

    def test_multi_tab_page_picks_correct_section(self):
        table_overall = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        table_raid = _make_iv_table(*[("Neck", i) for i in range(100, 116)])
        page = _make_iv_image_block(
            ("Overall BiS List", "overall-spec", table_overall),
            ("Raid Gear BiS List", "raid-spec", table_raid),
        )
        overall_slots = _iv_parse_bis_from_raw(page, "overall", _TEST_SLOT_MAP)
        raid_slots = _iv_parse_bis_from_raw(page, "raid", _TEST_SLOT_MAP)
        assert all(s.slot == "head" for s in overall_slots)
        assert all(s.slot == "neck" for s in raid_slots)

    def test_empty_html_returns_empty(self):
        assert _iv_parse_bis_from_raw("", "overall", _TEST_SLOT_MAP) == []


# ---------------------------------------------------------------------------
# _iv_parse_trinkets_from_raw
# ---------------------------------------------------------------------------


class TestIvParseTrinketsFromRaw:
    def _make_page_with_trinkets(self, *tiers: tuple[str, list[int]]) -> str:
        details = _make_trinket_details(*tiers)
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        return _make_iv_image_block(
            ("Overall BiS List", "overall-spec", table),
            ("Trinket Rankings", "", details),
        )

    def test_extracts_trinket_rows(self):
        page = self._make_page_with_trinkets(
            ("S Tier", [111111, 222222]),
            ("A Tier", [333333]),
        )
        rows = _iv_parse_trinkets_from_raw(page)
        by_tier: dict[str, list[int]] = {}
        for r in rows:
            by_tier.setdefault(r["tier"], []).append(r["item_id"])
        assert 111111 in by_tier["S"]
        assert 222222 in by_tier["S"]
        assert 333333 in by_tier["A"]

    def test_empty_html_returns_empty(self):
        assert _iv_parse_trinkets_from_raw("") == []

    def test_no_trinket_dropdown_returns_empty(self):
        table = _make_iv_table(*[("Head", i) for i in range(1, 17)])
        page = _make_iv_image_block(("Overall BiS List", "overall-spec", table))
        rows = _iv_parse_trinkets_from_raw(page)
        assert rows == []

    def test_multiple_dropdowns_combined(self):
        # Two separate trinket-dropdown sections (unusual but possible)
        d1 = _make_trinket_details(("S Tier", [111]))
        d2 = _make_trinket_details(("A Tier", [222]))
        html = f"<html><body>{d1}{d2}</body></html>"
        rows = _iv_parse_trinkets_from_raw(html)
        item_ids = [r["item_id"] for r in rows]
        assert 111 in item_ids
        assert 222 in item_ids

    def test_sort_order_preserved(self):
        page = self._make_page_with_trinkets(("S Tier", [10, 20, 30]))
        rows = _iv_parse_trinkets_from_raw(page)
        s_rows = [r for r in rows if r["tier"] == "S"]
        assert [r["sort_order"] for r in s_rows] == [0, 1, 2]
