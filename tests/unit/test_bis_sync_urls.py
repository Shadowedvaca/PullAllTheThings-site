"""Unit tests for BIS sync URL generation helpers.

Covers _iv_base_url, _iv_bis_role, _build_url, and _categorize_iv_area.
All URLs that are "known good" were manually verified against the live sites.
"""

import pytest

from sv_common.guild_sync.bis_sync import (
    _WOWHEAD_SLOT_MAP,
    _build_url,
    _categorize_iv_area,
    _iv_base_url,
    _iv_bis_role,
    _parse_ugg_ssr,
    _parse_ugg_popularity,
    _slug,
    _slug_to_pascal,
    _ugg_items_to_popularity,
    _ugg_url_to_section,
    _ugg_url_to_spec_key,
)


# ---------------------------------------------------------------------------
# _iv_bis_role
# ---------------------------------------------------------------------------


class TestIvBisRole:
    def test_tank(self):
        assert _iv_bis_role("Tank") == "tank"

    def test_tank_lowercase(self):
        assert _iv_bis_role("tank") == "tank"

    def test_healer(self):
        assert _iv_bis_role("Healer") == "healer"

    def test_healer_mixed_case(self):
        assert _iv_bis_role("Restoration Healer") == "healer"

    def test_melee_dps(self):
        assert _iv_bis_role("Melee DPS") == "dps"

    def test_ranged_dps(self):
        assert _iv_bis_role("Ranged DPS") == "dps"

    def test_dps_bare(self):
        assert _iv_bis_role("DPS") == "dps"

    def test_none_defaults_to_dps(self):
        assert _iv_bis_role(None) == "dps"

    def test_empty_defaults_to_dps(self):
        assert _iv_bis_role("") == "dps"


# ---------------------------------------------------------------------------
# _iv_base_url — verified against live site
# ---------------------------------------------------------------------------


class TestIvBaseUrl:
    def test_blood_dk_tank(self):
        """User-confirmed correct URL for Blood DK."""
        assert _iv_base_url("Death Knight", "Blood", "Tank") == (
            "https://www.icy-veins.com/wow/blood-death-knight-pve-tank-gear-best-in-slot"
        )

    def test_brewmaster_monk_tank(self):
        """Verified live: 200 OK, correct spec page."""
        assert _iv_base_url("Monk", "Brewmaster", "Tank") == (
            "https://www.icy-veins.com/wow/brewmaster-monk-pve-tank-gear-best-in-slot"
        )

    def test_balance_druid_ranged_dps(self):
        """Verified live: 200 OK, correct spec page."""
        assert _iv_base_url("Druid", "Balance", "Ranged DPS") == (
            "https://www.icy-veins.com/wow/balance-druid-pve-dps-gear-best-in-slot"
        )

    def test_devourer_demon_hunter_ranged_dps(self):
        """Verified live: 200 OK, Devourer DH page exists."""
        assert _iv_base_url("Demon Hunter", "Devourer", "Ranged DPS") == (
            "https://www.icy-veins.com/wow/devourer-demon-hunter-pve-dps-gear-best-in-slot"
        )

    def test_holy_paladin_healer(self):
        assert _iv_base_url("Paladin", "Holy", "Healer") == (
            "https://www.icy-veins.com/wow/holy-paladin-pve-healer-gear-best-in-slot"
        )

    def test_restoration_shaman_healer(self):
        assert _iv_base_url("Shaman", "Restoration", "Healer") == (
            "https://www.icy-veins.com/wow/restoration-shaman-pve-healer-gear-best-in-slot"
        )

    def test_fury_warrior_melee_dps(self):
        assert _iv_base_url("Warrior", "Fury", "Melee DPS") == (
            "https://www.icy-veins.com/wow/fury-warrior-pve-dps-gear-best-in-slot"
        )

    def test_multi_word_class_uses_hyphens(self):
        """Demon Hunter → demon-hunter, Death Knight → death-knight, etc."""
        url = _iv_base_url("Death Knight", "Frost", "Melee DPS")
        assert "death-knight" in url
        assert "frost" in url

    def test_multi_word_spec_uses_hyphens(self):
        url = _iv_base_url("Shaman", "Enhancement", "Melee DPS")
        assert "enhancement-shaman" in url


# ---------------------------------------------------------------------------
# _build_url — u.gg (origin='ugg')
# ---------------------------------------------------------------------------


class TestBuildUrlArchon:
    def test_archon_overall_returns_none(self):
        # u.gg has no overall page
        url = _build_url("ugg", "Death Knight", "Blood", "", "overall", "_")
        assert url is None

    def test_archon_raid(self):
        url = _build_url("ugg", "Death Knight", "Blood", "", "raid", "_")
        assert url == "https://u.gg/wow/blood/death_knight/gear/raid"

    def test_archon_mythic_plus(self):
        url = _build_url("ugg", "Death Knight", "Blood", "", "mythic_plus", "_")
        assert url == "https://u.gg/wow/blood/death_knight/gear"

    def test_archon_slug_separator_applied(self):
        url = _build_url("ugg", "Demon Hunter", "Havoc", "", "mythic_plus", "_")
        assert "demon_hunter" in url
        assert "havoc" in url


# ---------------------------------------------------------------------------
# _build_url — Wowhead
# ---------------------------------------------------------------------------


class TestBuildUrlWowhead:
    def test_wowhead_blood_dk(self):
        url = _build_url("wowhead", "Death Knight", "Blood", "san-layn", "overall", "-")
        assert url == "https://www.wowhead.com/guide/classes/death-knight/blood/bis-gear#bis-gear"

    def test_wowhead_all_content_types_give_same_url(self):
        """Wowhead has one combined BIS page — no raid/M+ URL split."""
        raid = _build_url("wowhead", "Death Knight", "Blood", "san-layn", "raid", "-")
        mplus = _build_url("wowhead", "Death Knight", "Blood", "san-layn", "mythic_plus", "-")
        overall = _build_url("wowhead", "Death Knight", "Blood", "san-layn", "overall", "-")
        assert raid == mplus == overall

    def test_wowhead_multi_word_class(self):
        url = _build_url("wowhead", "Demon Hunter", "Havoc", "aldrachi-reaver", "overall", "-")
        assert url == "https://www.wowhead.com/guide/classes/demon-hunter/havoc/bis-gear#bis-gear"

    def test_wowhead_always_uses_hyphens_regardless_of_separator(self):
        """Wowhead ignores slug_sep; always uses hyphens."""
        url_hyphen = _build_url("wowhead", "Death Knight", "Blood", "san-layn", "overall", "-")
        url_under = _build_url("wowhead", "Death Knight", "Blood", "san-layn", "overall", "_")
        assert url_hyphen == url_under
        assert "death-knight" in url_hyphen


# ---------------------------------------------------------------------------
# _build_url — Icy Veins (dead path — IV URLs come from _iv_base_url now)
# ---------------------------------------------------------------------------


class TestBuildUrlIcyVeins:
    def test_iv_returns_none(self):
        """_build_url for icy_veins is dead code — should return None."""
        assert _build_url("icy_veins", "Death Knight", "Blood", "san-layn", "overall", "-") is None


# ---------------------------------------------------------------------------
# _slug_to_pascal
# ---------------------------------------------------------------------------


class TestSlugToPascal:
    def test_single_word(self):
        assert _slug_to_pascal("warrior") == "Warrior"

    def test_hyphen_separated(self):
        assert _slug_to_pascal("death-knight") == "DeathKnight"

    def test_underscore_separated(self):
        assert _slug_to_pascal("demon_hunter") == "DemonHunter"

    def test_mixed_separators(self):
        assert _slug_to_pascal("death_knight") == "DeathKnight"


# ---------------------------------------------------------------------------
# _categorize_iv_area (kept for reference — no longer used in discovery)
# ---------------------------------------------------------------------------


class TestCategorizeIvArea:
    def test_mythic_in_label(self):
        ct, ht = _categorize_iv_area("Mythic+", ["San'layn", "Deathbringer"])
        assert ct == "mythic_plus"
        assert ht is None

    def test_raid_in_label(self):
        ct, ht = _categorize_iv_area("Raiding", ["San'layn", "Deathbringer"])
        assert ct == "raid"
        assert ht is None

    def test_ht_name_in_label(self):
        ct, ht = _categorize_iv_area("San'layn Overall", ["San'layn", "Deathbringer"])
        assert ct == "overall"
        assert ht == "San'layn"

    def test_no_match_defaults_to_overall_no_ht(self):
        ct, ht = _categorize_iv_area("General BiS", ["San'layn", "Deathbringer"])
        assert ct == "overall"
        assert ht is None


# ---------------------------------------------------------------------------
# _ugg_url_to_spec_key
# ---------------------------------------------------------------------------


class TestUggUrlToSpecKey:
    def test_blood_dk(self):
        url = "https://u.gg/wow/blood/death_knight/gear?hero=san-layn&role=raid"
        assert _ugg_url_to_spec_key(url) == "DeathKnight-Blood"

    def test_frost_mage(self):
        url = "https://u.gg/wow/frost/mage/gear?hero=spellslinger"
        assert _ugg_url_to_spec_key(url) == "Mage-Frost"

    def test_havoc_dh(self):
        url = "https://u.gg/wow/havoc/demon_hunter/gear"
        assert _ugg_url_to_spec_key(url) == "DemonHunter-Havoc"

    def test_non_ugg_url_returns_empty(self):
        assert _ugg_url_to_spec_key("https://www.wowhead.com/guide/classes/warrior/arms/bis-gear") == ""

    def test_empty_url_returns_empty(self):
        assert _ugg_url_to_spec_key("") == ""


# ---------------------------------------------------------------------------
# _ugg_url_to_section
# ---------------------------------------------------------------------------


class TestUggUrlToSection:
    def test_raid_path(self):
        assert _ugg_url_to_section("https://u.gg/wow/blood/death_knight/gear/raid") == "raid"

    def test_base_gear_url_is_mythic(self):
        assert _ugg_url_to_section("https://u.gg/wow/frost/mage/gear") == "mythic"

    def test_empty_url_defaults_mythic(self):
        assert _ugg_url_to_section("") == "mythic"


# ---------------------------------------------------------------------------
# _parse_ugg_ssr — section[all][spec_key][items_table] routing
# ---------------------------------------------------------------------------


def _make_items_table_entry(item_id: int) -> dict:
    """Build a minimal items_table slot entry."""
    return {"items": [{"item_id": item_id, "perc": 0.5}]}


def _make_ssr_with_section(section: str, spec_key: str, items_table: dict, affixes_weapon_id: int = 999999) -> dict:
    """Build a minimal SSR blob with a section[all][spec_key][items_table] path."""
    return {
        "https://stats2.u.gg/wow/builds/v29/all/Fake/Fake.json": {
            "data": {
                section: {
                    "all": {
                        spec_key: {
                            "items_table": {"items": items_table},
                        }
                    }
                },
                "affixes": {
                    "fortified": {
                        "99999": {
                            spec_key: {
                                "items": {
                                    "weapon1": {"dps_item": {"item_id": affixes_weapon_id}},
                                }
                            }
                        }
                    }
                },
            }
        }
    }


class TestParseArchonSsr:
    def test_raid_url_uses_raid_section(self):
        ssr = _make_ssr_with_section(
            "raid", "DeathKnight-Blood",
            {"weapon1": _make_items_table_entry(237846)},
            affixes_weapon_id=193716,
        )
        url = "https://u.gg/wow/blood/death_knight/gear/raid"
        slots = _parse_ugg_ssr(ssr, url)
        ids = {s.slot: s.blizzard_item_id for s in slots}
        assert ids.get("main_hand") == 237846, "Should use raid[all] items_table, not affixes"

    def test_mythic_url_uses_mythic_section(self):
        ssr = _make_ssr_with_section(
            "mythic", "Mage-Frost",
            {"head": _make_items_table_entry(249970)},
        )
        url = "https://u.gg/wow/frost/mage/gear"
        slots = _parse_ugg_ssr(ssr, url)
        ids = {s.slot: s.blizzard_item_id for s in slots}
        assert ids.get("head") == 249970

    def test_base_gear_url_uses_mythic_section(self):
        ssr = _make_ssr_with_section(
            "mythic", "Mage-Arcane",
            {"head": _make_items_table_entry(249970)},
        )
        url = "https://u.gg/wow/arcane/mage/gear"
        slots = _parse_ugg_ssr(ssr, url)
        ids = {s.slot: s.blizzard_item_id for s in slots}
        assert ids.get("head") == 249970

    def test_section_missing_falls_back_to_affixes(self):
        """If the section or spec key is absent, fall back to affixes."""
        ssr = _make_ssr_with_section(
            "single_target", "Warrior-Arms",  # wrong section for a raid URL
            {"head": _make_items_table_entry(249970)},
            affixes_weapon_id=237846,
        )
        url = "https://u.gg/wow/arms/warrior/gear?role=raid"  # looks for "raid" section
        slots = _parse_ugg_ssr(ssr, url)
        ids = {s.slot: s.blizzard_item_id for s in slots}
        assert ids.get("main_hand") == 237846, "Should fall back to affixes"

    def test_zero_item_id_skipped(self):
        ssr = _make_ssr_with_section(
            "raid", "DeathKnight-Blood",
            {"weapon2": _make_items_table_entry(0)},
        )
        url = "https://u.gg/wow/blood/death_knight/gear?role=raid"
        slots = _parse_ugg_ssr(ssr, url)
        assert not any(s.slot == "off_hand" for s in slots)


# ---------------------------------------------------------------------------
# _ugg_items_to_popularity
# ---------------------------------------------------------------------------


class TestUggItemsToPopularity:
    def _slot_data(self, items: list) -> dict:
        return {"items": items}

    def test_extracts_count_and_total(self):
        items_by_slot = {
            "head": {"items": [
                {"item_id": 111, "perc": 0.6, "count": 600, "total": 1000},
                {"item_id": 222, "perc": 0.4, "count": 400, "total": 1000},
            ]}
        }
        result = _ugg_items_to_popularity(items_by_slot)
        by_id = {r.blizzard_item_id: r for r in result}
        assert by_id[111].count == 600
        assert by_id[111].total == 1000
        assert by_id[222].count == 400
        assert by_id[222].total == 1000

    def test_derives_total_from_first_item(self):
        items_by_slot = {
            "head": {"items": [
                {"item_id": 111, "perc": 0.5, "count": 500, "total": 1000},
            ]}
        }
        result = _ugg_items_to_popularity(items_by_slot)
        assert result[0].total == 1000

    def test_derives_count_from_perc_when_missing(self):
        items_by_slot = {
            "head": {"items": [
                {"item_id": 111, "perc": 0.5, "total": 1000},  # no count
            ]}
        }
        result = _ugg_items_to_popularity(items_by_slot)
        assert result[0].count == 500
        assert result[0].total == 1000

    def test_skips_zero_item_id(self):
        items_by_slot = {
            "head": {"items": [
                {"item_id": 0, "count": 100, "total": 1000},
            ]}
        }
        result = _ugg_items_to_popularity(items_by_slot)
        assert result == []

    def test_skips_unknown_slot(self):
        items_by_slot = {
            "mystery_slot": {"items": [
                {"item_id": 111, "count": 100, "total": 1000},
            ]}
        }
        result = _ugg_items_to_popularity(items_by_slot)
        assert result == []

    def test_normalises_slot_names(self):
        items_by_slot = {
            "weapon1": {"items": [{"item_id": 123, "count": 100, "total": 500}]},
            "weapon2": {"items": [{"item_id": 456, "count": 50, "total": 500}]},
        }
        result = _ugg_items_to_popularity(items_by_slot)
        slots = {r.slot for r in result}
        # weapon1 emits both typed slots so popularity shows for any weapon build mode
        assert "main_hand_2h" in slots
        assert "main_hand_1h" in slots
        assert "off_hand" in slots

    def test_weapon1_emits_both_typed_slots(self):
        items_by_slot = {
            "weapon1": {"items": [{"item_id": 999, "count": 200, "total": 1000}]},
        }
        result = _ugg_items_to_popularity(items_by_slot)
        by_slot = {r.slot: r for r in result if r.blizzard_item_id == 999}
        assert "main_hand_2h" in by_slot
        assert "main_hand_1h" in by_slot
        assert by_slot["main_hand_2h"].count == 200
        assert by_slot["main_hand_1h"].count == 200

    def test_slot_total_at_slot_level_takes_priority(self):
        items_by_slot = {
            "head": {
                "total": 2000,
                "items": [{"item_id": 111, "count": 600, "total": 999}],  # per-item total ignored
            }
        }
        result = _ugg_items_to_popularity(items_by_slot)
        assert result[0].total == 2000


# ---------------------------------------------------------------------------
# _parse_ugg_popularity — SSR HTML parsing
# ---------------------------------------------------------------------------


def _make_ssr_html_with_popularity(section: str, spec_key: str, items_table: dict) -> str:
    """Build minimal u.gg-like SSR HTML for popularity testing."""
    import json
    data = {
        "https://stats2.u.gg/wow/builds/v29/all/Fake/Fake.json": {
            "data": {
                section: {
                    "all": {
                        spec_key: {
                            "items_table": {"items": items_table},
                        }
                    }
                }
            }
        }
    }
    return f"<html><script>window.__SSR_DATA__ = {json.dumps(data)}</script></html>"


class TestParseUggPopularity:
    def test_extracts_all_items_from_slot(self):
        html = _make_ssr_html_with_popularity(
            "raid", "DeathKnight-Blood",
            {
                "head": {"items": [
                    {"item_id": 111, "count": 600, "total": 1000},
                    {"item_id": 222, "count": 400, "total": 1000},
                ]}
            }
        )
        result = _parse_ugg_popularity(html, "https://u.gg/wow/blood/death_knight/gear/raid")
        by_id = {r.blizzard_item_id: r for r in result}
        assert 111 in by_id
        assert 222 in by_id
        assert by_id[111].slot == "head"
        assert by_id[111].count == 600
        assert by_id[111].total == 1000

    def test_mythic_section_used_for_base_url(self):
        html = _make_ssr_html_with_popularity(
            "mythic", "Mage-Frost",
            {"head": {"items": [{"item_id": 999, "count": 100, "total": 200}]}}
        )
        result = _parse_ugg_popularity(html, "https://u.gg/wow/frost/mage/gear")
        assert any(r.blizzard_item_id == 999 for r in result)

    def test_wrong_section_returns_empty(self):
        html = _make_ssr_html_with_popularity(
            "single_target", "Warrior-Arms",
            {"head": {"items": [{"item_id": 111, "count": 100, "total": 500}]}}
        )
        result = _parse_ugg_popularity(html, "https://u.gg/wow/arms/warrior/gear/raid")
        assert result == []

    def test_no_ssr_marker_returns_empty(self):
        result = _parse_ugg_popularity("<html>no SSR data here</html>", "https://u.gg/wow/blood/death_knight/gear/raid")
        assert result == []


# ---------------------------------------------------------------------------
# _WOWHEAD_SLOT_MAP — invtype coverage
# ---------------------------------------------------------------------------


class TestWowheadSlotMap:
    """Spot-checks that critical invtype IDs map to the expected slot names."""

    def test_ranged_weapon_maps_to_main_hand(self):
        # INVTYPE_RANGED (15) = bows, guns, crossbows — Hunter ranged slot
        assert _WOWHEAD_SLOT_MAP[15] == "main_hand"

    def test_2h_weapon_maps_to_main_hand(self):
        assert _WOWHEAD_SLOT_MAP[17] == "main_hand"

    def test_1h_weapon_maps_to_main_hand(self):
        assert _WOWHEAD_SLOT_MAP[13] == "main_hand"

    def test_shield_maps_to_off_hand(self):
        assert _WOWHEAD_SLOT_MAP[14] == "off_hand"

    def test_cloak_maps_to_back(self):
        assert _WOWHEAD_SLOT_MAP[16] == "back"
