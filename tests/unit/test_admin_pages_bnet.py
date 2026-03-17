"""
Unit tests for Phase 6.4 — admin_bnet_sync_user and admin_bnet_sync_all error reporting.

Tests:
1. admin_bnet_sync_user: report_error called on token failure + HTTP 422 returned
2. admin_bnet_sync_user: resolve_issue called on success
3. admin_bnet_sync_all: report_error called per-player on token failure
4. admin_bnet_sync_all: resolve_issue called per-player on success
"""

import inspect
import pytest


# ---------------------------------------------------------------------------
# Source inspection tests (no DB / HTTP needed)
# ---------------------------------------------------------------------------


class TestAdminBnetSyncUserSourceInspection:
    def test_sync_user_calls_report_error_on_token_failure(self):
        """admin_bnet_sync_user calls report_error when access token is None."""
        from guild_portal.pages.admin_pages import admin_bnet_sync_user
        src = inspect.getsource(admin_bnet_sync_user)
        assert "report_error" in src
        assert "bnet_token_expired" in src

    def test_sync_user_calls_resolve_issue_on_success(self):
        """admin_bnet_sync_user calls resolve_issue after a successful sync."""
        from guild_portal.pages.admin_pages import admin_bnet_sync_user
        src = inspect.getsource(admin_bnet_sync_user)
        assert "resolve_issue" in src
        assert "bnet_token_expired" in src
        assert "bnet_sync_error" in src

    def test_sync_user_still_returns_422_on_token_failure(self):
        """admin_bnet_sync_user still returns 422 even when report_error is called."""
        from guild_portal.pages.admin_pages import admin_bnet_sync_user
        src = inspect.getsource(admin_bnet_sync_user)
        assert "422" in src
        # report_error is called BEFORE the return (not replacing the HTTP error)
        re_pos = src.index("report_error")
        return_pos = src.index("status_code=422")
        assert re_pos < return_pos


class TestAdminBnetSyncAllSourceInspection:
    def test_sync_all_calls_report_error_on_token_failure(self):
        """admin_bnet_sync_all calls report_error per player when token is None."""
        from guild_portal.pages.admin_pages import admin_bnet_sync_all
        src = inspect.getsource(admin_bnet_sync_all)
        assert "report_error" in src
        assert "bnet_token_expired" in src

    def test_sync_all_calls_report_error_on_sync_exception(self):
        """admin_bnet_sync_all calls report_error on sync exception."""
        from guild_portal.pages.admin_pages import admin_bnet_sync_all
        src = inspect.getsource(admin_bnet_sync_all)
        assert "bnet_sync_error" in src

    def test_sync_all_calls_resolve_issue_on_success(self):
        """admin_bnet_sync_all calls resolve_issue per player on success."""
        from guild_portal.pages.admin_pages import admin_bnet_sync_all
        src = inspect.getsource(admin_bnet_sync_all)
        assert "resolve_issue" in src

    def test_sync_all_uses_player_id_as_identifier(self):
        """admin_bnet_sync_all uses str(player_id) as the error identifier."""
        from guild_portal.pages.admin_pages import admin_bnet_sync_all
        src = inspect.getsource(admin_bnet_sync_all)
        assert "str(player_id)" in src


# ---------------------------------------------------------------------------
# Reporter.py — new issue types registered
# ---------------------------------------------------------------------------


class TestReporterNewIssueTypes:
    def test_blizzard_sync_failed_in_emoji(self):
        from sv_common.guild_sync.reporter import ISSUE_EMOJI
        assert "blizzard_sync_failed" in ISSUE_EMOJI

    def test_crafting_sync_failed_in_emoji(self):
        from sv_common.guild_sync.reporter import ISSUE_EMOJI
        assert "crafting_sync_failed" in ISSUE_EMOJI

    def test_wcl_sync_failed_in_emoji(self):
        from sv_common.guild_sync.reporter import ISSUE_EMOJI
        assert "wcl_sync_failed" in ISSUE_EMOJI

    def test_attendance_sync_failed_in_emoji(self):
        from sv_common.guild_sync.reporter import ISSUE_EMOJI
        assert "attendance_sync_failed" in ISSUE_EMOJI

    def test_ah_sync_failed_in_emoji(self):
        from sv_common.guild_sync.reporter import ISSUE_EMOJI
        assert "ah_sync_failed" in ISSUE_EMOJI

    def test_all_new_types_in_names(self):
        from sv_common.guild_sync.reporter import ISSUE_TYPE_NAMES
        for key in ("blizzard_sync_failed", "crafting_sync_failed", "wcl_sync_failed",
                    "attendance_sync_failed", "ah_sync_failed"):
            assert key in ISSUE_TYPE_NAMES, f"Missing ISSUE_TYPE_NAMES entry: {key}"


# ---------------------------------------------------------------------------
# Scheduler — report_error calls in other jobs
# ---------------------------------------------------------------------------


class TestSchedulerJobErrorReporting:
    def test_run_blizzard_sync_uses_report_error(self):
        """run_blizzard_sync calls report_error on pipeline failure."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_blizzard_sync)
        assert "report_error" in src
        assert "blizzard_sync_failed" in src

    def test_run_crafting_sync_uses_report_error(self):
        """run_crafting_sync calls report_error on failure."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_crafting_sync)
        assert "report_error" in src
        assert "crafting_sync_failed" in src

    def test_run_wcl_sync_uses_report_error(self):
        """run_wcl_sync calls report_error on failure."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_wcl_sync)
        assert "report_error" in src
        assert "wcl_sync_failed" in src

    def test_run_attendance_processing_uses_report_error(self):
        """run_attendance_processing calls report_error on failure."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_attendance_processing)
        assert "report_error" in src
        assert "attendance_sync_failed" in src

    def test_run_ah_sync_uses_report_error(self):
        """run_ah_sync calls report_error on failure."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_ah_sync)
        assert "report_error" in src
        assert "ah_sync_failed" in src

    def test_run_bnet_character_refresh_uses_report_error(self):
        """run_bnet_character_refresh calls report_error."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_bnet_character_refresh)
        assert "report_error" in src
        assert "bnet_token_expired" in src
        assert "bnet_sync_error" in src

    def test_run_bnet_character_refresh_uses_resolve_issue(self):
        """run_bnet_character_refresh calls resolve_issue on success."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_bnet_character_refresh)
        assert "resolve_issue" in src

    def test_run_bnet_character_refresh_fetches_battletag(self):
        """run_bnet_character_refresh queries battletag for use as identifier."""
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        src = inspect.getsource(GuildSyncScheduler.run_bnet_character_refresh)
        assert "battletag" in src

    def test_bnet_character_sync_refresh_token_uses_report_error(self):
        """_refresh_token calls report_error on token expiry failure paths."""
        from sv_common.guild_sync import bnet_character_sync
        src = inspect.getsource(bnet_character_sync._refresh_token)
        assert "report_error" in src
        assert "bnet_token_expired" in src
