"""Unit tests for identity engine link attribution (Phase 3.0A)."""

import pytest

from sv_common.guild_sync.identity_engine import (
    _attribution_for_match,
    _find_discord_for_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _du(username, display_name=None, pid=None):
    return {
        "id": 1,
        "discord_id": "123",
        "username": username,
        "display_name": display_name,
        "player_id": pid,
    }


# ---------------------------------------------------------------------------
# _find_discord_for_key returns correct match_type
# ---------------------------------------------------------------------------

class TestFindDiscordForKey:
    def test_exact_username_match(self):
        discord_list = [_du("rocket")]
        result, match_type = _find_discord_for_key("rocket", discord_list)
        assert result is not None
        assert match_type == "exact_username"

    def test_exact_username_case_insensitive(self):
        discord_list = [_du("Rocket")]
        result, match_type = _find_discord_for_key("rocket", discord_list)
        assert result is not None
        assert match_type == "exact_username"

    def test_exact_display_name(self):
        discord_list = [_du("someuser", display_name="Rocket")]
        result, match_type = _find_discord_for_key("rocket", discord_list)
        assert result is not None
        assert match_type == "exact_display"

    def test_word_in_display_name(self):
        discord_list = [_du("someuser", display_name="Trog/Moon")]
        result, match_type = _find_discord_for_key("trog", discord_list)
        assert result is not None
        assert match_type == "word_in_display"

    def test_word_in_display_name_hyphen(self):
        discord_list = [_du("someuser", display_name="Cool-Guy")]
        result, match_type = _find_discord_for_key("cool", discord_list)
        assert result is not None
        assert match_type == "word_in_display"

    def test_substring_username(self):
        discord_list = [_du("trogmoon")]
        result, match_type = _find_discord_for_key("trog", discord_list)
        assert result is not None
        assert match_type == "substring_username"

    def test_substring_display_name(self):
        discord_list = [_du("other", display_name="Shadowedvaca")]
        result, match_type = _find_discord_for_key("shadow", discord_list)
        assert result is not None
        assert match_type == "substring_display"

    def test_no_match(self):
        discord_list = [_du("unrelated")]
        result, match_type = _find_discord_for_key("rocket", discord_list)
        assert result is None
        assert match_type == "none"

    def test_empty_key(self):
        discord_list = [_du("rocket")]
        result, match_type = _find_discord_for_key("", discord_list)
        assert result is None
        assert match_type == "none"

    def test_short_key_no_substring(self):
        # Key < 3 chars should not do substring matching (only exact matches allowed)
        # "ab" would only match "xabx" via substring — should return None
        discord_list = [_du("xabx")]
        result, match_type = _find_discord_for_key("ab", discord_list)
        assert result is None
        assert match_type == "none"

    def test_priority_exact_over_substring(self):
        # Exact username match should win over substring display
        discord_list = [
            _du("sho", display_name="Shodoomhavoc"),
            _du("other", display_name="Sho"),
        ]
        result, match_type = _find_discord_for_key("sho", discord_list)
        assert result["username"] == "sho"
        assert match_type == "exact_username"


# ---------------------------------------------------------------------------
# _attribution_for_match
# ---------------------------------------------------------------------------

class TestAttributionForMatch:
    def test_note_exact_username_high(self):
        du = _du("rocket")
        source, conf = _attribution_for_match("exact_username", du, from_note=True)
        assert source == "note_key"
        assert conf == "high"

    def test_note_exact_display_high(self):
        du = _du("x", display_name="Rocket")
        source, conf = _attribution_for_match("exact_display", du, from_note=True)
        assert source == "note_key"
        assert conf == "high"

    def test_note_word_in_display_medium(self):
        du = _du("x", display_name="Trog/Moon")
        source, conf = _attribution_for_match("word_in_display", du, from_note=True)
        assert source == "note_key"
        assert conf == "medium"

    def test_note_substring_medium(self):
        du = _du("trogmoon")
        source, conf = _attribution_for_match("substring_username", du, from_note=True)
        assert source == "note_key"
        assert conf == "medium"

    def test_stub_no_discord_low(self):
        source, conf = _attribution_for_match("none", None, from_note=True)
        assert source == "note_key_stub"
        assert conf == "low"

    def test_no_note_exact_name_high(self):
        du = _du("dashdashdash")
        source, conf = _attribution_for_match("exact_username", du, from_note=False)
        assert source == "exact_name"
        assert conf == "high"

    def test_no_note_fuzzy_medium(self):
        du = _du("dashdashdash")
        source, conf = _attribution_for_match("substring_display", du, from_note=False)
        assert source == "fuzzy_name"
        assert conf == "medium"
