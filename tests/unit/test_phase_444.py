"""
Unit tests for Phase 4.4.4 — Data Quality Simplification.

Tests cover:
1. get_registered_rules() returns empty list
2. Drift scanner skips oauth links (SQL WHERE clause check)
3. Player manager API response includes bnet_verified field
4. DELETE /api/v1/settings/characters/{id} returns 403 for battlenet_oauth links
"""

import inspect
import pytest


# ---------------------------------------------------------------------------
# Task 1: Matching rules registry is empty
# ---------------------------------------------------------------------------


class TestMatchingRulesEmpty:
    def test_get_registered_rules_returns_list(self):
        from sv_common.guild_sync.matching_rules import get_registered_rules
        result = get_registered_rules()
        assert isinstance(result, list)

    def test_get_registered_rules_is_empty(self):
        from sv_common.guild_sync.matching_rules import get_registered_rules
        result = get_registered_rules()
        assert result == [], f"Expected empty list, got {result}"

    def test_name_match_rule_does_not_exist(self):
        """name_match_rule.py should have been deleted."""
        try:
            from sv_common.guild_sync.matching_rules import name_match_rule  # noqa
            assert False, "name_match_rule module should not exist"
        except ImportError:
            pass  # Expected

    def test_note_group_rule_does_not_exist(self):
        """note_group_rule.py should have been deleted."""
        try:
            from sv_common.guild_sync.matching_rules import note_group_rule  # noqa
            assert False, "note_group_rule module should not exist"
        except ImportError:
            pass  # Expected


# ---------------------------------------------------------------------------
# Task 2: Drift scanner skips OAuth links
# ---------------------------------------------------------------------------


class TestDriftScannerSkipsOAuth:
    def test_detect_link_note_contradictions_excludes_battlenet_oauth_in_sql(self):
        """detect_link_note_contradictions SQL must filter out battlenet_oauth links."""
        from sv_common.guild_sync import integrity_checker
        src = inspect.getsource(integrity_checker.detect_link_note_contradictions)
        assert "battlenet_oauth" in src, (
            "detect_link_note_contradictions should exclude battlenet_oauth links"
        )
        assert "link_source != 'battlenet_oauth'" in src or "link_source != \"battlenet_oauth\"" in src, (
            "detect_link_note_contradictions SQL should have AND pc.link_source != 'battlenet_oauth'"
        )

    def test_detect_link_note_contradictions_docstring_mentions_oauth(self):
        """detect_link_note_contradictions should document the OAuth skip in its docstring."""
        from sv_common.guild_sync.integrity_checker import detect_link_note_contradictions
        doc = detect_link_note_contradictions.__doc__ or ""
        assert "battlenet_oauth" in doc.lower() or "oauth" in doc.lower(), (
            "detect_link_note_contradictions docstring should mention OAuth skip"
        )


# ---------------------------------------------------------------------------
# Task 3: Player manager API includes bnet_verified field
# ---------------------------------------------------------------------------


class TestPlayersDataBnetVerified:
    def test_bnet_verified_ids_query_in_admin_pages(self):
        """admin_pages.py should build bnet_verified_ids from battlenet_accounts."""
        from guild_portal.pages import admin_pages
        src = inspect.getsource(admin_pages.admin_players_data)
        assert "bnet_verified_ids" in src, (
            "admin_players_data should compute bnet_verified_ids set"
        )
        assert "battlenet_accounts" in src, (
            "admin_players_data should query guild_identity.battlenet_accounts"
        )

    def test_bnet_verified_field_in_player_dict(self):
        """admin_players_data should include bnet_verified in the player dict."""
        from guild_portal.pages import admin_pages
        src = inspect.getsource(admin_pages.admin_players_data)
        assert '"bnet_verified"' in src or "'bnet_verified'" in src, (
            "admin_players_data player dict should include 'bnet_verified' key"
        )
        assert "bnet_verified_ids" in src, (
            "bnet_verified should be set from bnet_verified_ids"
        )


# ---------------------------------------------------------------------------
# Task 4: DELETE /api/v1/settings/characters blocks battlenet_oauth
# ---------------------------------------------------------------------------


class TestApiRemoveCharacterOAuthBlock:
    def test_api_remove_character_exists(self):
        """api_remove_character route should be defined in profile_pages."""
        from guild_portal.pages import profile_pages
        assert hasattr(profile_pages, "api_remove_character"), (
            "api_remove_character function should be defined"
        )

    def test_api_remove_character_blocks_battlenet_oauth_in_source(self):
        """api_remove_character should return 403 for battlenet_oauth link_source."""
        from guild_portal.pages import profile_pages
        src = inspect.getsource(profile_pages.api_remove_character)
        assert "battlenet_oauth" in src, (
            "api_remove_character should check for battlenet_oauth link_source"
        )
        assert "403" in src, (
            "api_remove_character should return 403 for OAuth-linked chars"
        )

    def test_api_add_character_manually_exists(self):
        """api_add_character_manually route should be defined in profile_pages."""
        from guild_portal.pages import profile_pages
        assert hasattr(profile_pages, "api_add_character_manually"), (
            "api_add_character_manually function should be defined"
        )

    def test_api_add_character_manually_uses_manual_claim_source(self):
        """api_add_character_manually should use link_source='manual_claim'."""
        from guild_portal.pages import profile_pages
        src = inspect.getsource(profile_pages.api_add_character_manually)
        assert "manual_claim" in src, (
            "api_add_character_manually should set link_source='manual_claim'"
        )

    def test_oauth_reminder_endpoint_exists(self):
        """admin_send_oauth_reminder should be defined in admin_pages."""
        from guild_portal.pages import admin_pages
        assert hasattr(admin_pages, "admin_send_oauth_reminder"), (
            "admin_send_oauth_reminder endpoint should be defined"
        )

    def test_oauth_coverage_endpoint_exists(self):
        """admin_oauth_coverage should be defined in admin_pages."""
        from guild_portal.pages import admin_pages
        assert hasattr(admin_pages, "admin_oauth_coverage"), (
            "admin_oauth_coverage endpoint should be defined"
        )
