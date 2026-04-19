"""Unit tests for Method.gg BIS extraction.

Covers _parse_method_html with fixture HTML snapshots of the page structure.
All item IDs in fixtures are plausible but synthetic — not live data.
"""

import pytest

from sv_common.guild_sync.bis_sync import _build_url, _parse_method_html


# ---------------------------------------------------------------------------
# Fixture HTML helpers
# ---------------------------------------------------------------------------

def _make_method_page(
    overall_rows: list[tuple[str, int, str | None]],
    raid_rows: list[tuple[str, int, str | None]],
    mplus_rows: list[tuple[str, int, str | None]],
    bonus_ids: str = "",
) -> str:
    """Build a minimal Method.gg /gearing page with three tables.

    Each row tuple: (slot_label, item_id, source_text_or_None)
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

    return (
        "<html><body>"
        + _table(overall_rows)
        + _table(raid_rows)
        + _table(mplus_rows)
        + "</body></html>"
    )


_STANDARD_OVERALL = [
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

_STANDARD_RAID = [
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

_STANDARD_MPLUS = [
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
        """All three content types produce the same page URL."""
        overall = _build_url("method", "Mage", "Frost", "", "overall", "-")
        raid = _build_url("method", "Mage", "Frost", "", "raid", "-")
        mplus = _build_url("method", "Mage", "Frost", "", "mythic_plus", "-")
        assert overall == raid == mplus


# ---------------------------------------------------------------------------
# _parse_method_html — table selection by content_type
# ---------------------------------------------------------------------------


class TestParseMethodHtmlTableSelection:
    def setup_method(self):
        self.html = _make_method_page(_STANDARD_OVERALL, _STANDARD_RAID, _STANDARD_MPLUS)

    def test_overall_uses_table_0(self):
        slots = _parse_method_html(self.html, content_type="overall")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 200001 in item_ids
        assert 201001 not in item_ids
        assert 202001 not in item_ids

    def test_raid_uses_table_1(self):
        slots = _parse_method_html(self.html, content_type="raid")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 201001 in item_ids
        assert 200001 not in item_ids
        assert 202001 not in item_ids

    def test_mythic_plus_uses_table_2(self):
        slots = _parse_method_html(self.html, content_type="mythic_plus")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 202001 in item_ids
        assert 200001 not in item_ids
        assert 201001 not in item_ids

    def test_default_content_type_is_overall(self):
        slots_default = _parse_method_html(self.html)
        slots_overall = _parse_method_html(self.html, content_type="overall")
        assert [s.blizzard_item_id for s in slots_default] == [
            s.blizzard_item_id for s in slots_overall
        ]


# ---------------------------------------------------------------------------
# _parse_method_html — slot name normalisation
# ---------------------------------------------------------------------------


class TestParseMethodHtmlSlots:
    def _slots_dict(self, content_type: str = "overall") -> dict[str, int]:
        html = _make_method_page(_STANDARD_OVERALL, _STANDARD_RAID, _STANDARD_MPLUS)
        slots = _parse_method_html(html, content_type=content_type)
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

    def test_raid_16_slots_no_off_hand(self):
        d = self._slots_dict("raid")
        assert "off_hand" not in d
        assert len(d) == 15


# ---------------------------------------------------------------------------
# _parse_method_html — positional ring/trinket handling
# ---------------------------------------------------------------------------


class TestParseMethodHtmlPositional:
    def _positional_page(self) -> str:
        """Page where rings/trinkets use 'Ring'/'Trinket' label (no number)."""
        rows = [
            ("Head", 300001, None),
            ("Ring", 300011, None),
            ("Ring", 300012, None),
            ("Trinket", 300013, None),
            ("Trinket", 300014, None),
        ]
        return _make_method_page(rows, [], [])

    def test_positional_ring_1(self):
        slots = _parse_method_html(self._positional_page(), content_type="overall")
        d = {s.slot: s.blizzard_item_id for s in slots}
        assert d["ring_1"] == 300011

    def test_positional_ring_2(self):
        slots = _parse_method_html(self._positional_page(), content_type="overall")
        d = {s.slot: s.blizzard_item_id for s in slots}
        assert d["ring_2"] == 300012

    def test_positional_trinket_1(self):
        slots = _parse_method_html(self._positional_page(), content_type="overall")
        d = {s.slot: s.blizzard_item_id for s in slots}
        assert d["trinket_1"] == 300013

    def test_positional_trinket_2(self):
        slots = _parse_method_html(self._positional_page(), content_type="overall")
        d = {s.slot: s.blizzard_item_id for s in slots}
        assert d["trinket_2"] == 300014


# ---------------------------------------------------------------------------
# _parse_method_html — bonus ID extraction
# ---------------------------------------------------------------------------


class TestParseMethodHtmlBonusIds:
    def test_bonus_ids_extracted(self):
        rows = [("Head", 400001, "Boss")]
        html = _make_method_page(rows, [], [], bonus_ids="1472:6652:8767")
        slots = _parse_method_html(html, content_type="overall")
        assert slots[0].bonus_ids == [1472, 6652, 8767]

    def test_no_bonus_ids_returns_empty_list(self):
        rows = [("Head", 400002, "Boss")]
        html = _make_method_page(rows, [], [])
        slots = _parse_method_html(html, content_type="overall")
        assert slots[0].bonus_ids == []


# ---------------------------------------------------------------------------
# _parse_method_html — edge cases
# ---------------------------------------------------------------------------


class TestParseMethodHtmlEdgeCases:
    def test_missing_table_returns_empty(self):
        """Requesting table index 2 when page only has 1 table → empty list."""
        rows = [("Head", 500001, "Boss")]
        html = "<html><body><table><tbody><tr><td>Head</td><td><a href='/item=500001'>X</a></td></tr></tbody></table></body></html>"
        slots = _parse_method_html(html, content_type="mythic_plus")
        assert slots == []

    def test_row_without_link_skipped(self):
        html = (
            "<html><body><table>"
            "<tr><th>Slot</th><th>Item</th></tr>"
            "<tr><td>Head</td><td>No link here</td></tr>"
            "</table></body></html>"
        )
        slots = _parse_method_html(html, content_type="overall")
        assert slots == []

    def test_unknown_slot_skipped(self):
        rows = [
            ("Head", 600001, "Boss"),
            ("UNKNOWN_SLOT_XYZ", 600099, "Boss"),
        ]
        html = _make_method_page(rows, [], [])
        slots = _parse_method_html(html, content_type="overall")
        item_ids = {s.blizzard_item_id for s in slots}
        assert 600001 in item_ids
        assert 600099 not in item_ids

    def test_empty_html_returns_empty(self):
        slots = _parse_method_html("", content_type="overall")
        assert slots == []

    def test_hyphenated_main_hand(self):
        """Method may use 'Main-Hand' with a hyphen."""
        html = (
            "<html><body><table>"
            "<tr><th>Slot</th><th>Item</th></tr>"
            "<tr><td>Main-Hand</td><td><a href='https://www.wowhead.com/item=700001'>X</a></td></tr>"
            "</table></body></html>"
        )
        slots = _parse_method_html(html, content_type="overall")
        assert slots[0].slot == "main_hand"
        assert slots[0].blizzard_item_id == 700001
