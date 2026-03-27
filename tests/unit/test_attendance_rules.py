"""Unit tests for attendance rule evaluation logic.

All logic is pure Python — no DB or async required.
Functions mirror what will live in sv_common.guild_sync.attendance_processor.
Pattern matches tests/unit/test_attendance_snapshot.py.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Pure-Python mirrors of the rule evaluation helpers
# ---------------------------------------------------------------------------


def _compare(actual: float, operator: str, threshold: float) -> bool:
    """Evaluate `actual <operator> threshold`. Unknown operators return False."""
    if operator == ">=":
        return actual >= threshold
    if operator == ">":
        return actual > threshold
    if operator == "<=":
        return actual <= threshold
    if operator == "<":
        return actual < threshold
    if operator == "==":
        return actual == threshold
    return False


def _rule_auto_excused(
    was_available: bool | None,
    raid_helper_status: str | None,
    excuse_if_unavailable: bool,
    excuse_if_discord_absent: bool,
) -> bool:
    """Mirror of _compute_auto_excused in admin_routes.

    Returns True when the absent event should be counted as excused.
    NOTE: callers must gate on `not attended` before calling this —
    attending always overrides auto-excuse logic.
    """
    auto = False
    if excuse_if_unavailable and was_available is False:
        auto = True
    if excuse_if_discord_absent and raid_helper_status == "absence":
        auto = True
    return auto


def _eval_pct_condition(
    event_states: list[dict[str, Any]],
    operator: str,
    value: float,
) -> tuple[bool, dict[str, Any]]:
    """Evaluate an attendance_pct_in_window condition.

    Each item in event_states: {"attended": bool, "effectively_excused": bool}

    - excused events are removed from the denominator
    - pct = attended / eligible * 100
    - passes only if eligible > 0 AND _compare(pct, operator, value)

    Returns (passes, detail_dict).
    """
    total = len(event_states)
    excused_count = sum(1 for e in event_states if e["effectively_excused"])
    attended_count = sum(1 for e in event_states if e["attended"])
    eligible = total - excused_count

    if eligible <= 0:
        return False, {"pct": 0.0, "eligible": 0, "attended": attended_count}

    pct = 100.0 * attended_count / eligible
    passes = _compare(pct, operator, value)
    return passes, {"pct": pct, "eligible": eligible, "attended": attended_count}


def _eval_min_events_per_week(
    events_with_weeks: list[dict[str, Any]],
    operator: str,
    value: float,
) -> tuple[bool, dict[str, Any]]:
    """Evaluate a min_events_per_week condition.

    Each item: {"attended": bool, "effectively_excused": bool, "iso_week": (year, week_num)}

    - Group events by iso_week
    - Skip weeks where all events are effectively_excused (eligible == 0)
    - For each remaining week, count attended events; passes if _compare(attended, operator, value)
    - Overall: passes if has_eligible_weeks AND ALL eligible weeks pass

    Returns (passes, {"weeks_checked": N, "weeks_passed": N}).
    """
    # Group by iso_week
    weeks: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for event in events_with_weeks:
        key = event["iso_week"]
        weeks.setdefault(key, []).append(event)

    weeks_checked = 0
    weeks_passed = 0

    for week_events in weeks.values():
        eligible = [e for e in week_events if not e["effectively_excused"]]
        if not eligible:
            # Fully excused week — skip, doesn't count for or against
            continue
        weeks_checked += 1
        attended_this_week = sum(1 for e in eligible if e["attended"])
        if _compare(attended_this_week, operator, value):
            weeks_passed += 1

    if weeks_checked == 0:
        return False, {"weeks_checked": 0, "weeks_passed": 0}

    passes = weeks_passed == weeks_checked
    return passes, {"weeks_checked": weeks_checked, "weeks_passed": weeks_passed}


# ---------------------------------------------------------------------------
# TestCompare
# ---------------------------------------------------------------------------


class TestCompare:
    def test_gte_true(self):
        assert _compare(100.0, ">=", 100.0)

    def test_gte_false(self):
        assert not _compare(99.9, ">=", 100.0)

    def test_gt_true(self):
        assert _compare(100.1, ">", 100.0)

    def test_gt_false_equal(self):
        assert not _compare(100.0, ">", 100.0)

    def test_lte_true(self):
        assert _compare(50.0, "<=", 50.0)

    def test_lte_false(self):
        assert not _compare(50.1, "<=", 50.0)

    def test_lt_true(self):
        assert _compare(49.9, "<", 50.0)

    def test_lt_false_equal(self):
        assert not _compare(50.0, "<", 50.0)

    def test_eq_true(self):
        assert _compare(75.0, "==", 75.0)

    def test_eq_false(self):
        assert not _compare(75.0, "==", 74.9)

    def test_unknown_operator_returns_false(self):
        assert not _compare(100.0, "!=", 50.0)
        assert not _compare(100.0, "~=", 100.0)
        assert not _compare(100.0, "", 100.0)


# ---------------------------------------------------------------------------
# TestRuleAutoExcused
# ---------------------------------------------------------------------------


class TestRuleAutoExcused:
    """Tests for _rule_auto_excused — the attending-override pattern is
    demonstrated explicitly."""

    def test_both_off_never_excused(self):
        assert not _rule_auto_excused(False, "absence", False, False)
        assert not _rule_auto_excused(True, "accepted", False, False)
        assert not _rule_auto_excused(None, None, False, False)

    def test_unavailable_triggers_when_setting_on(self):
        assert _rule_auto_excused(False, "accepted", True, False)

    def test_available_does_not_trigger_unavailable_rule(self):
        assert not _rule_auto_excused(True, "accepted", True, False)

    def test_null_available_does_not_trigger_unavailable_rule(self):
        # was_available=None means no snapshot — should NOT count as unavailable
        assert not _rule_auto_excused(None, "accepted", True, False)

    def test_discord_absent_triggers_when_setting_on(self):
        assert _rule_auto_excused(True, "absence", False, True)

    def test_discord_non_absence_statuses_do_not_trigger(self):
        for status in ("accepted", "tentative", "bench", "unknown", None):
            assert not _rule_auto_excused(True, status, False, True)

    def test_both_settings_on_either_condition_triggers(self):
        assert _rule_auto_excused(False, "accepted", True, True)   # unavailable
        assert _rule_auto_excused(True, "absence", True, True)     # discord absent
        assert _rule_auto_excused(False, "absence", True, True)    # both

    def test_both_settings_on_neither_condition_no_excuse(self):
        assert not _rule_auto_excused(True, "accepted", True, True)

    def test_unavailable_when_attending_not_excused(self):
        """Attending overrides: caller gates on `not attended` before calling helper."""
        attended = True
        # Simulate the call-site gate: if attending, we skip the helper entirely
        result = (not attended) and _rule_auto_excused(False, "absence", True, True)
        assert not result

    def test_absent_unavailable_is_excused(self):
        attended = False
        result = (not attended) and _rule_auto_excused(False, "absence", True, True)
        assert result


# ---------------------------------------------------------------------------
# TestEvalPctCondition
# ---------------------------------------------------------------------------


def _make_event(attended: bool, excused: bool) -> dict[str, Any]:
    return {"attended": attended, "effectively_excused": excused}


class TestEvalPctCondition:
    def test_basic_100pct(self):
        events = [_make_event(True, False)] * 4
        passes, detail = _eval_pct_condition(events, ">=", 100)
        assert passes
        assert detail["pct"] == 100.0
        assert detail["eligible"] == 4
        assert detail["attended"] == 4

    def test_excused_excluded_from_denominator(self):
        # 3 attended, 1 excused → eligible=3, pct=100%
        events = [_make_event(True, False)] * 3 + [_make_event(False, True)]
        passes, detail = _eval_pct_condition(events, ">=", 100)
        assert passes
        assert detail["pct"] == 100.0
        assert detail["eligible"] == 3
        assert detail["attended"] == 3

    def test_all_excused_does_not_pass(self):
        events = [_make_event(False, True)] * 4
        passes, detail = _eval_pct_condition(events, ">=", 0)
        assert not passes
        assert detail["eligible"] == 0
        assert detail["pct"] == 0.0

    def test_no_events_does_not_pass(self):
        passes, detail = _eval_pct_condition([], ">=", 0)
        assert not passes
        assert detail["eligible"] == 0

    def test_partial_attendance_fails_100pct_threshold(self):
        # 2 attended, 1 absent, 1 excused → eligible=3, pct=66.7%
        events = [
            _make_event(True, False),
            _make_event(True, False),
            _make_event(False, False),
            _make_event(False, True),
        ]
        passes, detail = _eval_pct_condition(events, ">=", 100)
        assert not passes
        assert abs(detail["pct"] - 66.666) < 0.01
        assert detail["eligible"] == 3
        assert detail["attended"] == 2

    def test_95pct_threshold_passes(self):
        # 19 attended, 1 absent → eligible=20, pct=95%
        events = [_make_event(True, False)] * 19 + [_make_event(False, False)]
        passes, detail = _eval_pct_condition(events, ">=", 95)
        assert passes
        assert detail["pct"] == 95.0
        assert detail["eligible"] == 20

    def test_95pct_threshold_fails_one_short(self):
        # 18 attended, 2 absent → eligible=20, pct=90%
        events = [_make_event(True, False)] * 18 + [_make_event(False, False)] * 2
        passes, detail = _eval_pct_condition(events, ">=", 95)
        assert not passes
        assert detail["pct"] == 90.0

    def test_zero_pct_passes_lte_threshold(self):
        events = [_make_event(False, False)] * 3
        passes, detail = _eval_pct_condition(events, "<=", 50)
        assert passes
        assert detail["pct"] == 0.0
        assert detail["eligible"] == 3


# ---------------------------------------------------------------------------
# TestEvalMinEventsPerWeek
# ---------------------------------------------------------------------------


def _make_week_event(
    attended: bool,
    excused: bool,
    year: int,
    week: int,
) -> dict[str, Any]:
    return {
        "attended": attended,
        "effectively_excused": excused,
        "iso_week": (year, week),
    }


class TestEvalMinEventsPerWeek:
    def test_all_weeks_pass(self):
        # 2 weeks, 2 attended events each
        events = [
            _make_week_event(True, False, 2026, 1),
            _make_week_event(True, False, 2026, 1),
            _make_week_event(True, False, 2026, 2),
            _make_week_event(True, False, 2026, 2),
        ]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert passes
        assert detail["weeks_checked"] == 2
        assert detail["weeks_passed"] == 2

    def test_one_week_miss_fails(self):
        # Week 1: 1 attended. Week 2: 0 attended, 1 absent.
        events = [
            _make_week_event(True, False, 2026, 1),
            _make_week_event(False, False, 2026, 2),
        ]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert not passes
        assert detail["weeks_checked"] == 2
        assert detail["weeks_passed"] == 1

    def test_excused_week_skipped(self):
        # Week 1: 1 attended. Week 2: only excused events → skip week 2.
        # Only 1 week checked; passes if week 1 passes.
        events = [
            _make_week_event(True, False, 2026, 1),
            _make_week_event(False, True, 2026, 2),
            _make_week_event(False, True, 2026, 2),
        ]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert passes
        assert detail["weeks_checked"] == 1
        assert detail["weeks_passed"] == 1

    def test_no_eligible_events_does_not_pass(self):
        # All excused across all weeks
        events = [
            _make_week_event(False, True, 2026, 1),
            _make_week_event(False, True, 2026, 2),
        ]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert not passes
        assert detail["weeks_checked"] == 0

    def test_no_events_does_not_pass(self):
        passes, detail = _eval_min_events_per_week([], ">=", 1)
        assert not passes
        assert detail["weeks_checked"] == 0
        assert detail["weeks_passed"] == 0

    def test_single_week_single_event_attended_passes(self):
        events = [_make_week_event(True, False, 2026, 5)]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert passes
        assert detail["weeks_checked"] == 1
        assert detail["weeks_passed"] == 1

    def test_single_week_single_event_absent_fails(self):
        events = [_make_week_event(False, False, 2026, 5)]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert not passes
        assert detail["weeks_checked"] == 1
        assert detail["weeks_passed"] == 0

    def test_mixed_excused_and_unexcused_in_same_week(self):
        # Week 1: 1 excused absent + 1 attended → eligible=1, attended=1 → passes
        events = [
            _make_week_event(False, True, 2026, 1),
            _make_week_event(True, False, 2026, 1),
        ]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert passes
        assert detail["weeks_checked"] == 1
        assert detail["weeks_passed"] == 1

    def test_three_weeks_one_miss_in_middle(self):
        events = [
            _make_week_event(True, False, 2026, 10),
            _make_week_event(False, False, 2026, 11),  # missed
            _make_week_event(True, False, 2026, 12),
        ]
        passes, detail = _eval_min_events_per_week(events, ">=", 1)
        assert not passes
        assert detail["weeks_checked"] == 3
        assert detail["weeks_passed"] == 2

    def test_min_two_events_per_week(self):
        # Threshold: >=2 attended events per week
        events = [
            _make_week_event(True, False, 2026, 1),
            _make_week_event(True, False, 2026, 1),   # 2 attended this week
            _make_week_event(True, False, 2026, 2),
            _make_week_event(False, False, 2026, 2),  # only 1 attended week 2
        ]
        passes, detail = _eval_min_events_per_week(events, ">=", 2)
        assert not passes
        assert detail["weeks_checked"] == 2
        assert detail["weeks_passed"] == 1


# ---------------------------------------------------------------------------
# TestRuleOnlyAppliesToTargetRank
# ---------------------------------------------------------------------------


class TestRuleOnlyAppliesToTargetRank:
    """Simulate the target_rank_ids filtering that the rule engine applies
    before passing events to the condition evaluators."""

    def _players_matching_rule(
        self,
        players: list[dict[str, Any]],
        target_rank_ids: list[int],
    ) -> list[dict[str, Any]]:
        return [p for p in players if p["rank_id"] in target_rank_ids]

    def test_player_wrong_rank_excluded(self):
        players = [
            {"player_id": 1, "rank_id": 2},
            {"player_id": 2, "rank_id": 3},
            {"player_id": 3, "rank_id": 5},  # wrong rank
        ]
        rule_target = [2, 3]
        matched = self._players_matching_rule(players, rule_target)
        matched_ids = [p["player_id"] for p in matched]
        assert 1 in matched_ids
        assert 2 in matched_ids
        assert 3 not in matched_ids

    def test_no_players_match_empty_result(self):
        players = [{"player_id": 1, "rank_id": 9}]
        matched = self._players_matching_rule(players, [2, 3])
        assert matched == []

    def test_all_players_match(self):
        players = [
            {"player_id": 1, "rank_id": 2},
            {"player_id": 2, "rank_id": 2},
        ]
        matched = self._players_matching_rule(players, [2])
        assert len(matched) == 2

    def test_empty_player_list(self):
        matched = self._players_matching_rule([], [2, 3])
        assert matched == []


# ---------------------------------------------------------------------------
# TestAttendingOverridesAutoExcuse
# ---------------------------------------------------------------------------


class TestAttendingOverridesAutoExcuse:
    """Verify that an attending player is never counted as effectively_excused,
    even when both auto-excuse settings would fire if they were absent."""

    def _compute_effectively_excused(
        self,
        attended: bool,
        was_available: bool | None,
        raid_helper_status: str | None,
        excuse_if_unavailable: bool,
        excuse_if_discord_absent: bool,
    ) -> bool:
        """Simulate the full call-site logic: gate on attended first."""
        if attended:
            return False
        return _rule_auto_excused(
            was_available,
            raid_helper_status,
            excuse_if_unavailable,
            excuse_if_discord_absent,
        )

    def test_attending_player_not_auto_excused(self):
        result = self._compute_effectively_excused(
            attended=True,
            was_available=False,
            raid_helper_status="absence",
            excuse_if_unavailable=True,
            excuse_if_discord_absent=True,
        )
        assert not result

    def test_absent_unavailable_player_is_excused(self):
        result = self._compute_effectively_excused(
            attended=False,
            was_available=False,
            raid_helper_status="accepted",
            excuse_if_unavailable=True,
            excuse_if_discord_absent=False,
        )
        assert result

    def test_absent_discord_absent_player_is_excused(self):
        result = self._compute_effectively_excused(
            attended=False,
            was_available=True,
            raid_helper_status="absence",
            excuse_if_unavailable=False,
            excuse_if_discord_absent=True,
        )
        assert result

    def test_attending_player_counts_as_eligible_and_attended(self):
        """End-to-end: attending player with would-be-excused snapshot data
        should count as eligible=True, attended=True in pct calculation."""
        # Player attended every event. Snapshot says unavailable + discord_absent.
        # Since attended=True, effectively_excused must be False.
        events_raw = [
            {
                "attended": True,
                "was_available": False,
                "raid_helper_status": "absence",
            }
        ] * 4

        event_states = []
        for e in events_raw:
            eff_excused = self._compute_effectively_excused(
                attended=e["attended"],
                was_available=e["was_available"],
                raid_helper_status=e["raid_helper_status"],
                excuse_if_unavailable=True,
                excuse_if_discord_absent=True,
            )
            event_states.append({"attended": e["attended"], "effectively_excused": eff_excused})

        passes, detail = _eval_pct_condition(event_states, ">=", 100)
        assert passes
        assert detail["eligible"] == 4
        assert detail["attended"] == 4
        assert detail["pct"] == 100.0

    def test_absent_available_player_not_excused(self):
        """A player who was available and just didn't show — not auto-excused."""
        result = self._compute_effectively_excused(
            attended=False,
            was_available=True,
            raid_helper_status="accepted",
            excuse_if_unavailable=True,
            excuse_if_discord_absent=True,
        )
        assert not result
