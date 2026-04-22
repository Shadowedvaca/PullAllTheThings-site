"""Unit tests for Archon.gg BIS extraction — Phase B.

Covers:
  - _build_url() for origin='archon'
  - _parse_archon_page() — pure function, no DB or network
"""

import json
import pytest

from sv_common.guild_sync.bis_sync import (
    _build_url,
    _parse_archon_page,
)


# ---------------------------------------------------------------------------
# Slot map fixture — mirrors config.slot_labels seed data (migrations 0160, 0173)
# ---------------------------------------------------------------------------

_ARCHON_SLOT_MAP: dict[str, str | None] = {
    "head":       "head",
    "neck":       "neck",
    "shoulders":  "shoulder",
    "back":       "back",
    "chest":      "chest",
    "wrist":      "wrist",
    "gloves":     "hands",
    "belt":       "waist",
    "legs":       "legs",
    "feet":       "feet",
    "trinket":    None,   # expand to trinket_1 + trinket_2
    "rings":      None,   # expand to ring_1 + ring_2 (seeded in 0173)
    "main-hand":  "main_hand",
    "off-hand":   "off_hand",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(tables: list[dict], total_parses: int = 71309) -> dict:
    """Build a minimal Archon page object with a gear-tables section."""
    return {
        "totalParses": total_parses,
        "lastUpdated": "2026-04-16T12:00:00Z",
        "sections": [
            {
                "navigationId": "gear-tables",
                "props": {"tables": tables},
            }
        ],
    }


def _make_table(header: str, rows: list[dict]) -> dict:
    """Build a minimal Archon gear table."""
    return {
        "columns": {"item": {"header": header}},
        "data": rows,
    }


def _make_row(item_id: int, pct: float) -> dict:
    """Build a minimal Archon data row with JSX strings."""
    return {
        "item": f'<ItemIcon id={{{item_id}}} quality={{4}} itemLevel={{632}}>Item Name</ItemIcon>',
        "popularity": f'<Styled type="legendary">{pct}%</Styled>',
    }


# ---------------------------------------------------------------------------
# _build_url — archon origin
# ---------------------------------------------------------------------------


class TestBuildUrlArchonGg:
    def test_dungeon_balance_druid(self):
        url = _build_url("archon", "Druid", "Balance", "", "dungeon")
        assert url == (
            "https://www.archon.gg/wow/builds/balance/druid"
            "/mythic-plus/gear-and-tier-set/10/all-dungeons/this-week"
        )

    def test_dungeon_death_knight_blood(self):
        url = _build_url("archon", "Death Knight", "Blood", "", "dungeon")
        assert url == (
            "https://www.archon.gg/wow/builds/blood/death-knight"
            "/mythic-plus/gear-and-tier-set/10/all-dungeons/this-week"
        )

    def test_mythic_plus_alias_also_works(self):
        url = _build_url("archon", "Mage", "Frost", "", "mythic_plus")
        assert url == (
            "https://www.archon.gg/wow/builds/frost/mage"
            "/mythic-plus/gear-and-tier-set/10/all-dungeons/this-week"
        )

    def test_raid_restoration_shaman(self):
        url = _build_url("archon", "Shaman", "Restoration", "", "raid")
        assert url == (
            "https://www.archon.gg/wow/builds/restoration/shaman"
            "/raid/gear-and-tier-set/mythic/all-bosses"
        )

    def test_raid_demon_hunter_havoc(self):
        url = _build_url("archon", "Demon Hunter", "Havoc", "", "raid")
        assert url == (
            "https://www.archon.gg/wow/builds/havoc/demon-hunter"
            "/raid/gear-and-tier-set/mythic/all-bosses"
        )

    def test_spec_first_class_second_in_url(self):
        """Archon URL order is spec-first, class-second (not the usual class/spec order)."""
        url = _build_url("archon", "Druid", "Balance", "", "raid")
        assert "/balance/druid/" in url

    def test_overall_content_type_returns_none(self):
        """Archon has no overall page."""
        url = _build_url("archon", "Druid", "Balance", "", "overall")
        assert url is None

    def test_unknown_content_type_returns_none(self):
        url = _build_url("archon", "Druid", "Balance", "", "something_else")
        assert url is None

    def test_multi_word_class_uses_hyphens(self):
        url = _build_url("archon", "Demon Hunter", "Havoc", "", "dungeon")
        assert "demon-hunter" in url

    def test_multi_word_spec_uses_hyphens(self):
        url = _build_url("archon", "Shaman", "Enhancement", "", "dungeon")
        assert "enhancement" in url

    def test_slug_separator_param_ignored(self):
        """Archon always uses hyphens regardless of slug_sep param."""
        url_hyph = _build_url("archon", "Death Knight", "Blood", "", "raid", "-")
        url_under = _build_url("archon", "Death Knight", "Blood", "", "raid", "_")
        assert url_hyph == url_under
        assert "death-knight" in url_hyph


# ---------------------------------------------------------------------------
# _parse_archon_page — normal slots
# ---------------------------------------------------------------------------


class TestParseArchonPageNormalSlots:
    def test_single_slot_single_row(self):
        page = _make_page([_make_table("Head", [_make_row(237846, 59.6)])])
        slots, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 71309)
        assert len(slots) == 1
        assert slots[0].slot == "head"
        assert slots[0].blizzard_item_id == 237846

    def test_multiple_rows_preserve_order(self):
        """Row order determines guide_order via insert_bis_items(); first = guide_order 1."""
        page = _make_page([
            _make_table("Head", [
                _make_row(111, 60.0),
                _make_row(222, 30.0),
                _make_row(333, 10.0),
            ])
        ])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert [s.blizzard_item_id for s in slots] == [111, 222, 333]

    def test_all_standard_archon_labels_resolve(self):
        labels = ["Head", "Neck", "Shoulders", "Back", "Chest",
                  "Wrist", "Gloves", "Belt", "Legs", "Feet",
                  "Main-Hand", "Off-Hand"]
        tables = [_make_table(label, [_make_row(100 + i, 50.0)]) for i, label in enumerate(labels)]
        page = _make_page(tables)
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        expected_slot_keys = {
            "head", "neck", "shoulder", "back", "chest",
            "wrist", "hands", "waist", "legs", "feet",
            "main_hand", "off_hand",
        }
        assert {s.slot for s in slots} == expected_slot_keys

    def test_label_lookup_is_case_insensitive(self):
        """Archon sends title-case; slot_map has lowercase keys."""
        page = _make_page([_make_table("Head", [_make_row(999, 55.0)])])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert slots[0].slot == "head"

    def test_zero_item_id_skipped(self):
        page = _make_page([_make_table("Head", [_make_row(0, 50.0)])])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert slots == []

    def test_row_missing_item_id_skipped(self):
        page = _make_page([_make_table("Head", [{"item": "no id here", "popularity": "50%"}])])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert slots == []

    def test_header_jsx_tags_stripped(self):
        """Real Archon headers are JSX: <ImageIcon ...>Head</ImageIcon> — tags must be stripped."""
        jsx_header = "<ImageIcon lazyload='1' src='inv_helmet_02.jpg'>Head</ImageIcon>"
        table = {
            "columns": {"item": {"header": jsx_header}},
            "data": [_make_row(237846, 59.6)],
        }
        slots, _ = _parse_archon_page(_make_page([table]), _ARCHON_SLOT_MAP, 1000)
        assert len(slots) == 1
        assert slots[0].slot == "head"

    def test_trinket_header_jsx_stripped_and_expanded(self):
        jsx_header = "<ImageIcon lazyload='1' src='inv_jewelry_trinketpvp_02.jpg'>Trinket</ImageIcon>"
        table = {
            "columns": {"item": {"header": jsx_header}},
            "data": [_make_row(500, 45.0)],
        }
        slots, _ = _parse_archon_page(_make_page([table]), _ARCHON_SLOT_MAP, 1000)
        slot_keys = {s.slot for s in slots}
        assert "trinket_1" in slot_keys
        assert "trinket_2" in slot_keys

    def test_unknown_slot_label_skipped(self):
        page = _make_page([_make_table("Mystery Slot", [_make_row(999, 50.0)])])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert slots == []

    def test_no_gear_tables_section_returns_empty(self):
        page = {
            "totalParses": 1000,
            "sections": [{"navigationId": "embellishments", "props": {}}],
        }
        slots, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert slots == []
        assert pop == []

    def test_empty_sections_returns_empty(self):
        page = {"totalParses": 1000, "sections": []}
        slots, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert slots == []


# ---------------------------------------------------------------------------
# _parse_archon_page — paired slots (trinket / rings)
# ---------------------------------------------------------------------------


class TestParseArchonPagePairedSlots:
    def test_trinket_expands_to_both_slots(self):
        page = _make_page([_make_table("Trinket", [_make_row(500, 45.0)])])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        slot_keys = {s.slot for s in slots}
        assert "trinket_1" in slot_keys
        assert "trinket_2" in slot_keys

    def test_trinket_same_item_id_for_both_paired_slots(self):
        page = _make_page([_make_table("Trinket", [_make_row(500, 45.0)])])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert all(s.blizzard_item_id == 500 for s in slots)

    def test_rings_expands_to_both_slots(self):
        page = _make_page([_make_table("Rings", [_make_row(600, 40.0)])])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        slot_keys = {s.slot for s in slots}
        assert "ring_1" in slot_keys
        assert "ring_2" in slot_keys

    def test_multiple_trinket_rows_each_expand_to_pair(self):
        page = _make_page([
            _make_table("Trinket", [
                _make_row(501, 45.0),
                _make_row(502, 30.0),
            ])
        ])
        slots, _ = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        t1_ids = [s.blizzard_item_id for s in slots if s.slot == "trinket_1"]
        t2_ids = [s.blizzard_item_id for s in slots if s.slot == "trinket_2"]
        assert t1_ids == [501, 502]
        assert t2_ids == [501, 502]


# ---------------------------------------------------------------------------
# _parse_archon_page — popularity output
# ---------------------------------------------------------------------------


class TestParseArchonPagePopularity:
    def test_popularity_count_derived_from_pct_and_total(self):
        page = _make_page([_make_table("Head", [_make_row(237846, 59.6)])], total_parses=71309)
        _, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 71309)
        assert len(pop) == 1
        assert pop[0].blizzard_item_id == 237846
        assert pop[0].slot == "head"
        assert pop[0].total == 71309
        assert pop[0].count == round(0.596 * 71309)

    def test_popularity_zero_pct_gives_zero_count(self):
        page = _make_page([_make_table("Head", [_make_row(111, 0.0)])])
        _, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert pop[0].count == 0

    def test_popularity_row_missing_pct_jsx_gives_zero(self):
        row = {"item": "<ItemIcon id={999}>Name</ItemIcon>", "popularity": "no percent here"}
        page = _make_page([_make_table("Head", [row])])
        _, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 1000)
        assert pop[0].count == 0
        assert pop[0].total == 1000

    def test_trinket_popularity_emitted_for_both_paired_slots(self):
        page = _make_page([_make_table("Trinket", [_make_row(500, 45.0)])], total_parses=10000)
        _, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 10000)
        slots = {p.slot for p in pop}
        assert "trinket_1" in slots
        assert "trinket_2" in slots
        for p in pop:
            assert p.count == round(0.45 * 10000)
            assert p.total == 10000

    def test_multiple_rows_all_have_same_total(self):
        page = _make_page([
            _make_table("Head", [_make_row(111, 60.0), _make_row(222, 30.0)])
        ], total_parses=5000)
        _, pop = _parse_archon_page(page, _ARCHON_SLOT_MAP, 5000)
        assert all(p.total == 5000 for p in pop)


# ---------------------------------------------------------------------------
# _parse_archon_page — NEXT_DATA extraction helper
# ---------------------------------------------------------------------------


class TestExtractNextDataStructure:
    def test_parse_embedded_json_page(self):
        """round-trip: dump a page dict into a NEXT_DATA HTML block, re-parse it."""
        page = _make_page([_make_table("Head", [_make_row(237846, 59.6)])])
        next_data = {"props": {"pageProps": {"page": page}}}
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

        import re
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        assert m is not None
        extracted = json.loads(m.group(1))
        extracted_page = extracted["props"]["pageProps"]["page"]
        slots, _ = _parse_archon_page(extracted_page, _ARCHON_SLOT_MAP, page["totalParses"])
        assert slots[0].slot == "head"
        assert slots[0].blizzard_item_id == 237846
