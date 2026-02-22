"""
Unit tests for the LuaParser in the companion app.

LuaParser is a pure Python recursive-descent parser — no DB, no network.
Tests cover: simple tables, nested tables, arrays, booleans, nil,
string escaping, negative numbers, and a realistic SavedVariables file.
"""

import os
import tempfile

import pytest

from companion_app.patt_sync_watcher import LuaParser


class TestParseSimpleTable:
    def test_string_and_int_values(self):
        result, _ = LuaParser._parse_value('{ name = "Trog", level = 80 }', 0)
        assert result["name"] == "Trog"
        assert result["level"] == 80

    def test_float_value(self):
        result, _ = LuaParser._parse_value('{ ratio = 3.14 }', 0)
        assert abs(result["ratio"] - 3.14) < 0.001

    def test_negative_number(self):
        result, _ = LuaParser._parse_value('{ offset = -5 }', 0)
        assert result["offset"] == -5

    def test_boolean_true(self):
        result, _ = LuaParser._parse_value('{ isOnline = true }', 0)
        assert result["isOnline"] is True

    def test_boolean_false(self):
        result, _ = LuaParser._parse_value('{ isMobile = false }', 0)
        assert result["isMobile"] is False

    def test_nil_value(self):
        result, _ = LuaParser._parse_value('{ data = nil }', 0)
        assert result["data"] is None

    def test_empty_table(self):
        result, _ = LuaParser._parse_value('{}', 0)
        assert result == {}


class TestArrayTable:
    def test_simple_string_array(self):
        result, _ = LuaParser._parse_value('{ "one", "two", "three" }', 0)
        assert isinstance(result, list)
        assert result == ["one", "two", "three"]

    def test_integer_array(self):
        result, _ = LuaParser._parse_value('{ 1, 2, 3 }', 0)
        assert result == [1, 2, 3]

    def test_mixed_does_not_convert_to_list(self):
        # Mixed hash + sequential — stays as dict
        result, _ = LuaParser._parse_value('{ name = "x", 42 }', 0)
        assert isinstance(result, dict)


class TestNestedTable:
    def test_nested_dict(self):
        result, _ = LuaParser._parse_value(
            '{ lastExport = { memberCount = 45 } }', 0
        )
        assert result["lastExport"]["memberCount"] == 45

    def test_doubly_nested(self):
        result, _ = LuaParser._parse_value(
            '{ a = { b = { c = 99 } } }', 0
        )
        assert result["a"]["b"]["c"] == 99

    def test_nested_array(self):
        result, _ = LuaParser._parse_value(
            '{ chars = { "Alice", "Bob" } }', 0
        )
        assert result["chars"] == ["Alice", "Bob"]


class TestStringEscaping:
    def test_single_quoted_string(self):
        result, _ = LuaParser._parse_value("{ realm = 'Senjin' }", 0)
        assert result["realm"] == "Senjin"

    def test_double_quoted_string(self):
        result, _ = LuaParser._parse_value('{ realm = "Senjin" }', 0)
        assert result["realm"] == "Senjin"

    def test_escaped_newline(self):
        result, _ = LuaParser._parse_value('{ note = "line1\\nline2" }', 0)
        assert "\n" in result["note"]

    def test_escaped_tab(self):
        result, _ = LuaParser._parse_value('{ note = "col1\\tcol2" }', 0)
        assert "\t" in result["note"]

    def test_escaped_backslash(self):
        result, _ = LuaParser._parse_value('{ path = "C:\\\\wow" }', 0)
        assert result["path"] == "C:\\wow"

    def test_apostrophe_in_realm_name(self):
        result, _ = LuaParser._parse_value('{ name = "Sen\'jin" }', 0)
        assert result["name"] == "Sen'jin"


class TestBracketKeySyntax:
    def test_bracket_string_key(self):
        result, _ = LuaParser._parse_value('{ ["key"] = "value" }', 0)
        assert result["key"] == "value"

    def test_bracket_numeric_key(self):
        result, _ = LuaParser._parse_value('{ [1] = "first", [2] = "second" }', 0)
        # Numeric bracket keys treated as integers
        assert result[1] == "first"
        assert result[2] == "second"


class TestParseFile:
    def test_realistic_saved_variables(self):
        lua_content = '''PATTSyncDB = {
            lastExportTime = 1740153600,
            totalExports = 3,
            lastExport = {
                exportTime = 1740153600,
                exportTimeISO = "2026-02-21T18:00:00Z",
                addonVersion = "1.0.0",
                guildName = "Pull All The Things",
                memberCount = 2,
                characters = {
                    {
                        name = "Trogmoon",
                        realm = "Sen'jin",
                        class = "Druid",
                        level = 80,
                        rank = 0,
                        rankName = "Guild Leader",
                        note = "GM / Mike",
                        officerNote = "Discord: Trog",
                        isOnline = true,
                        lastOnline = "online",
                        zone = "Dornogal",
                    },
                    {
                        name = "Shodoom",
                        realm = "Bleeding Hollow",
                        class = "Shaman",
                        level = 80,
                        rank = 1,
                        rankName = "Officer",
                        note = "",
                        officerNote = "",
                        isOnline = false,
                        lastOnline = "2d 4h",
                        zone = "",
                    },
                },
            },
        }'''

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.lua', delete=False, encoding='utf-8'
        ) as f:
            f.write(lua_content)
            tmppath = f.name

        try:
            result = LuaParser.parse_file(tmppath)
        finally:
            os.unlink(tmppath)

        assert result["totalExports"] == 3
        assert result["lastExport"]["guildName"] == "Pull All The Things"
        assert result["lastExport"]["addonVersion"] == "1.0.0"

        chars = result["lastExport"]["characters"]
        assert len(chars) == 2
        assert chars[0]["name"] == "Trogmoon"
        assert chars[0]["note"] == "GM / Mike"
        assert chars[0]["officerNote"] == "Discord: Trog"
        assert chars[0]["isOnline"] is True
        assert chars[1]["name"] == "Shodoom"
        assert chars[1]["isOnline"] is False
        assert chars[1]["rank"] == 1

    def test_missing_pattsyndb_raises(self):
        lua_content = "SomeOtherDB = { foo = 1 }"

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.lua', delete=False, encoding='utf-8'
        ) as f:
            f.write(lua_content)
            tmppath = f.name

        try:
            with pytest.raises(ValueError, match="PATTSyncDB"):
                LuaParser.parse_file(tmppath)
        finally:
            os.unlink(tmppath)

    def test_nonexistent_file_raises(self):
        with pytest.raises((FileNotFoundError, OSError)):
            LuaParser.parse_file("/nonexistent/path/file.lua")

    def test_lua_line_comments_ignored(self):
        lua_content = '''PATTSyncDB = {
            -- This is a comment
            count = 5, -- inline comment
        }'''

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.lua', delete=False, encoding='utf-8'
        ) as f:
            f.write(lua_content)
            tmppath = f.name

        try:
            result = LuaParser.parse_file(tmppath)
        finally:
            os.unlink(tmppath)

        assert result["count"] == 5
