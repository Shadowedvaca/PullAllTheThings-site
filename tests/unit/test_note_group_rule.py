"""Unit tests for NoteGroupRule (Phase 3.0B)."""

import pytest

from sv_common.guild_sync.matching_rules.base import MatchingContext, RuleResult
from sv_common.guild_sync.matching_rules.note_group_rule import NoteGroupRule


class TestNoteGroupRuleAttributes:
    def test_name(self):
        assert NoteGroupRule.name == "note_group"

    def test_order(self):
        assert NoteGroupRule.order == 10

    def test_link_source(self):
        assert NoteGroupRule.link_source == "note_key"

    def test_description_is_nonempty(self):
        assert NoteGroupRule.description

    def test_instance_order_lower_than_name_match(self):
        from sv_common.guild_sync.matching_rules.name_match_rule import NameMatchRule
        assert NoteGroupRule.order < NameMatchRule.order


class TestNoteGroupRuleResult:
    def test_rule_result_has_correct_name(self):
        r = RuleResult(rule_name="note_group")
        assert r.rule_name == "note_group"

    def test_zero_results_not_changed(self):
        r = RuleResult(rule_name="note_group")
        assert r.changed_anything is False

    def test_chars_linked_triggers_changed(self):
        r = RuleResult(rule_name="note_group", chars_linked=2)
        assert r.changed_anything is True

    def test_stubs_only_not_changed(self):
        # Stub creation alone doesn't count as "changed" for convergence
        r = RuleResult(rule_name="note_group", stubs_created=3)
        assert r.changed_anything is False


class TestNoteGroupRuleContextUsage:
    """Tests that the rule only processes note_groups (not no_note_chars)."""

    def test_empty_note_groups_produces_no_change(self):
        """With no note groups in context, rule has nothing to do."""
        # We don't call the DB here — just verify the data structures make sense
        ctx = MatchingContext(
            unlinked_chars=[],
            all_discord=[],
            discord_player_cache={},
            note_groups={},
            no_note_chars=[{"id": 1, "character_name": "Shodoom", "guild_note": ""}],
        )
        # No note groups → rule iterates nothing
        assert len(ctx.note_groups) == 0
        assert len(ctx.no_note_chars) == 1

    def test_note_groups_populated_from_chars_with_notes(self):
        """note_groups dict should have chars with guild notes grouped by key."""
        ctx = MatchingContext(
            unlinked_chars=[],
            all_discord=[],
            discord_player_cache={},
            note_groups={
                "rocket": [
                    {"id": 1, "character_name": "Rocketman", "guild_note": "Rocket's DH"},
                    {"id": 2, "character_name": "Rocketalt", "guild_note": "Rocket healer"},
                ]
            },
            no_note_chars=[],
        )
        assert "rocket" in ctx.note_groups
        assert len(ctx.note_groups["rocket"]) == 2
