"""
Unit tests for pure functions in the identity engine and migration module.

No database required — these tests cover normalize_name,
extract_discord_hints_from_note, fuzzy_match_score, and get_role_category.
"""

import pytest

from sv_common.guild_sync.identity_engine import (
    normalize_name,
    extract_discord_hints_from_note,
    fuzzy_match_score,
)
from sv_common.guild_sync.migration import get_role_category


class TestNormalizeName:
    def test_basic_lowercase(self):
        assert normalize_name("Trogmoon") == "trogmoon"

    def test_strips_whitespace(self):
        assert normalize_name("  Trog  ") == "trog"

    def test_accent_stripping_tilde_n(self):
        assert normalize_name("Zatañña") == "zatanna"

    def test_accent_stripping_various(self):
        result = normalize_name("Élodie")
        assert result == "elodie"

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_none_returns_empty(self):
        assert normalize_name(None) == ""

    def test_already_lowercase(self):
        assert normalize_name("shodoom") == "shodoom"

    def test_mixed_case(self):
        assert normalize_name("SkateFarm") == "skatefarm"


class TestExtractDiscordHints:
    def test_discord_colon_pattern(self):
        hints = extract_discord_hints_from_note("Discord: Trog")
        assert any("trog" in h.lower() for h in hints)

    def test_dc_colon_pattern(self):
        hints = extract_discord_hints_from_note("DC: Trog")
        assert any("trog" in h.lower() for h in hints)

    def test_disc_colon_pattern(self):
        hints = extract_discord_hints_from_note("Disc: Rocket")
        assert any("rocket" in h.lower() for h in hints)

    def test_at_mention_pattern(self):
        hints = extract_discord_hints_from_note("@Shodoom")
        assert any("shodoom" in h.lower() for h in hints)

    def test_alt_of_pattern(self):
        hints = extract_discord_hints_from_note("alt of Trogmoon")
        assert any("trogmoon" in h.lower() for h in hints)

    def test_main_colon_pattern(self):
        hints = extract_discord_hints_from_note("Main: Trogmoon")
        assert any("trogmoon" in h.lower() for h in hints)

    def test_empty_note_returns_empty_list(self):
        assert extract_discord_hints_from_note("") == []

    def test_none_returns_empty_list(self):
        assert extract_discord_hints_from_note(None) == []

    def test_whitespace_only_returns_empty_list(self):
        assert extract_discord_hints_from_note("   ") == []

    def test_plain_note_no_patterns(self):
        assert extract_discord_hints_from_note("Just a regular note about raiding") == []

    def test_trailing_punctuation_stripped(self):
        hints = extract_discord_hints_from_note("Discord: Trog.")
        assert any("trog" in h.lower() for h in hints)
        # The trailing dot should be stripped
        assert not any(h.endswith(".") for h in hints)


class TestFuzzyMatchScore:
    def test_exact_match_scores_one(self):
        assert fuzzy_match_score("Shodoom", "Shodoom") == 1.0

    def test_case_insensitive_exact(self):
        assert fuzzy_match_score("Shodoom", "shodoom") == 1.0

    def test_prefix_containment(self):
        # "Trog" is contained in "Trogmoon"
        score = fuzzy_match_score("Trog", "Trogmoon")
        # Score is shorter/longer = 4/8 = 0.5
        assert abs(score - 0.5) < 0.01

    def test_suffix_containment(self):
        score = fuzzy_match_score("moon", "Trogmoon")
        # 4/8 = 0.5
        assert abs(score - 0.5) < 0.01

    def test_completely_different_names(self):
        score = fuzzy_match_score("Trogmoon", "Skatefarm")
        assert score < 0.4

    def test_very_similar_names(self):
        score = fuzzy_match_score("Trogmoon", "Trogmun")
        assert score > 0.7

    def test_empty_first_arg(self):
        assert fuzzy_match_score("", "test") == 0.0

    def test_empty_second_arg(self):
        assert fuzzy_match_score("test", "") == 0.0

    def test_both_empty(self):
        assert fuzzy_match_score("", "") == 0.0

    def test_score_between_zero_and_one(self):
        score = fuzzy_match_score("Alpha", "Beta")
        assert 0.0 <= score <= 1.0


class TestGetRoleCategory:
    def test_balance_druid_is_ranged(self):
        assert get_role_category("Druid", "Balance", "") == "Ranged"

    def test_feral_druid_is_melee(self):
        assert get_role_category("Druid", "Feral", "") == "Melee"

    def test_guardian_druid_is_tank(self):
        assert get_role_category("Druid", "Guardian", "") == "Tank"

    def test_restoration_druid_is_healer(self):
        assert get_role_category("Druid", "Restoration", "") == "Healer"

    def test_frost_death_knight_is_melee(self):
        assert get_role_category("Death Knight", "Frost", "") == "Melee"

    def test_frost_mage_is_ranged(self):
        assert get_role_category("Mage", "Frost", "") == "Ranged"

    def test_holy_paladin_is_healer(self):
        assert get_role_category("Paladin", "Holy", "") == "Healer"

    def test_holy_priest_is_healer(self):
        assert get_role_category("Priest", "Holy", "") == "Healer"

    def test_protection_paladin_is_tank(self):
        assert get_role_category("Paladin", "Protection", "") == "Tank"

    def test_protection_warrior_is_tank(self):
        assert get_role_category("Warrior", "Protection", "") == "Tank"

    def test_arms_warrior_is_melee(self):
        assert get_role_category("Warrior", "Arms", "") == "Melee"

    def test_beast_mastery_hunter_is_ranged(self):
        assert get_role_category("Hunter", "Beast Mastery", "") == "Ranged"

    def test_survival_hunter_is_melee(self):
        assert get_role_category("Hunter", "Survival", "") == "Melee"

    def test_explicit_role_overrides_spec_lookup(self):
        assert get_role_category("Unknown", "Unknown", "Tank") == "Tank"
        assert get_role_category("Druid", "Balance", "Healer") == "Healer"

    def test_unknown_spec_defaults_to_ranged(self):
        # Default fallback is Ranged
        result = get_role_category("SomeFutureClass", "NewSpec", "")
        assert result == "Ranged"

    def test_devastation_evoker_is_ranged(self):
        assert get_role_category("Evoker", "Devastation", "") == "Ranged"

    def test_augmentation_evoker_is_ranged(self):
        assert get_role_category("Evoker", "Augmentation", "") == "Ranged"

    def test_preservation_evoker_is_healer(self):
        assert get_role_category("Evoker", "Preservation", "") == "Healer"
