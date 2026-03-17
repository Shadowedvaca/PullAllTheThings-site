"""
Unit tests for Phase 5.0 — My Characters dashboard.

Tests cover:
1. member_routes module imports and structure
2. _build_char_dict helper output shape
3. _pick_default_character_id selection logic
4. GET /my-characters page route exists in profile_pages
5. GET /api/v1/me/characters endpoint registered in app
6. base.html contains My Characters nav link
"""

import inspect
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 1. Module imports
# ---------------------------------------------------------------------------


class TestMemberRoutesImport:
    def test_module_imports(self):
        from guild_portal.api import member_routes
        assert member_routes is not None

    def test_router_exists(self):
        from guild_portal.api.member_routes import router
        assert router is not None

    def test_router_prefix(self):
        from guild_portal.api.member_routes import router
        assert router.prefix == "/api/v1/me"

    def test_get_my_characters_exists(self):
        from guild_portal.api.member_routes import get_my_characters
        assert callable(get_my_characters)

    def test_helpers_exported(self):
        from guild_portal.api.member_routes import (
            _build_char_dict,
            _pick_default_character_id,
        )
        assert callable(_build_char_dict)
        assert callable(_pick_default_character_id)


# ---------------------------------------------------------------------------
# 2. _build_char_dict output shape
# ---------------------------------------------------------------------------


class TestBuildCharDict:
    def _make_char(self, char_id=1, name="Trogmoon", realm="senjin"):
        char = MagicMock()
        char.id = char_id
        char.character_name = name
        char.realm_slug = realm
        char.realm_name = "Sen'jin"
        char.item_level = 639
        char.last_login_timestamp = 1741900800000
        char.blizzard_last_sync = None

        cls = MagicMock()
        cls.name = "Druid"
        cls.color_hex = "#ff7c0a"
        char.wow_class = cls

        spec = MagicMock()
        spec.name = "Balance"
        char.active_spec = spec

        return char

    def _make_pc(self, char, link_source="self_service"):
        pc = MagicMock()
        pc.character = char
        pc.link_source = link_source
        return pc

    def _make_player(self, main_id=1, offspec_id=None):
        player = MagicMock()
        player.main_character_id = main_id
        player.offspec_character_id = offspec_id
        return player

    def test_returns_required_keys(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char()
        pc = self._make_pc(char)
        player = self._make_player()

        result = _build_char_dict(pc, player, {})

        required_keys = [
            "id", "character_name", "realm_slug", "realm_display",
            "class_name", "class_color", "class_emoji", "spec_name",
            "avg_item_level", "last_login_ms", "is_main", "is_offspec",
            "link_source", "armory_url", "raiderio_url", "wcl_url",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_is_main_flag(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char(char_id=42)
        pc = self._make_pc(char)
        player = self._make_player(main_id=42)

        result = _build_char_dict(pc, player, {})
        assert result["is_main"] is True
        assert result["is_offspec"] is False

    def test_not_main_when_different_id(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char(char_id=10)
        pc = self._make_pc(char)
        player = self._make_player(main_id=99)

        result = _build_char_dict(pc, player, {})
        assert result["is_main"] is False

    def test_class_emoji_druid(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char()
        pc = self._make_pc(char)
        player = self._make_player()

        result = _build_char_dict(pc, player, {})
        assert result["class_emoji"] == "🌿"

    def test_armory_url_format(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char(name="Trogmoon", realm="senjin")
        pc = self._make_pc(char)
        player = self._make_player()

        result = _build_char_dict(pc, player, {})
        assert "worldofwarcraft.blizzard.com" in result["armory_url"]
        assert "senjin" in result["armory_url"]
        assert "Trogmoon" in result["armory_url"]

    def test_wcl_url_format(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char(name="Trogmoon", realm="senjin")
        pc = self._make_pc(char)
        player = self._make_player()

        result = _build_char_dict(pc, player, {})
        assert "warcraftlogs.com" in result["wcl_url"]
        assert "senjin" in result["wcl_url"]

    def test_raiderio_url_from_profile(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char(char_id=5)
        pc = self._make_pc(char)
        player = self._make_player()

        rio = MagicMock()
        rio.profile_url = "https://raider.io/characters/us/senjin/Trogmoon"
        rio_by_char = {5: rio}

        result = _build_char_dict(pc, player, rio_by_char)
        assert result["raiderio_url"] == "https://raider.io/characters/us/senjin/Trogmoon"

    def test_raiderio_url_none_when_no_profile(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char(char_id=5)
        pc = self._make_pc(char)
        player = self._make_player()

        result = _build_char_dict(pc, player, {})
        assert result["raiderio_url"] is None

    def test_bnet_link_source(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char()
        pc = self._make_pc(char, link_source="battlenet_oauth")
        player = self._make_player()

        result = _build_char_dict(pc, player, {})
        assert result["link_source"] == "battlenet_oauth"

    def test_realm_display_falls_back_to_slug(self):
        from guild_portal.api.member_routes import _build_char_dict

        char = self._make_char(realm="area-52")
        char.realm_name = None  # no display name
        pc = self._make_pc(char)
        player = self._make_player()

        result = _build_char_dict(pc, player, {})
        # Should title-case the slug
        assert "Area" in result["realm_display"]


# ---------------------------------------------------------------------------
# 3. _pick_default_character_id selection logic
# ---------------------------------------------------------------------------


class TestPickDefaultCharacterId:
    def test_empty_returns_none(self):
        from guild_portal.api.member_routes import _pick_default_character_id
        assert _pick_default_character_id([], None, None) is None

    def test_main_preferred(self):
        from guild_portal.api.member_routes import _pick_default_character_id
        chars = [{"id": 1}, {"id": 2}, {"id": 3}]
        assert _pick_default_character_id(chars, 2, 3) == 2

    def test_offspec_when_no_main(self):
        from guild_portal.api.member_routes import _pick_default_character_id
        chars = [{"id": 1}, {"id": 2}, {"id": 3}]
        assert _pick_default_character_id(chars, None, 3) == 3

    def test_offspec_when_main_not_in_list(self):
        from guild_portal.api.member_routes import _pick_default_character_id
        chars = [{"id": 1}, {"id": 2}]
        assert _pick_default_character_id(chars, 99, 2) == 2

    def test_first_alphabetical_fallback(self):
        from guild_portal.api.member_routes import _pick_default_character_id
        chars = [{"id": 10}, {"id": 20}]
        assert _pick_default_character_id(chars, None, None) == 10

    def test_main_must_be_in_list(self):
        from guild_portal.api.member_routes import _pick_default_character_id
        chars = [{"id": 5}]
        # main_character_id=99 not in list, offspec=5
        result = _pick_default_character_id(chars, 99, 5)
        assert result == 5


# ---------------------------------------------------------------------------
# 4. Page route exists in profile_pages
# ---------------------------------------------------------------------------


class TestMyCharactersPageRoute:
    def test_my_characters_page_function_exists(self):
        from guild_portal.pages.profile_pages import my_characters_page
        assert callable(my_characters_page)

    def test_my_characters_page_is_async(self):
        from guild_portal.pages.profile_pages import my_characters_page
        assert inspect.iscoroutinefunction(my_characters_page)

    def test_profile_router_has_my_characters_route(self):
        from guild_portal.pages.profile_pages import router
        routes = [r.path for r in router.routes]
        assert "/my-characters" in routes


# ---------------------------------------------------------------------------
# 5. API endpoint registered in app router
# ---------------------------------------------------------------------------


class TestMemberRouterRegistered:
    def test_member_router_in_app(self):
        """member_routes router has the /api/v1/me/characters endpoint."""
        from guild_portal.api.member_routes import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/me/characters" in paths


# ---------------------------------------------------------------------------
# 6. base.html contains My Characters nav link
# ---------------------------------------------------------------------------


class TestBaseNavLink:
    def test_my_characters_link_in_base_html(self):
        from pathlib import Path
        base = Path(__file__).parents[2] / "src" / "guild_portal" / "templates" / "base.html"
        content = base.read_text(encoding="utf-8")
        assert "/my-characters" in content
        assert "My Characters" in content

    def test_my_characters_link_is_auth_gated(self):
        """The link must appear inside the {% if current_member %} block."""
        from pathlib import Path
        base = Path(__file__).parents[2] / "src" / "guild_portal" / "templates" / "base.html"
        content = base.read_text(encoding="utf-8")
        # Find the position of the nav link vs the if block
        if_pos = content.find("{% if current_member %}")
        link_pos = content.find("/my-characters")
        else_pos = content.find("{% else %}", if_pos)
        # Link must appear after the if block and before the else
        assert if_pos < link_pos < else_pos


# ---------------------------------------------------------------------------
# 7. my_characters.html template exists and extends base.html
# ---------------------------------------------------------------------------


class TestMyCharactersTemplate:
    def test_template_exists(self):
        from pathlib import Path
        tpl = (
            Path(__file__).parents[2]
            / "src"
            / "guild_portal"
            / "templates"
            / "member"
            / "my_characters.html"
        )
        assert tpl.exists()

    def test_template_extends_base(self):
        from pathlib import Path
        tpl = (
            Path(__file__).parents[2]
            / "src"
            / "guild_portal"
            / "templates"
            / "member"
            / "my_characters.html"
        )
        content = tpl.read_text(encoding="utf-8")
        assert 'extends "base.html"' in content

    def test_template_loads_js(self):
        from pathlib import Path
        tpl = (
            Path(__file__).parents[2]
            / "src"
            / "guild_portal"
            / "templates"
            / "member"
            / "my_characters.html"
        )
        content = tpl.read_text(encoding="utf-8")
        assert "my_characters.js" in content

    def test_template_loads_css(self):
        from pathlib import Path
        tpl = (
            Path(__file__).parents[2]
            / "src"
            / "guild_portal"
            / "templates"
            / "member"
            / "my_characters.html"
        )
        content = tpl.read_text(encoding="utf-8")
        assert "my_characters.css" in content
