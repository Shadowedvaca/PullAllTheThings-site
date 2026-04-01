"""Unit tests for sv_common.guide_links — pure URL builder."""

import pytest

from sv_common.guide_links import build_link_for_site


# ── Seed row fixtures ────────────────────────────────────────────────────────

WOWHEAD = dict(
    url_template="https://www.wowhead.com/guide/classes/{class}/{spec}/overview-pve-{role}",
    role_dps_slug="dps",
    role_tank_slug="tank",
    role_healer_slug="healer",
)

ICY_VEINS = dict(
    url_template="https://www.icy-veins.com/wow/{spec}-{class}-pve-{role}-guide",
    role_dps_slug="dps",
    role_tank_slug="tank",
    role_healer_slug="healing",
)

UGG = dict(
    url_template="https://u.gg/wow/{spec}/{class}/talents",
    role_dps_slug="dps",
    role_tank_slug="tank",
    role_healer_slug="healer",
    slug_separator="_",
)


def _build(site: dict, class_name: str, spec_name: str, role_name: str) -> str:
    return build_link_for_site(
        url_template     = site["url_template"],
        class_name       = class_name,
        spec_name        = spec_name,
        role_name        = role_name,
        role_dps_slug    = site["role_dps_slug"],
        role_tank_slug   = site["role_tank_slug"],
        role_healer_slug = site["role_healer_slug"],
        slug_separator   = site.get("slug_separator", "-"),
    )


# ── Tests ────────────────────────────────────────────────────────────────────

def test_balance_druid_wowhead():
    url = _build(WOWHEAD, "Druid", "Balance", "Ranged DPS")
    assert url == "https://www.wowhead.com/guide/classes/druid/balance/overview-pve-dps"


def test_balance_druid_icyveins():
    url = _build(ICY_VEINS, "Druid", "Balance", "Ranged DPS")
    assert url == "https://www.icy-veins.com/wow/balance-druid-pve-dps-guide"


def test_balance_druid_ugg():
    url = _build(UGG, "Druid", "Balance", "Ranged DPS")
    assert url == "https://u.gg/wow/balance/druid/talents"


def test_holy_paladin_wowhead_healer():
    url = _build(WOWHEAD, "Paladin", "Holy", "Healer")
    assert "healer" in url
    assert url == "https://www.wowhead.com/guide/classes/paladin/holy/overview-pve-healer"


def test_holy_paladin_icyveins_healing():
    url = _build(ICY_VEINS, "Paladin", "Holy", "Healer")
    assert "healing" in url
    assert url == "https://www.icy-veins.com/wow/holy-paladin-pve-healing-guide"


def test_blood_dk_tank():
    wowhead_url = _build(WOWHEAD, "Death Knight", "Blood", "Tank")
    assert "tank" in wowhead_url

    iv_url = _build(ICY_VEINS, "Death Knight", "Blood", "Tank")
    assert "tank" in iv_url


def test_augmentation_evoker_support():
    # "Support" role has no "tank" or "heal" → falls through to dps_slug
    url = _build(WOWHEAD, "Evoker", "Augmentation", "Support")
    assert "dps" in url


def test_death_knight_class_slug():
    url = _build(WOWHEAD, "Death Knight", "Frost", "Melee DPS")
    assert "death-knight" in url


def test_demon_hunter_class_slug():
    url = _build(ICY_VEINS, "Demon Hunter", "Havoc", "Melee DPS")
    assert "demon-hunter" in url


def test_ugg_no_role_placeholder():
    # u.gg template has no {role} — role slug should be absent from the URL
    url = _build(UGG, "Warrior", "Arms", "Melee DPS")
    assert "dps" not in url
    assert "tank" not in url
    assert url == "https://u.gg/wow/arms/warrior/talents"


def test_ugg_beast_mastery_uses_underscore():
    # Regression: u.gg uses beast_mastery (underscore), not beast-mastery (hyphen)
    url = _build(UGG, "Hunter", "Beast Mastery", "Ranged DPS")
    assert url == "https://u.gg/wow/beast_mastery/hunter/talents"


def test_ugg_death_knight_class_uses_underscore():
    url = _build(UGG, "Death Knight", "Blood", "Tank")
    assert url == "https://u.gg/wow/blood/death_knight/talents"


def test_ugg_demon_hunter_class_uses_underscore():
    url = _build(UGG, "Demon Hunter", "Havoc", "Melee DPS")
    assert url == "https://u.gg/wow/havoc/demon_hunter/talents"


def test_wowhead_beast_mastery_uses_hyphen():
    # Non-u.gg sites still use hyphens (default separator)
    url = _build(WOWHEAD, "Hunter", "Beast Mastery", "Ranged DPS")
    assert url == "https://www.wowhead.com/guide/classes/hunter/beast-mastery/overview-pve-dps"


def test_wowhead_death_knight_uses_hyphen():
    url = _build(WOWHEAD, "Death Knight", "Blood", "Tank")
    assert url == "https://www.wowhead.com/guide/classes/death-knight/blood/overview-pve-tank"
