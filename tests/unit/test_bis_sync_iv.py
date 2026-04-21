"""Unit tests for Icy Veins BIS extraction helpers.

Covers _iv_classify_section, _iv_parse_sections, _iv_extract_regular_rows,
_iv_extract_trinket_rows, and _iv_is_outlier.
All item IDs are synthetic.
"""

import pytest

from sv_common.guild_sync.bis_sync import (
    IVSection,
    _iv_classify_section,
    _iv_extract_regular_rows,
    _iv_extract_trinket_rows,
    _iv_is_outlier,
    _iv_parse_sections,
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
