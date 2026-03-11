"""Unit tests for sv_common.config_cache."""

import importlib
import pytest

import sv_common.config_cache as cc


def _reload_module():
    """Reload the module to reset the internal _cache between tests."""
    importlib.reload(cc)


def test_defaults_before_set():
    _reload_module()
    assert cc.get_guild_name() == "Guild Portal"
    assert cc.get_accent_color_int() == 0xD4A84B
    assert cc.get_home_realm_slug() == ""
    assert cc.get_guild_name_slug() == ""
    assert cc.get_discord_invite_url() == ""
    assert cc.is_guild_quotes_enabled() is False
    assert cc.is_contests_enabled() is True


def test_set_and_get_site_config():
    _reload_module()
    cc.set_site_config(
        {
            "guild_name": "Pull All The Things",
            "guild_tagline": "Casual Heroic Raiding",
            "accent_color_hex": "#d4a84b",
            "home_realm_slug": "senjin",
            "guild_name_slug": "pull-all-the-things",
            "discord_invite_url": "https://discord.gg/test",
            "enable_guild_quotes": True,
            "enable_contests": True,
            "setup_complete": True,
        }
    )

    assert cc.get_guild_name() == "Pull All The Things"
    assert cc.get_home_realm_slug() == "senjin"
    assert cc.get_guild_name_slug() == "pull-all-the-things"
    assert cc.get_discord_invite_url() == "https://discord.gg/test"
    assert cc.is_guild_quotes_enabled() is True
    assert cc.is_contests_enabled() is True


def test_accent_color_int_computed():
    _reload_module()
    cc.set_site_config({"accent_color_hex": "#d4a84b"})
    assert cc.get_accent_color_int() == 0xD4A84B


def test_accent_color_int_custom():
    _reload_module()
    cc.set_site_config({"accent_color_hex": "#ff0000"})
    assert cc.get_accent_color_int() == 0xFF0000


def test_accent_color_int_fallback_on_invalid():
    _reload_module()
    cc.set_site_config({"accent_color_hex": "not-a-color"})
    # Should fall back to default rather than raising
    assert cc.get_accent_color_int() == 0xD4A84B


def test_get_site_config_returns_copy():
    _reload_module()
    cc.set_site_config({"guild_name": "TestGuild"})
    c1 = cc.get_site_config()
    c1["guild_name"] = "Mutated"
    # Internal cache should be unaffected
    assert cc.get_guild_name() == "TestGuild"


def test_set_overwrites_previous():
    _reload_module()
    cc.set_site_config({"guild_name": "First"})
    cc.set_site_config({"guild_name": "Second", "home_realm_slug": "realmabc"})
    assert cc.get_guild_name() == "Second"
    assert cc.get_home_realm_slug() == "realmabc"


def test_feature_flags_default_to_false_for_quotes():
    _reload_module()
    cc.set_site_config({"guild_name": "Guild"})  # no enable_guild_quotes key
    assert cc.is_guild_quotes_enabled() is False


def test_feature_flags_contests_default_true():
    _reload_module()
    cc.set_site_config({"guild_name": "Guild"})  # no enable_contests key
    # Default in function is True, but cache doesn't have the key → returns False
    # The function uses dict.get(key, default)
    assert cc.is_contests_enabled() is True
