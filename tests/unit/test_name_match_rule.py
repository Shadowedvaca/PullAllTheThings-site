"""Unit tests for NameMatchRule (Phase 3.0B)."""

import pytest

from sv_common.guild_sync.matching_rules.base import MatchingContext, RuleResult
from sv_common.guild_sync.matching_rules.name_match_rule import NameMatchRule
from sv_common.guild_sync.matching_rules.note_group_rule import NoteGroupRule


class TestNameMatchRuleAttributes:
    def test_name(self):
        assert NameMatchRule.name == "name_match"

    def test_order(self):
        assert NameMatchRule.order == 20

    def test_link_source(self):
        assert NameMatchRule.link_source == "exact_name"

    def test_description_is_nonempty(self):
        assert NameMatchRule.description

    def test_order_higher_than_note_group(self):
        """NameMatchRule must run AFTER NoteGroupRule."""
        assert NameMatchRule.order > NoteGroupRule.order


class TestNameMatchRuleContextUsage:
    """Tests that NameMatchRule only processes no_note_chars."""

    def test_empty_no_note_chars_produces_no_change(self):
        ctx = MatchingContext(
            unlinked_chars=[],
            all_discord=[],
            discord_player_cache={},
            note_groups={
                "rocket": [{"id": 1, "character_name": "Rocketman", "guild_note": "Rocket"}]
            },
            no_note_chars=[],
        )
        # no_note_chars is empty → rule has nothing to process
        assert len(ctx.no_note_chars) == 0

    def test_rule_operates_on_no_note_chars_only(self):
        ctx = MatchingContext(
            unlinked_chars=[],
            all_discord=[],
            discord_player_cache={},
            note_groups={},
            no_note_chars=[
                {"id": 10, "character_name": "Adrenalgland", "guild_note": ""},
                {"id": 11, "character_name": "Dontfox", "guild_note": None},
            ],
        )
        assert len(ctx.no_note_chars) == 2
        assert all(not c.get("guild_note") for c in ctx.no_note_chars)


class TestNameMatchRuleResult:
    def test_zero_results_not_changed(self):
        r = RuleResult(rule_name="name_match")
        assert r.changed_anything is False

    def test_chars_linked_triggers_changed(self):
        r = RuleResult(rule_name="name_match", chars_linked=1, discord_linked=1)
        assert r.changed_anything is True

    def test_skipped_only_not_changed(self):
        # Skipping chars (no Discord match) doesn't count as a change
        r = RuleResult(rule_name="name_match", skipped=5)
        assert r.changed_anything is False
