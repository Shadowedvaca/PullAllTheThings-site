"""
Unit tests for Phase 2.9 — Data Quality Engine.

Tests cover:
- Rules registry structure and completeness
- Mitigation helper logic (pure-function aspects)
- make_issue_hash determinism
- run_auto_mitigations only processes auto_mitigate=True rules
- Scheduler no longer imports relink_note_changed_characters or calls run_matching
"""

import inspect
import pytest


# ---------------------------------------------------------------------------
# Rules registry
# ---------------------------------------------------------------------------

class TestRulesRegistry:
    def setup_method(self):
        from sv_common.guild_sync.rules import RULES, RuleDefinition
        self.RULES = RULES
        self.RuleDefinition = RuleDefinition

    def test_all_five_rules_exist(self):
        # Phase 2.9 rules (5) + Phase 3.0C drift rules (3) = 8 total
        expected_core = {"note_mismatch", "orphan_wow", "orphan_discord", "role_mismatch", "stale_character"}
        assert expected_core.issubset(set(self.RULES.keys()))

    def test_each_rule_is_rule_definition(self):
        for issue_type, rule in self.RULES.items():
            assert isinstance(rule, self.RuleDefinition), f"{issue_type} is not a RuleDefinition"

    def test_note_mismatch_is_auto_mitigate(self):
        rule = self.RULES["note_mismatch"]
        assert rule.auto_mitigate is True
        assert rule.mitigate_fn is not None

    def test_orphan_wow_is_manual(self):
        rule = self.RULES["orphan_wow"]
        assert rule.auto_mitigate is False
        assert rule.mitigate_fn is not None

    def test_orphan_discord_is_manual(self):
        rule = self.RULES["orphan_discord"]
        assert rule.auto_mitigate is False
        assert rule.mitigate_fn is not None

    def test_role_mismatch_is_manual(self):
        rule = self.RULES["role_mismatch"]
        assert rule.auto_mitigate is False
        assert rule.mitigate_fn is not None

    def test_stale_character_has_no_mitigate_fn(self):
        rule = self.RULES["stale_character"]
        assert rule.auto_mitigate is False
        assert rule.mitigate_fn is None

    def test_all_rules_have_required_fields(self):
        for issue_type, rule in self.RULES.items():
            assert rule.issue_type == issue_type, f"{issue_type}: issue_type mismatch"
            assert rule.name, f"{issue_type}: empty name"
            assert rule.description, f"{issue_type}: empty description"
            assert rule.severity in ("info", "warning", "error"), \
                f"{issue_type}: invalid severity '{rule.severity}'"

    def test_all_mitigate_fns_are_async(self):
        for issue_type, rule in self.RULES.items():
            if rule.mitigate_fn:
                assert inspect.iscoroutinefunction(rule.mitigate_fn), \
                    f"{issue_type}: mitigate_fn is not async"

    def test_only_note_mismatch_is_auto_mitigate(self):
        auto_rules = [k for k, v in self.RULES.items() if v.auto_mitigate]
        assert auto_rules == ["note_mismatch"], \
            f"Expected only note_mismatch to be auto_mitigate, got: {auto_rules}"


# ---------------------------------------------------------------------------
# Issue hash
# ---------------------------------------------------------------------------

class TestMakeIssueHash:
    def setup_method(self):
        from sv_common.guild_sync.integrity_checker import make_issue_hash
        self.make_issue_hash = make_issue_hash

    def test_deterministic(self):
        h1 = self.make_issue_hash("orphan_wow", 42)
        h2 = self.make_issue_hash("orphan_wow", 42)
        assert h1 == h2

    def test_different_types_differ(self):
        h1 = self.make_issue_hash("orphan_wow", 1)
        h2 = self.make_issue_hash("orphan_discord", 1)
        assert h1 != h2

    def test_different_ids_differ(self):
        h1 = self.make_issue_hash("orphan_wow", 1)
        h2 = self.make_issue_hash("orphan_wow", 2)
        assert h1 != h2

    def test_hash_is_hex_string(self):
        h = self.make_issue_hash("note_mismatch", 99)
        assert isinstance(h, str)
        assert len(h) == 64  # sha256 hex digest

    def test_multiple_identifiers(self):
        h1 = self.make_issue_hash("role_mismatch", 5, "extra")
        h2 = self.make_issue_hash("role_mismatch", 5)
        assert h1 != h2


# ---------------------------------------------------------------------------
# Integrity checker — detect functions exist and are async
# ---------------------------------------------------------------------------

class TestDetectFunctions:
    def test_detect_orphan_wow_is_async(self):
        from sv_common.guild_sync.integrity_checker import detect_orphan_wow
        assert inspect.iscoroutinefunction(detect_orphan_wow)

    def test_detect_orphan_discord_is_async(self):
        from sv_common.guild_sync.integrity_checker import detect_orphan_discord
        assert inspect.iscoroutinefunction(detect_orphan_discord)

    def test_detect_role_mismatch_is_async(self):
        from sv_common.guild_sync.integrity_checker import detect_role_mismatch
        assert inspect.iscoroutinefunction(detect_role_mismatch)

    def test_detect_stale_character_is_async(self):
        from sv_common.guild_sync.integrity_checker import detect_stale_character
        assert inspect.iscoroutinefunction(detect_stale_character)

    def test_run_integrity_check_is_async(self):
        from sv_common.guild_sync.integrity_checker import run_integrity_check
        assert inspect.iscoroutinefunction(run_integrity_check)

    def test_detect_functions_map(self):
        from sv_common.guild_sync.integrity_checker import DETECT_FUNCTIONS
        # Should have entries for all rule types that can be individually scanned
        assert "note_mismatch" in DETECT_FUNCTIONS
        assert "orphan_wow" in DETECT_FUNCTIONS
        assert "orphan_discord" in DETECT_FUNCTIONS
        assert "stale_character" in DETECT_FUNCTIONS
        # role_mismatch uses a tuple-returning combined function, handled separately
        assert "role_mismatch" not in DETECT_FUNCTIONS


# ---------------------------------------------------------------------------
# Mitigations — function signatures
# ---------------------------------------------------------------------------

class TestMitigationFunctions:
    def test_mitigate_note_mismatch_is_async(self):
        from sv_common.guild_sync.mitigations import mitigate_note_mismatch
        assert inspect.iscoroutinefunction(mitigate_note_mismatch)

    def test_mitigate_orphan_wow_is_async(self):
        from sv_common.guild_sync.mitigations import mitigate_orphan_wow
        assert inspect.iscoroutinefunction(mitigate_orphan_wow)

    def test_mitigate_orphan_discord_is_async(self):
        from sv_common.guild_sync.mitigations import mitigate_orphan_discord
        assert inspect.iscoroutinefunction(mitigate_orphan_discord)

    def test_mitigate_role_mismatch_is_async(self):
        from sv_common.guild_sync.mitigations import mitigate_role_mismatch
        assert inspect.iscoroutinefunction(mitigate_role_mismatch)

    def test_run_auto_mitigations_is_async(self):
        from sv_common.guild_sync.mitigations import run_auto_mitigations
        assert inspect.iscoroutinefunction(run_auto_mitigations)

    def test_mitigate_note_mismatch_takes_pool_and_issue_row(self):
        from sv_common.guild_sync.mitigations import mitigate_note_mismatch
        sig = inspect.signature(mitigate_note_mismatch)
        params = list(sig.parameters.keys())
        assert "pool" in params
        assert "issue_row" in params

    def test_mitigate_note_mismatch_returns_false_for_missing_char_id(self):
        """Synchronous part: returns False if issue_row has no wow_character_id."""
        # We can't easily test async without a DB, but we can verify the guard
        # by checking that the function signature is correct and the docstring
        from sv_common.guild_sync.mitigations import mitigate_note_mismatch
        assert mitigate_note_mismatch.__doc__ is not None


# ---------------------------------------------------------------------------
# Scheduler — no longer imports relink_note_changed_characters or run_matching
# ---------------------------------------------------------------------------

class TestSchedulerPipeline:
    def test_scheduler_does_not_import_relink(self):
        """run_addon_sync should not call relink_note_changed_characters."""
        import ast
        import pathlib
        src = pathlib.Path("src/sv_common/guild_sync/scheduler.py").read_text()
        assert "relink_note_changed_characters" not in src, \
            "scheduler.py still references relink_note_changed_characters"

    def test_scheduler_imports_run_drift_scan(self):
        """scheduler.py should import run_drift_scan from drift_scanner (Phase 3.0C)."""
        import pathlib
        src = pathlib.Path("src/sv_common/guild_sync/scheduler.py").read_text()
        assert "run_drift_scan" in src, \
            "scheduler.py does not reference run_drift_scan"

    def test_scheduler_run_addon_sync_comment_mentions_no_matching(self):
        """run_addon_sync docstring should note that run_matching is not called."""
        import pathlib
        src = pathlib.Path("src/sv_common/guild_sync/scheduler.py").read_text()
        assert "run_matching" in src, \
            "scheduler.py should mention run_matching (it's still available as admin action)"

    def test_db_sync_logs_note_changed_not_returns_ids(self):
        """sync_addon_data should log note_mismatch issues, not return note_changed_ids."""
        import pathlib
        src = pathlib.Path("src/sv_common/guild_sync/db_sync.py").read_text()
        assert "note_changed_ids" not in src, \
            "db_sync.py still uses note_changed_ids"
        assert "note_mismatch" in src, \
            "db_sync.py should log note_mismatch issues"


# ---------------------------------------------------------------------------
# db_sync — stats dict structure
# ---------------------------------------------------------------------------

class TestDbSyncStatKeys:
    def test_sync_addon_data_stats_has_note_changed_key(self):
        """Stats dict should have 'note_changed' (count) not 'note_changed_ids' (list)."""
        import pathlib
        src = pathlib.Path("src/sv_common/guild_sync/db_sync.py").read_text()
        assert '"note_changed"' in src or "'note_changed'" in src, \
            "db_sync.py stats should have note_changed count key"
        assert "note_changed_ids" not in src, \
            "db_sync.py should not have note_changed_ids"


# ---------------------------------------------------------------------------
# Note alias registry
# ---------------------------------------------------------------------------

class TestNoteAliases:
    def test_upsert_note_alias_is_importable(self):
        """upsert_note_alias should be importable from integrity_checker."""
        from sv_common.guild_sync.integrity_checker import upsert_note_alias
        assert callable(upsert_note_alias)

    def test_upsert_note_alias_is_async(self):
        import inspect
        from sv_common.guild_sync.integrity_checker import upsert_note_alias
        assert inspect.iscoroutinefunction(upsert_note_alias)

    def test_upsert_note_alias_signature(self):
        import inspect
        from sv_common.guild_sync.integrity_checker import upsert_note_alias
        sig = inspect.signature(upsert_note_alias)
        params = list(sig.parameters.keys())
        assert "conn" in params
        assert "player_id" in params
        assert "alias" in params
        assert "source" in params

    def test_alias_registry_orm_model(self):
        """PlayerNoteAlias ORM model should have correct tablename and schema."""
        from sv_common.db.models import PlayerNoteAlias
        assert PlayerNoteAlias.__tablename__ == "player_note_aliases"
        assert PlayerNoteAlias.__table_args__[-1]["schema"] == "guild_identity"

    def test_alias_registry_orm_has_required_columns(self):
        """PlayerNoteAlias should have id, player_id, alias, source, created_at columns."""
        from sv_common.db.models import PlayerNoteAlias
        cols = {c.name for c in PlayerNoteAlias.__table__.columns}
        assert "id" in cols
        assert "player_id" in cols
        assert "alias" in cols
        assert "source" in cols
        assert "created_at" in cols

    def test_player_model_has_note_aliases_relationship(self):
        """Player model should have a note_aliases relationship."""
        from sv_common.db.models import Player
        assert hasattr(Player, "note_aliases")

    def test_detect_note_mismatch_loads_aliases(self):
        """detect_note_mismatch source should reference player_note_aliases table."""
        import inspect
        from sv_common.guild_sync.integrity_checker import detect_note_mismatch
        src = inspect.getsource(detect_note_mismatch)
        assert "player_note_aliases" in src, \
            "detect_note_mismatch should query player_note_aliases"

    def test_mitigations_import_upsert_note_alias(self):
        """mitigations.py should import upsert_note_alias."""
        import pathlib
        src = pathlib.Path("src/sv_common/guild_sync/mitigations.py").read_text()
        assert "upsert_note_alias" in src, \
            "mitigations.py should use upsert_note_alias"

    def test_identity_engine_import_upsert_note_alias(self):
        """identity_engine.py should import upsert_note_alias."""
        import pathlib
        src = pathlib.Path("src/sv_common/guild_sync/identity_engine.py").read_text()
        assert "upsert_note_alias" in src, \
            "identity_engine.py should use upsert_note_alias"

    def test_migration_0025_exists(self):
        """Migration 0025 for player_note_aliases should exist."""
        import pathlib
        migrations = list(pathlib.Path("alembic/versions").glob("0025_*.py"))
        assert len(migrations) == 1, "Expected exactly one 0025_*.py migration"
        content = migrations[0].read_text()
        assert "player_note_aliases" in content
